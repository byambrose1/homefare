"""Step 2: work out each route's usual price and flag today's fares that are well below it.

Usual price = median of the cheapest fare recorded each day over the lookback window (default 90 days),
for the same route and travel month. Today's fare is a candidate when it is:
  - at least `min_pct_below` under the usual price (default 25%), AND
  - at or below the cheapest `cheapest_share` of past days (default the cheapest 10%), AND
  - the route-month has at least `min_days` days of history (default 20).
Route-months without enough history are listed as "learning".
"""
from __future__ import annotations

import datetime as dt
import sys
from collections import defaultdict

from engine.common import (
    DATA, REPORTS_DIR, aviasales_url, load_config, median, quantile, read_prices, write_json, write_rows,
)

BASELINE_FIELDS = ["origin", "destination", "depart_month", "days_of_history", "usual_price", "cheapest_10pct", "lowest", "today_price"]


def analyse(rows: list[dict], today: str, rules: dict, dest_names: dict[str, str]) -> tuple[list[dict], list[dict], list[dict]]:
    # cheapest price per (route-month, day)
    daily: dict[tuple, dict[str, float]] = defaultdict(dict)
    today_rows: dict[tuple, dict] = {}
    for r in rows:
        try:
            price = float(r["price"])
        except (TypeError, ValueError):
            continue
        key = (r["origin"], r["destination"], r["depart_month"])
        day = r["checked_on"]
        if price < daily[key].get(day, float("inf")):
            daily[key][day] = price
            if day == today:
                today_rows[key] = r

    candidates, learning, baselines = [], [], []
    for key, row in today_rows.items():
        past = [p for d, p in daily[key].items() if d < today]
        price = float(row["price"])
        origin, dest, month = key
        base = {
            "origin": origin, "destination": dest, "depart_month": month,
            "days_of_history": len(past), "today_price": price,
            "usual_price": round(median(past), 2) if past else "",
            "cheapest_10pct": round(quantile(past, rules["cheapest_share"]), 2) if past else "",
            "lowest": min(past) if past else "",
        }
        baselines.append(base)
        info = {
            "origin": origin, "destination": dest, "city": dest_names.get(dest, dest),
            "depart_month": month, "departure_at": row["departure_at"], "return_at": row["return_at"],
            "price": price, "currency": row["currency"], "airline": row["airline"],
            "transfers": row["transfers"], "return_transfers": row["return_transfers"],
            "origin_airport": row["origin_airport"], "destination_airport": row["destination_airport"],
            "aviasales_link": aviasales_url(row["link"]), "days_of_history": len(past),
        }
        if len(past) < rules["min_days"]:
            # rank learning routes by how cheap today looks against what little history exists
            info["pct_below_so_far"] = round(1 - price / median(past), 3) if len(past) >= 3 else None
            learning.append(info)
            continue
        usual = median(past)
        pct_below = 1 - price / usual
        if pct_below >= rules["min_pct_below"] and price <= quantile(past, rules["cheapest_share"]):
            info.update(usual_price=round(usual, 2), pct_below=round(pct_below, 3), source="our_history")
            candidates.append(info)

    candidates.sort(key=lambda c: c["pct_below"], reverse=True)
    learning.sort(key=lambda c: (c["pct_below_so_far"] is None, -(c["pct_below_so_far"] or 0)))
    baselines.sort(key=lambda b: (b["origin"], b["destination"], b["depart_month"]))
    return candidates, learning, baselines


def main() -> int:
    cfg = load_config()
    rules = cfg["deal_rules"]
    today = dt.date.today()
    since = today - dt.timedelta(days=rules["lookback_days"])
    rows = read_prices(since)
    dest_names = {d["code"]: d["city"] for d in cfg["destinations"]}

    candidates, learning, baselines = analyse(rows, today.isoformat(), rules, dest_names)
    write_json(DATA / "candidates.json", {"date": today.isoformat(), "candidates": candidates, "learning": learning})
    write_rows(DATA / "baselines.csv", baselines, BASELINE_FIELDS)
    report = {
        "date": today.isoformat(),
        "route_months_priced_today": len(baselines),
        "ready_with_history": len(baselines) - len(learning),
        "still_learning": len(learning),
        "candidates": len(candidates),
    }
    write_json(REPORTS_DIR / "find_latest.json", report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
