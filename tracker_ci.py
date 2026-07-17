"""Jet2holidays price tracker - GitHub Actions version.

Runs hourly via .github/workflows/price-check.yml. Loads the holiday page in
headless Chromium (Jet2's bot protection blocks plain HTTP clients), compares
against state.json committed in the repo, and emails on any price change.
Email addresses and the Gmail app password come from Actions secrets.
"""

import csv
import json
import os
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

URL = ("https://www.jet2holidays.com/beach/morocco/agadir-area/agadir/"
       "pickalbatros-palais-des-roses-agadir?duration=7&airport=99&date=23-08-2026"
       "&occupancy=r2c4&board=5&iflight=1356390&oflight=1356395&rooms=111607")
LABEL = ("Pickalbatros Palais des Roses Agadir, 7nts from 23 Aug 2026 "
         "(2 adults + 1 child, All Inclusive, ex-Stansted)")

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "state.json"
HISTORY_FILE = BASE / "price_history.csv"


def fetch_prices(attempts=3):
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            with sync_playwright() as p:
                launch_kwargs = {
                    "headless": True,
                    "args": ["--disable-blink-features=AutomationControlled",
                             "--disable-http2"],
                }
                # real Chrome (installed on GitHub runners) has a more
                # authentic fingerprint than bare Chromium; fall back if absent
                try:
                    browser = p.chromium.launch(channel="chrome", **launch_kwargs)
                except Exception:
                    browser = p.chromium.launch(**launch_kwargs)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                    locale="en-GB",
                    viewport={"width": 1366, "height": 900},
                )
                page = ctx.new_page()
                page.goto(URL, wait_until="domcontentloaded", timeout=60000)
                deadline = time.time() + 90
                state = None
                while time.time() < deadline:
                    time.sleep(3)
                    state = page.evaluate(
                        """() => {
                            const dock = document.querySelector('.basket-summary__dock-total-price');
                            const vals = [...document.querySelectorAll('.basket-summary__price-value')]
                                .map(e => e.textContent.trim());
                            let dlPrice = null;
                            if (window.dataLayer) {
                                const m = JSON.stringify(window.dataLayer).match(/"price":"([\\d.]+)"/);
                                if (m) dlPrice = parseFloat(m[1]);
                            }
                            return {dock: dock ? dock.textContent.trim() : null, vals, dlPrice};
                        }"""
                    )
                    if state["dlPrice"] or state["dock"]:
                        break
                browser.close()

            if not state or (not state["dlPrice"] and not state["dock"]):
                raise RuntimeError("price elements never appeared on page")

            total = state["dlPrice"]
            if total is None and state["dock"]:
                total = float(state["dock"].replace("£", "").replace(",", ""))

            per_person = None
            pounds = []
            for v in state["vals"]:
                try:
                    pounds.append(float(v.replace("£", "").replace(",", "")))
                except ValueError:
                    pass
            candidates = [x for x in pounds if 0 < x < total]
            if candidates:
                per_person = max(candidates)
            return {"total": total, "per_person": per_person}
        except Exception as e:  # noqa: BLE001 - retry on anything
            last_err = e
            print(f"fetch attempt {attempt} failed: {e}", file=sys.stderr)
            time.sleep(10)
    raise RuntimeError(f"all fetch attempts failed: {last_err}")


def send_email(subject, body):
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    frm = os.environ.get("EMAIL_FROM", "")
    to = os.environ.get("EMAIL_TO", frm)
    if not pw or not frm:
        print(f"email secrets not set - would have sent: {subject}", file=sys.stderr)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = to
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(frm, "".join(pw.split()))
        s.send_message(msg)
    print(f"email sent: {subject}")


def gbp(x):
    return f"£{x:,.2f}".rstrip("0").rstrip(".") if x is not None else "?"


def main():
    now = datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M")
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    try:
        prices = fetch_prices()
    except Exception as e:
        fails = state.get("consecutive_failures", 0) + 1
        state["consecutive_failures"] = fails
        STATE_FILE.write_text(json.dumps(state, indent=2))
        print(f"CHECK FAILED ({fails} in a row): {e}", file=sys.stderr)
        if fails == 6:
            send_email(
                f"Jet2 price tracker (cloud): broken for {fails} hours",
                f"The GitHub Actions tracker failed {fails} runs in a row.\n"
                f"Last error: {e}\nCheck the Actions tab of the repo.",
            )
        sys.exit(1)

    total, per_person = prices["total"], prices["per_person"]

    new_file = not HISTORY_FILE.exists()
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "total", "per_person"])
        w.writerow([now, total, per_person])

    last_total = state.get("last_total")
    state.update({
        "last_total": total,
        "last_per_person": per_person,
        "last_checked": now,
        "consecutive_failures": 0,
    })
    STATE_FILE.write_text(json.dumps(state, indent=2))

    if last_total is None:
        print(f"baseline recorded: {gbp(total)}")
        send_email(
            f"Jet2 cloud tracker live - {gbp(total)}",
            f"The PC-off (GitHub Actions) tracker is now running hourly.\n\n"
            f"{LABEL}\n\nCurrent price: {gbp(total)} total ({gbp(per_person)} pp)\n"
            f"Checked: {now} UK\n\n{URL}",
        )
    elif total != last_total:
        diff = total - last_total
        arrow = "UP" if diff > 0 else "DOWN"
        print(f"PRICE CHANGE: {last_total} -> {total}")
        send_email(
            f"Jet2 price {arrow} {gbp(abs(diff))}: now {gbp(total)}",
            f"{LABEL}\n\n"
            f"Price changed: {gbp(last_total)} -> {gbp(total)} "
            f"({'+' if diff > 0 else '-'}{gbp(abs(diff))})\n"
            f"Per person: {gbp(per_person)}\n"
            f"Checked: {now} UK\n\n{URL}",
        )
    else:
        print(f"no change: {gbp(total)} at {now}")


if __name__ == "__main__":
    main()
