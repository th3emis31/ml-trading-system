#!/usr/bin/env bash
# Duplicate-definition guard.
#   As PostToolUse hook: reads tool input JSON, looks at function/class names ADDED to the
#   edited file (git diff) and reports any that are already defined elsewhere. Exit 2 sends
#   the report back to Claude so it consolidates instead of duplicating.
#   With --all: prints every name defined in more than one place (used by /dedupe).
set -u; . "$(dirname "$0")/_common.sh"
root="$(root_dir)"; cd "$root" || exit 0
# Anchored at column zero. Indented definitions are class methods, and two classes
# each having close() or update() is ordinary code, not duplication. Matching those
# made the hook block routine edits, which teaches everyone to ignore hook output.
DEF_RE='^(def|class|async def|function|export function|export default function|export class)\s+([A-Za-z_][A-Za-z0-9_]*)'
EXCL='(^|/)(\.claude|node_modules|dist|build|\.git|__pycache__|\.venv|venv|site-packages)/'
all_defs(){ grep -rnE --include='*.py' --include='*.js' --include='*.jsx' --include='*.ts' --include='*.tsx' "$DEF_RE" . 2>/dev/null \
  | grep -vE "$EXCL" | sed -E "s#^\./##; s#^([^:]+):([0-9]+):\s*(async def|export default function|export function|export class|def|class|function)\s+([A-Za-z_][A-Za-z0-9_]*).*#\4\t\1:\2#"; }
if [ "${1:-}" = "--all" ]; then
  all_defs | sort | awk -F'\t' '{n[$1]++; loc[$1]=loc[$1]" "$2} END{for(k in n) if(n[k]>1) printf "%s (%d)\t%s\n", k, n[k], loc[k]}' | sort
  exit 0
fi
HOOK_STDIN="$(cat)"; export HOOK_STDIN
file="$(tool_file)"; [ -z "$file" ] && { log_hook dup-check "UNKNOWN FILE"; exit 0; }
case "$file" in *.py|*.js|*.jsx|*.ts|*.tsx) ;; *) exit 0 ;; esac
rel="$(rel_path "$file" "$root")"
[ -f "$rel" ] || exit 0
# names newly added in this file (unstaged diff vs HEAD; untracked file => all its defs)
if git ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
  added="$(git diff -U0 -- "$rel" | grep -E '^\+' | grep -vE '^\+\+\+' | sed -E 's/^\+//' | grep -E "$DEF_RE" | sed -E 's/^\s*(async def|export default function|export function|export class|def|class|function)\s+([A-Za-z_][A-Za-z0-9_]*).*/\2/' | sort -u)"
else
  added="$(grep -E "$DEF_RE" "$rel" | sed -E 's/^\s*(async def|export default function|export function|export class|def|class|function)\s+([A-Za-z_][A-Za-z0-9_]*).*/\2/' | sort -u)"
fi
[ -z "$added" ] && exit 0
report=""
defs="$(all_defs)"
for name in $added; do
  case "$name" in main|__init__|setUp|tearDown|test_*|setup|teardown|run|App|index) continue;; esac
  others="$(printf '%s\n' "$defs" | awk -F'\t' -v n="$name" -v f="$rel" '$1==n && index($2, f":")!=1 {print $2}')"
  [ -n "$others" ] && report="$report
  $name  is newly added in $rel but already defined at:$(printf '%s\n' "$others" | sed 's/^/ /' | tr '\n' ' ')"
done
if [ -n "$report" ]; then
  echo "DUPLICATE DEFINITION(S) — consolidate before continuing (import/extend the existing one; do not rename to bypass):$report" >&2
  log_hook dup-check "blocked: $rel"
  exit 2
fi
log_hook dup-check "ok: $rel"
exit 0
