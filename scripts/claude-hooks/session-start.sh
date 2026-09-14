#!/usr/bin/env bash
# SessionStart: show memory notes and git state. Never fails.
set -u; . "$(dirname "$0")/_common.sh"; cd "$(root_dir)" || exit 0
if [ -f .claude/memory/NOTES.md ]; then
  echo "=== session memory (.claude/memory/NOTES.md, last 40 lines) ==="; tail -n 40 .claude/memory/NOTES.md
fi
if [ -f .claude/memory/LESSONS.md ]; then
  echo "=== lessons (.claude/memory/LESSONS.md) ==="; grep -E "^- " .claude/memory/LESSONS.md | tail -n 15
fi
if [ -f .claude/memory/BACKLOG.md ]; then
  echo "=== open backlog items ==="; grep -n '^- \[ \]' .claude/memory/BACKLOG.md | head -10
fi
echo "=== git ==="; git status --short --branch 2>/dev/null | head -20 || echo "(not a git repo yet — run: git init)"
exit 0
