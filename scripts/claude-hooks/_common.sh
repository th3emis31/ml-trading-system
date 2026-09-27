# shared helpers for hooks (sourced)
#
# Three rules learned the hard way:
#   * Windows hands us C:\a\b, Git Bash's pwd hands back /c/a/b, and comparing the
#     two never matches. Everything is canonicalised to the /c/a/b form before any
#     comparison, or the project-relative path silently stays absolute and backups
#     land in a directory literally named "C:".
#   * A hook that cannot tell what it was given says so on stderr. Passing quietly
#     is how a file gets edited with no backup and nobody notices.
#   * A guard that cries wolf gets ignored, so it must not fire on ordinary code.

# Canonical form: forward slashes, and a drive letter folded to the /c/... form
# that Git Bash itself uses.
norm(){
  printf '%s' "$1" | tr '\\' '/' | sed -E 's#^([A-Za-z]):/#/\L\1/#'
}

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
rel_path(){
  local file root lf lr
  file="$(norm "$1")"; root="$(norm "$2")"
  lf="$(printf '%s' "$file" | tr 'A-Z' 'a-z')"
  lr="$(printf '%s' "$root" | tr 'A-Z' 'a-z')"
  case "$lf" in
    "$lr"/*) printf '%s' "${file:$((${#root}+1))}" ;;
    *) printf '%s' "$file" ;;
  esac
}

# An interpreter that EXISTS is not an interpreter that RUNS. On Windows,
# %LOCALAPPDATA%\Microsoft\WindowsApps\python3 is an App Execution Alias: a stub that
# `command -v` finds happily and that then prints "Python was not found; run without
# arguments to install from the Microsoft Store" and exits non-zero. This function used to
# pick it on that basis, so check-after-edit reported "SYNTAX ERROR ... Python was not
# found" for every Python file edited on 27 September 2026 - a guard that failed loudly
# enough to be dismissed each time and never actually parsed anything.
# So each candidate is probed with --version and the first one that truly answers wins.
py(){
  local c
  for c in python3 python py; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" --version >/dev/null 2>&1 && { printf '%s' "$c"; return 0; }
  done
  printf '%s' python   # nothing answered; let the caller fail with a real error
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
