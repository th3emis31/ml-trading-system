"""The model layer behind one interface: the only part of this system that is rented.

These tests pin the two things that make independence achievable - that availability is PROBED rather
than assumed, and that a local provider is preferred when one can answer - plus the two safety
properties: no prompt text is ever written to disk, and nothing here can trade.
"""
import json

import pytest

from src import ai_provider as ap


class FakeProvider(ap.Provider):
    def __init__(self, name, local, ok, text="answer"):
        super().__init__({})
        self.name, self.local, self._ok, self._text = name, local, ok, text
        self.calls = 0

    def available(self):
        return (True, "ready") if self._ok else (False, "not reachable")

    def complete(self, prompt, timeout=ap.DEFAULT_TIMEOUT):
        self.calls += 1
        return {"ok": True, "provider": self.name, "text": self._text, "seconds": 0.1}


def test_a_provider_must_answer_whether_it_can_answer():
    """available() is probed, never assumed - 'anywhere' includes places with no internet."""
    base = ap.Provider({})
    with pytest.raises(NotImplementedError):
        base.available()
    with pytest.raises(NotImplementedError):
        base.complete("hello")


def test_local_is_preferred_over_hosted_when_both_can_answer(monkeypatch):
    local, hosted = FakeProvider("local", True, True), FakeProvider("hosted", False, True)
    monkeypatch.setattr(ap, "providers", lambda config=None: [hosted, local])
    monkeypatch.setattr(ap, "internet_reachable", lambda timeout=3: True)
    assert ap.choose({"prefer_local": True}).name == "local"


def test_offline_forces_local_even_when_local_is_not_preferred(monkeypatch):
    local, hosted = FakeProvider("local", True, True), FakeProvider("hosted", False, True)
    monkeypatch.setattr(ap, "providers", lambda config=None: [hosted, local])
    monkeypatch.setattr(ap, "internet_reachable", lambda timeout=3: False)
    assert ap.choose({"prefer_local": False}).name == "local", "offline must fall back to local"


def test_an_unavailable_provider_is_never_chosen(monkeypatch):
    monkeypatch.setattr(ap, "providers", lambda config=None: [FakeProvider("dead", True, False)])
    monkeypatch.setattr(ap, "internet_reachable", lambda timeout=3: True)
    assert ap.choose({}) is None


def test_no_provider_returns_a_failure_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(ap, "provider_paths", lambda: {"config": tmp_path / "c.json",
                                                       "usage": tmp_path / "u.jsonl"})
    monkeypatch.setattr(ap, "choose", lambda config=None: None)
    out = ap.complete("hello", task="test")
    assert out["ok"] is False and "no provider can answer" in out["error"]


def test_the_verdict_says_plainly_whether_the_machine_is_independent():
    found = [{"name": "claude_cli", "local": False, "available": True, "reason": "ok"},
             {"name": "ollama", "local": True, "available": False, "reason": "no server"}]
    assert "Dependent" in ap._verdict(found, online=True)
    found[1]["available"] = True
    assert "Independent" in ap._verdict(found, online=True)
    for entry in found:
        entry["available"] = False
    assert "Nothing can answer" in ap._verdict(found, online=False)


def test_the_meter_records_the_call_but_never_the_prompt_or_the_answer(tmp_path):
    """A transcript on disk is a leak waiting to happen. This is a meter, not a transcript."""
    path = tmp_path / "usage.jsonl"
    ap.record_usage({"ok": True, "provider": "local", "text": "SECRET ANSWER", "seconds": 1.2},
                    task="a task", path=path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["provider"] == "local" and row["ok"] is True and row["chars_out"] == len("SECRET ANSWER")
    assert "SECRET ANSWER" not in path.read_text(encoding="utf-8")
    assert "text" not in row and "prompt" not in row


def test_usage_summarises_per_provider(tmp_path):
    path = tmp_path / "usage.jsonl"
    for ok in (True, True, False):
        ap.record_usage({"ok": ok, "provider": "local", "text": "x", "seconds": 1.0}, path=path)
    out = ap.usage_summary(path=path)
    assert out["calls"] == 3
    assert out["by_provider"]["local"] == {"calls": 3, "failed": 1, "seconds": 3.0}


def test_config_merges_over_the_defaults_without_losing_providers(tmp_path, monkeypatch):
    path = tmp_path / "ai_providers.json"
    path.write_text(json.dumps({"prefer_local": False,
                                "providers": {"ollama": {"model": "my-model"}}}), encoding="utf-8")
    monkeypatch.setattr(ap, "provider_paths", lambda: {"config": path, "usage": tmp_path / "u.jsonl"})
    config = ap.load_provider_config()
    assert config["prefer_local"] is False
    assert config["providers"]["ollama"]["model"] == "my-model"
    assert "claude_cli" in config["providers"], "overriding one provider must not delete the others"


def test_no_key_in_the_source_and_it_cannot_trade():
    text = open(ap.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5",
                      "sk-ant-", "api_key="):
        assert forbidden not in text
