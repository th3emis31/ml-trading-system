#!/usr/bin/env python3
"""One command that tells you whether this project is safe to work in.

    python scripts/doctor.py            # check everything
    python scripts/doctor.py --quiet    # only problems
    python scripts/doctor.py --json     # machine readable

Checks the things that have actually gone wrong here: a session started in the
wrong folder, hooks that silently do not run, memory that has stopped being
written, schedules that are disabled, state files that are empty or corrupt,
secrets in tracked files, and a live server reachable from the network without
authentication.

Read only. It opens files and asks the operating system questions. It never
writes, edits, deletes or restarts anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

FAIL, WARN, OK, INFO = "FAIL", "WARN", "OK", "INFO"
RESULTS = []


def add(status, area, message, fix=""):
    RESULTS.append({"status": status, "area": area, "message": message, "fix": fix})


def root_dir() -> Path:
    here = Path.cwd().resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "CLAUDE.md").exists() or (candidate / ".claude").is_dir():
            return candidate
    return here


def run(cmd, timeout=15):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False)
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return 1, ""


# --- 1. where am I -----------------------------------------------------------

def check_location(root: Path):
    here = Path.cwd().resolve()
    if here != root:
        add(FAIL, "location",
            "Running in %s but the project root is %s. Started this way, Claude loads no "
            "CLAUDE.md, no skills and no hooks, and relative paths write into the wrong folder."
            % (here, root),
            'cd "%s" then start again' % root)
    else:
        add(OK, "location", "project root is the working directory: %s" % root)


# --- 2. is the wiring actually loaded ---------------------------------------

def check_wiring(root: Path):
    for rel, why in (("CLAUDE.md", "project memory"),
                     (".claude/settings.json", "hooks and permissions"),
                     (".claude/skills", "slash commands"),
                     (".claude/memory", "notes, lessons and baseline")):
        path = root / rel
        if path.exists():
            extra = ""
            if rel == ".claude/skills":
                names = sorted(p.name for p in path.iterdir() if p.is_dir())
                extra = ": " + ", ".join(names) if names else " (empty)"
                if not names:
                    add(WARN, "wiring", "%s exists but has no skills in it" % rel, "run the kit installer")
                    continue
            add(OK, "wiring", "%s present (%s)%s" % (rel, why, extra))
        else:
            add(FAIL, "wiring", "%s is missing, so %s is not active" % (rel, why),
                "run the kit installer from the project root")


# --- 3. do the hooks point at files that exist, and can they run -------------

def check_hooks(root: Path):
    settings = root / ".claude/settings.json"
    if not settings.exists():
        return
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        add(FAIL, "hooks", "settings.json is not valid JSON (%s), so every hook is off" % exc.msg,
            "fix the JSON syntax")
        return

    referenced = set(re.findall(r'[\w./\\-]+\.(?:sh|py|ps1|cmd)', json.dumps(data)))
    if not referenced:
        add(WARN, "hooks", "settings.json names no hook scripts at all",
            "without hooks nothing backs up files before edits")
        return

    for ref in sorted(referenced):
        target = (root / ref.replace("\\", "/")).resolve()
        if target.exists():
            add(OK, "hooks", "%s exists" % ref)
        else:
            add(FAIL, "hooks", "settings.json refers to %s which does not exist, so that hook "
                               "silently never runs" % ref,
                "copy the script into place or correct the path in settings.json")

    if any(r.endswith(".sh") for r in referenced) and os.name == "nt":
        if shutil.which("bash"):
            add(OK, "hooks", "bash is on PATH, so the .sh hooks can run on Windows")
        else:
            add(FAIL, "hooks", "the hooks are .sh scripts but bash is not on PATH, so none of them run",
                "install Git for Windows, or point the hooks at .cmd equivalents")


# --- 4. is the brain still being written to ---------------------------------

def check_memory(root: Path):
    mem = root / ".claude/memory"
    if not mem.is_dir():
        return
    now = dt.datetime.now()
    for name, stale_days in (("NOTES.md", 7), ("BASELINE.md", 30), ("LESSONS.md", 60)):
        path = mem / name
        if not path.exists():
            add(WARN, "brain", "%s does not exist" % name, "create it; memory that is not written is not memory")
            continue
        age = (now - dt.datetime.fromtimestamp(path.stat().st_mtime)).days
        lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if age > stale_days:
            add(WARN, "brain", "%s has not been updated in %d days (%d lines)" % (name, age, lines),
                "if work happened since, it was not recorded")
        else:
            add(OK, "brain", "%s updated %d days ago, %d lines" % (name, age, lines))


# --- 5. the second brain, if one is installed -------------------------------

def check_second_brain(root: Path):
    home = Path.home()
    candidates = [home / ".claude-mem", home / ".claude" / "claude-mem"]
    found = [c for c in candidates if c.exists()]
    if not found:
        add(INFO, "second brain", "no claude-mem install found; the file memory above is the only brain")
        return
    store = found[0]
    newest = None
    for path in store.rglob("*"):
        if path.is_file():
            ts = path.stat().st_mtime
            newest = ts if newest is None or ts > newest else newest
    if newest is None:
        add(WARN, "second brain", "claude-mem is installed but its store is empty",
            "nothing is being remembered")
        return
    age = (dt.datetime.now() - dt.datetime.fromtimestamp(newest)).days
    if age > 2:
        add(WARN, "second brain", "claude-mem last wrote %d days ago. It stops silently when its "
                                  "provider allowance runs out." % age,
            "check its status; until it resumes, write to .claude/memory by hand")
    else:
        add(OK, "second brain", "claude-mem wrote %d days ago" % age)


# --- 6. scheduled tasks ------------------------------------------------------

def check_schedules(root: Path):
    if os.name != "nt":
        add(INFO, "schedules", "not Windows; no scheduled task check")
        return
    code, out = run(["schtasks", "/query", "/fo", "csv", "/v"], timeout=45)
    if code != 0 or not out.strip():
        add(WARN, "schedules", "could not read the Windows task list", "run as the same user that created them")
        return
    marker = root.name.lower()
    rows = [line for line in out.splitlines() if marker in line.lower()]
    if not rows:
        add(INFO, "schedules", "no scheduled tasks mention %s" % root.name)
        return
    disabled = [r for r in rows if "disabled" in r.lower()]
    add(OK if not disabled else WARN, "schedules",
        "%d scheduled tasks reference this project, %d disabled" % (len(rows), len(disabled)),
        "a disabled task is not running, whatever it is named" if disabled else "")


# --- 7. data files that are empty, corrupt, or duplicated --------------------

def check_data(root: Path):
    data = root / "data"
    if not data.is_dir():
        add(INFO, "data", "no data/ directory in the project")
    else:
        empty, corrupt, checked = [], [], 0
        for path in list(data.rglob("*.json"))[:400]:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            checked += 1
            if not raw.strip():
                empty.append(path)
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                corrupt.append(path)
                continue
            if isinstance(obj, dict):
                for key in ("trades", "history", "records"):
                    if isinstance(obj.get(key), list) and not obj[key] and path.stat().st_size > 10_000:
                        add(WARN, "data", "%s is %d bytes but its '%s' list is empty"
                            % (path.relative_to(root), path.stat().st_size, key),
                            "check whether history was dropped rather than archived")
        add(OK if not (empty or corrupt) else FAIL, "data",
            "%d json files checked, %d empty, %d corrupt" % (checked, len(empty), len(corrupt)),
            "; ".join(str(p.relative_to(root)) for p in (empty + corrupt)[:5]))

    stray = Path.home() / "data"
    if stray.is_dir() and stray != data:
        add(WARN, "data", "a stray %s exists in the home folder" % stray,
            "created by running the app from the wrong directory; check which one the app actually writes")


# --- 8. secrets in tracked files --------------------------------------------

SECRET_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "Anthropic API key"),
    (r"\b\d{9,10}:[A-Za-z0-9_\-]{30,}\b", "Telegram bot token"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]([^'\"]{12,})['\"]", "hard-coded credential"),
]

PLACEHOLDER_WORDS = re.compile(
    r"(?i)placeholder|example|changeme|set-?me|dummy|fake|sample|redacted|todo|"
    r"pick-a|random-string|insert|replace-?me|xxx+|^your[_\- ]|^my[_\- ]|^test[_\- ]")


def looks_like_a_real_secret(value: str) -> bool:
    """Decide whether a captured value is a credential or somebody's placeholder.

    A scanner that cries wolf gets ignored, which is worse than not having one. Both
    findings on this repository's first run were placeholders: YOUR_BOT_TOKEN and
    'pick-a-long-random-string'. So a value has to look like machine-generated
    material, not like an instruction to the reader.
    """
    if PLACEHOLDER_WORDS.search(value):
        return False
    if re.fullmatch(r"[A-Z0-9_]+", value):
        return False                       # SHOUTING_CONSTANT, not a key
    if re.fullmatch(r"[a-z\- ]+", value):
        return False                       # words-with-hyphens, not a key
    classes = sum(bool(re.search(cls, value)) for cls in (r"[a-z]", r"[A-Z]", r"\d"))
    return len(value) >= 16 and classes >= 2


def check_secrets(root: Path):
    code, out = run(["git", "ls-files"], timeout=30)
    if code != 0:
        add(INFO, "secrets", "not a git repository, so tracked files could not be listed")
        return
    files = [root / f for f in out.splitlines() if f.strip()]
    hits = []
    for path in files[:3000]:
        if path.suffix.lower() in {".png", ".jpg", ".zip", ".joblib", ".pyc", ".gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        if len(text) > 2_000_000:
            continue
        for pattern, label in SECRET_PATTERNS:
            for m in re.finditer(pattern, text):
                value = m.group(1) if m.groups() else m.group(0)
                if not looks_like_a_real_secret(value):
                    continue
                line = text[:m.start()].count("\n") + 1
                hits.append("%s:%d %s" % (path.relative_to(root), line, label))
                break
    if hits:
        add(FAIL, "secrets", "%d tracked files look like they contain credentials" % len(hits),
            "; ".join(sorted(set(hits))[:5]))
    else:
        add(OK, "secrets", "no credentials found in tracked files")


# --- 9. is anything listening, and to whom ----------------------------------

def check_server(root: Path, ports=(5000, 5001, 5002, 8787)):
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            local = sock.connect_ex(("127.0.0.1", port)) == 0
        if not local:
            continue
        exposed = False
        try:
            host_ip = socket.gethostbyname(socket.gethostname())
            if host_ip and not host_ip.startswith("127."):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock2:
                    sock2.settimeout(0.4)
                    exposed = sock2.connect_ex((host_ip, port)) == 0
        except OSError:
            pass
        if exposed:
            add(FAIL, "exposure", "port %d answers on the network address, not just localhost. "
                                  "Anything it serves is reachable by other devices." % port,
                "bind to 127.0.0.1 and reach it through a tunnel, or require a secret on every write")
        else:
            add(OK, "exposure", "port %d is listening on localhost only" % port)


# --- 10. git -----------------------------------------------------------------

def check_git(root: Path):
    code, out = run(["git", "status", "--short", "--branch"], timeout=20)
    if code != 0:
        add(INFO, "git", "not a git repository")
        return
    lines = out.splitlines()
    branch = lines[0] if lines else ""
    dirty = [l for l in lines[1:] if l.strip()]
    add(WARN if len(dirty) > 20 else OK, "git",
        "%s, %d uncommitted changes" % (branch.replace("## ", ""), len(dirty)),
        "a large uncommitted diff is work that a crash would lose" if len(dirty) > 20 else "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quiet", action="store_true", help="show only warnings and failures")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--skip-secrets", action="store_true", help="skip the credential scan (slow on big repos)")
    args = ap.parse_args(argv)

    root = root_dir()
    check_location(root)
    check_wiring(root)
    check_hooks(root)
    check_memory(root)
    check_second_brain(root)
    check_schedules(root)
    check_data(root)
    if not args.skip_secrets:
        check_secrets(root)
    check_server(root)
    check_git(root)

    if args.json:
        print(json.dumps({"root": str(root), "results": RESULTS}, indent=2))
    else:
        order = {FAIL: 0, WARN: 1, OK: 2, INFO: 3}
        rows = sorted(RESULTS, key=lambda r: order.get(r["status"], 9))
        width = max(len(r["area"]) for r in rows)
        print("project doctor — %s\n" % root)
        for r in rows:
            if args.quiet and r["status"] in (OK, INFO):
                continue
            print("  %-4s %-*s  %s" % (r["status"], width, r["area"], r["message"]))
            if r["fix"]:
                print("       %-*s  -> %s" % (width, "", r["fix"]))
        fails = sum(1 for r in RESULTS if r["status"] == FAIL)
        warns = sum(1 for r in RESULTS if r["status"] == WARN)
        print("\n  %d failures, %d warnings, %d checks total" % (fails, warns, len(RESULTS)))
        if fails:
            print("  Do not trust results from this project until the failures are fixed.")
    return 1 if any(r["status"] == FAIL for r in RESULTS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
