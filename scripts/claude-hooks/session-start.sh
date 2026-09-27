#!/usr/bin/env bash
# SessionStart: prove the session is wired correctly, anchor it in time, then show
# memory and git state. Never fails — a broken check prints a warning, it does not
# stop the session.
set -u; . "$(dirname "$0")/_common.sh"

ROOT="$(root_dir)"
HERE="$(norm "$(pwd -P)")"

# ---------------------------------------------------------------------------
# 1. WORKING DIRECTORY. Starting Claude outside the project root silently
#    disables CLAUDE.md, every skill and every hook, and relative paths then
#    resolve into the home folder, which is how a stray data/ directory gets
#    created and written to.
#
#    Note the limit honestly: when Claude starts outside the project, this hook
#    is not registered at all, so this banner cannot appear. Catching that case
#    needs a SessionStart hook in the USER-level ~/.claude/settings.json, or the
#    start.cmd shipped in the project root. See README.
# ---------------------------------------------------------------------------
if [ "$(printf '%s' "$HERE" | tr 'A-Z' 'a-z')" != "$(printf '%s' "$ROOT" | tr 'A-Z' 'a-z')" ]; then
  echo "!!! WRONG DIRECTORY ---------------------------------------------------"
  echo "!!! Claude started in : $HERE"
  echo "!!! Project root is   : $ROOT"
  echo "!!! Relative paths will resolve where you started, not in the project."
  echo "!!! Exit and restart with:  cd \"$ROOT\" && claude --continue"
  echo "!!! ------------------------------------------------------------------"
fi
cd "$ROOT" || exit 0

# ---------------------------------------------------------------------------
# 2. TIME ANCHOR. "Check the date of a pasted transcript" is unusable advice if
#    the session was never told what now is. An 11-day-old paste was once read
#    as live state because of exactly this gap.
# ---------------------------------------------------------------------------
echo "=== now === $(date -u +%Y-%m-%dT%H:%MZ)   HEAD: $(git log -1 --format='%h %ad %s' --date=short 2>/dev/null | cut -c1-70)"

# ---------------------------------------------------------------------------
# 3. WIRING, and whether the guardrails actually ran last session.
# ---------------------------------------------------------------------------
missing=""
[ -f CLAUDE.md ] || missing="$missing CLAUDE.md"
[ -d .claude/skills ] || missing="$missing .claude/skills"
[ -f .claude/settings.json ] || missing="$missing .claude/settings.json"
[ -d .claude/memory ] || missing="$missing .claude/memory"
if [ -n "$missing" ]; then
  echo "!!! MISSING WIRING:$missing  — run the kit installer before trusting this session."
else
  echo "=== wiring ok === $ROOT  skills: $(ls .claude/skills 2>/dev/null | tr '\n' ' ')"
fi

if [ -f .claude/hook-log ]; then
  last="$(tail -1 .claude/hook-log | cut -f1)"
  echo "    hooks last ran: ${last:-never}  ($(wc -l < .claude/hook-log | tr -d ' ') recorded invocations)"
  for h in backup-before-edit check-after-edit dup-check; do
    grep -q "	$h	" .claude/hook-log 2>/dev/null || echo "    WARNING: $h has never run — it may not be wired or bash may be missing"
  done
else
  echo "    WARNING: no .claude/hook-log — either this is the first session, or no hook has ever run."
fi

# ---------------------------------------------------------------------------
# 4. IS THE BRAIN STILL BEING WRITTEN TO? A memory that stopped recording looks
#    identical to a healthy one, because the old notes still scroll past.
# ---------------------------------------------------------------------------
now_s=$(date -u +%s)
for f in NOTES LESSONS BASELINE; do
  path=".claude/memory/$f.md"
  if [ -f "$path" ]; then
    m=$(date -u -r "$path" +%s 2>/dev/null || echo "$now_s")
    days=$(( (now_s - m) / 86400 ))
    line="    $f.md last written $days day(s) ago"
    [ "$f" = "NOTES" ] && [ "$days" -gt 2 ] && line="$line   <-- memory may have stopped recording; check the second-brain plugin's status and its provider allowance"
    echo "$line"
  else
    echo "    $f.md MISSING"
  fi
done

# ---------------------------------------------------------------------------
# 5. THE REPORTING CONTRACT, printed every session because a rule nobody reads
#    is not a rule.
# ---------------------------------------------------------------------------
cat <<'RULES'
=== before reporting ANY measured number ===
  1. Units: price increment per instrument, and every ratio parameter's unit
     (_pct is 0-100, _frac is 0-1, _bps is basis points). Detect, do not assume.
  2. Scale: the stop must be wider than a typical bar, and no threshold may be an
     absolute price when the data spans a large price range.
  3. Causality: signals use only bars up to t; say which bar the fill happens on.
  4. Control: run the inverse. Zero inverse trades is not a control.
  5. Sample: under 100 closed trades is insufficient evidence.
  6. Provenance: a pasted transcript is not current state. Check its date against
     the "now" printed above.
RULES

# Print the lessons file itself. Grepping for bullet starts silently dropped the
# second line of every wrapped lesson, which is where the instruction lived.
if [ -f .claude/memory/LESSONS.md ]; then
  echo "=== lessons (.claude/memory/LESSONS.md) ==="
  tail -n 40 .claude/memory/LESSONS.md
fi
[ -f .claude/memory/NOTES.md ] && { echo "=== session memory (last 40 lines) ==="; tail -n 40 .claude/memory/NOTES.md; }
[ -f .claude/memory/BACKLOG.md ] && { echo "=== open backlog items ==="; grep -n '^- \[ \]' .claude/memory/BACKLOG.md | head -10; }
echo "=== git ==="; git status --short --branch 2>/dev/null | head -20 || echo "(not a git repo yet — run: git init)"
exit 0
