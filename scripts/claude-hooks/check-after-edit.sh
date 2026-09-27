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
    # This used to run the WHOLE suite with `--timeout=60`, and two things were wrong with it.
    #
    # pytest-timeout is not installed, so pytest rejected the argument and exited non-zero, and the
    # hook reported "TESTS FAILED" on every single Python edit while running no test at all. A gate
    # that always says fail is read as noise within a day, exactly like one that always says pass.
    #
    # And the whole suite is not affordable per edit: tests/conftest.py copies data/ and models/
    # (~4.4 GB) into a temp sandbox on EVERY pytest invocation and the full run takes 11 minutes on
    # this 7.4 GB machine. A post-edit hook that costs that much gets disabled, and a killed one
    # leaves its sandbox behind.
    #
    # So it runs the ONE test file that covers the edited module, and when there is no such file it
    # says so plainly rather than implying coverage. KIT_RUN_TESTS=full opts back into the whole
    # suite for anyone who wants it.
    base="${file##*/}"; base="${base%.py}"
    target="tests/test_${base}.py"
    case "$file" in *"/tests/test_"*|tests/test_*) target="${file#"$root/"}" ;; esac
    if [ ! -d tests ]; then
      msg="$msg; no tests/ directory, so nothing ran"
    elif [ "${KIT_RUN_TESTS:-}" = "full" ]; then
      if out="$($(py) -m pytest -q -x -p no:cacheprovider 2>&1)"; then
        msg="$msg; FULL suite passes"
      else
        echo "TESTS FAILED after editing $file:" >&2; echo "$out" | tail -25 >&2
        log_hook check-after-edit "tests failed: $file"; exit 2
      fi
    elif [ -f "$target" ]; then
      if out="$($(py) -m pytest -q -x -p no:cacheprovider "$target" 2>&1)"; then
        msg="$msg; $target passes"
      else
        echo "TESTS FAILED in $target after editing $file:" >&2; echo "$out" | tail -25 >&2
        log_hook check-after-edit "tests failed: $file"; exit 2
      fi
    else
      msg="$msg; NO test file at $target, so nothing was verified — write one"
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
