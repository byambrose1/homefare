"""Step 1: collect today's cheapest return fares for every origin x destination x travel month.

Source: Travelpayouts / Aviasales Data API (free). Prices come from searches made by Aviasales
users in the last 48 hours, so they are a wide, cheap scan, not a live quote. Live checks happen
later, only for candidate deals (see live_check.py).

Needs the TRAVELPAYOUTS_TOKEN environment variable (stored as a GitHub secret).
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys
import time

import requests

from engine.common import (
    PRICE_FIELDS, REPORTS_DIR, load_config, months_ahead, shard_path, write_json, write_rows,
)

API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
PAUSE_SECONDS = 0.2


def fetch(session: requests.Session, token: str, origin: str, dest: str, month: str, currency: str) -> list[dict]:
    params = {
        "origin": origin, "destination": dest, "departure_at": month,
        "one_way": "false", "sorting": "price", "limit": 3, "currency": currency,
    }
    headers = {"X-Access-Token": token, "Accept-Encoding": "gzip, deflate"}
    for attempt in range(3):
        r = session.get(API, params=params, headers=headers, timeout=30)
        if r.status_code == 429:  # rate limited: back off and retry
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("success") is False:
            raise RuntimeError(str(body.get("error") or body)[:200])
        return body.get("data") or []
    raise RuntimeError("rate limited three times")


def to_row(ticket: dict, checked_on: str, origin: str, dest: str, month: str, currency: str) -> dict:
    return {
        "checked_on": checked_on,
        "origin": origin,
        "origin_airport": ticket.get("origin_airport", ""),
        "destination": dest,
        "destination_airport": ticket.get("destination_airport", ""),
        "depart_month": month,
        "departure_at": ticket.get("departure_at", ""),
        "return_at": ticket.get("return_at", ""),
        "price": ticket.get("price", ""),
        "currency": currency.upper(),
        "airline": ticket.get("airline", ""),
        "transfers": ticket.get("transfers", ""),
        "return_transfers": ticket.get("return_transfers", ""),
        "duration_mins": ticket.get("duration", ""),
        "link": ticket.get("link", ""),
    }


def main() -> int:
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
    if not token:
        print("TRAVELPAYOUTS_TOKEN is not set. Add it as a GitHub secret (see README).", file=sys.stderr)
        return 1

    cfg = load_config()
    currency = cfg.get("currency", "gbp")
    today = dt.date.today()
    checked_on = today.isoformat()
    months = months_ahead(today, cfg.get("months_ahead", 12))

    new_rows: list[dict] = []
    calls = empty = 0
    errors: list[str] = []
    session = requests.Session()

    for o in cfg["origins"]:
        for d in cfg["destinations"]:
            for month in months:
                calls += 1
                try:
                    tickets = fetch(session, token, o["code"], d["code"], month, currency)
                except Exception as exc:  # keep going; one bad route must not stop the run
                    errors.append(f"{o['code']}-{d['code']} {month}: {exc}")
                    tickets = []
                priced = [t for t in tickets if isinstance(t.get("price"), (int, float))]
                if not priced:
                    empty += 1
                else:
                    cheapest = min(priced, key=lambda t: t["price"])
                    new_rows.append(to_row(cheapest, checked_on, o["code"], d["code"], month, currency))
                time.sleep(PAUSE_SECONDS)

    # Re-running on the same day replaces that day's rows instead of duplicating them.
    path = shard_path(today)
    kept: list[dict] = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            kept = [r for r in csv.DictReader(f) if r["checked_on"] != checked_on]
    write_rows(path, kept + new_rows, PRICE_FIELDS)

    report = {
        "checked_on": checked_on,
        "route_months_requested": calls,
        "route_months_with_prices": len(new_rows),
        "route_months_without_data": empty,
        "coverage_pct": round(100 * len(new_rows) / calls, 1) if calls else 0,
        "errors": len(errors),
        "error_examples": errors[:10],
    }
    write_json(REPORTS_DIR / "collect_latest.json", report)
    print(report)
    # Fail the run only if nothing at all came back (likely a bad token or API outage).
    return 0 if new_rows else 2


if __name__ == "__main__":
    sys.exit(main())
