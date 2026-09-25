# CODEX START — 2026-09-25

This is the single router for the current GPT-6 Astra maintenance session.

## 0. Gate before spending Astra quota

The human runs, from the OCTOPUS root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\codex_preflight.ps1
```

Do not begin work unless it ends with:

`READY FOR CODEX / GPT-6 ASTRA`

The gate checks Git cleanliness/main ancestry, effective project Codex feature state, Astra-compatible CLI, ChatGPT authentication, worker execpolicy, the exact live free Step route, Python/Docker and hidden global Codex context.

If the repo is not trusted yet, Codex may ignore project `.codex/` config/rules. Approve the trust prompt before the first task, exit, and rerun preflight.

## 1. Current human directive

For this bounded maintenance window:

1. establish one baseline;
2. remove the obsolete OCTOPUS video engine;
3. replace it with a thin boundary to the pinned Agnes Video Generator service;
4. integrate only useful Hermes P0 primitives, beginning with the tool registry;
5. run regression tests, review the real diff and update state truthfully.

This directive overrides older "next action" prose in snapshots. It does **not** weaken OCTOPUS evidence, secret, cost, permission, external-action or promotion boundaries.

Reference at preparation time:
- `main@ae4d98dc9692aa10ba15051381a36809e25377df`;
- branch `prep/astra-local-orchestration`, prepared from that main with zero commits behind;
- `bench/free-workers-20260925` is evidence only, never a branch to merge into this work.

Git reality always wins. If `main` moved, reconcile before product changes.

## 2. Context budget

At startup use only:
- injected root `AGENTS.md`;
- this file.

Load phase documentation only when entering that phase:
- video removal → `VIDEO_ENGINE_REMOVAL.md`;
- Agnes → `AGNES_VIDEO_REPLACEMENT.md`;
- Hermes → `OCTOPUS_HERMES_REPLACEMENT_MATRIX.md`, then `HERMES_COMPONENT_EXTRACTION.md` only for the component being touched.

Do not preload `VISION.md`, `CURRENT_STATE.md`, `HANDOFF_WORK.md` or `CODEX_18H_HANDOFF.md`. Read a relevant section only if a concrete change reaches its semantic boundary.

Do not:
- broad-audit the repository again;
- enumerate historical branches;
- re-benchmark free models;
- revisit Astra Flash Orchestrator;
- redesign OCTOPUS.

Inspect only code needed by the current phase. Nested `AGENTS.md` instructions still apply to files in their scope.

## 3. Astra vs worker

Astra owns architecture, scope, permissions/security, contracts/oracles, ambiguous failures and final diff review.

The only current cheap implementation candidate is the existing OCTOPUS DevWorker route:

`kilo/stepfun/step-3.7-flash:free`

Use it only for bounded implementation with explicit exact `allowed_paths`, deterministic tests and acceptance criteria. It is not an architecture authority.

No fallback cascade. If Step fails, drifts, produces ambiguous evidence or an untrusted diff, the task returns to Astra. No paid fallback without explicit human authorization. For OCTOPUS self-development keep `allow_declarative_fallback=false`.

Broad surgery that must remove tests/workflows or cross protected boundaries is done directly by Astra rather than weakening DevWorker guards.

## 4. HARD quota boundary — worker is out-of-band

Astra must **never** launch Kilo or `python -m octopus night-shift` from its own model-driven shell. Project execpolicy forbids the direct commands.

Reason: Codex shell/background-process supervision can require later tool/model cycles. We do not base quota safety on how long a shell call happens to block.

Delegation protocol:

1. Astra fixes the contract.
2. If a new oracle is required, Astra writes/runs/commits it first.
3. Astra writes exactly one `policy: "product_ticket"` plan to ignored `cache/astra-tickets/<ticket>.json`.
4. Astra validates the plan, prints this command with the real path, then ends its turn:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_external_dev_ticket.ps1 -Plan cache\astra-tickets\<ticket>.json
```

Final line must be exactly:

`WAITING_FOR_EXTERNAL_WORKER`

5. The **human** runs the command in a separate PowerShell. Astra is not active while Step works.
6. The human returns `WORKER_FINISHED` or the complete failure output.
7. Root Astra reviews once: report, produced commit, actual diff, changed paths and tests. Only then may it integrate, issue one new bounded ticket, or take the task back.

Never use `/goal`, Codex native subagents, auto-review, `/review`, or repeated `write_stdin`/wait calls to supervise Step.

DevWorker constraints are intentional: clean source repo, exact paths, Docker sandbox, baseline oracle, acceptance contract, radius limits, protected paths/tests and local isolated commits. Do not weaken them to make a ticket fit.

Kill switch from another terminal:
`python -m octopus night-stop`

## 5. Execution

### A — Baseline
Verify branch/HEAD/status/merge-base and run the existing relevant/full Python suite once. Record environmental failures. No architecture rediscovery.

### B — Remove legacy video
Read `VIDEO_ENGINE_REMOVAL.md`. Disconnect runtime hooks, delete the identified Remotion/RunPod/TTS/B-roll/video-worker surfaces, preserve non-video economic jobs, repair references and run the full relevant regression suite. Commit this phase separately.

### C — Agnes
Read `AGNES_VIDEO_REPLACEMENT.md`.

Source pin:
`lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b`

Fetch via:
`scripts/fetch_pinned_upstreams.ps1 agnes`

Do not rebuild/vendor/fork its media pipeline. Agnes runs independently; OCTOPUS gets only the narrow adapter/probe needed by the current workflow. Keep `AGNES_API_KEY` in the Agnes process, bind the local service to loopback, and make repository tests deterministic/no-live-generation. A real smoke test requires explicit human authorization.

### D — Hermes P0
Read the replacement matrix, then only the needed extraction section.

Source pin:
`NousResearch/hermes-agent@59004a62356f3a4697ab0fe8ad5086d2b405e2a6`

Fetch via:
`scripts/fetch_pinned_upstreams.ps1 hermes`

Start with the tool registry because it has a concrete replacement target. For every port require: exact code replaced or bounded missing capability, preserved interface, tests, permission consequences, attribution if derived, and net complexity reduction.

Do not import Hermes loop, planner, persona, memory, LLM router or full orchestration. Do not create a second planner/router/journal/permission/evidence authority. P1 work is out of scope unless the human explicitly extends scope after P0 is clean.

## 6. Stop conditions

Stop and ask the human rather than exploring when:
- a required credential/account capability is absent;
- a live call would spend money or create an unapproved external effect;
- tests cannot distinguish correctness;
- an upstream pin materially contradicts the prepared contract;
- a Hermes port creates a parallel authority instead of replacing/bridging one;
- main moved in a way that changes the prepared scope.

## 7. Done

Prefer a smaller correct result over a sprawling migration.

Minimum useful session result:
- legacy OCTOPUS video runtime removed without shared-core regression;
- pinned Agnes service boundary + minimal tested OCTOPUS integration;
- first useful Hermes P0 replacement integrated, **or** code-based proof that keeping OCTOPUS is simpler;
- executed test evidence;
- reviewed diff;
- truthful current-state docs;
- no automatic merge to `main`.

Never equate infrastructure completion with economic success.
