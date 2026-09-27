---
name: doctor
description: Check whether this project is safe to work in — wrong working directory, hooks that silently never run, memory that has stopped being written, disabled schedules, empty or corrupt data files, credentials in tracked files, and a server exposed to the network. Run at the start of a session and before trusting any result.
---

# Project doctor

Run it:

```
python scripts/doctor.py            # everything
python scripts/doctor.py --quiet    # only problems
python scripts/doctor.py --json     # machine readable
```

Read only. It opens files and asks the operating system questions. It never writes,
edits, deletes or restarts anything.

## What it checks, and why each one is there

| Check | The failure it catches |
|---|---|
| location | Claude started outside the project root, so no memory, skills or hooks loaded and relative paths write to the home folder |
| wiring | CLAUDE.md, settings.json, skills and memory actually present |
| hooks | settings.json naming scripts that do not exist, so the hook silently never runs; .sh hooks on Windows with no bash on PATH |
| brain | NOTES, BASELINE or LESSONS not written for days, meaning work happened and was not recorded |
| second brain | claude-mem installed but not writing, which is how it stops silently when its allowance runs out |
| schedules | tasks referencing this project, and how many are disabled |
| data | json files that are empty or corrupt, a large state file whose trade list is empty, a stray data/ in the home folder |
| secrets | credentials in tracked files, with placeholders filtered out |
| exposure | a port answering on the network address rather than localhost only |
| git | branch and the size of the uncommitted diff |

## How to use the result

A FAIL means results from this project cannot be trusted until it is fixed. Say so
rather than working around it. A WARN is something to decide about, not ignore.

Run it at the start of a session, before any measurement run, and after changing
hooks, settings or schedules.

## When it is wrong

The detectors are heuristics. If it flags something that is fine, do not just
silence it: tighten the rule and test it against a known-good and a known-bad case,
then record that in LESSONS.md. A tool that cries wolf gets ignored, which is worse
than not having one.
