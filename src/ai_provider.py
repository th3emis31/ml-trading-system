"""The model layer, behind one interface, so the system is not welded to any single AI.

Everything else this system does - kernel, context, memory, skills, tools, agents, scheduling,
verification - is ordinary software the owner owns outright. The MODEL is the only part that is
rented. So independence is won by putting a hard interface in front of it, not by rebuilding
everything else, and the cheapest time to put that interface in is before more provider-specific
code is written.

Today the only AI caller in the system is ``src/ai_employee.py``, which shells out to the ``claude``
CLI (``run_claude_code``, ai_employee.py:403) and already accepts an injectable ``runner``. That is
the seam this module fits, and nothing here is wired into it yet.

WITH INTERNET, WITHOUT INTERNET. ``available()`` is probed, never assumed, and ``choose()`` prefers a
LOCAL provider when one is reachable - so an offline machine degrades to local work instead of
failing. A provider that cannot answer says so; it never pretends.

WHAT IT DOES NOT DO. It calls nothing on import, holds no API key in code, logs no prompt content and
no secret, and cannot trade. Adding a provider is a config entry plus a class, not an edit to every
caller.

    python -m src.ai_provider status
    python -m src.ai_provider usage
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .runtime_paths import smartentry_data_dir

CONFIG_NAME = "ai_providers.json"
USAGE_NAME = "ai_provider_usage.jsonl"
PROBE_TIMEOUT = 3
DEFAULT_TIMEOUT = 900

# Declared here, overridable from data/ai_providers.json. No key ever lives in this file.
DEFAULT_CONFIG = {
    "prefer_local": True,
    "providers": {
        "claude_cli": {"enabled": True, "kind": "claude_cli", "local": False,
                       "note": "the Claude Code subscription, invoked as a CLI; needs the internet"},
        "ollama": {"enabled": True, "kind": "ollama", "local": True,
                   "url": "http://127.0.0.1:11434", "model": "qwen2.5-coder:7b",
                   "note": "a local model server; works with no internet at all"},
    },
}


def provider_paths() -> dict:
    data = smartentry_data_dir()
    return {"config": data / CONFIG_NAME, "usage": data / USAGE_NAME}


def load_provider_config() -> dict:
    path = provider_paths()["config"]
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in stored.items() if k != "providers"})
    providers = dict(DEFAULT_CONFIG["providers"])
    for name, spec in (stored.get("providers") or {}).items():
        providers[name] = {**providers.get(name, {}), **spec}
    merged["providers"] = providers
    return merged


class Provider:
    """One way of asking a model a question. Implementations answer three things honestly."""

    name = "provider"
    local = False

    def __init__(self, spec: Optional[dict] = None):
        self.spec = spec or {}

    def available(self) -> tuple[bool, str]:
        """(can it answer right now, why not). Probed, never assumed."""
        raise NotImplementedError

    def complete(self, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
        """{ok, text, provider, seconds, error}. Never raises for an expected failure."""
        raise NotImplementedError

    def describe(self) -> dict:
        ok, why = self.available()
        return {"name": self.name, "local": self.local, "available": ok, "reason": why,
                "model": self.spec.get("model"), "note": self.spec.get("note")}


class ClaudeCliProvider(Provider):
    """The Claude Code subscription, invoked exactly as ai_employee.py already invokes it."""

    name = "claude_cli"
    local = False

    def available(self) -> tuple[bool, str]:
        found = shutil.which("claude") or shutil.which("claude.cmd")
        if not found:
            return False, "the claude CLI is not on PATH"
        if not internet_reachable():
            return False, "no internet: this provider is a hosted model"
        return True, f"claude CLI at {found}"

    def complete(self, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
        ok, why = self.available()
        if not ok:
            return {"ok": False, "provider": self.name, "error": why, "text": ""}
        cli = shutil.which("claude") or shutil.which("claude.cmd")
        started = time.monotonic()
        env = {k: v for k, v in os.environ.items()}
        try:
            proc = subprocess.run([cli, "--print"], input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout, env=env,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "provider": self.name, "error": f"timed out after {timeout}s", "text": "",
                    "seconds": round(time.monotonic() - started, 1)}
        except OSError as exc:
            return {"ok": False, "provider": self.name, "error": f"{type(exc).__name__}: {exc}", "text": ""}
        return {"ok": proc.returncode == 0, "provider": self.name, "text": proc.stdout or "",
                "error": (proc.stderr or "").strip()[:400] if proc.returncode else "",
                "seconds": round(time.monotonic() - started, 1)}


class OllamaProvider(Provider):
    """A local model server. The point of this one is that it needs no internet at all."""

    name = "ollama"
    local = True

    def _url(self) -> str:
        return str(self.spec.get("url") or "http://127.0.0.1:11434").rstrip("/")

    def available(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self._url()}/api/tags", timeout=PROBE_TIMEOUT) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, f"no local model server at {self._url()} ({type(exc).__name__})"
        names = [m.get("name") for m in (body.get("models") or [])]
        wanted = self.spec.get("model")
        if wanted and wanted not in names:
            return False, f"server is up but {wanted!r} is not pulled (has {len(names)} model(s))"
        return True, f"{self._url()} with {len(names)} model(s)"

    def complete(self, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
        ok, why = self.available()
        if not ok:
            return {"ok": False, "provider": self.name, "error": why, "text": ""}
        payload = json.dumps({"model": self.spec.get("model"), "prompt": prompt, "stream": False}).encode("utf-8")
        request = urllib.request.Request(f"{self._url()}/api/generate", data=payload,
                                         headers={"Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"ok": False, "provider": self.name, "error": f"{type(exc).__name__}: {exc}", "text": "",
                    "seconds": round(time.monotonic() - started, 1)}
        return {"ok": True, "provider": self.name, "text": body.get("response") or "",
                "seconds": round(time.monotonic() - started, 1)}


KINDS = {"claude_cli": ClaudeCliProvider, "ollama": OllamaProvider}


def internet_reachable(timeout: int = PROBE_TIMEOUT) -> bool:
    """Probed once per call rather than assumed, because 'anywhere' includes trains and aeroplanes."""
    for url in ("https://api.anthropic.com", "https://www.google.com"):
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True
        except urllib.error.HTTPError:
            return True                      # it answered, which is what we are testing
        except (urllib.error.URLError, OSError):
            continue
    return False


def providers(config: Optional[dict] = None) -> list[Provider]:
    config = config or load_provider_config()
    out = []
    for name, spec in (config.get("providers") or {}).items():
        if not spec.get("enabled", True):
            continue
        cls = KINDS.get(spec.get("kind") or name)
        if cls is None:
            continue
        provider = cls(spec)
        provider.name = name
        out.append(provider)
    return out


def provider_status(config: Optional[dict] = None) -> dict:
    config = config or load_provider_config()
    found = [p.describe() for p in providers(config)]
    online = internet_reachable()
    usable = [p for p in found if p["available"]]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "internet": online, "prefer_local": bool(config.get("prefer_local", True)),
        "providers": found, "usable": [p["name"] for p in usable],
        "local_usable": [p["name"] for p in usable if p["local"]],
        "independent": bool([p for p in usable if p["local"]]),
        "verdict": _verdict(found, online),
    }


def _verdict(found: list[dict], online: bool) -> str:
    local_ok = [p for p in found if p["available"] and p["local"]]
    hosted_ok = [p for p in found if p["available"] and not p["local"]]
    if local_ok:
        return (f"Independent: {local_ok[0]['name']} answers locally, so this machine can work "
                f"{'offline' if not online else 'without the subscription'}.")
    if hosted_ok:
        return (f"Dependent: only {hosted_ok[0]['name']} can answer, and it is a hosted model. "
                f"No local provider is reachable, so an offline machine could not work.")
    return "Nothing can answer right now - no local server and no reachable hosted model."


def choose(config: Optional[dict] = None, want_local: Optional[bool] = None) -> Optional[Provider]:
    """The first provider that can actually answer, preferring local when asked or when offline."""
    config = config or load_provider_config()
    usable = [p for p in providers(config) if p.available()[0]]
    if not usable:
        return None
    prefer_local = config.get("prefer_local", True) if want_local is None else want_local
    if prefer_local or not internet_reachable():
        local = [p for p in usable if p.local]
        if local:
            return local[0]
    return usable[0]


def record_usage(result: dict, task: str = "", path: Optional[Path] = None) -> None:
    """A metered ledger: which provider did what, how long it took, whether it worked.

    Prompt and response text are deliberately NOT written - this is a meter, not a transcript, and a
    transcript on disk is a leak waiting to happen.
    """
    row = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "task": task[:80],
           "provider": result.get("provider"), "ok": bool(result.get("ok")),
           "seconds": result.get("seconds"), "chars_out": len(result.get("text") or ""),
           "error": (result.get("error") or "")[:160]}
    target = Path(path or provider_paths()["usage"])
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def usage_summary(path: Optional[Path] = None, limit: int = 500) -> dict:
    target = Path(path or provider_paths()["usage"])
    if not target.exists():
        return {"available": False, "reason": "nothing has used a provider yet", "calls": 0}
    rows = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    by_provider: dict[str, dict] = {}
    for row in rows:
        entry = by_provider.setdefault(row.get("provider") or "?",
                                       {"calls": 0, "failed": 0, "seconds": 0.0})
        entry["calls"] += 1
        entry["failed"] += 0 if row.get("ok") else 1
        entry["seconds"] += float(row.get("seconds") or 0)
    for entry in by_provider.values():
        entry["seconds"] = round(entry["seconds"], 1)
    return {"available": True, "calls": len(rows), "by_provider": by_provider,
            "note": "A meter, not a transcript: no prompt or response text is ever stored."}


def complete(prompt: str, task: str = "", timeout: int = DEFAULT_TIMEOUT,
             config: Optional[dict] = None, chooser: Optional[Callable] = None) -> dict:
    """Ask whichever provider can answer, and meter it. Returns a failure rather than raising."""
    provider = (chooser or choose)(config)
    if provider is None:
        result = {"ok": False, "provider": None, "text": "",
                  "error": "no provider can answer right now (no local server, no reachable hosted model)"}
        record_usage(result, task)
        return result
    result = provider.complete(prompt, timeout=timeout)
    record_usage(result, task)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The model layer: who can answer, and what they have done.")
    parser.add_argument("command", choices=["status", "usage"])
    args = parser.parse_args(argv)

    if args.command == "usage":
        print(json.dumps(usage_summary(), indent=1))
        return 0

    state = provider_status()
    print(f"internet: {'reachable' if state['internet'] else 'NOT reachable'}   "
          f"prefer local: {state['prefer_local']}\n")
    for entry in state["providers"]:
        mark = "OK " if entry["available"] else "-- "
        kind = "local " if entry["local"] else "hosted"
        print(f"  {mark}{entry['name']:12s} {kind}  {entry['reason']}")
    print(f"\n  {state['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
