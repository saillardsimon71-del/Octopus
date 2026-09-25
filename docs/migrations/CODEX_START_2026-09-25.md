# CODEX START — 2026-09-25

Single execution router for the current OCTOPUS construction window.

## 0. How this session is launched

The supported entry point is:

\`scripts/start_octopus_astra.ps1\`

It uses a dedicated \`CODEX_HOME\`, runs the local preflight, launches GPT-6 Astra through \`codex exec --json --strict-config --model gpt-6-astra\`, captures the exact Codex \`thread_id\`, and resumes that same thread after bounded external workers.

Do not replace this with an ad-hoc interactive Codex session for the current migration.

Current supervisor defaults:
- max Astra turns: 8;
- max Step relay cycles: 6;
- exact thread-id verification on every resume;
- any non-zero Codex exit stops the supervisor;
- any worker result is reviewed by root Astra before integration.

\`codex exec\` is deliberately useful here: in the audited client it is non-interactive and forces approval policy \`never\`, so sandbox/escalation failures are returned to Astra instead of opening hidden approval/reviewer loops.

## 1. Current directive

For this bounded maintenance window:

1. establish one baseline;
2. remove the obsolete OCTOPUS video engine;
3. replace it with a thin boundary to the pinned Agnes Video Generator service;
4. integrate only useful Hermes P0 primitives, beginning with the tool registry;
5. run regression tests, review the real diff and update state truthfully.

This directive overrides older "next action" prose in snapshots. It does **not** weaken OCTOPUS evidence, secret, cost, permission, external-action or promotion boundaries.

Reference at preparation time:
- protected reference: \`main@ae4d98dc9692aa10ba15051381a36809e25377df\`;
- working branch: \`prep/astra-local-orchestration\`;
- benchmark branch \`bench/free-workers-20260925\` is evidence only and is never merged as a dependency.

Git reality wins. If main moved materially, stop/reconcile before product changes.

## 2. Context budget

At startup use only:
- injected root \`AGENTS.md\`;
- this file.

Load phase documents only on entry to that phase:
- video removal → \`VIDEO_ENGINE_REMOVAL.md\`;
- Agnes → \`AGNES_VIDEO_REPLACEMENT.md\`;
- Hermes → \`OCTOPUS_HERMES_REPLACEMENT_MATRIX.md\`, then only the relevant section of \`HERMES_COMPONENT_EXTRACTION.md\`.

Do not preload \`VISION.md\`, \`CURRENT_STATE.md\`, \`HANDOFF_WORK.md\` or \`CODEX_18H_HANDOFF.md\`.
Read a relevant section only if a concrete modification reaches that semantic boundary.

Do not:
- broad-audit the repo again;
- enumerate historical branches;
- re-benchmark free models;
- revisit Astra Flash Orchestrator;
- redesign OCTOPUS.

Inspect only code needed by the current phase. Nested \`AGENTS.md\` still applies to files in its scope.

## 3. Astra vs Step

GPT-6 Astra owns:
- architecture and scope;
- interfaces/contracts;
- permissions/security/trust boundaries;
- oracle/test design;
- ambiguous failures;
- final diff/evidence review.

Current bounded implementation worker:

\`kilo/stepfun/step-3.7-flash:free\`

Step is not an architecture authority.

Delegate only when:
- contract and acceptance criteria are already fixed by Astra;
- exact writable paths are small and explicit;
- deterministic relevant tests exist;
- protected trust boundaries are not being modified;
- a wrong implementation is detectable by tests + Astra review.

No fallback cascade.
If Step fails, drifts, times out, changes wrong scope or returns ambiguous evidence, the task returns to Astra.
No implicit paid fallback.
Keep \`allow_declarative_fallback=false\`.

Broad surgery that must remove tests/workflows or cross protected paths is done directly by Astra instead of weakening DevWorker protections.

## 4. Automatic quota-safe relay

Astra must **never** launch Kilo, \`night-shift\`, or the external worker runner itself from its model shell.

The project execpolicy blocks the direct common commands as defense in depth. It is not treated as an absolute security boundary; the canonical boundary is architectural: only the deterministic parent supervisor launches Step.

When Astra wants to delegate:

1. Fix the contract.
2. If a new oracle is needed, write/run/commit it first so the source repo is clean.
3. Write exactly one \`policy: "product_ticket"\` plan under:
   \`cache/astra-tickets/<ticket>.json\`
4. Atomically publish:
   \`cache/astra-relay/request.json\`
5. End the current Astra turn immediately.

Relay request schema:

\`\`\`json
{
  "version": 1,
  "request_id": "short-unique-id",
  "plan_path": "cache/astra-tickets/example.json",
  "hours": 1.0
}
\`\`\`

Publish atomically: write a temporary file in the same directory, then rename/move it to \`request.json\`. Never partially edit a live request.

The deterministic supervisor then:

\`\`\`text
Astra turn ends
  -> consumes request.json
  -> runs run_external_dev_ticket.ps1
  -> OCTOPUS night-shift / DevWorker
  -> Step 3.7 works + OCTOPUS tests/acceptance
  -> receipt/result written
  -> one codex exec resume <EXACT_THREAD_ID>
  -> root Astra reviews
\`\`\`

While Step works there are **zero Astra model turns**.

The supervisor never decides architecture or correctness. It only:
- validates/consumes a request;
- runs the bounded worker;
- records exit/result;
- resumes the exact recorded Astra thread once.

It refuses accidental replay of the same plan by SHA-256 receipt unless an operator explicitly overrides it.

A resumed Astra turn must inspect:
- worker result receipt;
- night-shift report/log;
- produced commit/worktree;
- actual diff;
- changed paths;
- tests and acceptance evidence.

Then Astra either:
- integrates the reviewed result;
- takes the task back itself;
- emits one new bounded relay request;
- or completes/stops at a human boundary.

Do not use:
- Codex native subagents;
- Goals;
- \`/review\`;
- auto-review;
- model-side sleep/polling;
- repeated \`write_stdin\` waits;
- direct worker execution.

## 5. Model/quality fail-closed behavior

The constructor is launched with exact \`--model gpt-6-astra\` and project config also pins \`gpt-6-astra\`.

The audited Luna Reserve automatic-switch path is implemented in the interactive TUI; this constructor uses non-interactive \`codex exec\` instead. Do not add a fallback model to the supervisor.

If a Codex exec/resume call is rejected, rate-limited or exits non-zero, the supervisor stops. It must never compensate by selecting Luna, another Codex model or an API-billed provider.

The dedicated Codex home is ChatGPT-authenticated and the preflight rejects OpenAI API/provider overrides.

## 6. Execution phases

### A — Baseline

Verify branch/HEAD/status/merge-base and run the existing relevant/full Python suite once.
Record environmental failures truthfully.
No architecture rediscovery.

### B — Remove legacy video

Read \`VIDEO_ENGINE_REMOVAL.md\`.

Disconnect runtime hooks, delete the identified Remotion/RunPod/TTS/B-roll/video-worker surfaces, preserve non-video economic jobs, repair references and run the relevant/full regression suite.

This is broad surgery; Astra may do it directly.

Commit the phase separately.

### C — Agnes

Read \`AGNES_VIDEO_REPLACEMENT.md\`.

Pinned source:
\`lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b\`

Preflight has already fetched it under:
\`cache/upstreams/agnes-video-generator\`

Do not rebuild/vendor/fork its media pipeline.
Agnes remains an independent local service; OCTOPUS gets only the narrow adapter/probe needed by the current workflow.

Keep \`AGNES_API_KEY\` inside the Agnes process/container.
Bind the service to loopback.
Repository tests are deterministic and make no live generation call.
A real generation smoke test requires explicit human authorization.

### D — Hermes P0

Read the replacement matrix, then only the needed extraction section.

Pinned source:
\`NousResearch/hermes-agent@59004a62356f3a4697ab0fe8ad5086d2b405e2a6\`

Preflight has already fetched it under:
\`cache/upstreams/hermes-agent\`

Start with tool registry because it has a concrete replacement target.

For every Hermes component require:
1. exact OCTOPUS code replaced or concrete bounded missing capability;
2. stable interface;
3. tests;
4. permission/security consequences;
5. attribution if substantially derived;
6. net complexity reduction.

Do not import Hermes loop, planner, persona, general memory, LLM router or full orchestration.
Do not create a second planner/router/journal/permission/evidence authority.

P1 work stays out of scope unless P0 is clean and the human explicitly extends scope.

## 7. Stop conditions

Stop rather than explore when:
- a required credential/account capability is absent;
- a live call would spend money or create an unapproved external effect;
- tests cannot distinguish correctness;
- an upstream pin materially contradicts the prepared contract;
- a Hermes port creates parallel authority rather than replacement/bridge;
- main moved materially;
- Codex/Astra is rate-limited or returns non-zero.

## 8. Done

Prefer a smaller correct result over a sprawling migration.

Minimum useful result:
- legacy video runtime removed without shared-core regression;
- pinned Agnes service boundary + minimal tested OCTOPUS integration;
- first useful Hermes P0 replacement integrated, or code-based proof current OCTOPUS is simpler;
- executed test evidence;
- Astra-reviewed diff;
- truthful state docs;
- no automatic merge to \`main\`.

Never equate infrastructure completion with economic success.
