#!/usr/bin/env bash
# Duplicate-definition guard.
#   As PostToolUse hook: reads tool input JSON, looks at function/class names ADDED to the
#   edited file and reports any that are already defined elsewhere in the same project.
#   Exit 2 sends the report back to Claude so it consolidates instead of duplicating.
#   With --all: prints every name defined in more than one place (used by /dedupe).
#
#   Python: top-level def/class only (nested helpers and methods such as `score` or
#   `status` are not project-level duplicates). Duplicates that already existed when a
#   project was first checked are recorded in a baseline and not reported again, so
#   untracked projects (no git diff) do not repeat the same list on every edit.
set -u
# Resolve the hook folder before any `cd`: a relative $0 stops pointing at it once the
# script moves into the project root (the baseline file could then never be written).
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$HOOK_DIR/_common.sh"
DEF_RE_PY='^(async def|def|class)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)'
DEF_RE_JS='^[[:space:]]*(function|export function|export default function|export class|class)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*)'
EXCL='(^|/)(\.claude|node_modules|dist|build|\.git|__pycache__|\.venv|venv|site-packages|_backups|AppData|ml_trading_system)/'
NAME_SED='s/^[[:space:]]*(async def|export default function|export function|export class|def|class|function)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*).*/\2/'

search_paths(){
  # A project with app.py and src/ (the live app) is searched in its code folders only,
  # not the whole home directory it lives in.
  if [ -f app.py ] && [ -d src ]; then
    local p
    for p in app.py src trading ai voice memory tests jarvis_*.py; do [ -e "$p" ] && printf '%s\n' "$p"; done
  else
    echo .
  fi
}

all_defs(){
  local paths=()
  while IFS= read -r p; do paths+=("$p"); done < <(search_paths)
  { grep -rnE --include='*.py' "$DEF_RE_PY" "${paths[@]}" 2>/dev/null
    grep -rnE --include='*.js' --include='*.jsx' --include='*.ts' --include='*.tsx' "$DEF_RE_JS" "${paths[@]}" 2>/dev/null
  } | grep -vE "$EXCL" \
    | sed -E "s#^\./##; s#^([^:]+):([0-9]+):[[:space:]]*(async def|export default function|export function|export class|def|class|function)[[:space:]]+([A-Za-z_][A-Za-z0-9_]*).*#\4\t\1:\2#"
}

if [ "${1:-}" = "--all" ]; then
  root="$(root_dir)"; cd "$root" || exit 0
  all_defs | sort | awk -F'\t' '{n[$1]++; loc[$1]=loc[$1]" "$2} END{for(k in n) if(n[k]>1) printf "%s (%d)\t%s\n", k, n[k], loc[k]}' | sort
  exit 0
fi

raw="$(cat | tool_file)"; [ -z "$raw" ] && exit 0
file="$(to_posix "$raw")"
case "$file" in *.py|*.js|*.jsx|*.ts|*.tsx) ;; *) exit 0 ;; esac
root="$(project_root_for "$file")"; cd "$root" || exit 0
rel="${file#"$root"/}"
[ -f "$rel" ] || exit 0
case "$rel" in *.py) DEF_RE="$DEF_RE_PY" ;; *) DEF_RE="$DEF_RE_JS" ;; esac

if git ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
  added="$(git diff -U0 -- "$rel" | grep -E '^\+' | grep -vE '^\+\+\+' | sed -E 's/^\+//' | grep -E "$DEF_RE" | sed -E "$NAME_SED" | sort -u)"
else
  added="$(grep -E "$DEF_RE" "$rel" | sed -E "$NAME_SED" | sort -u)"
fi
[ -z "$added" ] && exit 0

defs="$(all_defs)"
baseline="$HOOK_DIR/.dup-baseline-$(printf '%s' "$root" | tr -c 'A-Za-z0-9' '_').txt"
if [ ! -f "$baseline" ]; then
  printf '%s\n' "$defs" | awk -F'\t' 'NF>1 {n[$1]++} END{for(k in n) if(n[k]>1) print k}' | sort > "$baseline"
fi

report=""
for name in $added; do
  case "$name" in main|__init__|setUp|tearDown|test_*|setup|teardown|run|App|index) continue ;; esac
  grep -qxF "$name" "$baseline" 2>/dev/null && continue
  others="$(printf '%s\n' "$defs" | NAME="$name" FILE="$rel" awk -F'\t' '$1==ENVIRON["NAME"] && index($2, ENVIRON["FILE"] ":")!=1 {print $2}')"
  [ -n "$others" ] && report="$report
  $name  is newly added in $rel but already defined at:$(printf '%s\n' "$others" | sed 's/^/ /' | tr '\n' ' ')"
done
if [ -n "$report" ]; then
  echo "DUPLICATE DEFINITION(S) — consolidate before continuing (import/extend the existing one; do not rename to bypass):$report" >&2
  exit 2
fi
exit 0
