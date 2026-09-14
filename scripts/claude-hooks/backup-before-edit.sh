#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit): snapshot the file before it changes. Never blocks.
set -u; . "$(dirname "$0")/_common.sh"
file="$(cat | tool_file)"; [ -z "$file" ] && exit 0; [ -f "$file" ] || exit 0
root="$(root_dir)"; case "$file" in "$root"/*) rel="${file#$root/}";; *) rel="$(basename "$file")";; esac
dest="$root/.claude/backups/$(date +%Y%m%d-%H%M%S)/$rel"
mkdir -p "$(dirname "$dest")" && cp "$file" "$dest" 2>/dev/null
ls -1dt "$root"/.claude/backups/*/ 2>/dev/null | tail -n +31 | xargs -r rm -rf
exit 0
