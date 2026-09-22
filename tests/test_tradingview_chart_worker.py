"""The TradingView chart worker: it must fail loudly, change nothing when unsure, and never trade.

This worker drives a real browser against the owner's real TradingView account, so the things worth
testing are its refusals, not its happy path. Every test here is about what it does NOT do.
"""
import ast
import json

import pytest

from src import tradingview_chart_worker as w


def _code_only(path: str) -> str:
    """The module's source with every docstring and comment stripped out.

    A safety test has to read what the code does, not what its documentation claims it does.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_a_cycle_without_the_app_reports_why_instead_of_crashing(tmp_path, monkeypatch):
    """The scheduler calls this hourly. An unreachable app is a normal condition, not a traceback."""
    monkeypatch.setattr(w, "STATE_PATH", tmp_path / "chart_worker.json")
    out = w.run_worker_cycle(app="http://127.0.0.1:9")          # nothing listens there
    assert out["ok"] is False
    assert "app" in out["reason"].lower() or "indicator" in out["reason"].lower()
    assert out["places_orders"] is False
    assert json.loads((tmp_path / "chart_worker.json").read_text())["ok"] is False


def test_a_served_page_that_is_not_pine_is_refused(monkeypatch):
    """A login page or an error page returns 200 too. Saving that over the owner's indicator would
    replace a working script with HTML, so the content is checked, not the status code."""
    class FakeResponse:
        def read(self): return b"<html>please sign in</html>"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(w.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert w.fetch_script() is None


def test_the_worker_refuses_a_script_it_does_not_own():
    """The worst thing this worker could do is overwrite a script the owner wrote by hand, so an
    open editor showing any other script must abort with nothing changed."""
    class FakeLocator:
        def __init__(self, text="Someone else's strategy"): self._text = text
        def click(self, **k): pass
        def inner_text(self, **k): return self._text
        def count(self): return 1
        @property
        def first(self): return self

    class FakePage:
        def locator(self, sel):
            return FakeLocator("My own private script" if "Title" in sel or "title" in sel else "editor")
        def wait_for_timeout(self, ms): pass

    out = w._save_script(FakePage(), "//@version=5\nindicator('x')")
    assert out["ok"] is False and out["changed"] is False
    assert "changed nothing" in out["reason"]


def test_state_reads_cleanly_before_the_worker_has_ever_run(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "STATE_PATH", tmp_path / "missing.json")
    assert w.read_worker_state()["available"] is False


def test_the_worker_attaches_and_never_closes_the_owners_browser():
    """The worker runs inside the owner's own Edge, so the danger is no longer a stray profile - it
    is disturbing a browser they are using. It must open its own tab and close only that tab.

    This replaced a test asserting the opposite. The first build used a separate Chromium with no
    TradingView session, which asked them to sign in twice; they said "using wrong broweser", and
    their browser is Edge. The guard has to follow the design, not outlive it.
    """
    code = _code_only(w.__file__)
    assert "ctx.new_page()" in code, "must open its own tab rather than reuse one of theirs"
    assert "page.close()" in code, "must close the tab it opened"
    assert "ctx.close()" not in code, "closing the context would take the owner's browser with it"
    assert "9222" in w.CDP_URL


def test_an_unreachable_browser_is_its_own_reason(tmp_path, monkeypatch):
    """'Changed nothing' and 'could not reach the browser' are different problems with different
    fixes, and collapsing them would hide a worker that is simply dead."""
    monkeypatch.setattr(w, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(w, "fetch_script", lambda *a, **k: "//@version=5\nindicator('x')")
    monkeypatch.setattr(w, "_browser_reachable", lambda: False)
    out = w.run_worker_cycle()
    assert out["ok"] is False and "debug port" in out["reason"]


def test_no_executable_line_can_reach_an_order():
    """Scanned on the code with its prose removed. The first version of this test read the whole file
    and failed on the docstring that PROMISES not to open the Trade panel - a promise in a comment is
    exactly what a safety test must not accept as evidence."""
    code = _code_only(w.__file__)
    for forbidden in ("Trade panel", "placeOrder", "order_send", "order_check", "Buy", "Sell"):
        assert forbidden not in code, f"executable code mentions {forbidden!r}"


def test_playwright_absence_is_a_status_not_an_import_error(tmp_path, monkeypatch):
    """Optional integrations degrade rather than fail, per the project's own architecture rule."""
    monkeypatch.setattr(w, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(w, "_playwright", lambda: None)
    monkeypatch.setattr(w, "fetch_script", lambda *a, **k: "//@version=5\nindicator('x')")
    out = w.run_worker_cycle()
    assert out["ok"] is False and "Playwright" in out["reason"]


def test_the_worker_targets_the_script_that_is_actually_on_the_chart():
    """The first version aimed at 'SmartEntry Daily Plan', which is not on the owner's chart - the
    indicator there is the market map. Every cycle would have refused and changed nothing, for ever,
    while reporting a tidy reason. A worker that can never succeed is worse than no worker."""
    from src import tradingview_plan as tp
    assert w.SCRIPT_KEY in tp.PINE_SCRIPTS
    assert tp.PINE_SCRIPTS[w.SCRIPT_KEY][0] == w.SCRIPT_NAME
    assert tp.PINE_SCRIPTS[w.SCRIPT_KEY][1].exists()


def test_both_indicators_accept_the_board():
    """The injection used to anchor on the daily plan's input label. The map calls its input something
    else, so anchoring on a label would have silently left the map's board empty."""
    from src import tradingview_plan as tp
    board = {"strategies": [{"name": "VTB", "available": True, "sending_orders": True,
                             "waiting_for": "no breakout"}]}
    for key, (name, path) in tp.PINE_SCRIPTS.items():
        text = tp.pine_script_text(path=path, board=board)
        line = [l for l in text.splitlines() if l.startswith("boardText = input.string(")]
        assert line and "VTB :: SENDING" in line[0], f"{key} did not receive the board"
