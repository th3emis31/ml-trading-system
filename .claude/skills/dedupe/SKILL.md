---
name: dedupe
description: Find and consolidate duplicated functions, classes, formulas, and status files without deleting behaviour. Use when the user mentions duplication, "same code twice", two versions of X, or before large features on a monolith.
---

# Dedupe (consolidate, never delete behaviour)

1. **Inventory duplicates**:
   ```bash
   bash scripts/claude-hooks/dup-check.sh --all
   ```
   This lists every function/class name defined in more than one place, with file:line.
2. **Classify each group**:
   - *Identical*: keep one canonical location, make the others import/call it (leave a
     one-line wrapper if callers depend on the old path). Nothing is removed from the API.
   - *Diverged* (e.g. two ensemble formulas): write down the difference, pick the correct
     one with the user or from the strategy doc, make both paths call one shared function
     with an explicit parameter for the old behaviour if it must survive.
   - *Intentional* (tests, fixtures): mark it in `.claude/memory/NOTES.md` so it is not
     reported again.
3. **Status/summary files**: do not delete them; add a line to NOTES.md marking them stale.
4. **Verify** with `/verify` and the tests. Commit one consolidation per commit.
5. **Record** each consolidation in NOTES.md and add remaining groups to BACKLOG.md.
