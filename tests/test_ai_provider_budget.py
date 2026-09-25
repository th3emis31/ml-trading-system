"""Layer 0 must never truncate, never invent, and never hide which model answered.

Build map step 1 (docs/I40_PILOT_ARCHITECTURE.md). The owner's constraint is that the system runs
offline on a small local model, which reasons worse than the hosted one. That only stays honest if a
caller can tell the two apart, and if an oversized prompt fails loudly instead of being cut down.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import ai_provider as ap


class _Fake(ap.Provider):
    def __init__(self, name, local, ok=True, text="answer", error="", context=8192):
        super().__init__({"context_tokens": context})
        self.name, self.local, self._ok, self._text, self._error = name, local, ok, text, error

    def available(self):
        return True, "test double"

    def complete(self, prompt, timeout=ap.DEFAULT_TIMEOUT):
        oversized = self.too_long(prompt)
        if oversized:
            return oversized
        return {"ok": self._ok, "provider": self.name, "text": self._text if self._ok else "",
                "error": self._error}


def _cfg(*names):
    return {"prefer_local": True,
            "providers": {n: {"enabled": True, "kind": n} for n in names}}


def test_an_oversized_prompt_is_refused_with_numbers_not_truncated():
    """A silently shortened prompt asks a different question than the caller believes it asked."""
    p = _Fake("local_small", True, context=1000)
    out = p.complete("x" * (4 * 5000))          # ~5000 tokens into a 1000-token window
    assert out["ok"] is False
    assert "Refused rather than truncated" in out["error"]
    assert out["prompt_tokens"] > out["context_tokens"]


def test_a_prompt_that_fits_is_not_refused():
    assert _Fake("local_small", True, context=1000).complete("x" * 400)["ok"] is True


def test_context_window_comes_from_the_spec_and_is_reported():
    assert ap.ClaudeCliProvider({"context_tokens": 180000}).context_tokens() == 180000
    assert "context_tokens" in ap.OllamaProvider({"context_tokens": 4096}).describe()


def test_the_default_window_is_small_on_purpose():
    """Guessing high would let an oversized prompt through to be truncated by the model itself."""
    assert ap.Provider().context_tokens() <= 8192


def test_a_local_answer_is_marked_degraded_when_a_hosted_one_was_available(monkeypatch):
    hosted, local = _Fake("hosted", False), _Fake("local", True)
    monkeypatch.setattr(ap, "providers", lambda config=None: [hosted, local])
    monkeypatch.setattr(ap, "record_usage", lambda *a, **k: None)
    out = ap.complete("hi", config=_cfg("hosted", "local"), chooser=lambda c: local)
    assert out["ok"] is True and out["provider"] == "local"
    assert out["degraded"] is True, "a 7B answering while the hosted model was up must say so"
    assert "lower confidence" in out["degraded_reason"]


def test_the_best_provider_answering_is_not_degraded(monkeypatch):
    hosted, local = _Fake("hosted", False), _Fake("local", True)
    monkeypatch.setattr(ap, "providers", lambda config=None: [hosted, local])
    monkeypatch.setattr(ap, "record_usage", lambda *a, **k: None)
    out = ap.complete("hi", config=_cfg("hosted", "local"), chooser=lambda c: hosted)
    assert out["ok"] is True and out["degraded"] is False


def test_it_falls_back_when_the_chosen_provider_fails_mid_call(monkeypatch):
    """available() passing does not mean the call will work - a server can die between the two."""
    broken, local = _Fake("hosted", False, ok=False, error="server died"), _Fake("local", True)
    monkeypatch.setattr(ap, "providers", lambda config=None: [broken, local])
    monkeypatch.setattr(ap, "record_usage", lambda *a, **k: None)
    out = ap.complete("hi", config=_cfg("hosted", "local"), chooser=lambda c: broken)
    assert out["ok"] is True and out["provider"] == "local"
    assert out["degraded"] is True
    assert out["attempts"][0]["provider"] == "hosted" and "server died" in out["attempts"][0]["error"]


def test_with_nothing_reachable_it_returns_a_failure_and_never_text(monkeypatch):
    """The rule this codebase already has: never invent data to fill a gap."""
    monkeypatch.setattr(ap, "providers", lambda config=None: [])
    monkeypatch.setattr(ap, "record_usage", lambda *a, **k: None)
    out = ap.complete("hi", config=_cfg(), chooser=lambda c: None)
    assert out["ok"] is False and out["text"] == ""
    assert "no provider can answer" in out["error"]


def test_every_provider_failing_reports_each_reason(monkeypatch):
    a = _Fake("hosted", False, ok=False, error="timeout")
    b = _Fake("local", True, ok=False, error="no model pulled")
    monkeypatch.setattr(ap, "providers", lambda config=None: [a, b])
    monkeypatch.setattr(ap, "record_usage", lambda *a, **k: None)
    out = ap.complete("hi", config=_cfg("hosted", "local"), chooser=lambda c: a)
    assert out["ok"] is False and out["text"] == ""
    assert "timeout" in out["error"] and "no model pulled" in out["error"]


def test_the_usage_meter_never_stores_prompt_or_response_text(tmp_path):
    path = tmp_path / "usage.jsonl"
    ap.record_usage({"ok": True, "provider": "local", "text": "SECRET ANSWER", "seconds": 1.0},
                    task="t", path=path)
    written = path.read_text(encoding="utf-8")
    assert "SECRET ANSWER" not in written and "chars_out" in written
