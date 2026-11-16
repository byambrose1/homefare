"""Step 4: refresh the "Real return fares from London" examples on the website.

Takes the most recent day of prices, picks the cheapest London return fare in each home region
(departing at least a week from now), and rewrites the block between the FARES markers in
site/homefare.html and site/index.html. Fares re-checked live on Google Flights the same day get
a "Checked live" tag. Also writes site/fares.json with the same data.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

from engine.common import DATA, ROOT, load_config, read_prices, write_json

SITE = ROOT / "site"
PAGES = [SITE / "homefare.html", SITE / "index.html"]
START, END = "<!--FARES:START-->", "<!--FARES:END-->"
MIN_DAYS_AHEAD = 7
MAX_CARDS = 6

AIRLINES = {
    "AT": "Royal Air Maroc", "GF": "Gulf Air", "MF": "Xiamen Air", "EY": "Etihad", "AI": "Air India",
    "G9": "Air Arabia", "3O": "Air Arabia Maroc", "BA": "British Airways", "VS": "Virgin Atlantic",
    "EK": "Emirates", "QR": "Qatar Airways", "TK": "Turkish Airlines", "PC": "Pegasus", "KQ": "Kenya Airways",
    "ET": "Ethiopian Airlines", "MS": "EgyptAir", "WY": "Oman Air", "SV": "Saudia", "PK": "PIA",
    "UL": "SriLankan Airlines", "BG": "Biman Bangladesh", "LH": "Lufthansa", "AF": "Air France", "KL": "KLM",
    "LX": "Swiss", "OS": "Austrian", "SN": "Brussels Airlines", "TP": "TAP Air Portugal", "IB": "Iberia",
    "UX": "Air Europa", "AZ": "ITA Airways", "W6": "Wizz Air", "FR": "Ryanair", "U2": "easyJet", "LS": "Jet2",
    "VY": "Vueling", "XQ": "SunExpress", "AH": "Air Algérie", "TU": "Tunisair", "RJ": "Royal Jordanian",
    "IA": "Iraqi Airways", "CX": "Cathay Pacific", "PR": "Philippine Airlines", "VN": "Vietnam Airlines",
    "MH": "Malaysia Airlines", "SQ": "Singapore Airlines", "CA": "Air China", "MU": "China Eastern",
    "CZ": "China Southern", "HU": "Hainan Airlines", "6E": "IndiGo", "WB": "RwandAir", "SA": "South African Airways",
    "4Z": "Airlink", "P4": "Air Peace", "HF": "Air Côte d'Ivoire", "KP": "ASKY", "DT": "TAAG", "FZ": "flydubai",
    "J9": "Jazeera Airways", "KU": "Kuwait Airways", "TG": "Thai Airways", "BY": "TUI", "DE": "Condor",
    "A3": "Aegean", "LO": "LOT", "AY": "Finnair", "EI": "Aer Lingus", "BW": "Caribbean Airlines",
    "B6": "JetBlue", "AA": "American Airlines", "UA": "United", "DL": "Delta", "AC": "Air Canada",
    "WS": "WestJet", "HY": "Uzbekistan Airways", "KC": "Air Astana", "LY": "El Al", "ME": "Middle East Airlines",
    "GP": "APG Airlines",
}


def day_range(out: dt.date, back: dt.date) -> str:
    if out.year == back.year and out.month == back.month:
        return f"{out.day} – {back.day} {back:%b}"
    return f"{out.day} {out:%b} – {back.day} {back:%b}"


def stops_text(transfers: str) -> str:
    try:
        n = int(float(transfers))
    except (TypeError, ValueError):
        return ""
    return "Direct" if n == 0 else f"{n} stop" + ("s" if n > 1 else "")


def pick(rows: list[dict], cfg: dict, today: dt.date) -> tuple[str, list[dict]]:
    london = [r for r in rows if r["origin"] == "LON"]
    if not london:
        return "", []
    latest = max(r["checked_on"] for r in london)
    earliest_departure = (today + dt.timedelta(days=MIN_DAYS_AHEAD)).isoformat()
    dests = {d["code"]: d for d in cfg["destinations"]}
    best_in_region: dict[str, dict] = {}
    for r in london:
        if r["checked_on"] != latest or r["destination"] not in dests:
            continue
        if not r["departure_at"] or not r["return_at"] or r["departure_at"][:10] < earliest_departure:
            continue
        try:
            price = float(r["price"])
        except (TypeError, ValueError):
            continue
        region = dests[r["destination"]]["region"]
        if region not in best_in_region or price < float(best_in_region[region]["price"]):
            best_in_region[region] = r
    chosen = sorted(best_in_region.values(), key=lambda r: float(r["price"]))[:MAX_CARDS]
    return latest, [dict(r, **{"_dest": dests[r["destination"]]}) for r in chosen]


def live_checked(row: dict, live: dict, latest: str) -> bool:
    if not live.get("checked_at", "").startswith(latest):
        return False
    for res in live.get("results", []):
        if (res.get("origin") == row["origin"] and res.get("destination") == row["destination"]
                and res.get("outbound_date") == row["departure_at"][:10] and res.get("live_price")):
            return True
    return False


def card(f: dict) -> str:
    e = html.escape
    tag = '<p class="fv">Checked live on Google Flights</p>' if f["checked_live"] else ""
    details = " · ".join(x for x in (f["dates"], f["airline"], f["stops"]) if x)
    return (f'        <article class="fare"><div class="fr">{e(f["from"])} → {e(f["to"])}</div>'
            f'<h3>{e(f["place"])}</h3><div class="fp">£{f["price"]} <small>return</small></div>'
            f'<p>{e(details)}</p>{tag}</article>')


def block(date_label: str, fares: list[dict]) -> str:
    cards = "\n".join(card(f) for f in fares)
    return f"""{START}
      <div class="head">
        <p class="kicker">Spotted by our price engine · {date_label}</p>
        <h2>Real return fares from London right now</h2>
        <p class="muted">Examples from our daily price scan, not offers: the cheapest fare we saw to each region on our latest scan. Fares change fast, which is exactly why we check every day.</p>
      </div>
      <div class="fare-grid">
{cards}
      </div>
      <p class="small">Per person, economy, seen on {date_label}. Updated every morning. Bag allowances vary by fare. Members get alerts like these from 1 December.</p>
      {END}"""


def build(rows: list[dict], cfg: dict, live: dict, today: dt.date) -> tuple[str, list[dict]]:
    latest, chosen = pick(rows, cfg, today)
    fares = []
    for r in chosen:
        d = r["_dest"]
        out = dt.date.fromisoformat(r["departure_at"][:10])
        back = dt.date.fromisoformat(r["return_at"][:10])
        fares.append({
            "from": r["origin_airport"] or r["origin"], "to": r["destination_airport"] or r["destination"],
            "place": f'{d["city"]}, {d["country"]}' if d.get("country") else d["city"],
            "price": round(float(r["price"])), "dates": day_range(out, back),
            "airline": AIRLINES.get(r["airline"], r["airline"]), "stops": stops_text(r["transfers"]),
            "checked_live": live_checked(r, live, latest),
        })
    return latest, fares


def main() -> int:
    cfg = load_config()
    today = dt.date.today()
    rows = read_prices(today - dt.timedelta(days=3))
    live_path = DATA / "live_checks_latest.json"
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path.exists() else {}
    latest, fares = build(rows, cfg, live, today)
    if len(fares) < 3:
        print("Too few fares to show; leaving the website as it is.")
        return 0
    d = dt.date.fromisoformat(latest)
    label = f"{d.day} {d:%b %Y}"
    write_json(SITE / "fares.json", {"seen_on": latest, "fares": fares})
    new_block = block(label, fares)
    pattern = re.compile(re.escape(START) + ".*?" + re.escape(END), re.S)
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        if START not in text:
            print(f"No fares markers in {page.name}; skipped.", file=sys.stderr)
            continue
        page.write_text(pattern.sub(lambda _: new_block, text, count=1), encoding="utf-8")
    print({"seen_on": latest, "fares": [f'{f["place"]} £{f["price"]}' for f in fares]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
