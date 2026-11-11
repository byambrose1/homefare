"""Shared helpers for the HomeFare engine."""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PRICES_DIR = DATA / "prices"
REPORTS_DIR = DATA / "reports"

PRICE_FIELDS = [
    "checked_on", "origin", "origin_airport", "destination", "destination_airport",
    "depart_month", "departure_at", "return_at", "price", "currency", "airline",
    "transfers", "return_transfers", "duration_mins", "link",
]


def load_config() -> dict:
    with open(ROOT / "config" / "routes.json", encoding="utf-8") as f:
        return json.load(f)


def months_ahead(start: dt.date, count: int) -> list[str]:
    """['2026-11', '2026-12', ...] starting with the month of `start`."""
    out, y, m = [], start.year, start.month
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def shard_path(day: dt.date) -> Path:
    """Prices are stored one file per calendar month of the check date, to keep files small."""
    return PRICES_DIR / f"{day:%Y-%m}.csv"


def read_prices(since: dt.date) -> list[dict]:
    rows: list[dict] = []
    if not PRICES_DIR.exists():
        return rows
    first_shard = f"{since:%Y-%m}"
    for path in sorted(PRICES_DIR.glob("*.csv")):
        if path.stem < first_shard:
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["checked_on"] >= since.isoformat():
                    rows.append(row)
    return rows


def write_rows(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile: the value at or below which share q of observations fall."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q * len(s) + 0.5)) - 1))
    return s[k]


def aviasales_url(link: str) -> str:
    if not link:
        return ""
    if link.startswith("http"):
        return link
    return "https://www.aviasales.com" + (link if link.startswith("/") else "/" + link)
