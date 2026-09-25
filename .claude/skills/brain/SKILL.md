---
name: brain
family: core
description: Read or update the project's persistent memory (CLAUDE.md, .claude/memory/NOTES.md, BACKLOG.md). Use when the user says "remember", "what did we decide", "memory", "context", or "brain".
---

# Brain (memory / context)

| Layer | File | Content |
|-------|------|---------|
| Long-term | `CLAUDE.md` | Architecture, rules, commands. Auto-loaded every session. |
| Working notes | `.claude/memory/NOTES.md` | Dated log of decisions, learnings, gotchas. |
| Backlog | `.claude/memory/BACKLOG.md` | Improvement ideas with checkboxes. |
| Backups | `.claude/backups/` | Automatic pre-edit snapshots (git-ignored). |

Commands:
- **recall** (default): last 30 lines of NOTES.md + open BACKLOG items + 3-bullet summary.
- **remember <text>**: append `- YYYY-MM-DD: <text>` to NOTES.md.
- **backlog <text>**: append `- [ ] <text>` to BACKLOG.md.
- **promote <text>**: a rule for every session → add to `CLAUDE.md` (keep it under ~150
  lines; move detail to docs/).
- **fill**: if `CLAUDE.md` still has empty "What this project is" lines, explore the code
  and fill them in now.

Never delete memory lines; strike through with `~~` if obsolete.

## Acceptance

```acceptance
appended: .claude/memory/NOTES.md
run: python -m src.second_brain status
ask: can the new entry be traced to a measurement or a dated event, rather than an impression?
```
