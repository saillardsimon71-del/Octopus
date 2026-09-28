# Astra local orchestration for OCTOPUS

Date: 2026-09-25

Current execution brief: `docs/migrations/CODEX_START_2026-09-27.md`. This file
continues to define the Astra/Step ownership and relay mechanics; its original
phase B/C/D scope is historical.

## Decision

Do **not** install or load `ethanplusai/astra-flash-orchestrator` as a global Codex skill or policy.

OCTOPUS already owns the implementation-worker boundary in `octopus/dev_worker.py`.
The root GPT-6 Astra session remains the planner, architect, security reviewer and final integrator.
Bounded implementation may be delegated through OCTOPUS `development.task` only when doing so preserves quality.

This is **quality-first, free-when-qualified**, not free-at-any-cost.

## Canonical path

```text
Astra reads and searches the local repository directly with bounded output
  -> Astra defines a product ticket with exact edit paths and pytest targets
  -> host validates the ticket, branch, HEAD, paths, tests and budgets
  -> Step implements in an isolated worktree and runs the approved tests
  -> host validates the compact receipt and actual commit diff
  -> fresh Astra call reviews the diff, requests a checkpoint or continues
```

A zero-result or multi-result local search remains ordinary evidence for Astra.
The host does not prepare phase-specific symbol or handler lookups.

No global `SKILL.md`, no global Codex `AGENTS.md` injection, no second planner, no second journal and no external orchestration package.

## Current worker

At this revision, `octopus/dev_worker.py` pins:

```text
kilo/stepfun/step-3.7-flash:free
```

Step 3.7 is a **candidate implementation worker**, not an authority.

The free-worker benchmark is evidence for bounded repair ability only. It does not authorize delegating architecture, security, permissions, economic policy or ambiguous multi-system design.

## Routing rule

Delegate only if ALL are true:

1. Astra has already fixed the contract and acceptance criteria.
2. The writable file set is explicit and small enough for `development.task`.
3. Deterministic tests exist and are relevant.
4. The task does not require changing an OCTOPUS protected trust boundary.
5. A wrong implementation is detectable by tests + Astra diff review.
6. No provider spending is required.

Otherwise Astra implements/reasons directly.

### No fallback cascade

If Step:
- fails to run;
- exceeds its task budget;
- changes the wrong scope;
- fails tests;
- returns ambiguous evidence;
- produces a diff Astra does not trust;

then the task returns to **Astra**.

Do not silently try Nemotron, Laguna, Hy3, MiniMax, another free model, a paid model, or a declarative fallback merely because it is available.

For OCTOPUS self-development, keep `allow_declarative_fallback=false`.

## Ownership split for the 2026-09-25 Hermes + Agnes session

Astra owns:
- baseline and branch correctness;
- deletion boundaries for the old video engine;
- Hermes-versus-OCTOPUS architectural decisions;
- capability/security/permission boundaries;
- MCP trust boundary;
- computer-use consequence policy;
- evidence semantics;
- final integration and regression analysis.

A bounded Step worker may be used for:
- mechanical deletion after Astra identifies the exact list;
- import/reference cleanup with explicit paths and tests;
- implementing a bounded OCTOPUS HTTP adapter to the pinned Agnes service after Astra fixes the contract;
- narrowly scoped adapter code after Astra fixes interfaces;
- targeted regression repairs.

Do not delegate an entire “integrate Hermes” or “make OCTOPUS ready” goal.

## Existing OCTOPUS protections to preserve

`development.task` already provides:
- isolated Git task worktree;
- explicit `allowed_paths` for Kilo;
- sensitive paths denied, including env files, credentials, SSH/AWS/Docker secrets and Git internals;
- no worker shell, nested agent, MCP, web search or external directory access;
- protected OCTOPUS governance paths;
- explicit pytest commands;
- radius limits;
- strict repository preflight;
- optional baseline oracle;
- acceptance contracts for product tickets;
- no implicit paid fallback.

Do not duplicate these controls in a second orchestrator.

## Astra review rule

Worker completion means only `ready_for_review`.

Astra must inspect:
- the actual changed paths;
- the actual diff;
- test results;
- any untracked additions;
- security/permission effects;
- whether acceptance criteria were truly met.

A green test is not enough if the contract or architecture is wrong.

## Source branches

- migration preparation: `prep/hermes-agnes-migration`
- free-worker evidence: `bench/free-workers-20260925`

The benchmark branch is evidence, not a runtime dependency.

## Execution mechanics that Astra must know before delegating

### Hard boundary: automatic external relay

Long Kilo/Step work must remain outside the Codex model process.

The supported supervisor is \`scripts/start_octopus_astra.ps1\`.
It launches Astra with \`codex exec --json\`, consumes a bounded relay request, runs Step through \`run_external_dev_ticket.ps1\`, then starts one fresh Astra call for review when the call budget allows. Continuity is recorded in \`cache/astra-relay/handoff.json\`.

No model call is active while Step works.

Astra never launches Kilo/night-shift/runner itself.
The execpolicy is defense in depth, not the primary boundary.

Relay request:

```json
{
  "version": 1,
  "request_id": "unique-id",
  "phase": "G",
  "base_head": "current-full-commit-sha",
  "product_ticket": "cache/astra-tickets/task.json",
  "hours": 1.0
}
```

The product ticket supplies the objective, exact repository-relative `allowed_edit_paths`,
existing `test_targets`, acceptance criteria and work limits. The host validates these
when the request is dispatched. The Step product policy still forbids editing test
oracles and protected governance paths. Astra may create and checkpoint an oracle
before delegation when none exists.

The runner:
- requires a clean source repository;
- records SHA-256 of the plan;
- refuses accidental replay of the same plan;
- records logs/result/night-shift report;
- leaves worker commits isolated for review.

The supervisor:
- caps fresh Astra calls (two by default) and relay cycles;
- runs requested full pytest validation outside the model from a fixed \`full_pytest\` request, with a compact result file;
- records token usage per call, \`run_totals\` for the current launcher run and cumulative \`lifetime_totals\` in \`cache/astra-relay/usage.json\`;
- runs each Codex call with \`--ephemeral\`;
- stops on any non-zero Codex call;
- does not select a fallback model.

## Compact context protocol

Each Astra call receives one `HOST_CONTEXT_JSON` packet with objective, phase,
branch, HEAD, compact handoff, and any Step or validation receipt. Only the first
call includes the baseline status. The host does not inject a repository snapshot,
context manifest, phase search results or raw test logs.

Astra can make several relevant local reads and searches during one call. Each
command output is limited to 200 lines or 20000 characters. Long tests and external
network access are disabled in Astra. Full pytest runs through a fixed host
validation request. Publishing a Step, checkpoint or validation request ends the
turn; the host processes it and starts a fresh review call.

Astra supplies `ASTRA_STATE_JSON` with concise facts, hypotheses, inspected paths,
tests, ticket IDs and remaining criteria. The host hashes inspected files and
returns `changed_since_inspection` in the next packet. Astra rereads changed
files and marks their entries `refresh=true`; unchanged excerpts stay cached.
The host continues automatically unless Astra
declares a terminal state; `ASTRA_CONTINUE` may name the next local action.
`ASTRA_STATUS: STABLE` and `ASTRA_STATUS: BLOCKED` are terminal decisions.
`STABLE` requires an integrated Step checkpoint and passing full pytest on
the current HEAD; the host requests that validation automatically if needed.
The next call begins automatically, including after a 2/2 mini-session, until
`MaxRelayCycles` is reached.

The handoff is capped at 6000 characters, the context packet at 8000, and the
prepared context including root `AGENTS.md` at 30000. Run-level minute and
reported-token budgets are checked before each model call. The host records
character counts and actual model usage. These run-level checks do not interrupt
an individual call already in progress.

The host keeps the existing hard boundaries: authorized branch and HEAD, clean
source tree for Step, explicit edit paths and test targets, no push or merge,
no external economic action, bounded worker scope, and verified diff review.
There is no human polling step and no resident LLM supervisor.
