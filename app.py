"""
Outdoor Adventure Day 2026 - Activity Sign-up App
Friends of Big Bear Valley (FOBBV)

A small web app where people sign up for activity time slots. On signup it:
  - checks the slot still has room (and reserves it safely),
  - saves the registration to a list you can review/export,
  - texts the person a confirmation + "ticket" via Twilio (optional).

Everything is configured through the .env file. If Twilio is not set up yet,
the app still works -- it just shows the ticket on screen instead of texting.

Run:  python app.py    (then open the printed address in a browser)
"""

import os
import re
import csv
import io
import sqlite3
import secrets
import json
import base64
import threading
import smtplib
import urllib.request
import urllib.error
from email.message import EmailMessage
from datetime import datetime
from flask import (
    Flask, request, jsonify, render_template, redirect,
    url_for, Response, g, abort, session, send_file,
)

# Load .env if present (so TWILIO_* and ADMIN_KEY are available)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DB location can be overridden so a host (e.g. Render persistent disk) can point
# it at durable storage. Locally it defaults to data/oad.db.
DB_PATH = os.environ.get("DATABASE_FILE") or os.path.join(BASE_DIR, "data", "oad.db")
ACTIVITIES_JSON = os.path.join(BASE_DIR, "data", "activities.json")

# ----- Config (from environment / .env) -----------------------------------
ADMIN_KEY = os.environ.get("ADMIN_KEY", "fobbv2026")     # organizer list page
STAFF_PIN = os.environ.get("STAFF_PIN", "bigbear")       # booth staff login
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")  # signs cookies
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
DEFAULT_COUNTRY_CODE = os.environ.get("DEFAULT_COUNTRY_CODE", "1")  # US

# Public base URL (used to build absolute ticket links + QR codes). If unset we
# derive it from the incoming request and force https for the hosted site.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Email (optional) — sends a ticket to participants who give an email address.
# Works with any SMTP provider (Gmail app password, SendGrid, Mailgun, etc.).
# If these are unset the app simply skips email, same as Twilio.
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
EMAIL_FROM = (os.environ.get("EMAIL_FROM", "") or SMTP_USER).strip()
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Friends of Big Bear Valley").strip()

# Show the participant self-service opt-in box on the ticket page? Off by default
# (booth volunteers collect contact info). Set SHOW_TICKET_OPTIN=1 to turn it on
# for future events where participants opt in themselves.
SHOW_TICKET_OPTIN = os.environ.get("SHOW_TICKET_OPTIN", "").strip().lower() in ("1", "true", "yes", "on")

# === GitHub sync — pushes data/activities.json back to the repo when the
# curated activity list changes via /admin/activities. The actual push runs
# in a background thread debounced by GITHUB_SYNC_DEBOUNCE_SECONDS so a burst
# of edits (e.g. auto-save firing on each blur) becomes ONE commit.
# Disabled if GITHUB_TOKEN is unset; the admin UI still loads normally.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "VisibleIntellect/oad-signup").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
GITHUB_FILE_PATH = os.environ.get("GITHUB_FILE_PATH", "data/activities.json").strip()
GITHUB_SYNC_DEBOUNCE_SECONDS = int(os.environ.get("GITHUB_SYNC_DEBOUNCE_SECONDS", "10"))

app = Flask(__name__)
app.secret_key = SECRET_KEY

with open(ACTIVITIES_JSON, "r") as f:
    EVENT_DATA = json.load(f)
EVENT = EVENT_DATA["event"]

# Logo: if you drop the official PNG at static/logo.png it is used everywhere.
# Otherwise this built-in Big Bear emblem is served, so a logo ALWAYS shows.
LOGO_PNG = os.path.join(BASE_DIR, "static", "logo.png")
DEFAULT_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<circle cx="50" cy="50" r="48" fill="#ffffff" stroke="#2f6a45" stroke-width="4"/>
<clipPath id="r"><circle cx="50" cy="50" r="46"/></clipPath>
<g clip-path="url(#r)">
<rect width="100" height="100" fill="#eef3ea"/>
<circle cx="50" cy="42" r="12" fill="#d9a84e"/>
<path d="M-5 80 L22 48 L42 72 L60 46 L82 74 L106 50 L106 101 L-5 101 Z" fill="#3a7d54"/>
<path d="M-5 88 L26 62 L50 86 L74 60 L106 88 L106 101 L-5 101 Z" fill="#24512f"/>
</g>
<path d="M27 46 Q41 36 48 45 L50 41.5 L52 45 Q59 36 73 46" fill="none"
 stroke="#33291c" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def _logo_response():
    if os.path.exists(LOGO_PNG):
        return send_file(LOGO_PNG, mimetype="image/png")
    return Response(DEFAULT_LOGO_SVG, mimetype="image/svg+xml")


# ----- Database -------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        # WAL gives nicer concurrency but FAILS on iCloud/Dropbox-synced folders.
        # Try it; fall back to the default journal mode if the folder rejects it.
        try:
            g.db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slot TEXT,
            capacity INTEGER NOT NULL,
            registered INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER,
            location TEXT
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            party_size INTEGER NOT NULL,
            activity_id TEXT NOT NULL,
            activity_name TEXT NOT NULL,
            slot TEXT,
            registered_by TEXT,
            created_at TEXT NOT NULL
        )""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            party_size INTEGER NOT NULL,
            activity_id TEXT NOT NULL,
            activity_name TEXT NOT NULL,
            slot TEXT,
            sms_status TEXT,
            registered_by TEXT,
            created_at TEXT NOT NULL
        )""")
    # Defensive migrations: add columns if an older DB predates them.
    cols = [r[1] for r in db.execute("PRAGMA table_info(registrations)").fetchall()]
    if "registered_by" not in cols:
        db.execute("ALTER TABLE registrations ADD COLUMN registered_by TEXT")
    if "consent_at" not in cols:
        db.execute("ALTER TABLE registrations ADD COLUMN consent_at TEXT")
    if "consent_source" not in cols:
        db.execute("ALTER TABLE registrations ADD COLUMN consent_source TEXT")
    acols = [r[1] for r in db.execute("PRAGMA table_info(activities)").fetchall()]
    if "location" not in acols:
        db.execute("ALTER TABLE activities ADD COLUMN location TEXT")
    # Seed activities once (only if table empty). Edit data/activities.json and
    # delete data/oad.db to re-seed from scratch.
    count = db.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    if count == 0:
        for i, a in enumerate(EVENT_DATA["activities"]):
            db.execute(
                "INSERT INTO activities (id,name,slot,capacity,registered,sort_order,location)"
                " VALUES (?,?,?,?,?,?,?)",
                (a["id"], a["name"], a.get("slot"), int(a["capacity"]),
                 int(a.get("registered", 0)), i, a.get("location")),
            )
    db.commit()
    db.close()


# ----- Helpers --------------------------------------------------------------
def pretty_slot(slot):
    """Turn '09:30' into '9:30 AM'. Returns 'See schedule' when blank."""
    if not slot:
        return "See schedule"
    try:
        return datetime.strptime(slot, "%H:%M").strftime("%-I:%M %p")
    except Exception:
        return slot


def normalize_phone(raw):
    """Best-effort E.164 (+1XXXXXXXXXX) for US numbers; returns None if junk."""
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("+"):
        return digits
    digits = re.sub(r"\D", "", digits)
    if len(digits) == 10:
        return "+" + DEFAULT_COUNTRY_CODE + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if digits:
        return "+" + digits
    return None


def make_ticket_code():
    return "OAD-" + secrets.token_hex(3).upper()


def build_message(name, activity_name, slot, party_size, code):
    return (
        f"FOBBV {EVENT['name']} - You're confirmed!\n"
        f"Name: {name}\n"
        f"Activity: {activity_name} @ {pretty_slot(slot)}\n"
        f"Spots: {party_size}\n"
        f"Ticket: {code}\n"
        f"{EVENT['date']} - {EVENT['location']}\n"
        f"Show this text at the activity. See you there!\n"
        f"Reply STOP to opt out, HELP for help."
    )


def send_sms(to_phone, body):
    """Returns a status string. Never raises."""
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM):
        return "not_configured"
    if not to_phone:
        return "no_phone"
    try:
        from twilio.rest import Client
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(body=body, from_=TWILIO_FROM, to=to_phone)
        return "sent"
    except Exception as e:
        app.logger.warning("Twilio send failed: %s", e)
        return "failed"


def base_url():
    """Absolute site root, e.g. https://oad-availability.onrender.com — used to
    build ticket links and QR codes. Prefers PUBLIC_BASE_URL, else derives from
    the request and upgrades http->https for the hosted site."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    root = request.host_url.rstrip("/")
    if root.startswith("http://") and not any(
        h in root for h in ("localhost", "127.0.0.1", "0.0.0.0")
    ):
        root = "https://" + root[len("http://"):]
    return root


def ticket_url(code):
    return f"{base_url()}/t/{code}"


def build_ticket_email(name, activity_name, slot, party_size, code, turl, loc=None):
    """Returns (plain_text, html) for a ticket confirmation email."""
    when = f"{activity_name} @ {pretty_slot(slot)}"
    meet_line = f"Meet at: {loc}\n" if loc else ""
    meet_row = (
        "<tr><td style=\"color:#6f6a5d;padding:4px 0\">Meet at</td>"
        f"<td style=\"font-weight:600;text-align:right\">{loc}</td></tr>"
    ) if loc else ""
    text = (
        f"You're confirmed for {EVENT['name']}!\n\n"
        f"Name: {name}\n"
        f"Activity: {when}\n"
        f"Spots: {party_size}\n"
        f"Ticket: {code}\n"
        f"{meet_line}"
        f"Date: {EVENT['date']}\n\n"
        f"View or show your ticket: {turl}\n\n"
        f"Show your ticket (this email or the link) at the activity. See you there!\n"
        f"— Friends of Big Bear Valley"
    )
    html = (
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        "max-width:460px;margin:0 auto;border:1px solid #e6dfce;border-radius:14px;overflow:hidden\">"
        "<div style=\"background:linear-gradient(160deg,#2f6a45,#1f4e31);color:#fff;padding:18px 20px;text-align:center\">"
        "<div style=\"font-size:.8rem;letter-spacing:.05em;text-transform:uppercase;opacity:.9\">Friends of Big Bear Valley</div>"
        f"<div style=\"font-size:1.25rem;font-weight:700;margin-top:2px\">{EVENT['name']}</div>"
        "<div style=\"margin-top:4px\">You're confirmed!</div></div>"
        "<div style=\"padding:20px\">"
        f"<div style=\"text-align:center;font-size:1.5rem;font-weight:800;letter-spacing:1px;color:#1f4e31;"
        f"border:2px dashed #2f6a45;border-radius:12px;padding:12px;margin-bottom:16px\">{code}</div>"
        "<table style=\"width:100%;border-collapse:collapse;font-size:.95rem\">"
        f"<tr><td style=\"color:#6f6a5d;padding:4px 0\">Name</td><td style=\"font-weight:600;text-align:right\">{name}</td></tr>"
        f"<tr><td style=\"color:#6f6a5d;padding:4px 0\">Activity</td><td style=\"font-weight:600;text-align:right\">{when}</td></tr>"
        f"<tr><td style=\"color:#6f6a5d;padding:4px 0\">Spots</td><td style=\"font-weight:600;text-align:right\">{party_size}</td></tr>"
        f"{meet_row}"
        f"<tr><td style=\"color:#6f6a5d;padding:4px 0\">Date</td><td style=\"font-weight:600;text-align:right\">{EVENT['date']}</td></tr>"
        "</table>"
        f"<div style=\"text-align:center;margin-top:18px\"><a href=\"{turl}\" "
        "style=\"display:inline-block;background:#2f6a45;color:#fff;text-decoration:none;padding:11px 20px;"
        "border-radius:10px;font-weight:700\">View your ticket</a></div>"
        "<p style=\"color:#6f6a5d;font-size:.85rem;text-align:center;margin:16px 0 0\">"
        "Show this email or your ticket link at the activity. See you there!</p>"
        "</div></div>"
    )
    return text, html


def send_email(to_addr, subject, text_body, html_body=None):
    """Best-effort SMTP send. Returns a status string, never raises."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and EMAIL_FROM):
        return "email_not_configured"
    if not to_addr:
        return "no_email"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
        msg["To"] = to_addr
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        return "sent"
    except Exception as e:
        app.logger.warning("Email send failed: %s", e)
        return "failed"


def require_admin():
    key = request.args.get("key") or request.form.get("key")
    if key != ADMIN_KEY:
        abort(401)
    return key


# ----- Public routes --------------------------------------------------------
@app.route("/")
def board():
    # Public, read-only availability board. This is the QR-code target.
    # Shows only activities + spots remaining -- no names, phones, or emails.
    return render_template("board.html", event=EVENT)


@app.route("/logo")
def logo():
    return _logo_response()


@app.route("/t/<code>")
def ticket_page(code):
    """Public, branded ticket a participant can pull up on their own phone
    (the QR-code target from the booth screen). Shows only their own booking."""
    db = get_db()
    r = db.execute(
        "SELECT * FROM registrations WHERE ticket_code=?", (code,)
    ).fetchone()
    if not r:
        resp = _policy_page(
            "Ticket not found",
            "<p>Sorry, we couldn't find that ticket. Please double-check the link, "
            "or see a Friends of Big Bear Valley volunteer at the registration table.</p>",
        )
        resp.status_code = 404
        return resp
    loc_row = db.execute(
        "SELECT location FROM activities WHERE id=?", (r["activity_id"],)
    ).fetchone()
    loc = loc_row["location"] if loc_row else None
    return render_template(
        "ticket.html", event=EVENT, r=r, loc=loc, pretty_slot=pretty_slot,
        show_optin=SHOW_TICKET_OPTIN,
    )


def _branded_qr_png(url, logo_frac=0.45):
    """Branded QR: FOBBV-green modules with the eagle/bear logo set into the
    center along its actual silhouette (thin white halo for legibility). High
    error correction keeps it scannable. Returns PNG bytes."""
    import qrcode
    from PIL import Image, ImageFilter
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H,
                       box_size=12, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1f4e31", back_color="white").convert("RGBA")
    W, H = img.size
    logo = Image.open(os.path.join(BASE_DIR, "static", "logo.png")).convert("RGBA")
    target = int(W * logo_frac)
    lw, lh = logo.size
    s = target / max(lw, lh)
    logo = logo.resize((max(1, int(lw * s)), max(1, int(lh * s))), Image.LANCZOS)
    lw, lh = logo.size
    halo = logo.split()[3].point(lambda a: 255 if a > 40 else 0)
    for _ in range(3):
        halo = halo.filter(ImageFilter.MaxFilter(5))
    pos = ((W - lw) // 2, (H - lh) // 2)
    img.paste(Image.new("RGBA", (lw, lh), (255, 255, 255, 255)), pos, halo)
    img.paste(logo, pos, logo)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


@app.route("/ticketqr/<code>.png")
def ticket_qr(code):
    """Branded QR (FOBBV logo centered) encoding the participant's ticket URL.
    Falls back to a plain QR if Pillow or the logo file is unavailable."""
    url = ticket_url(code)
    try:
        resp = Response(_branded_qr_png(url), mimetype="image/png")
    except Exception as e:
        app.logger.warning("Branded QR failed (%s); falling back to plain SVG", e)
        try:
            import qrcode
            import qrcode.image.svg
            qr = qrcode.QRCode(box_size=10, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            b = io.BytesIO()
            qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(b)
            resp = Response(b.getvalue().decode("utf-8"), mimetype="image/svg+xml")
        except Exception:
            resp = Response(
                "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'></svg>",
                mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


# ----- SMS compliance pages (for Twilio A2P 10DLC registration) -------------
SMS_PRIVACY_BODY = (
    "<p><strong>Outdoor Adventure Day (OAD) text messages.</strong> This policy explains how we "
    "handle mobile phone numbers and text messages for the Outdoor Adventure Day activity sign-up.</p>"
    "<h2>What we collect and why</h2>"
    "<p>When you register for an activity time slot, a Friends of Big Bear Valley volunteer collects "
    "your name and mobile phone number for one purpose only: to send you a confirmation text with your "
    "ticket details.</p>"
    "<h2>We do not share or sell your information</h2>"
    "<p>We do <strong>not</strong> sell, rent, or share your mobile phone number or your SMS consent "
    "with any third parties or affiliates for marketing or promotional purposes. No mobile information "
    "is shared with third parties for their own marketing or promotional purposes. Your number is shared "
    "only with our messaging provider solely to deliver the confirmation you requested.</p>"
    "<h2>Message frequency and cost</h2>"
    "<p>This is a transactional program — you typically receive one message per registration. "
    "Message and data rates may apply.</p>"
    "<h2>Opting out and help</h2>"
    "<p>Reply <strong>STOP</strong> to any message to opt out at any time. Reply <strong>HELP</strong> "
    "for assistance, or contact us at "
    "<a href=\"https://friendsofbigbearvalley.org/contact/\">friendsofbigbearvalley.org/contact</a>.</p>"
)

SMS_TERMS_BODY = (
    "<p><strong>Friends of Big Bear Valley — Outdoor Adventure Day (OAD) text messages.</strong></p>"
    "<h2>Program description</h2>"
    "<p>When you register for an activity at Friends of Big Bear Valley's Outdoor Adventure Day, you "
    "will receive a one-time SMS text message confirming your activity, time slot, number of spots, "
    "and ticket code. This is a transactional confirmation only — not a marketing or recurring "
    "program.</p>"
    "<h2>How you opt in (consent)</h2>"
    "<p>Outdoor Adventure Day is a free, in-person community event hosted by Friends of Big Bear "
    "Valley at the Christmas Tree Lot in Big Bear Lake, California. At the event's staffed "
    "registration table, a Friends of Big Bear Valley volunteer asks whether you would like a "
    "text-message confirmation of your activity reservation. You opt in by giving the volunteer your "
    "mobile number and agreeing to receive the confirmation text; the volunteer enters your number "
    "only after you agree. The following consent statement is presented at the registration table at "
    "the point of sign-up:</p>"
    "<p style=\"border-left:4px solid #2f6a45;padding:8px 14px;background:#f3f7f2;font-style:italic\">"
    "“By providing your mobile number, you agree to receive a one-time SMS text message from "
    "Friends of Big Bear Valley confirming your Outdoor Adventure Day activity reservation. Message and "
    "data rates may apply. Reply STOP to opt out, HELP for help.”</p>"
    "<p>Providing your number is optional. If you would rather not receive a text, the volunteer will "
    "give you a printed or on-screen confirmation instead. Your consent is for this single confirmation "
    "message only — your number is not used for any other purpose and is never shared or sold.</p>"
    "<h2>Sample message</h2>"
    "<p style=\"border-left:4px solid #2f6a45;padding:8px 14px;background:#f3f7f2;white-space:pre-line\">"
    "FOBBV Outdoor Adventure Day (OAD) 2026 - You're confirmed!\n"
    "Activity: Kayak @ 10:00 AM\n"
    "Spots: 2\n"
    "Ticket: OAD-1A2B3C\n"
    "July 11, 2026 - Christmas Tree Lot, Big Bear Lake\n"
    "Show this text at the activity. Reply STOP to opt out, HELP for help.</p>"
    "<h2>Message frequency</h2>"
    "<p>Transactional — generally one message per registration.</p>"
    "<h2>Cost</h2>"
    "<p>Message and data rates may apply.</p>"
    "<h2>To opt out or get help</h2>"
    "<p>Reply <strong>STOP</strong> at any time to stop receiving messages. Reply <strong>HELP</strong> "
    "for help, or contact us at "
    "<a href=\"https://friendsofbigbearvalley.org/contact/\">friendsofbigbearvalley.org/contact</a>.</p>"
    "<h2>Privacy</h2>"
    "<p>See our <a href=\"/sms-privacy\">SMS Privacy Policy</a> for how we handle your mobile number. "
    "We do not sell or share mobile numbers or SMS consent with third parties for marketing.</p>"
    "<h2>Disclaimer</h2>"
    "<p>Carriers are not liable for delayed or undelivered messages. Supported carriers include major "
    "U.S. mobile carriers.</p>"
)


def _policy_page(title, body):
    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>" + title + " - Friends of Big Bear Valley</title><style>"
        "body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;"
        "background:#f5f1e7;color:#28251f;line-height:1.55}"
        "header{background:linear-gradient(160deg,#2f6a45,#1f4e31);color:#fff;padding:20px 18px;text-align:center}"
        "header .org{font-size:.9rem;letter-spacing:.04em;text-transform:uppercase;color:#dcebdf}"
        "header h1{margin:4px 0 0;font-size:1.4rem}"
        ".wrap{max-width:760px;margin:0 auto;padding:22px 18px}"
        ".card{background:#fff;border:1px solid #e6dfce;border-radius:14px;padding:20px 24px}"
        "h2{color:#1f4e31;font-size:1.05rem;margin:20px 0 6px}a{color:#2f6a45}"
        "</style></head><body><header><div class=\"org\">Friends of Big Bear Valley</div>"
        "<h1>" + title + "</h1></header><div class=\"wrap\"><div class=\"card\">" + body +
        "</div></div></body></html>"
    )
    return Response(page, mimetype="text/html")


@app.route("/sms-privacy")
def sms_privacy():
    return _policy_page("SMS Privacy Policy", SMS_PRIVACY_BODY)


@app.route("/sms-terms")
def sms_terms():
    return _policy_page("SMS Terms &amp; Conditions", SMS_TERMS_BODY)


@app.route("/signup")
def signup_page():
    # Booth-staff-only page used to register people (and send the text).
    # Protected by a staff PIN because it lives on the public internet.
    if not session.get("booth_ok"):
        return render_template("booth_login.html", event=EVENT, error=None)
    return render_template("index.html", event=EVENT)


@app.route("/booth-login", methods=["POST"])
def booth_login():
    pin = (request.form.get("pin") or "").strip()
    if pin == STAFF_PIN:
        session["booth_ok"] = True
        session.permanent = True
        return redirect(url_for("signup_page"))
    return render_template("booth_login.html", event=EVENT,
                           error="Incorrect PIN — please try again.")


@app.route("/booth-logout")
def booth_logout():
    session.pop("booth_ok", None)
    return redirect(url_for("signup_page"))


@app.route("/api/activities")
def api_activities():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM activities ORDER BY sort_order"
    ).fetchall()
    groups = {}
    for r in rows:
        left = r["capacity"] - r["registered"]
        groups.setdefault(r["name"], []).append({
            "id": r["id"],
            "slot": r["slot"],
            "slot_label": pretty_slot(r["slot"]),
            "capacity": r["capacity"],
            "spots_left": left,
            "status": "Full" if left <= 0 else "Open",
            "location": r["location"],
        })
    out_groups = []
    for k, v in groups.items():
        loc = next((s["location"] for s in v if s.get("location")), None)
        out_groups.append({"name": k, "location": loc, "slots": v})
    return jsonify({"event": EVENT, "groups": out_groups})


@app.route("/api/signup", methods=["POST"])
def api_signup():
    if not session.get("booth_ok"):
        return jsonify({"ok": False, "error": "Booth login required. Please refresh and sign in."}), 401
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    phone_raw = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    slot_id = (data.get("slot_id") or "").strip()
    registered_by = (data.get("registered_by") or "").strip()
    delivery = (data.get("delivery") or "text").strip().lower()
    if delivery not in ("text", "physical"):
        delivery = "text"
    try:
        party_size = int(data.get("party_size") or 1)
    except (ValueError, TypeError):
        party_size = 1

    if not name:
        return jsonify({"ok": False, "error": "Please enter a name."}), 400
    if party_size < 1:
        return jsonify({"ok": False, "error": "Number of spots must be at least 1."}), 400
    phone = normalize_phone(phone_raw)
    if delivery == "text" and not phone:
        return jsonify({"ok": False, "error": "A mobile phone number is required to text the ticket. Or choose 'Physical ticket'."}), 400

    db = get_db()
    act = db.execute("SELECT * FROM activities WHERE id=?", (slot_id,)).fetchone()
    if not act:
        return jsonify({"ok": False, "error": "That activity slot was not found."}), 400

    # Atomic reservation: only succeeds if enough room remains.
    cur = db.execute(
        "UPDATE activities SET registered = registered + ? "
        "WHERE id = ? AND (capacity - registered) >= ?",
        (party_size, slot_id, party_size),
    )
    if cur.rowcount == 0:
        left = act["capacity"] - act["registered"]
        db.commit()
        return jsonify({
            "ok": False,
            "error": f"Sorry, only {max(left,0)} spot(s) left for that time. "
                     "Please pick another slot.",
        }), 409

    code = make_ticket_code()
    body = build_message(name, act["name"], act["slot"], party_size, code)
    sms_status = send_sms(phone, body) if delivery == "text" else "physical"

    db.execute(
        "INSERT INTO registrations "
        "(ticket_code,name,phone,email,party_size,activity_id,activity_name,slot,sms_status,registered_by,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (code, name, phone, email, party_size, slot_id, act["name"], act["slot"],
         sms_status, registered_by, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()

    turl = ticket_url(code)

    # If they gave an email, send them a ticket too (best-effort, non-blocking
    # to the signup — the QR/on-screen ticket is always available regardless).
    email_status = None
    if email:
        etext, ehtml = build_ticket_email(
            name, act["name"], act["slot"], party_size, code, turl, act["location"]
        )
        email_status = send_email(
            email, f"Your {EVENT['name']} ticket — {act['name']}", etext, ehtml
        )

    return jsonify({
        "ok": True,
        "ticket_code": code,
        "ticket_url": turl,
        "name": name,
        "activity_name": act["name"],
        "slot_label": pretty_slot(act["slot"]),
        "party_size": party_size,
        "message": body,
        "sms_status": sms_status,
        "email_status": email_status,
        "event": EVENT,
    })


@app.route("/api/ticket-optin", methods=["POST"])
def api_ticket_optin():
    """Self-service opt-in: the participant, on their own ticket page, enters
    their own phone and/or email and explicitly consents to receive their
    confirmation. This is the carrier-compliant, self-initiated opt-in that
    A2P 10DLC / toll-free verification want to see. Records proof of consent."""
    data = request.get_json(silent=True) or request.form
    code = (data.get("code") or "").strip()
    phone_raw = (data.get("phone") or "").strip()
    email = (data.get("email") or "").strip()
    consent = str(data.get("consent")).lower() in ("true", "1", "on", "yes")

    if not consent:
        return jsonify({"ok": False, "error": "Please check the box to agree before we send your ticket."}), 400
    phone = normalize_phone(phone_raw) if phone_raw else None
    if not phone and not email:
        return jsonify({"ok": False, "error": "Enter a mobile number or an email so we can send your ticket."}), 400

    db = get_db()
    r = db.execute(
        "SELECT * FROM registrations WHERE ticket_code=?", (code,)
    ).fetchone()
    if not r:
        return jsonify({"ok": False, "error": "We couldn't find that ticket."}), 404

    # Record the self-service consent (timestamp + source = proof of opt-in).
    db.execute(
        "UPDATE registrations SET phone=COALESCE(?,phone), email=COALESCE(?,email), "
        "consent_at=?, consent_source=? WHERE ticket_code=?",
        (phone, (email or None), datetime.now().isoformat(timespec="seconds"),
         "self-service web opt-in", code),
    )
    db.commit()

    turl = ticket_url(code)
    sms_status = None
    if phone:
        body = build_message(r["name"], r["activity_name"], r["slot"], r["party_size"], code)
        sms_status = send_sms(phone, body)
        db.execute(
            "UPDATE registrations SET sms_status=? WHERE ticket_code=?",
            (sms_status, code),
        )
        db.commit()
    email_status = None
    if email:
        loc_row = db.execute(
            "SELECT location FROM activities WHERE id=?", (r["activity_id"],)
        ).fetchone()
        optin_loc = loc_row["location"] if loc_row else None
        etext, ehtml = build_ticket_email(
            r["name"], r["activity_name"], r["slot"], r["party_size"], code, turl, optin_loc
        )
        email_status = send_email(
            email, f"Your {EVENT['name']} ticket — {r['activity_name']}", etext, ehtml
        )

    return jsonify({"ok": True, "sms_status": sms_status, "email_status": email_status})


# ----- Admin routes ---------------------------------------------------------
@app.route("/admin")
def admin():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return render_template("admin_login.html")
    db = get_db()
    regs = db.execute(
        "SELECT * FROM registrations ORDER BY created_at DESC"
    ).fetchall()
    acts = db.execute("SELECT * FROM activities ORDER BY sort_order").fetchall()
    waitlist = db.execute("SELECT * FROM waitlist ORDER BY created_at").fetchall()
    total_spots = sum(r["party_size"] for r in regs)
    return render_template(
        "admin.html", event=EVENT, regs=regs, acts=acts, waitlist=waitlist,
        key=key, pretty_slot=pretty_slot, total_regs=len(regs),
        total_spots=total_spots,
    )


@app.route("/admin/export.csv")
def export_csv():
    require_admin()
    db = get_db()
    regs = db.execute(
        "SELECT * FROM registrations ORDER BY created_at"
    ).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Ticket", "Name", "Phone", "Email", "Spots",
                "Activity", "Time", "SMS Status", "Booth Volunteer", "Registered At"])
    for r in regs:
        w.writerow([r["ticket_code"], r["name"], r["phone"], r["email"],
                    r["party_size"], r["activity_name"], pretty_slot(r["slot"]),
                    r["sms_status"], r["registered_by"], r["created_at"]])
    return Response(
        out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=oad_registrations.csv"},
    )


# ----- Manage Activities (live editing, no re-seed needed) ------------------
def admin_key_ok():
    key = request.args.get("key") or request.form.get("key")
    if not key:
        key = (request.get_json(silent=True) or {}).get("key")
    return key == ADMIN_KEY


def norm_slot(raw):
    """Accept 'HH:MM' (24h) or blank -> None. Returns (value, error)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        datetime.strptime(raw, "%H:%M")
        return raw, None
    except ValueError:
        return None, "Time must be in 24-hour HH:MM form (e.g. 14:00), or left blank."


# ----- GitHub sync ----------------------------------------------------------
# Background, debounced "push back to GitHub" so the seed activities.json in
# the repo stays in sync with the curated DB. Triggered from every admin
# write that touches the curated activity list (add / update / delete /
# rename). Does NOT fire on registration changes — those are runtime state,
# not curation, per the requirements.
_sync_lock = threading.Lock()
_sync_timer = None  # type: threading.Timer | None
_sync_state = {
    "enabled": bool(GITHUB_TOKEN and GITHUB_REPO),
    "last_change_at": None,          # ISO ts of most recent curated edit
    "last_sync_attempt_at": None,
    "last_sync_success_at": None,
    "last_sync_error": None,         # string when the last push failed
    "in_flight": False,              # push currently running
    "pending": False,                # debounce timer scheduled
}


def _build_activities_json():
    """Snapshot the live DB into the activities.json shape used as the
    first-boot seed. We never write the runtime registered count — that's
    transient state, not curation."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, name, slot, capacity, location FROM activities ORDER BY sort_order"
    ).fetchall()
    db.close()
    activities = []
    for r in rows:
        a = {
            "id": r["id"],
            "name": r["name"],
            "slot": r["slot"],
            "capacity": int(r["capacity"]),
            "registered": 0,
        }
        if r["location"]:
            a["location"] = r["location"]
        activities.append(a)
    return {"event": EVENT, "activities": activities}


def _github_get_current_file():
    """Returns (sha, parsed_obj_or_None). Raises on any HTTP error."""
    url = (f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
           f"{GITHUB_FILE_PATH}?ref={GITHUB_BRANCH}")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fobbv-oad-sync",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    try:
        parsed = json.loads(base64.b64decode(body["content"]).decode())
    except Exception:
        parsed = None
    return body["sha"], parsed


def _github_put_file(content_str, current_sha, commit_message):
    url = (f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
           f"{GITHUB_FILE_PATH}")
    payload = {
        "message": commit_message,
        "content": base64.b64encode(content_str.encode()).decode(),
        "branch": GITHUB_BRANCH,
        "sha": current_sha,
    }
    req = urllib.request.Request(
        url,
        method="PUT",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "fobbv-oad-sync",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _run_sync():
    """Body of the debounce timer. Diffs the live DB against the file on
    GitHub and pushes ONLY if the curated content actually changed —
    no empty commits."""
    global _sync_timer
    with _sync_lock:
        _sync_timer = None
        _sync_state["pending"] = False
        _sync_state["in_flight"] = True
        _sync_state["last_sync_attempt_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        if not _sync_state["enabled"]:
            raise RuntimeError("GITHUB_TOKEN not set — sync disabled")
        local_obj = _build_activities_json()
        local_str = json.dumps(local_obj, indent=2) + "\n"
        sha, github_obj = _github_get_current_file()
        if github_obj == local_obj:
            with _sync_lock:
                _sync_state["last_sync_success_at"] = datetime.now().isoformat(timespec="seconds")
                _sync_state["last_sync_error"] = None
                _sync_state["in_flight"] = False
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"Sync activities.json from live DB — {ts}"
        _github_put_file(local_str, sha, msg)
        with _sync_lock:
            _sync_state["last_sync_success_at"] = datetime.now().isoformat(timespec="seconds")
            _sync_state["last_sync_error"] = None
            _sync_state["in_flight"] = False
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        err = f"HTTP {e.code} {e.reason}: {body}"
        with _sync_lock:
            _sync_state["last_sync_error"] = err
            _sync_state["in_flight"] = False
        app.logger.warning("GitHub sync failed: %s", err)
    except Exception as e:
        with _sync_lock:
            _sync_state["last_sync_error"] = f"{type(e).__name__}: {e}"
            _sync_state["in_flight"] = False
        app.logger.warning("GitHub sync failed: %s", e)


def schedule_github_sync():
    """Debounced trigger called by curated-write endpoints. A burst of
    edits within GITHUB_SYNC_DEBOUNCE_SECONDS becomes ONE GitHub commit."""
    global _sync_timer
    now = datetime.now().isoformat(timespec="seconds")
    with _sync_lock:
        _sync_state["last_change_at"] = now
        if not _sync_state["enabled"]:
            return                        # quiet no-op when no token set
        _sync_state["pending"] = True
        if _sync_timer is not None:
            _sync_timer.cancel()
        _sync_timer = threading.Timer(GITHUB_SYNC_DEBOUNCE_SECONDS, _run_sync)
        _sync_timer.daemon = True
        _sync_timer.start()


@app.route("/api/admin/sync_status")
def admin_sync_status():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    with _sync_lock:
        out = dict(_sync_state)
    out["repo"] = GITHUB_REPO if _sync_state["enabled"] else None
    out["branch"] = GITHUB_BRANCH
    out["file"] = GITHUB_FILE_PATH
    out["debounce_seconds"] = GITHUB_SYNC_DEBOUNCE_SECONDS
    return jsonify(out)


@app.route("/api/admin/sync_now", methods=["POST"])
def admin_sync_now():
    """Cancel any debounce and run the sync immediately. Useful for verifying
    that the token + repo wiring is correct without waiting."""
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    global _sync_timer
    with _sync_lock:
        if _sync_timer is not None:
            _sync_timer.cancel()
            _sync_timer = None
    threading.Thread(target=_run_sync, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/admin/activities")
def manage_page():
    key = request.args.get("key", "")
    if key != ADMIN_KEY:
        return render_template("admin_login.html")
    db = get_db()
    acts = db.execute("SELECT * FROM activities ORDER BY sort_order").fetchall()
    names = []
    for a in acts:
        if a["name"] not in names:
            names.append(a["name"])
    return render_template("manage.html", event=EVENT, acts=acts, names=names,
                           key=key, pretty_slot=pretty_slot)


@app.route("/api/admin/add", methods=["POST"])
def admin_add():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Activity name is required."}), 400
    slot, err = norm_slot(d.get("slot"))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        capacity = int(d.get("capacity"))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Capacity must be a whole number."}), 400
    if capacity < 0:
        return jsonify({"ok": False, "error": "Capacity can't be negative."}), 400
    location = (d.get("location") or "").strip() or None
    db = get_db()
    new_id = "U" + secrets.token_hex(3).upper()
    nxt = (db.execute("SELECT MAX(sort_order) FROM activities").fetchone()[0] or 0) + 1
    db.execute("INSERT INTO activities (id,name,slot,capacity,registered,sort_order,location)"
               " VALUES (?,?,?,?,0,?,?)", (new_id, name, slot, capacity, nxt, location))
    db.commit()
    schedule_github_sync()
    return jsonify({"ok": True})


@app.route("/api/admin/update", methods=["POST"])
def admin_update():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    d = request.get_json(silent=True) or {}
    slot_id = (d.get("id") or "").strip()
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Activity name is required."}), 400
    slot, err = norm_slot(d.get("slot"))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    try:
        capacity = int(d.get("capacity"))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Capacity must be a whole number."}), 400
    db = get_db()
    row = db.execute("SELECT registered FROM activities WHERE id=?", (slot_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "That slot no longer exists."}), 404
    if capacity < row["registered"]:
        return jsonify({"ok": False,
                        "error": f"Capacity can't be below the {row['registered']} already booked."}), 400
    location = (d.get("location") or "").strip() or None
    db.execute("UPDATE activities SET name=?, slot=?, capacity=?, location=? WHERE id=?",
               (name, slot, capacity, location, slot_id))
    db.commit()
    schedule_github_sync()
    return jsonify({"ok": True})


@app.route("/api/admin/delete", methods=["POST"])
def admin_delete():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    d = request.get_json(silent=True) or {}
    slot_id = (d.get("id") or "").strip()
    db = get_db()
    row = db.execute("SELECT registered FROM activities WHERE id=?", (slot_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "That slot no longer exists."}), 404
    if row["registered"] > 0:
        return jsonify({"ok": False,
                        "error": "This slot has registrations and can't be deleted. "
                                 "Set its capacity to the number booked to close it instead."}), 409
    db.execute("DELETE FROM activities WHERE id=?", (slot_id,))
    db.commit()
    schedule_github_sync()
    return jsonify({"ok": True})


@app.route("/api/admin/rename", methods=["POST"])
def admin_rename():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    d = request.get_json(silent=True) or {}
    old = (d.get("old_name") or "").strip()
    new = (d.get("new_name") or "").strip()
    if not old or not new:
        return jsonify({"ok": False, "error": "Both the existing and new names are required."}), 400
    db = get_db()
    cur = db.execute("UPDATE activities SET name=? WHERE name=?", (new, old))
    # Keep past registration records readable too.
    db.execute("UPDATE registrations SET activity_name=? WHERE activity_name=?", (new, old))
    db.commit()
    schedule_github_sync()
    return jsonify({"ok": True, "updated": cur.rowcount})


@app.route("/api/admin/cancel_registration", methods=["POST"])
def admin_cancel_reg():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    rid = (request.get_json(silent=True) or {}).get("id")
    db = get_db()
    reg = db.execute("SELECT * FROM registrations WHERE id=?", (rid,)).fetchone()
    if not reg:
        return jsonify({"ok": False, "error": "Registration not found."}), 404
    db.execute("UPDATE activities SET registered = MAX(registered - ?, 0) WHERE id=?",
               (reg["party_size"], reg["activity_id"]))
    db.execute("DELETE FROM registrations WHERE id=?", (rid,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/admin/move_registration", methods=["POST"])
def admin_move_reg():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    d = request.get_json(silent=True) or {}
    rid = d.get("id")
    new_id = (d.get("new_slot_id") or "").strip()
    db = get_db()
    reg = db.execute("SELECT * FROM registrations WHERE id=?", (rid,)).fetchone()
    if not reg:
        return jsonify({"ok": False, "error": "Registration not found."}), 404
    if new_id == reg["activity_id"]:
        return jsonify({"ok": False, "error": "That's already the current slot."}), 400
    newact = db.execute("SELECT * FROM activities WHERE id=?", (new_id,)).fetchone()
    if not newact:
        return jsonify({"ok": False, "error": "Target slot not found."}), 404
    n = reg["party_size"]
    cur = db.execute(
        "UPDATE activities SET registered = registered + ? "
        "WHERE id = ? AND (capacity - registered) >= ?", (n, new_id, n))
    if cur.rowcount == 0:
        left = newact["capacity"] - newact["registered"]
        db.commit()
        return jsonify({"ok": False, "error": f"Only {max(left,0)} spot(s) left in that slot."}), 409
    db.execute("UPDATE activities SET registered = MAX(registered - ?, 0) WHERE id=?",
               (n, reg["activity_id"]))
    db.execute("UPDATE registrations SET activity_id=?, activity_name=?, slot=? WHERE id=?",
               (new_id, newact["name"], newact["slot"], rid))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/waitlist", methods=["POST"])
def api_waitlist():
    if not session.get("booth_ok"):
        return jsonify({"ok": False, "error": "Booth login required. Please refresh and sign in."}), 401
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = normalize_phone((data.get("phone") or "").strip())
    slot_id = (data.get("slot_id") or "").strip()
    registered_by = (data.get("registered_by") or "").strip()
    try:
        party_size = int(data.get("party_size") or 1)
    except (ValueError, TypeError):
        party_size = 1
    if not name:
        return jsonify({"ok": False, "error": "Please enter a name."}), 400
    if not phone:
        return jsonify({"ok": False, "error": "A phone number is required for the waitlist, so they can be contacted."}), 400
    db = get_db()
    act = db.execute("SELECT * FROM activities WHERE id=?", (slot_id,)).fetchone()
    if not act:
        return jsonify({"ok": False, "error": "That activity slot was not found."}), 400
    db.execute(
        "INSERT INTO waitlist (name,phone,party_size,activity_id,activity_name,slot,registered_by,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (name, phone, party_size, slot_id, act["name"], act["slot"], registered_by,
         datetime.now().isoformat(timespec="seconds")))
    db.commit()
    return jsonify({"ok": True, "waitlist": True, "name": name,
                    "activity_name": act["name"], "slot_label": pretty_slot(act["slot"]),
                    "party_size": party_size})


@app.route("/api/admin/waitlist_remove", methods=["POST"])
def admin_waitlist_remove():
    if not admin_key_ok():
        return jsonify({"ok": False, "error": "Not authorized."}), 401
    wid = (request.get_json(silent=True) or {}).get("id")
    db = get_db()
    db.execute("DELETE FROM waitlist WHERE id=?", (wid,))
    db.commit()
    return jsonify({"ok": True})


# Initialize the database on import so it also runs under a production server
# (gunicorn), which never executes the __main__ block below.
try:
    init_db()
    _db = sqlite3.connect(DB_PATH)
    ACTIVITY_COUNT = _db.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    _db.close()
except Exception as _e:
    ACTIVITY_COUNT = -1
    print("\n*** DATABASE PROBLEM ***")
    print("Could not set up the database at:", DB_PATH)
    print("Reason:", _e)
    print("If this folder is in iCloud Drive / Dropbox / OneDrive, that's the cause.")
    print("Fix: move the 'oad-signup' folder to a non-synced location (e.g. your")
    print("Desktop or a local Documents folder that isn't syncing), then run again.\n")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print("\n" + "=" * 60)
    print("  Outdoor Adventure Day app is running!")
    print(f"  Activities loaded: {ACTIVITY_COUNT}")
    print(f"  Public availability board (QR target): http://127.0.0.1:{port}/")
    print(f"  Booth sign-up screen   (PIN: {STAFF_PIN}) : http://127.0.0.1:{port}/signup")
    print(f"  Organizer / registration list        : http://127.0.0.1:{port}/admin?key={ADMIN_KEY}")
    print(f"  Manage activities                    : http://127.0.0.1:{port}/admin/activities?key={ADMIN_KEY}")
    twilio_on = bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM)
    print(f"  Texting (Twilio): {'ON' if twilio_on else 'OFF (showing ticket on screen)'}")
    print("=" * 60 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False)
