#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit): fast correctness check for the edited file.
# Exit 2 sends the error back to Claude so it fixes it immediately.
set -u; . "$(dirname "$0")/_common.sh"
file="$(cat | tool_file)"; [ -z "$file" ] && exit 0
root="$(root_dir)"; cd "$root" || exit 0
case "$file" in
  *.py)
    out="$($(py) -m py_compile "$file" 2>&1)" || { echo "SYNTAX ERROR in $file — fix before continuing:" >&2; echo "$out" | tail -20 >&2; exit 2; }
    echo "[kit] python compile OK: ${file##*/}"; exit 0 ;;
  *.js|*.jsx|*.ts|*.tsx|*.mjs|*.cjs)
    if [ -f package.json ] && [ -d node_modules ] && grep -q '"build"' package.json; then
      out="$(npm run build --silent 2>&1)" || { echo "BUILD FAILED after editing $file:" >&2; echo "$out" | tail -40 >&2; exit 2; }
      echo "[kit] build OK: ${file##*/}"
    else
      out="$(node --check "$file" 2>&1)" || { echo "SYNTAX ERROR in $file:" >&2; echo "$out" | tail -20 >&2; exit 2; }
      echo "[kit] node syntax OK: ${file##*/}"
    fi; exit 0 ;;
  *.json)
    out="$(node -e 'JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"))' "$file" 2>&1)" \
      || out="$($(py) -c 'import json,sys;json.load(open(sys.argv[1]))' "$file" 2>&1)" \
      || { echo "INVALID JSON in $file:" >&2; echo "$out" | tail -5 >&2; exit 2; }
    echo "[kit] json OK: ${file##*/}"; exit 0 ;;
  *) exit 0 ;;
esac
