import json
from datetime import datetime, timezone

from src import ea_monitor as em

INPUTS = ("EMA 21/51 slope 1 trendEMA 0 | push 15 > 0.50 ATR | tol 0.55 ATR bullClose on | RSI on 14>40.0 ADX off | "
          "ATR 12 stop swing 1.50 | TP on RR fixed 2.00 | trail on 4.50 | maxBars on 150 | lots STP_LOTS_RISK risk 1.00% magic 996611")


def _deal(ticket, position, time, dtype, entry, price, profit=0.0, swap=0.0, sl=0.0, tp=0.0, reason=3, comment=""):
    return {"ticket": ticket, "position": position, "time": time, "time_msc": time * 1000, "type": dtype, "entry": entry,
            "volume": 0.1, "price": price, "profit": profit, "swap": swap, "commission": 0.0, "fee": 0.0,
            "sl": sl, "tp": tp, "reason": reason, "comment": comment}


def test_pair_trades_closed_and_open():
    deals = [
        _deal(1, 10, 1_000_000, "BUY", "IN", 2000.0, sl=1990.0, tp=2020.0, comment="Swing Pullback"),
        _deal(2, 10, 1_036_000, "SELL", "OUT", 1990.0, profit=-100.0, swap=-5.0, reason=4, comment="sl 1990.00"),
        _deal(3, 11, 1_100_000, "BUY", "IN", 2010.0, sl=2000.0),
    ]
    trades = em.pair_trades(deals, offset=3 * 3600)
    assert [t["status"] for t in trades] == ["closed", "open"]
    closed = trades[0]
    assert closed["side"] == "BUY" and closed["net"] == -105.0
    assert closed["exit_reason"] == "stop loss" and closed["r_multiple"] == -1.0
    assert closed["hours_held"] == 10.0
    assert closed["open_time_utc"] == datetime.fromtimestamp(1_000_000 - 3 * 3600, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def test_merge_never_removes_and_updates_open_to_closed():
    store = {"trades": {}}
    open_only = em.pair_trades([_deal(3, 11, 1_100_000, "BUY", "IN", 2010.0, sl=2000.0)])
    assert em.merge_trades(store, open_only, login=1)["added"] == 1
    closed = em.pair_trades([_deal(3, 11, 1_100_000, "BUY", "IN", 2010.0, sl=2000.0),
                             _deal(4, 11, 1_110_000, "SELL", "OUT", 2030.0, profit=200.0, reason=5, comment="tp 2030")])
    result = em.merge_trades(store, closed, login=1)
    assert result == {"added": 0, "updated": 1, "total": 1}
    assert store["trades"]["1:11"]["status"] == "closed" and store["trades"]["1:11"]["first_seen_utc"]
    assert em.merge_trades(store, [], login=1)["total"] == 1  # an empty export does not delete history


def test_trade_stats():
    trades = [{"status": "closed", "net": n, "swap": 0, "close_time_server": i, "close_time_utc": f"2026-09-0{i + 1} 10:00:00",
               "exit_reason": "take profit" if n > 0 else "stop loss", "hours_held": 4.0, "r_multiple": 1.0 if n > 0 else -1.0}
              for i, n in enumerate([100.0, -50.0, -50.0, 200.0])]
    trades.append({"status": "open", "net": 0})
    stats = em.trade_stats(trades)
    assert stats["closed_trades"] == 4 and stats["open_trades"] == 1
    assert stats["win_rate"] == 50.0 and stats["profit_factor"] == 3.0 and stats["net"] == 200.0
    assert stats["max_drawdown"] == 100.0 and stats["max_consecutive_losses"] == 2
    assert stats["equity_curve"][-1]["equity"] == 200.0


def test_inputs_parse_and_compare(tmp_path):
    active = em.parse_inputs("inputs: " + INPUTS)
    assert active["ema_slow"] == 51 and active["trail_atr"] == 4.5 and active["bull_close"] is True
    preset_file = tmp_path / "p.set"
    preset_file.write_text("; header\nEMAFastLen=21\nEMASlowLen=51\nTrailAtrMult=4.5\nRequireBullClose=true\nRiskPercent=2.0\n")
    diffs = em.compare_with_preset(active, em.preset_inputs(preset_file))
    assert diffs == [{"input": "RiskPercent", "active": 1.0, "preset": 2.0}]


def test_parse_log_lines():
    lines = [
        "HI\t0\t17:14:07.365\tSwingTrendPullback (XAUUSD,H4)\tSwingPullback: initialized on XAUUSD H4, v2.01",
        "FJ\t0\t17:14:07.365\tSwingTrendPullback (XAUUSD,H4)\tSwingPullback: inputs: " + INPUTS,
        "LQ\t0\t17:13:34.046\tExperts\texpert SwingTrendPullback (XAUUSD,H4) removed",
        "XX\t0\t17:20:00.000\tDT_Copy (SP500,M15)\tsomething else",
        "AB\t0\t18:00:00.000\tSwingTrendPullback (XAUUSD,H4)\tSwingPullback: OrderSend failed, retcode=10018 Market closed (will retry this bar)",
    ]
    events = em.parse_log_lines("2026-09-13", lines)
    assert [e["kind"] for e in events] == ["start", "inputs", "detached", "retry"]
    assert events[0]["time"] == "2026-09-13 17:14:07"


def test_health_without_export_and_with_good_status(tmp_path):
    no_export = em.health_checks(None, [], None, None, now_utc=datetime(2026, 9, 14, 10, tzinfo=timezone.utc))
    assert no_export["overall"] == "warn"
    status = {"running": True, "file_age_seconds": 5, "version": em.EXPECTED_VERSION, "terminal_connected": True,
              "algo_terminal": True, "algo_expert": True, "account_trade_mode": 0, "login": 1, "balance": 100,
              "server_time": 1000, "tick_time": 995, "spread_points": 20, "inputs": INPUTS}
    active = em.parse_inputs(INPUTS)
    preset = {"EMAFastLen": "21", "EMASlowLen": "51"}
    good = em.health_checks(status, [], active, preset, now_utc=datetime(2026, 9, 14, 10, tzinfo=timezone.utc))
    assert good["overall"] == "ok", good
    bad = em.health_checks(dict(status, algo_expert=False), [], active, preset,
                           now_utc=datetime(2026, 9, 14, 10, tzinfo=timezone.utc))
    assert bad["overall"] == "bad"


def test_build_summary_syncs_store(tmp_path):
    common = tmp_path / "common"
    common.mkdir()
    status = {"version": em.EXPECTED_VERSION, "running": True, "server_time": 10_800 + 1_200_000, "gmt_time": 1_200_000,
              "login": 25446287, "inputs": INPUTS, "terminal_connected": True, "algo_terminal": True, "algo_expert": True,
              "account_trade_mode": 0, "tick_time": 10_800 + 1_200_000}
    deals = {"login": 25446287, "server_time": 10_800 + 1_200_000, "gmt_time": 1_200_000, "deals": [
        _deal(1, 10, 1_000_000, "BUY", "IN", 2000.0, sl=1990.0),
        _deal(2, 10, 1_036_000, "SELL", "OUT", 2020.0, profit=200.0, reason=5, comment="tp 2020")]}
    (common / "25446287_XAUUSD_996611_status.json").write_text(json.dumps(status))
    (common / "25446287_XAUUSD_996611_deals.json").write_text(json.dumps(deals))
    store = tmp_path / "store" / "trades.json"
    summary = em.build_summary(common_dir=common, terminal_dir=tmp_path / "terminal", store_path=store)
    assert summary["store"]["added"] == 1 and store.exists()
    assert summary["stats"]["closed_trades"] == 1 and summary["stats"]["net"] == 200.0
    assert summary["export"]["server_offset_hours"] == 3.0
    (common / "25446287_XAUUSD_996611_deals.json").write_text(json.dumps(dict(deals, deals=[])))
    again = em.build_summary(common_dir=common, terminal_dir=tmp_path / "terminal", store_path=store)
    assert again["stats"]["closed_trades"] == 1  # the permanent record keeps the trade
