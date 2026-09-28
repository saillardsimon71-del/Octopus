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
host prepare
  -> bounded Git state, phase-targeted searches, runtime paths, symbols and likely tests
GPT-6 Astra root
  -> decide architecture/security/contracts from the prepared packet
  -> create a bounded development.task contract
  -> OCTOPUS DevWorker
       -> isolated worktree
       -> explicit allowed_paths
       -> secrets/config/Git internals denied
       -> Kilo worker pinned by OCTOPUS
       -> OCTOPUS runs the approved tests
       -> scope/radius/security validation
       -> local task commit in isolated worktree
  -> Astra reviews actual diff + evidence
  -> Astra accepts, requests one targeted correction, or takes the task back
```

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

\`\`\`json
{
  "version": 1,
  "request_id": "unique-id",
  "plan_path": "cache/astra-tickets/task.json",
  "hours": 1.0
}
\`\`\`

The plan must use \`policy: product_ticket\`.
New deterministic tests/oracles must be created and committed by Astra before delegation because product tickets cannot modify tests/protected trust-boundary files.

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

Before CALL 1, the host deterministically writes and injects:

- \`snapshot.json\`: objective, branch, HEAD, dirty paths, compact baseline status and the current review range;
- \`context-manifest.json\`: path, SHA-256, size, role and changed status for each possible reference document;
- \`handoff.json\`: decisions, modified files, bounded test facts, pending host requests, blocker and next decision;
- \`context-metrics.json\`: character counts for the prompt, snapshot, manifest, handoff and injected root instructions.

For CALL 1, \`snapshot.json\` also contains HOST PREPARE: up to three predefined phase searches with two matches each, up to eight runtime paths, five symbols/entrypoints and eight likely tests discovered from source names and targeted test searches. Individual search results are capped at 220 characters and the HOST PREPARE object at 4200 characters.

The hard limits are 6000 characters for the handoff, 8000 for the snapshot, 6000 for the manifest, 4200 for HOST PREPARE and 30000 for the prepared context including root \`AGENTS.md\`. Exceeding a limit stops before an Astra call.

The phase document is not preloaded. Root \`AGENTS.md\` is already injected by Codex and must not be reread. An unchanged document is not read again automatically. If a missing fact requires source inspection, Astra names that fact and uses one targeted section. Astra does not read raw JSONL, full pytest logs, night-shift reports, generated files or lockfiles.

CALL 2 does not repeat CALL 1 discovery and does not inject the context manifest or handoff as separate blocks. Its single review packet contains only current HEAD/branch, the previous decision, changed files, compact Step summary, compact host-test summary, diff summary, review kind and blocker. A validation receipt contains exit code, passed/failed/skipped counts, duration and at most eight failure lines capped at 240 characters. Raw pytest output is never injected.

The Astra command budget is zero by default. Broad repository searches, recursive discovery, manual test discovery, full-file reads and long tests are forbidden. One targeted search or bounded excerpt is allowed only for a named missing fact. As soon as files, behavior, oracle/tests and limits form a mechanical contract, Astra publishes the Step ticket and ends the turn. Publishing a Step, checkpoint or validation request always ends the turn immediately.

Astra then reviews the actual worker result/diff/tests and either integrates, takes back, delegates one further bounded task, or completes.

This preserves the intended shape:

\`\`\`text
host prepares bounded evidence
  -> Astra plans/decides
  -> deterministic relay
  -> Step implements/tests
  -> host records bounded test/diff receipts
  -> deterministic relay
  -> fresh Astra call reviews from compact handoff
\`\`\`

There is no human polling step and no resident LLM supervisor.
