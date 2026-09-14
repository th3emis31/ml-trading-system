#!/usr/bin/env bash
# Stop: remind about uncommitted work. Never blocks.
set -u; . "$(dirname "$0")/_common.sh"; cd "$(root_dir)" || exit 0
n="$(git status --short 2>/dev/null | grep -v '.claude/backups' | wc -l | tr -d ' ')"
[ "${n:-0}" != "0" ] && echo "[kit] $n uncommitted change(s). Run /verify, then commit on a claude/* branch."
exit 0
