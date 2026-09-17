"""CFTC positioning: parsing the report file, net / percentile / week change, the dollar proxy, age, page and endpoint."""
import csv
import io
import json
import zipfile

import pytest

from src import positioning as po

HEADER = ["Market and Exchange Names", "As of Date in Form YYMMDD", "As of Date in Form YYYY-MM-DD", "code", "initials",
          "region", "commodity", "Open Interest (All)", "NC Long", "NC Short", "NC Spreading", "Comm Long"]


def cftc_zip(rows):
    """A zip shaped like deacot<year>.zip: one annual.txt with the report's column order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    for market, as_of, oi, long_, short_, spreading in rows:
        writer.writerow([market, as_of.replace("-", "")[2:], as_of, "001602", "CMX", "00", "001", oi, long_, short_,
                         spreading, 0])
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("annual.txt", buffer.getvalue())
    return blob.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_fetch_keeps_only_the_wanted_markets_and_caches_them(tmp_path, monkeypatch):
    monkeypatch.setattr(po, "positioning_dir", lambda: tmp_path)
    rows = [(po.MARKETS["GOLD"], "2026-09-08", 411000, 261007, 29047, 150000),
            (po.MARKETS["EUR"], "2026-09-08", 947000, 198509, 241125, 90000),
            ("WHEAT-SRW - CHICAGO BOARD OF TRADE", "2026-09-08", 484680, 131898, 121428, 150911)]
    blob = cftc_zip(rows)
    calls = []

    def opener(request, timeout=0):
        calls.append(getattr(request, "full_url", request))
        return FakeResponse(blob)

    count, note = po.fetch_year(2026, opener=opener)
    assert count == 2 and note == "downloaded" and "deacot2026.zip" in calls[0]
    cached = list(csv.DictReader((tmp_path / "cot_2026.csv").open(encoding="utf-8")))
    assert {r["market"] for r in cached} == {"GOLD", "EUR"}, "other markets are dropped"
    assert po.fetch_year(2026, opener=opener)[1] == "cached" and len(calls) == 1, "a second call uses the cache"


def test_market_row_net_percentile_and_week_change(tmp_path, monkeypatch):
    monkeypatch.setattr(po, "positioning_dir", lambda: tmp_path)
    weeks = [(po.MARKETS["GOLD"], f"2026-01-{d:02d}", 400000, 100000 + i * 100, 50000, 0)
             for i, d in enumerate(range(1, 26))]
    weeks.append((po.MARKETS["GOLD"], "2026-09-08", 411000, 261007, 29047, 150000))
    po.fetch_year(2026, opener=lambda request, timeout=0: FakeResponse(cftc_zip(weeks)))
    row = po.market_row("GOLD", po.load_history(1, now=__import__("datetime").datetime(2026, 9, 17))["GOLD"])
    assert row["available"] and row["as_of"] == "2026-09-08" and row["stance"] == "net long"
    assert row["net"] == 231960 and row["net_pct_of_open_interest"] == pytest.approx(56.4, abs=0.1)
    assert row["week_change_net"] == 231960 - (100000 + 24 * 100 - 50000) and row["week_change_direction"] == "more long"
    assert row["percentile_net_pct"] == 100.0 and "crowded long" in row["extreme"]
    assert po.market_row("SILVER", [])["available"] is False


def test_dollar_proxy_inverts_the_currency_net():
    rows = [{"market": m, "available": True, "net": n, "as_of": "2026-09-08"}
            for m, n in (("EUR", -42616), ("GBP", -58836), ("JPY", 10796), ("AUD", -34870), ("CAD", -70499), ("CHF", -29985))]
    proxy = po.dollar_proxy(rows)
    assert proxy["available"] and proxy["net_of_currencies"] == -226010 and proxy["stance"] == "net long dollars"
    assert "ICE" in proxy["note"]
    assert po.dollar_proxy(rows[:2])["available"] is False


def test_release_schedule_and_age():
    info = po.next_release("2026-09-08", now=__import__("datetime").datetime(2026, 9, 17, 12, 0,
                                                                            tzinfo=__import__("datetime").timezone.utc))
    assert info["live"] is False and info["released"].startswith("2026-09-11")
    assert info["age_days"] == pytest.approx(9.5, abs=0.1) and info["next_release_estimate"].startswith("2026-09-18")


def test_endpoint_and_page_report_missing_data_honestly(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(po, "positioning_dir", lambda: tmp_path)
    client = app_module.app.test_client()
    body = client.get("/api/positioning").get_json()
    assert body["available"] is False and "SmartEntry Positioning" in body["reason"]
    (tmp_path / "latest.json").write_text(json.dumps({"available": True, "generated_at": "2026-09-17 20:00:00",
                                                      "report": {"as_of_tuesday": "2026-09-08", "age_days": 9.8, "live": False},
                                                      "markets": [], "usd_proxy": {"available": False, "reason": "x"}}),
                                          encoding="utf-8")
    assert client.get("/api/positioning").get_json()["report"]["as_of_tuesday"] == "2026-09-08"
    html = client.get("/positioning").get_data(as_text=True)
    assert "Positioning" in html and "/api/positioning" in html and "Commitments of Traders" in html
