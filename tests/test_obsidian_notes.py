import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import obsidian_notes as on
from test_learning_curve import _write  # one JSON file helper for the test suite


def _data(tmp_path):
    data = tmp_path / "data"
    _write(data / "daily_reports" / "2026-09-13.json", {"date": "2026-09-13", "generated_at": "2026-09-13 05:45", "markets": {}, "system": {}})
    _write(data / "daily_reports" / "2026-09-14.json", {
        "date": "2026-09-14", "generated_at": "2026-09-14 05:45",
        "markets": {"XAUUSD": {"available": True, "bias": {"label": "bullish", "score": 0.54, "reasons": ["1d trend up"]},
                               "levels": {"close": 4348.14, "atr": 107.45, "levels": [{"name": "Previous day high", "price": 4434.76, "distance_pct": 1.99}]}}},
        "system": {"doctor": {"overall": "healthy", "checked_at": "2026-09-14 05:30", "problems": []},
                   "strategy_lab": {"markets": {"XAUUSD:4h": {"evaluated": 10, "validated": 2, "holdout_passed": 0}}}},
    })
    _write(data / "tradingview_plans" / "2026-09-14_XAUUSD.json", {
        "available": True, "date": "2026-09-14", "headline": "NEW BUY setup", "entry": 4300.5, "stop_loss": 4250, "take_profit": 4401,
        "reward_risk": 2.0, "checklist": [{"name": "H4 uptrend", "ok": True}, {"name": "RSI above 40", "ok": False}]})
    _write(data / "tradingview_alerts.json", [{"symbol": "BTCUSD", "side": "BUY", "entry": 76800.0, "source": "tradingview",
                                              "received_at": "2026-09-13 14:10:45", "alignment": "Aligned"}])
    return data


def test_safe_name_removes_characters_obsidian_and_windows_reject():
    assert on.safe_name('Paper 2026-09-14 05:00 XAUUSD BUY') == "Paper 2026-09-14 05 00 XAUUSD BUY"
    assert on.safe_name("a/b\\c|d#e[f]") == "a b c d e f"


def test_write_all_creates_the_vault_and_notes(tmp_path):
    data = _data(tmp_path)
    vault = tmp_path / "vault"
    summary = on.write_all(vault, data_dir=data, baseline_path=tmp_path / "missing.md", now=datetime(2026, 9, 14, 13, 0))
    assert (vault / ".obsidian" / "app.json").exists()
    daily = (vault / "Daily" / "2026-09-14.md").read_text(encoding="utf-8")
    assert "NEW BUY setup" in daily and "4,300.50" in daily and "- [x] H4 uptrend" in daily and "- [ ] RSI above 40" in daily
    assert "previous [[Daily/2026-09-13]]" in daily and on.MARKER in daily and on.MY_NOTES in daily
    assert "passed the locked holdout" in daily
    assert (vault / "Learning" / "2026-09-14 Learning.md").exists()
    assert "BASELINE.md was not found." in (vault / "Research" / "Research Log.md").read_text(encoding="utf-8")
    journal = (vault / "Trades" / "Trade Journal.md").read_text(encoding="utf-8")
    assert "TradingView 2026-09-13 14 10 45 BTCUSD BUY" in journal
    assert (vault / "Trades" / "TradingView 2026-09-13 14 10 45 BTCUSD BUY.md").exists()
    assert "[[Daily/2026-09-14]]" in (vault / "Home.md").read_text(encoding="utf-8")
    assert summary["counts"].get("written", 0) == summary["notes"]


def test_refresh_keeps_my_notes_trade_comments_and_notes_taken_over(tmp_path):
    data = _data(tmp_path)
    vault = tmp_path / "vault"
    now = datetime(2026, 9, 14, 13, 0)
    on.write_all(vault, data_dir=data, baseline_path=tmp_path / "missing.md", now=now)
    daily = vault / "Daily" / "2026-09-14.md"
    daily.write_text(daily.read_text(encoding="utf-8") + "Waited for London open.\n", encoding="utf-8")
    trade = vault / "Trades" / "TradingView 2026-09-13 14 10 45 BTCUSD BUY.md"
    trade.write_text("my own trade review", encoding="utf-8")
    home = vault / "Home.md"
    home.write_text("my own home page", encoding="utf-8")
    summary = on.write_all(vault, data_dir=data, baseline_path=tmp_path / "missing.md", now=now)
    assert "Waited for London open." in daily.read_text(encoding="utf-8")
    assert trade.read_text(encoding="utf-8") == "my own trade review"
    assert home.read_text(encoding="utf-8") == "my own home page"
    assert summary["results"]["Home.md"] == "skipped (edited by you)"


def test_register_vault_adds_once(tmp_path):
    config = tmp_path / "obsidian" / "obsidian.json"
    vault = tmp_path / "vault"
    vault.mkdir()
    assert on.register_vault(vault, config) == "registered"
    assert on.register_vault(vault, config) == "already registered"
    saved = json.loads(config.read_text(encoding="utf-8"))
    (entry,) = saved["vaults"].values()
    assert entry["open"] is True and Path(entry["path"]) == vault.resolve()
