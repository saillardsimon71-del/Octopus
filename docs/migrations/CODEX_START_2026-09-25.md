# CODEX START — 2026-09-25 — OCTOPUS maintenance window

This file is the **single entry point for the current Codex/Astra session**.
It records the human operator's explicit current directive. Do not spend model quota rediscovering the roadmap from historical branches or old conversation artifacts.

## Current directive

For this session only, the operator explicitly authorizes a bounded maintenance/migration window:

1. reconcile the preparation branch with the actual repository state;
2. remove the obsolete OCTOPUS video engine cleanly;
3. add the standalone Agnes video workshop;
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

## Read budget

Read these files first, in this order:

1. `AGENTS.md`
2. this file
3. `docs/migrations/CODEX_18H_HANDOFF.md`
4. `docs/migrations/VIDEO_ENGINE_REMOVAL.md`
5. `docs/migrations/AGNES_VIDEO_REPLACEMENT.md`
6. `docs/migrations/OCTOPUS_HERMES_REPLACEMENT_MATRIX.md`

Read `docs/migrations/HERMES_COMPONENT_EXTRACTION.md` only when starting Hermes work.

`docs/CURRENT_STATE.md`, `docs/VISION.md` and `docs/HANDOFF_WORK.md` remain canonical background for economic truth, but their older "next action" text is **not** the priority authority for this explicitly authorized maintenance window.

Do not perform a broad repository audit before starting.
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

### C — Agnes standalone workshop

Target:
`apps/agnes-video/index.html`

Use `AGNES_VIDEO_REPLACEMENT.md`.

Astra should first add/commit a small deterministic static test if none exists, then may delegate the single implementation file to Step with exact `allowed_paths`.

Do not perform a live paid generation during tests.

Security scope for V1:
- this no-backend build is a **local/private operator tool**, not a public multi-user deployment;
- do not claim that exposing/saving a personal Agnes API key in a public browser application is secure;
- do not add a backend today merely to solve production secret management;
- leave public deployment hardening for a separate explicit task.

API target for this migration remains intentionally `agnes-video-v2.0`.
Do not spontaneously migrate to 2.5.

### D — Hermes P0, one replacement at a time

Pinned source:
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

## Agnes facts already checked

The public Agnes material available on 2026-09-25 supports:
- `POST https://apihub.agnes-ai.com/v1/videos`;
- Bearer authentication;
- legacy `agnes-video-v2.0` remains documented;
- polling by returned `video_id` through `/agnesapi?video_id=...`;
- optional `model_name=agnes-video-v2.0`;
- v2.0 `num_frames <= 441` with the `8n+1` constraint;
- public reference rate for default/free video access: 1 actual RPM.

Agnes also has newer 2.5 video models. That is not a reason to change this migration contract.

Still unverified without a real account/key:
- browser CORS behavior for the standalone local page;
- exact live response JSON fields for this account;
- account-specific quota/entitlement;
- whether the operator's key accepts v2.0 today;
- whether v2.0 video accepts the transferred Data URI image form; public docs currently show a reference URL.

Treat those as runtime facts to test later, not reasons for speculative implementation.

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
- Agnes standalone implementation present with static tests;
- one real Hermes P0 replacement (registry) integrated cleanly, or a precise code-based proof that keeping the current implementation is simpler;
- tests executed;
- diff reviewed;
- no automatic merge to main;
- current-state documentation updated to what is actually true.
