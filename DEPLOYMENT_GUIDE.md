# Going Live — Step-by-Step (GitHub + Render)

This puts your app online at a permanent web address that all the booth laptops
and the public QR code will share. Plan for about **20 minutes**. You'll make two
free accounts (GitHub + Render) and add a card to Render for the always-on plan
(about **$7/month** — cancel after the event).

You'll use the file **`oad-signup-deploy.zip`** I gave you. **Double-click it to
unzip** — you'll get a folder with all the app files inside.

---

## Part 1 — Put the files on GitHub (about 8 min)

1. Go to **https://github.com** and click **Sign up** (free). Verify your email.
2. Click the **+** in the top-right → **New repository**.
3. Name it **`oad-signup`**. Choose **Private**. Do **not** check "Add a README."
   Click **Create repository**.
4. On the next page, click the link **"uploading an existing file."**
5. Open the unzipped folder on your Mac. Select **everything inside it**
   (click one file, then press **Cmd+A** to select all), and **drag it onto the
   GitHub upload area.**
   - Important: drag the *contents* (app.py, render.yaml, the templates folder,
     etc.) — not the outer folder. `render.yaml` must end up at the top level.
6. Wait for the files to finish uploading, then click **Commit changes.**

You should now see your files listed in the repository, including `render.yaml`.

---

## Part 2 — Deploy on Render (about 8 min)

1. Go to **https://render.com** and click **Get Started**. Choose
   **"Sign up with GitHub"** — this automatically connects your code. Approve the
   access GitHub asks for.
2. In Render, click **New +** (top right) → **Blueprint**.
3. Find and select your **`oad-signup`** repository → click **Connect**.
4. Render reads `render.yaml` and shows a service called **oad-availability**.
   It will ask you to fill in a few values:
   - **ADMIN_KEY** — your private organizer password (e.g. make up something only
     you know). This protects the registrations list.
   - **STAFF_PIN** — the PIN your booth volunteers will type (e.g. a simple word
     or number you'll share with the team).
   - **TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER** — leave
     these **blank for now** (we'll add texting later).
   - (SECRET_KEY fills itself in automatically — leave it.)
5. Click **Apply** / **Create**. Render will ask you to **add a payment method**
   (the always-on Starter plan + small data disk ≈ $7/month). Enter your card.
6. Render builds and starts the app. This takes about **3–5 minutes**. When the
   status shows **"Live,"** you're done.

---

## Part 3 — Get your link and test

1. At the top of the Render service page you'll see your address, like
   **`https://oad-availability.onrender.com`**.
   - Want a nicer name? Render → your service → **Settings** → **Name** → rename,
     and the address updates.
2. Open it — you should see the **public availability board**.
3. Test the booth screen: add **`/signup`** to the address, enter your STAFF_PIN,
   and try registering a test person. (Texting is off until Part 4, so it'll show
   the ticket on screen — that's expected.)
4. Check the list: add **`/admin?key=YOUR_ADMIN_KEY`**.
5. **Send me the live address** and I'll generate your printable QR code for the
   public board.

---

## Part 4 — Turn on text confirmations (later, when ready)

When you want real texts sent, we'll set up Twilio (about $1–2/month + ~1¢/text)
and paste three values into Render → your service → **Environment**. I'll walk you
through it — just say the word.

---

### Good to know
- **Updating activities later:** use the Manage Activities page in the app
  (`/admin/activities?key=YOUR_ADMIN_KEY`) — no need to touch GitHub again.
- **Your data is safe:** registrations live on the Render data disk and survive
  restarts and redeploys.
- **Changing the code later:** upload the changed file on GitHub (or re-upload),
  and Render redeploys automatically.
