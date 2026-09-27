#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit|NotebookEdit): snapshot the file before it changes.
# Never blocks the edit, and never fails silently either.
set -u; . "$(dirname "$0")/_common.sh"

HOOK_STDIN="$(cat)"; export HOOK_STDIN
file="$(tool_file)"
root="$(root_dir)"

if [ -z "$file" ]; then
  echo "[kit] could not determine which file is being edited — NOT backed up" >&2
  log_hook backup-before-edit "UNKNOWN FILE"
  exit 0
fi
if [ ! -f "$file" ]; then
  log_hook backup-before-edit "new file, nothing to back up: $file"
  exit 0
fi

rel="$(rel_path "$file" "$root")"
dest="$root/.claude/backups/$(date +%Y%m%d-%H%M%S)/$rel"
if mkdir -p "$(dirname "$dest")" 2>/dev/null && cp "$file" "$dest" 2>/dev/null; then
  log_hook backup-before-edit "$rel"
else
  echo "[kit] BACKUP FAILED for $rel — the next edit to it is unrecoverable" >&2
  log_hook backup-before-edit "FAILED $rel"
fi

# Prune by AGE, never by count. Count-based pruning deleted the previous day's
# snapshot during a busy afternoon, which is precisely when it was needed.
# Snapshots holding data, models or anything named like state are never pruned.
find "$root/.claude/backups" -maxdepth 1 -mindepth 1 -type d -mtime +14 2>/dev/null | while read -r dir; do
  if find "$dir" -type f \( -path '*data*' -o -path '*model*' -o -name '*state*' \) 2>/dev/null | grep -q .; then
    continue
  fi
  rm -rf "$dir" 2>/dev/null
done
exit 0
