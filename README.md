# Jet2 price tracker (cloud)

Hourly price watch for a Jet2holidays package, running on GitHub Actions so it
works with the owner's PC off. Emails on any price change via Gmail SMTP.

- `tracker_ci.py` — loads the holiday page in headless Chromium (Playwright),
  extracts the total/per-person price, compares to `state.json`, emails on change.
- `.github/workflows/price-check.yml` — hourly schedule + manual trigger.
- `state.json` / `price_history.csv` — committed back by each run.

Secrets required (repo → Settings → Secrets and variables → Actions):
`EMAIL_FROM`, `EMAIL_TO`, `GMAIL_APP_PASSWORD` (a Google App Password).

Note: GitHub cron is best-effort — runs can start up to ~15 minutes late.
