import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src import strategy_book as sb
from src import strategy_lab as lab

# ``bars`` (synthetic 4h gold bars) comes from tests/conftest.py.


def test_monte_carlo_properties():
    assert sb.monte_carlo([1.0] * 5) is None
    steady = sb.monte_carlo([1.0] * 40)
    assert steady["loss_probability"] == 0 and steady["p95_max_drawdown_pct"] == 0
    edge = sb.monte_carlo([2.0, -1.0] * 30)
    assert edge["loss_probability"] < 0.2 and edge["p95_max_drawdown_pct"] > 0
    losing = sb.monte_carlo([-1.0, 0.5] * 30)
    assert losing["loss_probability"] > 0.8
    assert sb.monte_carlo([2.0, -1.0] * 30) == edge  # fixed seed: reproducible


def test_rolling_consistency_counts_windows():
    times = pd.date_range("2020-01-01", periods=48, freq="MS", tz="UTC")
    trades = [{"entry_time": lab._iso(t), "net_pct": 1.0 if t.year < 2022 else -1.5} for t in times]
    result = sb.rolling_consistency(trades)
    assert result["windows"] >= 8 and 0 < result["positive_share"] < 1 and result["worst_window_pct"] < 0
    assert sb.rolling_consistency([]) is None


def test_neighbours_change_one_setting_one_step():
    spec = lab.SWING_TREND_PULLBACK_SPEC
    rows = sb.neighbours(spec)
    assert len(rows) >= 10
    for child in rows:
        diffs = [(g, k) for g in ("params", "exits") for k in spec[g] if child[g].get(k) != spec[g].get(k)]
        assert len(diffs) == 1 and diffs[0][1] != "side"
        group, key = diffs[0]
        grid = (lab.FAMILIES[spec["family"]] if group == "params" else lab.EXIT_GRID)[key]
        assert abs(grid.index(child[group][key]) - grid.index(spec[group][key])) == 1
        assert lab._valid_spec(child)


def test_preset_text_maps_the_expert_inputs():
    text = sb.preset_text(lab.EA_SPECS["tradingview"], "XAUUSD:4h", {"status": "watchlist"})
    for line in ("EMAFastLen=21", "EMASlowLen=51", "AtrLen=12", "TrailAtrMult=4.5", "UseTakeProfit=true", "RrRatio=2.0",
                 "StopMode=0", "UseRsiFilter=true", "RsiMin=40.0", "MaxBarsInTrade=150", "TradeSide=0"):
        assert line in text, line
    assert "XAUUSD H4" in text
    assert sb.preset_text(lab.EA_SPECS["tradingview"], "BTCUSD:4h") is None
    donchian = {"family": "donchian_breakout", "params": {"side": "long", "lookback": 20, "trend_ema": 0},
                "exits": {"stop": "atr", "sl_atr": 1.0, "rr": 2.0, "trail_atr": 0.0, "max_bars": 12, "swing_lookback": 5}}
    assert sb.preset_text(donchian, "XAUUSD:4h") is None


def _evidence(passed=True, validated=True, holdout_pf=1.5, trades=40, loss_prob=0.05, share=0.8, rolling=0.7):
    return {"record": {"validated": validated, "gate_reasons": [] if validated else ["x"],
                       "holdout": {"trades": trades, "profit_factor": holdout_pf, "total_return_pct": 5.0}},
            "verdict": {"passed": passed, "checks": {"deflated_sharpe": passed}},
            "monte_carlo": {"loss_probability": loss_prob}, "neighbours": {"share": share},
            "rolling": {"positive_share": rolling}}


def test_classify_statuses():
    assert sb.classify(_evidence()) == ("approved_for_demo", [])
    status, reasons = sb.classify(_evidence(passed=False))
    assert status == "watchlist" and any("deflated_sharpe" in r for r in reasons)
    assert sb.classify(_evidence(share=0.3))[0] == "watchlist"
    assert sb.classify(_evidence(passed=False, holdout_pf=1.05))[0] is None
    assert sb.classify(_evidence(validated=False))[0] is None


def test_cap_watchlist_keeps_the_best_and_archives_the_rest(tmp_path):
    def entry(i, market, status, dsr, loss=0.1):
        return {"id": f"id{i}", "market": market, "status": status, "spec": lab.SWING_TREND_PULLBACK_SPEC,
                "latest": {"deflated_sharpe": dsr, "monte_carlo": {"loss_probability": loss}, "holdout": {"profit_factor": 1.3, "trades": 40}}}
    entries = [entry(i, "XAUUSD:4h", "watchlist", dsr=i / 20) for i in range(14)]
    entries += [entry(20, "XAUUSD:4h", "approved_for_demo", dsr=0.1), entry(21, "BTCUSD:4h", "watchlist", dsr=0.5)]
    book = {"entries": {f"{e['market']}|{e['id']}": e for e in entries}}
    assert sb.cap_watchlist(book, per_market=10) == {"XAUUSD:4h": 4}
    statuses = {e["id"]: e["status"] for e in book["entries"].values()}
    assert [statuses[f"id{i}"] for i in range(4)] == ["archived"] * 4                 # the four weakest
    assert all(statuses[f"id{i}"] == "watchlist" for i in range(4, 14))
    assert statuses["id20"] == "approved_for_demo" and statuses["id21"] == "watchlist"  # approved and other markets untouched
    assert book["entries"]["XAUUSD:4h|id0"]["archived_reason"] and book["entries"]["XAUUSD:4h|id0"]["latest"]  # kept, not deleted
    path = tmp_path / "book.json"
    sb.save_book(book, path)
    summary = sb.book_summary(path)
    assert summary["counts"]["archived"] == 4 and all(e["status"] != "archived" for e in summary["entries"])
    assert len(sb.book_specs("XAUUSD:4h", path)) == 11                                  # 10 watchlist + 1 approved as parents


def test_dedupe_archives_identical_results_and_keeps_the_best_copy():
    def entry(i, status, trades=52, pf=2.708, dsr=0.641, market="XAUUSD:4h", added="2026-09-13 08:00", family="donchian_breakout"):
        period = {"trades": trades, "profit_factor": pf, "total_return_pct": 39.9, "max_drawdown_pct": 4.8}
        return {"id": f"id{i}", "market": market, "family": family, "status": status, "first_added": added,
                "latest": {"deflated_sharpe": dsr, "monte_carlo": {"loss_probability": 0.1},
                           "search": dict(period), "validation": dict(period), "holdout": dict(period)}}
    entries = [entry(1, "watchlist", added="2026-09-13 09:00"), entry(2, "watchlist", added="2026-09-13 08:00"),
               entry(3, "watchlist", pf=2.5),                                   # different results: kept
               entry(4, "watchlist", market="XAUUSD:1d"),                       # other market: kept
               entry(5, "watchlist", family="ema_pullback"),                    # other family: kept
               entry(6, "approved_for_demo", trades=40), entry(7, "watchlist", trades=40),
               entry(8, "demoted", trades=60), entry(9, "watchlist", trades=60)]  # demoted is not a copy source
    book = {"entries": {e["id"]: e for e in entries}}
    assert sb.dedupe_watchlist(book) == {"XAUUSD:4h": 2}
    status = {e["id"]: e["status"] for e in book["entries"].values()}
    assert status["id2"] == "watchlist" and status["id1"] == "archived"          # tie -> earliest added stays
    assert book["entries"]["id1"]["duplicate_of"] == "id2" and book["entries"]["id1"]["latest"]  # kept, not deleted
    assert status["id6"] == "approved_for_demo" and status["id7"] == "archived"  # approved copy wins, never archived
    assert all(status[i] == "watchlist" for i in ("id3", "id4", "id5", "id9")) and status["id8"] == "demoted"
    assert sb.dedupe_watchlist(book) == {}                                        # idempotent
    no_evidence = {"entries": {"a": {"id": "a", "market": "m", "status": "watchlist", "latest": {}},
                               "b": {"id": "b", "market": "m", "status": "watchlist", "latest": {}}}}
    assert sb.dedupe_watchlist(no_evidence) == {}                                 # never guess without results


def test_leader_history_follows_the_best_deflated_sharpe_per_market(tmp_path):
    def entry(i, market, status, dsr):
        return {"id": f"id{i}", "market": market, "status": status,
                "latest": {"deflated_sharpe": dsr, "n_trials": 100, "monte_carlo": {"loss_probability": 0.1},
                           "holdout": {"profit_factor": 1.3, "trades": 40}}}
    book = {"entries": {"a": entry(1, "XAUUSD:4h", "watchlist", 0.8185), "b": entry(2, "XAUUSD:4h", "watchlist", 0.6),
                        "c": entry(3, "XAUUSD:4h", "archived", 0.99), "d": entry(4, "BTCUSD:1h", "demoted", 0.2)}}
    leaders = sb.market_leaders(book)
    assert leaders["XAUUSD:4h"]["id"] == "id1" and leaders["XAUUSD:4h"]["deflated_sharpe"] == 0.8185  # archived ignored
    assert leaders["BTCUSD:1h"]["id"] == "id4"
    sb.record_leaders(book, "2026-09-13 08:20")
    book["entries"]["a"]["latest"]["deflated_sharpe"] = 0.7543                         # same leader, weaker evidence
    sb.record_leaders(book, "2026-09-14 08:20")
    book["entries"]["b"]["latest"]["deflated_sharpe"] = 0.9                            # a different strategy takes the lead
    sb.record_leaders(book, "2026-09-14 09:20")
    trend = sb.leader_trend(book)["XAUUSD:4h"]
    assert trend["updates"] == 3 and trend["latest_id"] == "id2" and trend["leader_changed"] is True
    assert trend["change_since_previous"] == pytest.approx(0.9 - 0.7543, abs=1e-4) and trend["best_ever"] == 0.9
    assert [p["deflated_sharpe"] for p in trend["series"]] == [0.8185, 0.7543, 0.9]
    path = tmp_path / "book.json"
    sb.save_book(book, path)
    assert sb.book_summary(path)["leader_trend"]["BTCUSD:1h"]["change_since_previous"] == 0.0
    assert sb.leader_trend({"entries": {}}) == {}


def test_update_book_rescores_adds_and_writes_presets(tmp_path, bars, monkeypatch):
    registry_path, status_path, book_path = tmp_path / "registry.json", tmp_path / "status.json", tmp_path / "book.json"
    now = bars["datetime"].iloc[-1] + pd.Timedelta(days=1)
    loader = lambda symbol, timeframe: bars
    lab.run_search(["XAUUSD:4h"], max_candidates=25, seed=1, loader=loader, registry_path=registry_path,
                   status_path=status_path, now=now)
    registry = lab.load_registry(registry_path)
    baseline = next(rec for rec in registry["candidates"].values() if rec.get("tag"))
    planted = dict(baseline, tag=None, validated=True, id="planted00001", cost_model=None)  # old cost model -> rescored
    planted.pop("tag")
    registry["candidates"]["XAUUSD:4h|planted00001"] = planted
    lab.save_registry(registry, registry_path)

    monkeypatch.setattr(sb, "PRESET_DIR", tmp_path / "presets")
    monkeypatch.setattr(sb, "classify", lambda evidence: ("watchlist", ["test: forced status"]))
    original_rescore = sb.rescore_market

    def rescore_and_keep_planted(registry, market, deadline=None, on_progress=None):  # keep the planted record eligible
        result = original_rescore(registry, market, deadline, on_progress=on_progress)
        rec = registry["candidates"]["XAUUSD:4h|planted00001"]
        rec["validated"], rec["id"] = True, "planted00001"
        if not rec.get("holdout"):
            rec.update({k: baseline[k] for k in ("holdout", "holdout_returns")})
        return result

    monkeypatch.setattr(sb, "rescore_market", rescore_and_keep_planted)
    report = sb.update_book(["XAUUSD:4h"], minutes=5, loader=loader, registry_path=registry_path, book_path=book_path,
                            status_path=status_path, write_mt5_presets=False, now=now)
    assert report["markets"]["XAUUSD:4h"]["rescore"]["rescored"] >= 1
    book = json.loads(book_path.read_text(encoding="utf-8"))
    entry = book["entries"]["XAUUSD:4h|planted00001"]
    assert entry["status"] == "watchlist" and entry["history"] and entry["latest"]["cost_model"].startswith("spread+swap")
    assert Path(entry["presets"]["file"]).exists() and "mt5_file" not in entry["presets"]
    assert lab.read_status(status_path)["state"] != "running" and "last_book_update" in lab.read_status(status_path)
    assert sb.book_specs("XAUUSD:4h", book_path)  # watchlist strategies become search parents
    summary = sb.book_summary(book_path)
    assert summary["counts"].get("watchlist", 0) >= 1
