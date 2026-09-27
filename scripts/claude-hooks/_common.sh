# shared helpers for hooks (sourced)
#
# Two rules learned the hard way:
#   * Windows hands us C:\a\b while git hands back C:/a/b, so every path is
#     normalised to forward slashes before anything is compared. Without this the
#     project-relative path never matched, so backups landed flat and the
#     duplicate checker treated every tracked file as brand new.
#   * A hook that cannot tell what it was given says so on stderr. Passing quietly
#     is how a file gets edited with no backup and nobody notices.

norm(){ printf '%s' "$1" | tr '\\' '/'; }

# The project root is the kit's own location, not wherever git happens to think.
# A home directory that is not a repository used to resolve to itself, which made
# the wrong-directory check pass in exactly the case it existed for.
root_dir(){
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd -P)" || here="$(pwd -P)"
  norm "$here"
}

tool_file(){
  local raw=""
  raw="$(printf '%s' "${HOOK_STDIN:-}" | node -e '
let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const j=JSON.parse(s);
process.stdout.write((j.tool_input&&(j.tool_input.file_path||j.tool_input.path))||"")}catch(e){}})' 2>/dev/null)"
  if [ -z "$raw" ]; then
    raw="$(printf '%s' "${HOOK_STDIN:-}" | $(py) -c 'import sys,json
try:
  j=json.load(sys.stdin); print((j.get("tool_input") or {}).get("file_path") or (j.get("tool_input") or {}).get("path") or "", end="")
except Exception: pass' 2>/dev/null)"
  fi
  norm "$raw"
}

# Project-relative path, compared case-insensitively so a drive letter cannot
# defeat the match on Windows.
# `C:/Users/x` and `/c/Users/x` name the same file and compared UNEQUAL, because root_dir() reports
# the MSYS form while the tool input carries the Windows one. rel_path therefore fell through to its
# absolute branch, and dup-check's self-exclusion - which tests whether a match starts with the file's
# own relative path - matched nothing. Every new Python file was reported as duplicating itself, on
# every definition it contained. A guard that fires on correct work is worse than no guard: it is the
# reason hook output stops being read.
msys(){
  case "$1" in
    [A-Za-z]:/*) printf '/%s/%s' "$(printf '%s' "${1%%:*}" | tr 'A-Z' 'a-z')" "${1#*:/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

rel_path(){
  local file root lf lr
  file="$(msys "$(norm "$1")")"; root="$(msys "$(norm "$2")")"
  lf="$(printf '%s' "$file" | tr 'A-Z' 'a-z')"
  lr="$(printf '%s' "$root" | tr 'A-Z' 'a-z')"
  case "$lf" in
    "$lr"/*) printf '%s' "${file:$((${#root}+1))}" ;;
    *) printf '%s' "$file" ;;
  esac
}

# The candidate must actually RUN, not merely be on PATH. Windows ships a `python3`
# App Execution Alias that exists, exits non-zero and prints "Python was not found",
# so a `command -v` probe picks the stub and every syntax check reports a false error.
py(){
  local c
  for c in python python3 py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c '' >/dev/null 2>&1; then echo "$c"; return 0; fi
  done
  echo python
}

# Every invocation is recorded, so "did the guardrails actually run?" is answerable
# at the start of the next session rather than assumed.
log_hook(){
  local root logf
  root="$(root_dir)"; logf="$root/.claude/hook-log"
  mkdir -p "$root/.claude" 2>/dev/null
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${1:-?}" "${2:-}" >> "$logf" 2>/dev/null
  if [ "$(wc -l < "$logf" 2>/dev/null || echo 0)" -gt 5000 ]; then
    tail -n 2000 "$logf" > "$logf.tmp" 2>/dev/null && mv "$logf.tmp" "$logf" 2>/dev/null
  fi
}
