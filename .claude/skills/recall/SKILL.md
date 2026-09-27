---
name: recall
family: core
description: Search what this project already knows before doing new work — prior results, decisions, lessons, failed approaches and existing code. Use at the start of any task, and whenever tempted to build, test or measure something that might already exist.
---

# Recall before building

Most wasted work in this project has been repeated work: a strategy retested under a
new name, a module written beside one that already did the job, a conclusion
rediscovered a week after it was recorded. This runs first.

## Order of search, cheapest first

1. **Decisions and results** — `.claude/memory/NOTES.md`, `BASELINE.md`, `LESSONS.md`.
   Grep for the instrument, the strategy family, and the metric.
2. **Existing code** — `grep -rn "<concept>" src/ scripts/ tests/`. Look for a function
   that already does it before writing one that nearly does.
3. **Prior measurements** — every row in BASELINE.md, including the failures. A strategy
   that already failed does not get retested under a new name without saying so.
4. **Git history** — `git log --oneline -30` and `git log -S"<term>"` for when and why a
   thing changed.

## What to report before starting

Two or three lines, always:

- what already exists that is relevant
- what was already tried and what happened
- what is genuinely new about this task

If a previous attempt failed, name the reason it failed and say what is different this
time. "Trying again" is not a reason.

## The duplication rule

If a function, module or strategy already exists, extend it. Do not create a second one
with a different name. The dup-check hook blocks the obvious cases; this skill catches
the ones it cannot see, such as the same idea under a different vocabulary.

## Acceptance

This skill may not report success on its own say-so.
At least one of these is adjudicated by something other than the model.

```acceptance
run: grep -rn --include=NOTES.md --include=LESSONS.md --include=BASELINE.md -e . .claude/memory
appended: .claude/memory/NOTES.md
ask: was a previous attempt at this found, and if it failed, is the reason it failed named?
```
