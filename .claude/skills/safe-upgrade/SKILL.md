---
name: safe-upgrade
description: Make an additive, zero-error improvement to this project. Use for any feature, fix, or refactor request. Never deletes; backs up, edits minimally, runs checks, verifies, commits.
---

# Safe upgrade protocol

Owner's standing rule: **always improve, always safe, nothing deleted, no errors.**

1. **Understand** — read `CLAUDE.md` and the code you will touch. Restate the change in one
   sentence and list the functions/files involved. If the request would remove a feature,
   file, function, or config key: keep it and add the new behaviour beside it (flag or new
   branch of logic).
2. **Back up** — the PreToolUse hook snapshots every edited file into `.claude/backups/`.
   Make sure `git status` is clean or committed before large edits.
3. **Edit minimally** — new pure functions next to existing helpers; new fields optional
   with defaults; keep the project's existing style.
4. **Check** — the PostToolUse hook compiles/builds after each edit and returns failures to
   you. Fix them before anything else. Then run the project's `check`/`test` command from
   `CLAUDE.md`.
5. **Verify** — run `/verify`. `git diff --stat` must show only intended files.
6. **Record and commit** — append a dated line to `.claude/memory/NOTES.md`; commit on a
   `claude/*` branch with an imperative message. Never push to `main`; never force-push.

## Acceptance

```acceptance
run: python -m compileall -q .
run: python -m pytest -q
run: bash scripts/claude-hooks/dup-check.sh
ask: was anything removed, and if so was it replaced rather than dropped?
```
