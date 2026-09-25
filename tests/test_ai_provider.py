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
    proven = {"proven": True, "provider": "ollama", "at": "2026-09-25 16:24:22", "why": "answered"}
    assert "Dependent" in ap._verdict(found, online=True, proven=proven)
    found[1]["available"] = True
    assert "Independent" in ap._verdict(found, online=True, proven=proven)
    for entry in found:
        entry["available"] = False
    assert "Nothing can answer" in ap._verdict(found, online=False, proven=proven)


def test_a_reachable_local_model_that_has_never_answered_is_not_independence():
    """The false claim this guards, from 25 Sep 2026: the server was up and listed its model, so the
    probe passed and the build map said "Without internet: Working now" - while every local request
    died with std::bad_alloc because a 7.6B model cannot load in 0.8 GB of free RAM. Listing a model
    proves the file is on disk, not that it can run."""
    found = [{"name": "ollama", "local": True, "available": True, "reason": "up, 1 model"}]
    unproven = {"proven": False, "provider": None, "at": None, "why": "has never returned an answer"}
    verdict = ap._verdict(found, online=True, proven=unproven)
    assert "NOT yet independent" in verdict
    assert "RAM" in verdict, "it must point at the cause that actually produced this"


def test_local_proven_reads_the_ledger_and_ignores_the_hosted_provider(tmp_path):
    """Only a LOCAL success counts. A hosted answer proves the internet works, not independence."""
    ledger = tmp_path / "usage.jsonl"

    def append(provider, ok, at):
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": at, "provider": provider, "ok": ok}) + chr(10))

    append("claude_cli", True, "2026-09-25 10:00:00")   # hosted: proves the internet, not this
    append("ollama", False, "2026-09-25 10:01:00")      # local but FAILED: still not proof
    assert ap.local_proven(path=ledger)["proven"] is False
    append("ollama", True, "2026-09-25 10:02:00")       # local AND worked: now it is proven
    out = ap.local_proven(path=ledger)
    assert out["proven"] is True and out["provider"] == "ollama"
    assert out["at"] == "2026-09-25 10:02:00", "it must report WHEN, so a stale proof looks stale"


def test_local_proven_is_false_when_nothing_has_ever_run(tmp_path):
    assert ap.local_proven(path=tmp_path / "missing.jsonl")["proven"] is False


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


def test_the_local_model_does_not_sit_in_ram_after_answering():
    """Measured 25 September 2026: 7.5 GB total, 0.9 GB free, 10.9 GB already paged to disk. Ollama's
    default keep_alive of 5 minutes leaves ~2 GB of that resident long after the answer came back, which
    on this machine is the difference between the next thing loading and not loading."""
    sent = {}

    class FakeResponse:
        def read(self):
            return b'{"response": "ok"}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(request, timeout=None):
        if getattr(request, "data", None):
            sent.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse()

    provider = ap.OllamaProvider({"model": "granite4:micro-h", "url": "http://127.0.0.1:11434"})
    original = ap.urllib.request.urlopen
    ap.urllib.request.urlopen = fake_urlopen
    try:
        provider.available = lambda: (True, "stubbed")
        out = provider.complete("hello")
    finally:
        ap.urllib.request.urlopen = original

    assert out["ok"] is True
    assert sent.get("keep_alive") == "60s", f"keep_alive must be sent, got {sent.get('keep_alive')!r}"


def test_keep_alive_can_be_overridden_per_machine():
    """A machine with plenty of RAM should be able to keep the model warm for longer."""
    provider = ap.OllamaProvider({"model": "m", "keep_alive": "30m"})
    assert provider.spec["keep_alive"] == "30m"
    assert ap.DEFAULT_CONFIG["providers"]["ollama"]["keep_alive"] == "60s"
