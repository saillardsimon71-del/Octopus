# CODEX START — 2026-09-25 — OCTOPUS maintenance window

## Mandatory gate before opening Codex

From the OCTOPUS repository root, the human must first run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\codex_preflight.ps1
```

Do not start the GPT-6 Astra session unless the script ends with:

```text
READY FOR CODEX / GPT-6 ASTRA
```

This gate verifies the prepared branch/clean tree/main ancestry, project Codex policy, Astra-compatible CLI version, ChatGPT sign-in, the exact live free Step route, Python, Docker, and absence of active Astra Flash Orchestrator residue. A failure is a setup issue to fix before spending Astra allowance.

This file is the **single entry point for the current Codex/Astra session**.
It records the human operator's explicit current directive. Do not spend model quota rediscovering the roadmap from historical branches or old conversation artifacts.

## Current directive

For this session only, the operator explicitly authorizes a bounded maintenance/migration window:

1. reconcile the preparation branch with the actual repository state;
2. remove the obsolete OCTOPUS video engine cleanly;
3. replace the legacy video runtime with a thin boundary to the pinned Agnes Video Generator service;
4. integrate only the Hermes P0 primitives that replace duplication or establish a needed bounded capability, starting with the tool registry;
5. finish with tests, diff review and a truthful current-state update.

This directive does **not** weaken evidence, spending, permission, secret, action or promotion boundaries.
It does not make infrastructure work an economic success signal.

Do not debate whether the maintenance window is the commercial priority. The human has already decided it is today's bounded prerequisite.

## Verified repository facts

At preparation time:

- protected reference: `main@ae4d98dc9692aa10ba15051381a36809e25377df`;
- working branch: `prep/astra-local-orchestration`;
- that branch is based directly on the current main: ahead, zero commits behind;
- benchmark branch: `bench/free-workers-20260925` is evidence only and is **not** a dependency to merge;
- third-party global `astra-flash-orchestrator` installation was removed from the operator's Codex environment;
- OCTOPUS already contains `octopus/dev_worker.py` / `development.task`; do not build a second orchestrator.

Verify Git state with commands. If main moved after this file was written, reconcile normally before product changes.

## Context budget — progressive disclosure

At session start, read only:
1. root `AGENTS.md` (Codex normally injects it automatically);
2. this file.

Then load the one document needed for the phase actually being executed:
- Phase B: `docs/migrations/VIDEO_ENGINE_REMOVAL.md`;
- Phase C: `docs/migrations/AGNES_VIDEO_REPLACEMENT.md`;
- Phase D: `docs/migrations/OCTOPUS_HERMES_REPLACEMENT_MATRIX.md`, then `HERMES_COMPONENT_EXTRACTION.md` only for the component being integrated.

`CODEX_18H_HANDOFF.md` is a preparation record, not required reading for execution.
`docs/CURRENT_STATE.md`, `docs/VISION.md` and `docs/HANDOFF_WORK.md` remain background sources for economic semantics; read only the relevant section if a concrete change touches that semantic boundary.

Do not preload the whole migration packet.

Do not perform a broad repository audit before starting. The preparation audit has already been done; inspect only code required by the current phase.
Do not enumerate every historical branch.
Do not re-benchmark free models.
Do not inspect Astra Flash Orchestrator again.
Do not redesign OCTOPUS.

When a concrete code reference in a migration document is stale, inspect only the directly relevant files and repair the plan from Git reality.

## Model/quota discipline

GPT-6 Astra owns:
- architecture and scope decisions;
- security/permission/trust-boundary changes;
- test/oracle design;
- final review and integration;
- ambiguous failures.

Use the existing OCTOPUS Kilo worker only for bounded implementation when tests already exist.

Current worker route in `octopus/dev_worker.py`:
`kilo/stepfun/step-3.7-flash:free`.

Rules:
- no silent fallback cascade;
- `allow_declarative_fallback=false`;
- no other free model merely because it is free;
- no paid fallback without explicit human authorization;
- worker result is only ready-for-review;
- Astra reviews the actual diff and test evidence before integration.

### Important DevWorker mechanics

Do not waste time fighting these intentional gates:

- `night-shift` preflight requires the source repository to be clean;
- product code delegation must use `policy: "product_ticket"`;
- `product_ticket` cannot modify test files or protected trust-boundary files;
- therefore Astra must create/commit deterministic tests **before** delegating implementation that relies on new tests;
- `allowed_paths` are exact file paths, not broad directory wildcards;
- product tickets require an explicit acceptance contract, Docker test sandbox, baseline oracle, radius limits and no declarative fallback;
- worker changes land in isolated task/night-shift worktrees and local commits; they are not automatically merged into the current branch;
- review/cherry-pick only after inspecting the produced commit;
- do not poll Kilo from Astra. Launch the blocking local command, let it finish, then review once.

Kill switch:
`python -m octopus night-stop`
Resume:
`python -m octopus night-resume`

For broad deletions that require changing/removing tests, workflows and many exact paths, Astra should perform the surgery directly rather than forcing it through `product_ticket`.

## Execution order

### A — Baseline

Only:
- branch/HEAD/status;
- confirm merge-base with main;
- run the existing relevant/full Python suite once;
- record environmental failures truthfully.

Do not start with architecture analysis.

### B — Remove legacy video engine

Use `VIDEO_ENGINE_REMOVAL.md`.

This is broad surgery and may require test/workflow deletion, so Astra may perform it directly.

Goals:
- disconnect runtime hooks;
- delete legacy Remotion/RunPod/TTS/B-roll/video-worker surfaces identified by the removal plan;
- preserve non-video economic jobs;
- repair stale imports;
- search the listed legacy reference strings;
- full regression suite.

Commit this phase separately before the next one.

### C — Agnes upstream engine

Use `AGNES_VIDEO_REPLACEMENT.md`.

The engine is **not** to be rebuilt in OCTOPUS. Fetch it with `scripts/fetch_pinned_upstreams.ps1 agnes`, then use the pinned MIT upstream:
`lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b`.

Treat Agnes as an independently running local service and build only the narrow OCTOPUS HTTP adapter/probe required by the current workflow. The upstream service already owns video API protocol, rate limiting/retries, media pipelines, TTS/subtitles/composition and UI.

Do not vendor the whole upstream repo, create a Git submodule, or fork/copy its internals during this first migration unless a concrete incompatibility forces that decision.

Astra should write/commit deterministic adapter tests before delegating bounded adapter implementation to Step.

Do not perform a live generation during tests. A real smoke test requires explicit human authorization and a configured Agnes key.

Keep `AGNES_API_KEY` in the Agnes process environment; never store it in OCTOPUS Git or send it to an OCTOPUS browser UI. Force the Agnes service to `HOST=127.0.0.1`; the pinned upstream defaults to `0.0.0.0`, which is not acceptable for this local secret-bearing service.

### D — Hermes P0, one replacement at a time

Pinned source (fetch with `scripts/fetch_pinned_upstreams.ps1 hermes`):
`NousResearch/hermes-agent@59004a62356f3a4697ab0fe8ad5086d2b405e2a6`.

Verified upstream files at that exact pin:
- `tools/registry.py`
- `tools/computer_use_tool.py`
- `agent/transports/hermes_tools_mcp_server.py`
- `agent/tool_guardrails.py`
- `agent/verification_evidence.py`
- P1 references also exist, but P1 is not today's starting point.

Start with **tool registry** because it has a concrete OCTOPUS replacement target.
Do not import Hermes's loop, planner, persona, memory, LLM router or full orchestration.

For each Hermes component, require:
1. exact OCTOPUS code it replaces or concrete missing bounded capability;
2. interface to preserve;
3. tests;
4. permission consequences;
5. source attribution if code is substantially derived;
6. net complexity assessment.

If it merely adds a parallel abstraction, stop and keep OCTOPUS.

After registry is clean, proceed to MCP/cua-driver only if it can be integrated without creating a second authority and the relevant tests can be stated first.

Known upstream facts for computer-use: Hermes uses cua-driver over MCP/stdio; Windows is supported without an install-time OS permission grant; upstream sanitizes the cua-driver child environment and disables its telemetry. Preserve those security properties if adapting the backend. Do not run an upstream auto-installer implicitly from OCTOPUS.
Guardrails/evidence follow the existing OCTOPUS capability/journal authorities; never create a second permission or evidence database.

Do not start scheduler, retry taxonomy, skills, memory or subagent-lifecycle work in this session unless all P0 work is already clean and the human explicitly extends scope.

## Agnes source already checked

Pinned upstream `lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b` already exposes a local REST service. Relevant documented endpoints include:
- `POST /api/tasks/simple`
- `POST /api/tasks/creative`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/stop`
- `POST /api/tasks/{task_id}/resume`
- `GET /api/video/{task_id}`
- `GET /api/tasks/{task_id}/artifacts`

At that pin, upstream already handles Agnes v2.0/2.5 protocol differences, hosted-reference upload with Base64 fallback, retry/rate limiting, polling and completed-video URL extraction. Do not duplicate those concerns inside OCTOPUS.

Still runtime-dependent:
- whether the pinned service starts cleanly in this Windows environment;
- whether the operator's Agnes key is valid and entitled to the selected model;
- live generation behavior.

Treat these as smoke-test facts, not reasons to redesign the adapter.

## Stop conditions

Stop and return to the human instead of exploring when:
- a required external credential/account capability is missing;
- the pinned Hermes source contradicts the migration map materially;
- tests cannot distinguish a correct implementation;
- a proposed Hermes port creates a second authority;
- a live external call would spend money or create side effects;
- main moved in a way that materially changes the prepared scope.

## Definition of done for this session

Prefer a smaller correct result over a sprawling migration.

Minimum useful result:
- obsolete video engine removed without shared-core regression;
- pinned Agnes engine validated as an independent service and a thin OCTOPUS adapter present with deterministic tests;
- one real Hermes P0 replacement (registry) integrated cleanly, or a precise code-based proof that keeping the current implementation is simpler;
- tests executed;
- diff reviewed;
- no automatic merge to main;
- current-state documentation updated to what is actually true.
