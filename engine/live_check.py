"""Step 3: check candidate deals live on Google Flights (through SerpApi) before anyone sees them.

Each check costs one SerpApi search, so the number per day is capped (config: live_check.max_checks_per_day).

Two kinds of check:
  1. Candidates from our own history (find_deals.py): still a deal if the live price is at least
     `still_a_deal_pct_below` under our usual price.
  2. Early days, while routes are still learning: we spend any spare checks on the most promising
     learning routes and use Google's own "typical price range" instead. Still a deal if the live
     price is below the bottom of Google's typical range.

Results go to data/live_checks_latest.json and verified deals are added to data/deal_log.csv with
status "awaiting_review". Nothing is sent to members from here: a person approves every alert.

Needs the SERPAPI_KEY environment variable (stored as a GitHub secret).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys

import requests

from engine.common import DATA, REPORTS_DIR, load_config, write_json

SERPAPI = "https://serpapi.com/search.json"
LOG_FIELDS = [
    "logged_at", "status", "origin", "destination", "city", "outbound_date", "return_date",
    "live_price", "usual_price", "usual_source", "pct_below", "google_price_level",
    "google_typical_low", "google_typical_high", "airlines", "stops", "google_flights_url",
    "aviasales_link", "still_live_24h",
]


def search(key: str, departure_ids: str, arrival_id: str, outbound: str, inbound: str) -> dict:
    params = {
        "engine": "google_flights", "departure_id": departure_ids, "arrival_id": arrival_id,
        "outbound_date": outbound, "return_date": inbound, "type": "1",
        "currency": "GBP", "gl": "uk", "hl": "en", "api_key": key,
    }
    r = requests.get(SERPAPI, params=params, timeout=60)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"])[:200])
    return body


def summarise(body: dict) -> dict:
    flights = (body.get("best_flights") or []) + (body.get("other_flights") or [])
    priced = [f for f in flights if isinstance(f.get("price"), (int, float))]
    insights = body.get("price_insights") or {}
    typical = insights.get("typical_price_range") or [None, None]
    out = {
        "live_price": None, "airlines": "", "stops": None,
        "google_price_level": insights.get("price_level", ""),
        "google_typical_low": typical[0] if len(typical) > 0 else None,
        "google_typical_high": typical[1] if len(typical) > 1 else None,
        "google_flights_url": (body.get("search_metadata") or {}).get("google_flights_url", ""),
    }
    if priced:
        best = min(priced, key=lambda f: f["price"])
        legs = best.get("flights") or []
        out["live_price"] = best["price"]
        out["airlines"] = ", ".join(dict.fromkeys(l.get("airline", "") for l in legs if l.get("airline")))
        out["stops"] = len(best.get("layovers") or [])
    return out


def judge(candidate: dict, live: dict, still_pct: float) -> tuple[bool, float | None, str, float | None]:
    """Returns (is_deal, usual_price, usual_source, pct_below)."""
    price = live["live_price"]
    if price is None:
        return False, None, "", None
    if candidate.get("usual_price"):
        usual = float(candidate["usual_price"])
        pct = 1 - price / usual
        return pct >= still_pct, usual, "our_history", round(pct, 3)
    low, high = live["google_typical_low"], live["google_typical_high"]
    if low and high:
        usual = (low + high) / 2
        return price < low, usual, "google_typical_range", round(1 - price / usual, 3)
    return False, None, "", None


def main() -> int:
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        print("SERPAPI_KEY is not set. Add it as a GitHub secret (see README).", file=sys.stderr)
        return 1
    cfg = load_config()
    lc = cfg["live_check"]
    origins = {o["code"]: o["serpapi_ids"] for o in cfg["origins"]}
    with open(DATA / "candidates.json", encoding="utf-8") as f:
        found = json.load(f)

    queue = list(found["candidates"])
    spare = max(0, lc["max_checks_per_day"] - len(queue))
    queue += found["learning"][:spare]
    queue = queue[: lc["max_checks_per_day"]]

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results, verified, errors = [], [], []
    for c in queue:
        outbound, inbound = c["departure_at"][:10], c["return_at"][:10]
        if not outbound or not inbound or outbound < dt.date.today().isoformat():
            continue
        try:
            live = summarise(search(key, origins.get(c["origin"], c["origin"]), c["destination"], outbound, inbound))
        except Exception as exc:
            errors.append(f"{c['origin']}-{c['destination']} {outbound}: {exc}")
            continue
        is_deal, usual, source, pct = judge(c, live, lc["still_a_deal_pct_below"])
        rec = {**c, **live, "checked_at": now, "outbound_date": outbound, "return_date": inbound,
               "is_deal": is_deal, "usual_price": usual, "usual_source": source, "pct_below": pct}
        results.append(rec)
        if is_deal:
            verified.append(rec)

    write_json(DATA / "live_checks_latest.json", {"checked_at": now, "results": results, "errors": errors})

    log_path = DATA / "deal_log.csv"
    new_file = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for v in verified:
            w.writerow({**v, "logged_at": now, "status": "awaiting_review", "still_live_24h": ""})

    report = {"checked_at": now, "searches_used": len(results) + len(errors), "verified_deals": len(verified), "errors": errors[:5]}
    write_json(REPORTS_DIR / "live_check_latest.json", report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
