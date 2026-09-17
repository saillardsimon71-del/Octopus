# OCTOPUS — Autonomous Business Implementation Foundation

## Mission

Evolve OCTOPUS from an entrepreneurial control surface into a durable, multi-business operating system without creating a second runtime.

The existing architecture remains authoritative:

`Business Workspace → ORBIT mission → octopus.tasks → Worker → agents/tools → Journal/Evidence → Decision → next mission`

The GUI is a control surface, not a business runtime.

## Current baseline

The Intelligence Workbench already exposes eight strategic actions:

1. Discover
2. Validate
3. Offer
4. Content
5. Funnel
6. Clients
7. Reinvest
8. Strategic Review

Each action builds a contextual ORBIT objective from the active business and known offers. The current implementation deliberately does not invent CRM, revenue, margin or social analytics data.

## Implementation status (2026-09-17, working tree not yet committed)

| Phase | Status | Where |
|---|---|---|
| 1 Persistent strategy model | Implemented, tested | `octopus/strategy.py`, migration v5 in `octopus/journal.py`, `tests/test_strategy.py` |
| 2 Evidence / provenance | Implemented, tested | `strategy_evidence` (nature, source, capture time, confidence, immutable) |
| 3 Mission linkage | Implemented, tested with simulated LLM | `orbit.mission` in `agents/task_handlers.py`; business propagated in `agents/runtime.py` |
| 4 Recurring loop | Implemented, tested | `strategy.review` handler (no LLM), `strategy.schedule_review`, `octopus schedule <business> strategy.review --every N` |
| 5 Connectors | Boundary only, no real source | `octopus/connectors.py` (every domain "non configuré") |
| 6 Reinvestment decision support | Not started | requires a real finance source first |
| 7 Strategic GUI | Read-only state card | `agents/gui/intelligence.py`; creation via `python -m octopus strategy` |
| 8 Second business | Tested end to end (simulated LLM, no real network) | `tests/test_second_business_loop.py` |

Not verified: a real ORBIT mission with a real model, real GUI clicks with real data, any CI run.

## Target operating loop

```text
BUSINESS
  ↓
OBJECTIVES
  ↓
HYPOTHESES
  ↓
EXPERIMENTS
  ↓
ORBIT MISSION
  ↓
TASK / WORKER
  ↓
AGENTS + CONNECTORS
  ↓
EVIDENCE
  ↓
RESULT
  ↓
DECISION
  ↓
REVIEW / SCHEDULE
  ↺
```

## Non-negotiable architecture rules

- Do not create a second agent runtime.
- Do not create a second task queue.
- Do not create a second source of truth for runs.
- Reuse `octopus.tasks` for durable execution.
- Reuse `octopus.journal` for runs/events/cost/traceability where appropriate.
- Keep ORBIT as the strategic coordinator.
- Keep the six existing business roles: SOUT, CONVERT, FORGE, GROWTH, LEDGER, ORBIT.
- Keep business logic outside Tkinter.
- Long-running work must remain asynchronous from the GUI.
- Missing external data must remain explicitly missing; never fabricate metrics.
- Human approval remains mandatory for irreversible/high-risk operations.
- Preserve `web_guard`, cloud rendering idempotency, H3 cloud-only, and publication dry-run safeguards.

## Phase 1 — Persistent strategy model

Introduce a small domain layer under the existing Octopus persistence boundary for:

- `BusinessObjective`
- `Hypothesis`
- `Experiment`
- `Decision`
- `StrategicReview`

Every object should have:

- stable ID
- business ID
- timestamps
- lifecycle/status
- source/provenance where applicable
- parent relationship where applicable
- concise human-readable summary

Do not over-engineer this into a generic enterprise framework.

### Objective

An objective describes the durable outcome a business is pursuing.

Minimum semantics:

- objective
- business
- priority
- status
- timeframe
- success criteria
- owner/runtime context

### Hypothesis

A falsifiable assumption linked to an objective.

Minimum semantics:

- statement
- expected signal
- failure/stop criterion
- evidence required
- status

### Experiment

A bounded action designed to test a hypothesis.

Minimum semantics:

- hypothesis
- action
- budget/time boundary
- expected result
- actual result
- status
- evidence links

### Decision

A durable record of what was decided and why.

Minimum semantics:

- decision
- evidence considered
- alternatives considered
- resulting action
- decision maker/runtime
- timestamp

### StrategicReview

A periodic review that summarizes progress and produces decisions/next experiments.

Minimum semantics:

- business
- period
- objectives reviewed
- evidence summary
- decisions
- next actions
- unresolved risks

## Phase 2 — Evidence / provenance

Create one coherent evidence abstraction rather than scattering URLs/text blobs through strategic objects.

Evidence should support at minimum:

- URL/source reference
- captured timestamp
- source type
- title/summary
- extracted facts or observation
- confidence/provenance metadata
- related objective/hypothesis/experiment/decision

The system must distinguish:

- observed fact
- model inference
- user-provided fact
- recommendation

This distinction is critical for long-term autonomous operation.

## Phase 3 — Mission linkage

Extend the existing ORBIT mission path so a strategic mission can carry durable IDs:

- business_id
- objective_id
- hypothesis_id when relevant
- experiment_id when relevant
- parent review/decision IDs when relevant

The GUI should launch missions through the same existing mission path. Do not implement strategy execution directly inside GUI classes.

Mission completion should be able to attach:

- result summary
- evidence IDs
- decision candidates
- experiment outcome
- follow-up mission/task

## Phase 4 — Recurring strategic loop

Add scheduling only through the existing scheduling/task infrastructure.

Examples:

- weekly strategic review
- experiment follow-up after N days
- monthly portfolio review
- stalled experiment reminder

Do not create a custom scheduler inside the GUI.

The first implementation can be conservative: create a scheduled review mission rather than automatically taking irreversible actions.

## Phase 5 — External data adapters

Create explicit connector boundaries. Do not implement fake CRM/finance/social data.

Suggested interfaces:

```text
CustomerSource
FinancialSource
SocialAnalyticsSource
MessagingSource
PublicationSource

Each connector:
  fetch → normalize → evidence → domain state
```

Adapters should be optional and unavailable sources must be represented as unavailable.

### CRM

Need customer/account/contact lifecycle, interactions, status, next action and retention signals.

### Finance

Need revenue, direct cost, operating cost, margin and cash/budget signals with provenance and period.

### Social

Need platform, content, reach, engagement, conversion signals and time period.

### Funnel / messaging

Need leads, conversation state, qualification, CTA events and handoff state.

### Publication

Need draft/published/failed state, platform, remote ID, timestamp and idempotency key.

## Phase 6 — Reinvestment engine

Do not build autonomous capital allocation first.

First build a decision-support layer that:

1. reads verified financial evidence;
2. separates committed costs, operating reserve and experimental budget;
3. computes simple transparent ratios;
4. proposes allocations;
5. records the decision and rationale;
6. requires human approval for actual movement of money.

Never infer financial facts from content performance alone.

## Phase 7 — Strategic GUI

Once persistence exists, the Intelligence page should stop being only an action launcher and become the visible strategic state of the active business.

Add progressively:

- current objectives
- active hypotheses
- experiments and status
- recent evidence
- recent decisions
- upcoming reviews
- missing integrations/data
- portfolio view across businesses

Keep the current Business Workspace first principle.

## Multi-business acceptance test

The architecture is not considered generic until a second business can:

1. be created/selected;
2. own objectives;
3. own hypotheses and experiments;
4. launch ORBIT missions;
5. collect evidence;
6. produce decisions;
7. schedule reviews;
8. do all of the above without Podalux-specific code paths.

## Required tests

At minimum add tests for:

- persistence CRUD for each strategic object;
- business isolation;
- objective → hypothesis → experiment relationships;
- evidence provenance;
- mission context propagation;
- decision creation from mission results;
- scheduled review creation;
- missing connector behavior;
- second-business operation;
- GUI strategy action regression;
- no fabricated metrics in objectives/results.

Do not claim the tests pass unless actually executed.

## Implementation order

1. Inspect existing state/journal/tasks/business abstractions.
2. Identify the smallest persistence extension point.
3. Implement domain models + migrations.
4. Add tests.
5. Link ORBIT missions to durable strategy IDs.
6. Add evidence/provenance.
7. Add recurring review scheduling.
8. Add connector interfaces with no fake implementations.
9. Upgrade Intelligence GUI to consume persisted state.
10. Prove the complete loop with a second business.

At every step keep the repository runnable and avoid unrelated refactors.

## Definition of done

The phase is complete only when:

- a business can have a persistent long-term objective;
- that objective can produce a hypothesis;
- the hypothesis can produce an experiment;
- the experiment can launch a real ORBIT mission;
- the mission result can be stored with evidence;
- a decision can be recorded from that evidence;
- a future review can be scheduled;
- the GUI displays this state;
- another business can execute the same loop;
- tests cover the critical path;
- no unsupported data is presented as fact.
