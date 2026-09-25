# Astra local orchestration for OCTOPUS

Date: 2026-09-25

## Decision

Do **not** install or load `ethanplusai/astra-flash-orchestrator` as a global Codex skill or policy.

OCTOPUS already owns the implementation-worker boundary in `octopus/dev_worker.py`.
The root GPT-6 Astra session remains the planner, architect, security reviewer and final integrator.
Bounded implementation may be delegated through OCTOPUS `development.task` only when doing so preserves quality.

This is **quality-first, free-when-qualified**, not free-at-any-cost.

## Canonical path

```text
GPT-6 Astra root
  -> inspect repo + decide architecture/security/contracts
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
It launches Astra with \`codex exec --json\`, records the exact \`thread_id\`, consumes a bounded relay request, runs Step through \`run_external_dev_ticket.ps1\`, then performs exactly one \`codex exec resume <thread_id>\` for Astra review.

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
- verifies the resumed Codex \`thread_id\` equals the original;
- caps Astra turns and relay cycles;
- stops on any non-zero Codex call;
- does not select a fallback model.

Astra then reviews the actual worker result/diff/tests and either integrates, takes back, delegates one further bounded task, or completes.

This preserves the intended shape:

\`\`\`text
Astra plans/decides
  -> deterministic relay
  -> Step implements/tests
  -> deterministic relay
  -> same Astra thread reviews
\`\`\`

There is no human polling step and no resident LLM supervisor.
