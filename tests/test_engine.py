import datetime as dt
import json

import pytest

from engine import collect, find_deals, live_check
from engine.common import median, months_ahead, quantile

RULES = {"lookback_days": 90, "min_days": 20, "min_pct_below": 0.25, "cheapest_share": 0.10}


def row(day, price, origin="LON", dest="LOS", month="2027-02"):
    return {"checked_on": day, "origin": origin, "origin_airport": "LHR", "destination": dest,
            "destination_airport": dest, "depart_month": month, "departure_at": "2027-02-10T10:00:00Z",
            "return_at": "2027-02-24T10:00:00Z", "price": str(price), "currency": "GBP", "airline": "XX",
            "transfers": "1", "return_transfers": "1", "duration_mins": "600", "link": "/search/abc"}


def history(days, start_price=700):
    base = dt.date(2026, 10, 1)
    return [row((base + dt.timedelta(days=i)).isoformat(), start_price + (i % 5) * 10) for i in range(days)]


def test_months_ahead_rolls_over_year():
    assert months_ahead(dt.date(2026, 11, 11), 3) == ["2026-11", "2026-12", "2027-01"]


def test_median_and_quantile():
    assert median([1, 3, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert quantile(list(range(1, 101)), 0.10) == 10


def test_deal_flagged_when_well_below_usual():
    rows = history(30) + [row("2026-11-11", 480)]
    cands, learning, base = find_deals.analyse(rows, "2026-11-11", RULES, {"LOS": "Lagos"})
    assert len(cands) == 1 and not learning
    c = cands[0]
    assert c["city"] == "Lagos" and c["usual_price"] == 720.0
    assert c["pct_below"] == pytest.approx(1 - 480 / 720, abs=0.001)
    assert c["aviasales_link"] == "https://www.aviasales.com/search/abc"


def test_normal_price_is_not_a_deal():
    rows = history(30) + [row("2026-11-11", 690)]
    cands, _, _ = find_deals.analyse(rows, "2026-11-11", RULES, {})
    assert cands == []


def test_short_history_is_learning_not_deal():
    rows = history(5) + [row("2026-11-11", 300)]
    cands, learning, _ = find_deals.analyse(rows, "2026-11-11", RULES, {})
    assert cands == [] and len(learning) == 1
    assert learning[0]["pct_below_so_far"] > 0.5


def test_cheapest_per_day_is_used():
    rows = history(30) + [row("2026-11-11", 900), row("2026-11-11", 470)]
    cands, _, _ = find_deals.analyse(rows, "2026-11-11", RULES, {})
    assert cands[0]["price"] == 470


SERP = {
    "best_flights": [{"price": 455, "flights": [{"airline": "Turkish Airlines"}, {"airline": "Turkish Airlines"}], "layovers": [{}]}],
    "other_flights": [{"price": 610, "flights": [{"airline": "British Airways"}], "layovers": []}],
    "price_insights": {"lowest_price": 455, "price_level": "low", "typical_price_range": [560, 820]},
    "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x"},
}


def test_summarise_serpapi():
    s = live_check.summarise(SERP)
    assert s["live_price"] == 455 and s["airlines"] == "Turkish Airlines" and s["stops"] == 1
    assert (s["google_typical_low"], s["google_typical_high"]) == (560, 820)


def test_judge_with_our_history():
    ok, usual, src, pct = live_check.judge({"usual_price": 720}, live_check.summarise(SERP), 0.20)
    assert ok and src == "our_history" and pct == pytest.approx(0.368, abs=0.001)


def test_judge_with_google_range_when_learning():
    ok, usual, src, _ = live_check.judge({}, live_check.summarise(SERP), 0.20)
    assert ok and src == "google_typical_range" and usual == 690


def test_judge_rejects_when_price_gone_up():
    body = dict(SERP, best_flights=[{"price": 700, "flights": [], "layovers": []}], other_flights=[])
    ok, *_ = live_check.judge({"usual_price": 720}, live_check.summarise(body), 0.20)
    assert not ok


def test_collect_main_with_fake_api(tmp_path, monkeypatch):
    import engine.common as common
    monkeypatch.setattr(common, "PRICES_DIR", tmp_path / "prices")
    monkeypatch.setattr(collect, "shard_path", lambda d: tmp_path / "prices" / f"{d:%Y-%m}.csv")
    monkeypatch.setattr(collect, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(collect, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(collect, "load_config", lambda: {
        "currency": "gbp", "months_ahead": 2,
        "origins": [{"code": "LON"}], "destinations": [{"code": "LOS"}, {"code": "KIN"}]})
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "test")

    class Resp:
        def __init__(self, data): self.status_code, self._d = 200, data
        def raise_for_status(self): pass
        def json(self): return self._d

    def fake_get(self, url, params=None, headers=None, timeout=None):
        assert headers["X-Access-Token"] == "test" and params["currency"] == "gbp"
        if params["destination"] == "KIN":
            return Resp({"success": True, "data": []})
        return Resp({"success": True, "data": [{"price": 610, "airline": "TK"}, {"price": 540, "airline": "AT", "link": "/s"}]})

    monkeypatch.setattr(collect.requests.Session, "get", fake_get)
    assert collect.main() == 0
    report = json.loads((tmp_path / "reports" / "collect_latest.json").read_text())
    assert report["route_months_requested"] == 4 and report["route_months_with_prices"] == 2
    text = next((tmp_path / "prices").glob("*.csv")).read_text()
    assert "540" in text and "610" not in text
    # running twice on the same day doesn't duplicate rows
    assert collect.main() == 0
    assert next((tmp_path / "prices").glob("*.csv")).read_text().count("\n") == 3
