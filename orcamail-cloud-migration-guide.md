# OrcaMail Cloud Migration Guide
**Written for Keiko — no developer knowledge needed**

Everything Claude has already done is marked ✅. Steps you need to do yourself are marked 👉.

---

## What's Already Done ✅

- `orcamail-server.py` — updated so data file paths and port come from environment variables (Railway-compatible)
- `requirements.txt`, `Procfile`, `railway.toml`, `.gitignore` — created on your Desktop
- GitHub repo created and all files pushed: **https://github.com/Keiko-Dev-LCAI/orcamail-server**
- `orcamail-v2.html` — Privacy Policy added (footer link + full modal)

---

## Part 1: Set Up Railway (the cloud server)

### Step 1 — Create a Railway account

👉 Go to **https://railway.app** and click **"Login"** → **"Login with GitHub"**.
Sign in with your **Keiko-Dev-LCAI** GitHub account. Railway will ask permission to access your repos — click **Authorize**.

---

### Step 2 — Create a new Railway project

👉 Once logged in, click the big **"+ New Project"** button.

👉 Choose **"Deploy from GitHub repo"**.

👉 Search for **`orcamail-server`** and select it (it'll show as `Keiko-Dev-LCAI/orcamail-server`).

👉 Click **"Deploy Now"**. Railway will detect the `Procfile` and start building. The first deploy will probably fail — that's okay, because we still need to add environment variables and a volume. Continue below.

---

### Step 3 — Add a Persistent Volume (so data survives restarts)

This is the most important step. Without a volume, your messages and pubkeys are lost every time the server restarts.

👉 In your Railway project, click on the **orcamail-server** service (the box that appeared).

👉 Go to the **"Volumes"** tab in the panel that opens on the right.

👉 Click **"Add Volume"**.

👉 Set the **Mount Path** to:
```
/data
```
👉 Leave the size at the default (1 GB is plenty). Click **"Create"**.

---

### Step 4 — Set Environment Variables

👉 Still inside the orcamail-server service panel, click the **"Variables"** tab.

👉 Click **"Add Variable"** for each of the following. Add them one at a time:

| Variable Name | Value |
|---|---|
| `DATA_DIR` | `/data` |
| `PORT` | *(leave blank — Railway sets this automatically)* |
| `PRIVATE_KEY` | *(paste your server private key from your systemd service — check with `sudo systemctl cat orcamail-server`)* |

> ⚠️ **Important:** Your private key is currently set in the systemd service on your PC. You need to find it and add it here so the cloud server can sign transactions. Look in `/etc/systemd/system/orcamail-server.service` or wherever you set it up — it's in an `Environment=` line. **Never share this key with anyone.**

If you have SMTP email notifications configured, also add:

| Variable Name | Value |
|---|---|
| `SMTP_HOST` | e.g. `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASS` | your Gmail app password |

👉 After adding all variables, Railway will automatically redeploy. Wait for the green **"Active"** badge.

---

### Step 5 — Upload Your Existing Data Files (optional but recommended)

You have existing messages and user data on your PC. To move them to the Railway volume:

**Option A — Railway's built-in shell (easiest):**

👉 In Railway, click on your service → **"Deploy"** tab → **"Shell"** (a terminal will open in your browser).

👉 Run these commands one at a time (copy the content of each file from your PC and paste):
```bash
cat > /data/orcamail-messages.json << 'EOF'
(paste the contents of ~/orcamail-messages.json here)
EOF
```
Repeat for:
- `/data/orcamail-pubkeys.json`
- `/data/orcamail-optins.json`
- `/data/orcamail-sends.json`

**Option B — Use `railway CLI` (if you're comfortable with a terminal):**
```bash
# Install Railway CLI
npm install -g @railway/cli
railway login
railway link   # select your project
railway volume cp ~/orcamail-messages.json /data/orcamail-messages.json
railway volume cp ~/orcamail-pubkeys.json /data/orcamail-pubkeys.json
railway volume cp ~/orcamail-optins.json /data/orcamail-optins.json
railway volume cp ~/orcamail-sends.json /data/orcamail-sends.json
```

---

### Step 6 — Get Your Railway URL

👉 In Railway, click your service → **"Settings"** tab → look for **"Domains"**.

👉 Click **"Generate Domain"** to get a free Railway URL like:
```
orcamail-server-production-xxxx.up.railway.app
```

👉 Copy this URL — you'll need it for the Cloudflare step below.

👉 Test it: open your browser and go to:
```
https://orcamail-server-production-xxxx.up.railway.app/api/health
```
You should see: `{"status": "ok", ...}` ✅

---

## Part 2: Switch Cloudflare from Tunnel to Railway

Right now, `orcamail.ai` is routed through a Cloudflare tunnel to your PC. Once Railway is running, follow these steps to switch it.

> ⚠️ **Do these steps only after Step 6 above confirms Railway is working.**
> There will be a brief (< 1 minute) outage when you switch.

---

### Step 7 — Remove the Cloudflare Tunnel

👉 Go to **https://one.dash.cloudflare.com** and log in.

👉 In the left sidebar, click **"Networks"** → **"Tunnels"**.

👉 Find your OrcaMail tunnel. Click the **"…"** (three dots) menu next to it → **"Delete"**.

👉 Confirm deletion. This disconnects the tunnel from your PC.

---

### Step 8 — Add a CNAME Record Pointing to Railway

👉 Go to **https://dash.cloudflare.com** and log in.

👉 Click on your domain **`orcamail.ai`**.

👉 In the left sidebar, click **"DNS"** → **"Records"**.

👉 Look for any existing `CNAME` or `A` record for the root domain (`@`) or `www`. If you see one that was created by the tunnel, delete it.

👉 Click **"Add record"** and fill in:

| Field | Value |
|---|---|
| Type | `CNAME` |
| Name | `@` *(this means the root domain, orcamail.ai)* |
| Target | `orcamail-server-production-xxxx.up.railway.app` *(your Railway URL from Step 6, without https://)* |
| Proxy status | **Proxied** (orange cloud icon — keep this ON) |
| TTL | Auto |

👉 Click **"Save"**.

> **Why keep Cloudflare proxy ON?** It gives you DDoS protection, hides Railway's IP, and you can keep using Cloudflare's SSL certificate.

---

### Step 9 — Verify the Switch

👉 Wait 1–2 minutes for DNS to propagate, then open:
```
https://orcamail.ai/api/health
```
You should see `{"status": "ok", ...}` coming from Railway ✅

👉 Open **https://orcamail.ai** in your browser and make sure the site loads normally.

👉 Connect your wallet and send a test message to confirm the full flow works.

---

### Step 10 — Stop the Local Server (when you're ready)

Once you've confirmed Railway is working correctly:

👉 On your PC, open a terminal and run:
```bash
sudo systemctl stop orcamail-server
sudo systemctl disable orcamail-server
```

This stops the server on your Lenovo Legion. Your data is now entirely off your personal machine. 🎉

---

## Environment Variables Reference (Railway)

| Variable | Required | Description |
|---|---|---|
| `DATA_DIR` | ✅ Yes | Set to `/data` (your Railway volume mount path) |
| `PORT` | Auto | Set by Railway automatically — do NOT set manually |
| `PRIVATE_KEY` | If used | Server signing key from your systemd service |
| `SMTP_HOST` | Optional | SMTP server for email notifications |
| `SMTP_PORT` | Optional | Default: 587 |
| `SMTP_USER` | Optional | SMTP login email |
| `SMTP_PASS` | Optional | SMTP app password |
| `NOTIFY_FROM` | Optional | Default: orcamail@orcamail.ai |

---

## Troubleshooting

**Railway build fails immediately:**
→ Check the build logs in Railway → your service → "Deployments" tab. The most common cause is a missing environment variable.

**`/api/health` returns an error:**
→ Click on your service → "Logs" tab to see what the Python server is printing.

**Messages aren't showing up after data migration:**
→ In Railway's shell, run `ls -la /data/` to confirm the JSON files are there.

**Cloudflare shows "Error 1001" after DNS switch:**
→ Wait 5 more minutes and try again — DNS propagation can take a bit.

**Need to put the site in maintenance mode:**
→ In Railway's shell, run: `touch /data/MAINTENANCE_MODE`
→ To bring it back: `rm /data/MAINTENANCE_MODE`

---

## Summary of What Was Changed

| File | Change |
|---|---|
| `orcamail-server.py` | `DATA_DIR` + `PORT` read from env vars; `SMTP_*` from env vars; `sys.path` pylibs conditional |
| `requirements.txt` | Created — stdlib only, no pip packages |
| `Procfile` | Created — `web: python orcamail-server.py` |
| `railway.toml` | Created — health check config |
| `.gitignore` | Created — excludes data JSON files |
| `orcamail-v2.html` | Privacy Policy footer link + full modal added |

GitHub repo: **https://github.com/Keiko-Dev-LCAI/orcamail-server**

---

*Questions? Email danmimna@gmail.com or open an issue on the GitHub repo.*
