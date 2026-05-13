# ⬡ PortfolioIQ — Deployment Guide
## GitHub + Render · ~15 minutes · Free

---

## STEP 1 — Create a GitHub account
1. Go to **https://github.com** in Safari
2. Click **Sign up**
3. Enter email, password, username
4. Verify your email
5. Choose **Free** plan

---

## STEP 2 — Create a new repository
1. Click the **+** icon (top right) → **New repository**
2. Name: `portfolioiq`
3. Set to **Private**
4. Check **Add a README file**
5. Click **Create repository**

---

## STEP 3 — Upload your files
1. Click **Add file** → **Upload files**
2. Upload these files from the zip:
   - `app.py`
   - `requirements.txt`
   - `render.yaml`
   - `portfolio_data.json`
   - `.gitignore`
3. Click **Commit changes**
4. Click **Add file** → **Upload files** again
5. Type `templates/` in the path box at the top
6. Upload `index.html`
7. Click **Commit changes**

---

## STEP 4 — Create a Render account
1. Go to **https://render.com**
2. Click **Get Started for Free**
3. Click **Sign up with GitHub** — this links them automatically
4. Authorize Render

---

## STEP 5 — Deploy on Render
1. In Render dashboard click **+ New** → **Web Service**
2. Select your `portfolioiq` repository
3. Verify these settings:
   - Runtime: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --workers 1 --bind 0.0.0.0:$PORT --timeout 120`
   - Instance Type: `Free`
4. Scroll to **Environment Variables** and add:

   | Key | Value |
   |-----|-------|
   | REPORT_EMAIL | rinjoque@me.com |
   | SMTP_SERVER | smtp.mail.me.com |
   | SMTP_PORT | 587 |
   | SMTP_USER | rinjoque@icloud.com |
   | SMTP_PASS | (your App-Specific Password) |

5. Click **Create Web Service**
6. Wait 3–5 minutes for build to complete
7. Your URL will be: `https://portfolioiq-xxxx.onrender.com`

---

## STEP 6 — Get Apple App-Specific Password
1. Go to **https://appleid.apple.com**
2. Sign In and Security → App-Specific Passwords
3. Click **+** → name it `PortfolioIQ`
4. Copy the password (xxxx-xxxx-xxxx-xxxx)
5. Paste it as `SMTP_PASS` in Render environment variables

---

## STEP 7 — Add to browser bookmarks
1. Open your Render URL in Safari or Chrome
2. Bookmark it for easy access
3. On iPad: Share → Add to Home Screen

---

## Notes
- Free tier sleeps after 15 min inactivity — first load takes ~30 sec
- Your tickers (VDE, VUG, VST, ICLN, BE, QQQM + PPX watchlist) are pre-loaded
- Weekly report sends every Monday at 8am automatically
