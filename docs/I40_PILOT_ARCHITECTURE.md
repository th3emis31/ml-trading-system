# i40 Pilot — Architecture and Build Map

Owner's goal, stated 25 September 2026: an AI system of their own — memory, brain, context
engineering, loop engineering, skills, tools — able to build trading systems, EAs, indicators, bots,
dashboards, websites, software, automation, SEO, marketing and financial analysis, Pine script, video
and images; able to use the PC fully when asked; safe by construction; and able to run **anywhere with
internet, or entirely locally with none**.

It already has a name and a skeleton: `src/i40_pilot.py` carries identity, memory, skills, schedule,
tools, context, loop and signature rules. **This document extends that. It does not restart it.**

---

## 1. The one constraint that shapes everything

Every other requirement is negotiable. This one is not:

> **It must work with no internet and no AI subscription.**

Offline means the model is a small local one — a quantised 7B–30B running on this machine — not a
frontier model. A local model is *materially less capable*: it reasons worse, hallucinates more, and
forgets instructions. Any architecture that assumes a clever model will collapse the moment it runs
offline, which is the exact moment it is most needed.

So the architecture has one thesis:

> ### Put the competence in the SYSTEM, not in the model.

The model supplies language and judgement. **Everything that must be correct is carried by the system
around it**: memory that states what is already known, skills that carry the procedure, tools that do
hard work deterministically, and verification that refuses work which fails its own check.

This is also the honest answer to "no mistakes". No model, local or frontier, is mistake-free. A
system can still be *trustworthy* if every claim it makes has been checked by something that is not
the model. That is what the layers below are for.

---

## 2. The eight layers

```
 8  INTERFACES     dashboard · voice · CLI · API · phone
 7  GOVERNANCE     permission tiers · approval queue · audit · kill switch
 6  TOOLS          typed actions: files, shell, browser, MT5, media, web
 5  SKILLS         instruction packs: trading, EA, Pine, SEO, marketing, video...
 4  LOOP           perceive -> decide -> act -> VERIFY -> RECORD
 3  BRAIN          goal -> plan -> step selection -> self-critique
 2  CONTEXT        what enters the model's window, and what is deliberately left out
 1  MEMORY         episodic · semantic · procedural · evidence
 0  RUNTIME        model provider (cloud OR local) · portable data root
```

Each layer may only call the one below it. A skill never touches the model provider; a tool never
edits memory directly. That rule is what keeps the system swappable — replacing Claude with a local
model touches layer 0 alone.

---

### Layer 0 — Runtime: the swap that makes it portable

A single `Provider` interface with three implementations:

| provider | when | quality |
|---|---|---|
| `claude` | online, subscription present | best |
| `local` | offline, privacy-sensitive, or free-running | lower, compensated by layers 1–4 |
| `hybrid` | local drafts, cloud reviews on demand | good and cheap |

The rest of the system must not know which is active. Two consequences have to be designed in from
the start, not retrofitted:

- **Every prompt has a token budget and degrades gracefully.** A 200k context is not available
  locally. Layer 2 must produce a 4k brief as readily as a 100k one.
- **No capability may depend on the model being clever.** If a task needs precision — arithmetic,
  backtests, file edits, price data — a *tool* does it and the model only reads the result.

Portability is this layer's second concern: one `I40_HOME` directory holds memory, evidence, config
and artefacts, so the whole system moves by copying a folder.

---

### Layer 1 — Memory: four kinds, not one

Most "AI memory" is a single note pile, and it fails because the four kinds have different rules.

| kind | holds | lifetime | written by |
|---|---|---|---|
| **Episodic** | what happened, when, and what came of it | rolling, prunable | the loop, automatically |
| **Semantic** | what is true about this world | long, correctable | promoted from episodic |
| **Procedural** | how to do a thing here | long, versioned | skills, refined by use |
| **Evidence** | measured results, including failures | permanent, append-only | verification only |

The **evidence** store is what separates this from a chatbot with notes. It is append-only, it records
failures as first-class entries, and **no claim may be presented as fact unless it is in there**.
`.claude/memory/BASELINE.md` is already this, and it is why the session that produced this document
could say "that improvement is an artefact" instead of shipping it.

Rules that must survive the rewrite:

- Contradiction is resolved by **recency plus evidence**, never by whichever note was read last.
- A memory that cannot be traced to an event or a measurement is a **hypothesis**, and is labelled so.
- Forgetting is a feature: episodic memory is compacted on a schedule, and nothing is compacted
  before whatever earned promotion has been promoted.

---

### Layer 2 — Context engineering: what is left OUT

The job is not "retrieve relevant things". It is **to spend a limited window well**, and locally that
window is small. Every request is assembled from five slots with a fixed budget:

```
identity + signature rules       always, small, never truncated
task brief                       the goal, restated, plus its acceptance test
evidence relevant to THIS task   retrieved, ranked, and CITED
working set                      the files or data actually being changed
recent episodic                  only what changes the next action
```

Two disciplines carry most of the value:

- **Cite or omit.** Anything injected as fact carries its source. Uncited material is dropped rather
  than trusted — this is how a weaker model is stopped from confabulating over a half-remembered note.
- **State the acceptance test before the work.** "Done" is defined before starting, in a form
  something other than the model can check. This is the highest-leverage habit in the whole design.

---

### Layer 3 — Brain: plan, then criticise the plan

```
goal -> restate -> retrieve evidence -> plan (<=7 steps, each with an acceptance test)
     -> critique the plan against known failure modes -> execute -> verify -> record
```

The critique step is not decoration. It is a separate pass whose only job is to ask: *what would make
this wrong, and what does it cost to check?* Asked **before** acting rather than after, that question
is the difference between finding that a 12x backtest improvement was an artefact and shipping it as
a win.

The brain keeps a **failure-mode register** — the specific ways work here has gone wrong before:
fitting on an outlier year; a spike mistaken for a plateau; counting another expert's trades as the
system's; a framework default treated as a signature; measuring a filter on the wrong date range.
Each new failure adds a line. The critique pass reads it every time.

---

### Layer 4 — Loop engineering: closure is the point

```
perceive -> decide -> act -> VERIFY -> RECORD -> (compact)
```

Three loop kinds, all sharing the same closure:

- **Scheduled** — heartbeat work: health, learning, reports. Exists today.
- **Event** — something changed: a trade closed, a file moved, a page failed.
- **Goal** — a multi-step objective that survives across sessions until done or abandoned.

**A loop without closure is a script.** Every iteration ends by writing what it did, what it measured,
and whether the acceptance test passed. `i40_pilot.loop_closure` already does this; it becomes
mandatory for every loop rather than one of them.

Long work is **resumable and incremental by default** — state written after each step, never only at
the end. This machine kills long jobs; a run that can only lose its current step is the difference
between a result and a wasted hour.

---

### Layer 5 — Skills: how a capability is added

A skill is a folder: instructions, the tools it needs, its acceptance tests, and worked examples.

```
skills/<name>/
  SKILL.md        when to use it · the procedure · what "good" looks like
  acceptance.md   how to tell whether the output is actually correct
  examples/       one good, one bad, and why
```

**Adding SEO, or video, or Pine script is adding a folder — not a subsystem.** That is what makes
"bit by bit" possible and what stops the system sprawling. The existing twelve skills (`backtest`,
`brain`, `safe-upgrade`, `verify`, `strategy`, `tv-plan`, …) already follow this shape.

Planned skill families: **trading** (system, EA, indicator, bot, autotrade, Pine) · **software** (app,
website, dashboard, automation) · **market** (SEO, marketing, deep analysis, promotion) ·
**business** (financial, planning) · **media** (video, images).

---

### Layer 6 — Tools: typed, permissioned, honest about failure

Every tool declares its inputs, its outputs, its **permission tier**, and what it does when it fails.
A tool never silently substitutes a plausible value — the rule already written into this codebase:
*never invent data to fill a gap; return `available: false` instead of a plausible-looking number.*
That rule exists because this system once published BUY signals generated from a random walk.

Core set: filesystem · shell · browser · web search and fetch · MT5/MT4 · chart and image · video ·
scheduler · notifier.

---

### Layer 7 — Governance: four tiers, because full PC access is the ask

| tier | examples | authorisation |
|---|---|---|
| **T0 read** | read files, fetch a page, query the account | none |
| **T1 local write** | write in the workspace, run a backtest, edit a draft | none, but logged |
| **T2 outward** | send an email, post, publish, install software | explicit, per action |
| **T3 money and system** | place an order, move funds, change system settings, delete data | explicit, with the exact figures shown, never inferred from an earlier approval |

Plus: an **append-only audit** of every T1+ action; a **kill switch** that halts all loops and leaves
open positions alone (the owner's standing rule — trades close naturally, never by hand); and a
**dry-run default**, so a new capability proves itself before it is allowed to act.

---

### Layer 8 — Interfaces

Dashboard (exists) · voice (exists, must use the PC engine) · CLI · HTTP API · phone. All are *views
onto the same brain* — none may contain logic of its own. A rule enforced anywhere but the brain is a
rule the next interface will bypass.

---

## 3. How any new capability is added

Always the same five moves, and the fifth is the one usually skipped:

1. **Skill** — write the procedure down.
2. **Tools** — give it the deterministic actions it needs.
3. **Acceptance test** — define, before building, how to tell the output is correct.
4. **Dry run** — produce output without side effects until it passes.
5. **Evidence** — record the result, including the failures, in the permanent store.

---

## 4. Build map — one step per week, each useful alone

| # | Step | Delivers | Done when |
|---|---|---|---|
| **1** | ~~Provider abstraction~~ **DONE 2026-09-25** | budgets, degraded reporting, fallback chain on the existing `ai_provider` | tests pin it; the machine currently reports *Dependent* because no local model is installed yet - honestly, rather than pretending |
| **2** | ~~Memory split~~ **DONE 2026-09-25** | the four kinds typed with their own rules, provenance tracing, hypothesis flagging, promotion and compaction proposals | **89.0 % of the 493-entry record is traceable**; 19 unsupported claims are named rather than hidden |
| **3** | ~~Context builder~~ **DONE 2026-09-25** | five budgeted slots; identity and task never truncated; every evidence line carries its source | measured: the same task builds at 282 tokens on a 4k window and 1,490 on 180k, 8 vs 40 cited results |
| **4** | ~~Acceptance tests~~ **DONE 2026-09-25** | four check kinds - `run`, `file`, `number`, `appended` - plus `ask` for the owner; shell checks gated by governance | coverage 0% -> **100%**; `may_report_success()` refuses on no check, any failure, or nothing adjudicated |
| **5** | ~~Loop closure~~ **DONE 2026-09-25** | the ledger and `closing_run`; two loops wired, the other 11 reported SILENT rather than assumed fine | a loop's state is now visible: `daily_learning` **CLOSED** (4 of 14 runs acted), `system_doctor` WATCHING |
| **6** | ~~Governance~~ **DONE 2026-09-25** | tiers that cannot be self-declared downward; approval that names one action and does not carry; dry run gating every tier | a T3 action is refused without approval, with drifted figures, while halted, or in dry run |
| **7** | ~~Failure-mode register~~ **DONE 2026-09-25** | 14 modes from this repo's own history; `critique(plan)` raises only what applies | a parameter sweep now carries *'test the neighbours'* and *'name the window you chose on'* before any work starts |
| **8** | Skill families | trading · software · market · business · media | each family has one skill passing its own acceptance test end to end |
| **9** | Portability | one `I40_HOME`, copy-to-move | the system runs from a USB drive on another machine, offline |
| **10** | Self-improvement loop | proposals from evidence, owner approves, effect measured | a change is proposed, applied and measured without being asked |

Order matters. **1–3 make it possible to run locally; 4–7 make it trustworthy; 8–10 make it broad and
then self-improving.** Broadening before step 7 produces a system that can do many things and cannot
be believed about any of them.

---

## 5. What exists today

| Layer | Already built | Still to build |
|---|---|---|
| 0 Runtime | `src/ai_provider.py`: Claude CLI + Ollama, internet probe, selection, usage meter, **context budgets, degraded flag, fallback chain** (step 1, done 2026-09-25) | `I40_HOME`, and a local model actually installed |
| 1 Memory | the four kinds typed in `second_brain.KIND_RULES`; `trace()`, `hypotheses()`, `promotion_candidates()`, `compaction_candidates()`, `provenance_report()` (step 2, done 2026-09-25); BASELINE prose now indexed | promotion/compaction actually applied, under governance |
| 2 Context | `src/context_builder.py`: five budgeted slots, cite-or-omit, depth scales with the window, refuses rather than truncates (step 3, done 2026-09-25) | working-set retrieval from the actual diff |
| 3 Brain | `src/failure_modes.py`: 14 modes, each citing the occasion and the check that would have caught it, wired into every brief (step 7, done 2026-09-25) | the plan-and-execute loop itself |
| 4 Loop | `src/loop_ledger.py`: append-only closure ledger, `closing_run` records even on a raise, SILENT/WATCHING/OPEN/CLOSED/STALE states (step 5, done 2026-09-25); doctor and daily learning wired | the remaining 11 loops adopting it; event and goal loops |
| 5 Skills | 12 skills, **all 12 declaring an independent acceptance check**; `src/skill_acceptance.py` adjudicates and gates the success claim (step 4, done 2026-09-25) | the five families |
| 6 Tools | MT5/MT4, browser, files, shell, charts | media, SEO, typed declarations, tiers |
| 7 Governance | `src/governance.py`: four tiers, per-action approval with figure matching, append-only audit, halt that never closes positions, dry-run by default (step 6, done 2026-09-25); plus the existing execution guard | every caller routed through it |
| 8 Interfaces | dashboard, voice, API | CLI, phone |

Roughly half of layers 1–5 exists in some form. **The gap is not features — it is discipline:**
budgets, acceptance tests, closure and tiers applied uniformly instead of only where somebody
remembered.

---

## 6. The standard this is held to

Three rules, all earned the hard way in this codebase:

1. **Nothing is true because the model said it.** Claims cite evidence or are marked as hypotheses.
2. **Nothing acts outward without its tier's authorisation** — and approval for one action never
   carries to the next.
3. **Failures are recorded as carefully as successes.** The rejected candidates in `BASELINE.md` are
   worth more than the accepted ones, because they are what stops the same ground being re-walked.
