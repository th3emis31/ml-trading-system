#!/usr/bin/env bash
# PostToolUse (Edit|Write|MultiEdit): fast correctness check for the edited file.
# Exit 2 sends the error back to Claude so it fixes it immediately.
set -u; . "$(dirname "$0")/_common.sh"
HOOK_STDIN="$(cat)"; export HOOK_STDIN
file="$(tool_file)"; [ -z "$file" ] && { echo "[kit] could not determine the edited file — NOT checked" >&2; log_hook check-after-edit "UNKNOWN FILE"; exit 0; }
root="$(root_dir)"; cd "$root" || exit 0
case "$file" in
  *.py)
    out="$($(py) -m py_compile "$file" 2>&1)" || { echo "SYNTAX ERROR in $file — fix before continuing:" >&2; echo "$out" | tail -20 >&2; exit 2; }
    # py_compile proves the file PARSES. It does not import it, does not run a
    # test, and does not look at any other file. Calling that "OK" was read as
    # verification, and every wrong result this project produced was syntactically
    # perfect Python. So say what was actually checked, and run the fast tests
    # when the project has them.
    msg="[kit] ${file##*/}: SYNTAX ONLY — not verified"
    if [ -d tests ]; then
      # --timeout comes from pytest-timeout, which is NOT installed everywhere. Passing it blindly
      # makes pytest exit on a usage error, which this hook then reported as "TESTS FAILED" - a
      # guard accusing the code of something the guard did. Added only when the plugin is present.
      tmo=""; $(py) -c "import pytest_timeout" >/dev/null 2>&1 && tmo="--timeout=60"
      if out="$($(py) -m pytest -q -x $tmo 2>&1)"; then
        msg="$msg; tests pass"
      else
        echo "TESTS FAILED after editing $file:" >&2; echo "$out" | tail -25 >&2
        log_hook check-after-edit "tests failed: $file"; exit 2
      fi
    else
      msg="$msg; no tests/ directory, so nothing ran"
    fi
    echo "$msg"; log_hook check-after-edit "$file"; exit 0 ;;
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
