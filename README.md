# Outdoor Adventure Day 2026 — Activity Sign-up App

A simple web app for **Friends of Big Bear Valley (FOBBV)**. It has three parts:

- **Public availability board** (`/`) — the page your **QR code** points to.
  Anyone can scan it to see, in real time, which activities and times still have
  spots left. It shows **no names, phones, or emails** — just availability.
- **Booth sign-up screen** (`/signup`) — your 5 booth laptops all open this same
  page to register tourists. It's **locked behind a staff PIN** (it's on the
  public internet). It has a quick activity search, remembers which volunteer is
  on each laptop, requires a phone number, reserves the spots, and texts the
  tourist a confirmation + ticket. All laptops share one live pool of spots, so
  two booths can't book the same seat.
- **Organizer list** (`/admin`) — see and download everyone who registered,
  including which booth volunteer signed them up.

**Default logins** (change these — see the .env / Render env steps):
- Booth PIN (`/signup`): `bigbear`
- Organizer key (`/admin`): `fobbv2026`

**Logo:** every page shows a logo served by the app at `/logo`. By default it's a
built-in Big Bear emblem. To use the **official FOBBV logo**, save the image file
as `static/logo.png` in the `oad-signup` folder — the app picks it up
automatically, no other changes needed.

The activities, times, and capacities were loaded from your
`ACTIVITES.xlsm` spreadsheet (48 time slots, 642 total spots).

---

## Quick start (run it on your Mac)

1. Open the `oad-signup` folder.
2. **Double-click `run.command`.** A black Terminal window opens and sets things
   up the first time (about a minute). Leave that window open.
   - If macOS blocks it: right-click `run.command` → **Open** → **Open**.
3. When it's running, open these in your browser:
   - Public availability board: **http://127.0.0.1:5050/**
   - Volunteer sign-up screen: **http://127.0.0.1:5050/signup**
   - Organizer list: **http://127.0.0.1:5050/admin?key=fobbv2026**

   *(If you ever see "Port already taken," macOS is using that port for AirPlay.
   You can change the port by editing the last lines of `app.py`, or turn off
   AirPlay Receiver in System Settings → General → AirDrop & Handoff.)*

That's it for testing. Out of the box, texting is **off**, so each signup just
shows a ticket on screen. Turn on texting below when you're ready.

---

## Booth setup on event day

All five booth laptops use the **same** deployed app (the Render link below), so
they share one live pool of spots.

1. On each laptop, open `https://your-app.onrender.com/signup`.
2. Enter the **staff PIN** once (it's remembered on that laptop for the day).
3. Type the volunteer's name in the "Booth volunteer (you)" box — it's saved on
   that laptop and attached to every signup they do.
4. For each tourist: type part of the activity name in the search box (e.g.
   "kayak" or "2:00"), tap the time slot they want, enter name + phone + number
   of spots, and press **Sign up & send text**. The confirmation text goes out
   and the screen resets for the next person.

If two volunteers grab the last seat at the same moment, only the first is
booked; the second sees "only X spots left" and the list refreshes — no
double-booking. The public board and every booth screen update automatically.

---

## Putting the public availability board online (Render + QR code)

`127.0.0.1` only works on your computer. To get a permanent public link that a
QR code can point to, deploy to **Render**. This folder includes `render.yaml`,
which sets everything up for you.

**Steps**
1. Put this `oad-signup` folder into a GitHub repository (free github.com account).
2. At https://render.com, sign in and click **New + → Blueprint**.
3. Connect your GitHub repo. Render reads `render.yaml` and configures the service.
4. Before the first deploy, set these values (Render will prompt for them, or add
   them under the service's **Environment** tab):
   - `ADMIN_KEY` — your own secret for the organizer page.
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` — only if you
     want texts (you can add these later).
5. Click **Deploy**. When it finishes, Render gives you a public address like
   `https://oad-availability.onrender.com`. You can rename the service to change
   the address.

**Make the QR code**
On your Mac, in the `oad-signup` folder, run (use your real Render address):

```
python3 make_qr.py https://oad-availability.onrender.com
```

This saves `availability_qr.png`. Print it or display it at the registration
table — scanning it opens the live availability board.

### A note on cost (please read)

`render.yaml` is set to the **Starter** plan (~$7/month), which I recommend
because it stays awake (instant load when scanned) and keeps your signup data on
a small persistent disk so it's never lost. You can cancel after the event.

You *can* switch to **free** by changing `plan: starter` to `plan: free` and
removing the `disk:` section — but two important catches:
- The page **sleeps after 15 minutes** of no traffic, so the first scan waits
  30–60 seconds for it to wake up.
- A free Render web service has **no persistent storage**, so every time it
  sleeps or redeploys, all registrations reset. To use free hosting safely you'd
  need a separate database (Render's free database is deleted 30 days after you
  create it). Tell me if you want this route and I'll set it up.

---

## Turning on confirmation texts (Twilio)

Real texting needs a Twilio account. Costs are small (around 1¢ per text plus
about $1–2/month for a phone number). A free **trial** account can only text
phone numbers you've verified in Twilio — fine for testing, but for the real
event you'll want to upgrade so you can text anyone.

1. Sign up at https://www.twilio.com and get a phone number.
2. From the Twilio Console copy your **Account SID**, **Auth Token**, and your
   **Twilio phone number**.
3. In the `oad-signup` folder, make a copy of `.env.example` named `.env`.
4. Open `.env` and paste your three Twilio values in. Save.
5. Restart the app (close the Terminal window and double-click `run.command`
   again). It will now print "Texting (Twilio): ON".

---

## The organizer page

`/admin?key=fobbv2026`

- See every registration (name, phone, email, spots, activity, time, whether the
  text sent).
- See remaining capacity per activity.
- **Download CSV** button to open the full list in Excel/Google Sheets.

Change the access key in `.env` (`ADMIN_KEY=...`) so it isn't the default.

---

## Adding / editing activities (the easy way)

Open the organizer page (`/admin?key=...`) and click **⚙ Manage activities**
(direct link: `/admin/activities?key=YOUR_ADMIN_KEY`). From there you can, at any
time — even after signups have started, with no data loss:

- **Add a time slot** — name, time, capacity. Use the *same* name as an existing
  activity to add another time to it; a new name starts a new activity.
- **Rename an activity** — updates all of its time slots at once (e.g. merge
  "Kayaks" into "Kayak").
- **Edit** a slot's name, time, or capacity inline, then **Save**.
- **Delete** a slot. (Safeguards: capacity can't go below the number already
  booked, and a slot with bookings can't be deleted — lower its capacity to the
  booked number to close it instead.)

Changes are live immediately on the booth screens and the public board.

### Under the hood / starting completely fresh

- The first time the app runs, it loads activities from `data/activities.json`.
  After that, the live data lives in the database (`data/oad.db` locally, or the
  Render disk), which is what the Manage Activities page edits.
- To wipe everything (all signups **and** activity edits) and reload from the
  JSON, delete `data/oad.db` and restart. Only do this before going live.

---

## Notes on your spreadsheet data

I loaded the slots exactly as they were. A few things you may want to tidy in
`data/activities.json`:

- There are both **"Kayak"** and **"Kayaks"** as separate activities — likely a
  duplicate.
- Some **Tourzilla Jeep Tours (Sat)** slots show early-morning times (1:45,
  2:30, 3:15, 4:45, 5:30) that look like afternoon times entered without "PM."
- **Big Bear Queen** slots have no time listed (they show "See schedule").

Edit the JSON and delete `data/oad.db` to apply any fixes.
