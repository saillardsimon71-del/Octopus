# CLAUDE — handoff for `feat/cloud-video-foundation`

## Current state

This branch is the active cloud-video migration branch. Do not restart the architecture from scratch and do not rewrite `agents/runtime.py` unless there is a concrete regression requiring it.

The branch includes the cloud-first control-plane/video foundation, the integrated Chromium browser guard, OmniRoute routing, MiniMax H3 cloud routing, local diagnostics, one-command Windows bootstrap, artifact checksum verification, CI coverage, an optional Orca development bridge, and a unified entrepreneurial desktop Workbench. Do not claim CI is green until a fresh run passes.

Orca is deliberately scoped to repository-development work: it is invoked only through its public CLI and remains disabled unless `OCTOPUS_ORCA_ENABLED=1` is set. It must not replace the Podalux business runtime, `octopus.tasks`, or RunPod video execution.

### Autonomous-business foundation (branch `feat/autonomous-business-foundation`, 2026-09-17, uncommitted working tree)

Implemented and tested locally (see `docs/AUTONOMOUS_SESSION_REPORT.md`, `docs/AUTONOMOUS_WORK_LOG.md`):
- `octopus/strategy.py` + journal migration v5: objectives, hypotheses, experiments, decisions, reviews, evidence and links in `octopus.db`, strict business isolation, checked transitions, `strategy.*` events. Evidence nature is explicit (see economic loop below). Only `actor="human"` approves a decision that commits money (`spend_amount`); other decisions can be approved by a policy or an agent. Numbers are evidence values with a nature, never free metric columns.
- `orbit.mission` task (any business, optional objective/hypothesis/experiment context) and `strategy.review` task (no LLM, one-off via `strategy.schedule_review`, recurring via `octopus schedule`).
- Business propagation: `run_agent`/`run_mission` take `business` (default: enclosing run, else `podalux`); LLM costs follow the current run. `render_offer` is refused outside `podalux`; ORBIT plans are capped at 5 subtasks.
- `octopus/connectors.py`: every external domain is "non configuré" until a probe is registered. Nothing is simulated.
- CLI: `python -m octopus strategy add|list|show|move|link|mission|review|snapshot`.
- GUI: Intelligence shows persisted strategy state; Workbench startup/layout/refresh crashes fixed and covered by `tests/test_gui_smoke.py`.
- Economic loop (`octopus/economy.py`, `octopus/actions.py`, same v5 migration): free-form channels, immutable multi-currency ledger (only `observed` cash counts; CSV import of real exports), spend allowances + `authorize_spend` (no spend without an envelope granted by a human or a human-set reinvestment policy), experiment metrics and code-computed verdicts (`cash_net:<CUR>` or observed metric sums), learnings, reinvestment, opt-in ORBIT drive (`economy.cycle` with `drive`/`portfolio`). Evidence natures are now `observed/computed/inferred/hypothesis/unverified`. Paid cloud calls (RunPod video, H3) require `PODALUX_VIDEO_JOB_COST_ESTIMATE` / `OCTOPUS_H3_JOB_COST_ESTIMATE` and an allowance. Channel actions run only with human-granted `act` access and a registered executor (none shipped). Agents get generic tools: `economy_status`, `record_observation`, `propose_experiment`, `start_experiment`, `register_channel`, `act_on_channel`, `open_business`, `request_spend`. CLI: `python -m octopus economy ...`.
- Tests are run with `.venv` (created as `setup-local.ps1` does). Browser integration tests need the Playwright build that matches an installed Chromium (the global Python 3.11 had it; `.venv` did not).

## Architecture to preserve

```text
OCTOPUS control plane
  ├─ GUI Workbench (`agents/gui/workbench.py`)
  │    └─ Intelligence extension (`agents/gui/intelligence.py`)
  ├─ Business Workspace registry (`agents/gui/workspaces.py`)
  ├─ strategic mission templates (`agents/gui/strategy.py`)
  ├─ agents/runtime.py (stable ReAct runtime)
  ├─ agents/cycle.py
  ├─ octopus.tasks (durable local queue / leases / human handoff)
  ├─ octopus.llm -> OmniRoute local gateway -> auto/free
  ├─ Playwright Chromium + agents/web_guard.py
  └─ VideoService -> RunPod Serverless -> cloud video worker
                                  └─ TTS + Remotion + FFmpeg + technical QC

Optional development side channel
  Octopus CLI -> agents/orca.py -> Orca CLI
                                  └─ Run -> Task -> Worker -> Claude/Codex/...
```

Agent roles remain:
`SOUT -> CONVERT -> FORGE -> GROWTH -> LEDGER -> ORBIT`.

`ORBIT` coordinates missions and delegates to sub-agents. `web_guard.session()` must wrap a whole mission so account-read state survives across sub-agents.

## GUI Workbench

`python run_gui.py` is the primary human control surface. `agents.run gui` and `agents.gui.app.main` also open the same Workbench.

The Workbench is a **Business Workspace first** rather than a collection of unrelated screens. The active business is persisted locally and filters the user's working context without moving business logic into Tkinter.

Navigation:
- Cockpit: run status, business KPI, active task count, human requests, activity feed, agent overview and quick actions.
- Business: portfolio of business workspaces, offer grouping, business creation and direct navigation to Missions/Production.
- Intelligence: long-horizon entrepreneurial loop. ORBIT can be explicitly tasked to discover opportunities, validate hypotheses, build offers, design content engines, design funnels, improve client operations, propose reinvestment rules and perform strategic reviews.
- Missions: ORBIT objectives, task filters, worker control and active business context.
- Agents: activity and workload cards for all six roles.
- Production: active-business offer selection, cycle, Studio, final output and QC metrics.
- Humain: pending handoffs with direct responses.
- Navigateur: guarded Chromium observation, URL and latest screenshot.
- Système: local preflight, logs and optional Orca status.

The Intelligence page is not a second autonomous runtime. Its actions compose explicit objectives through `agents/gui/strategy.py` and launch the existing ORBIT mission path. It must not invent revenue, margin, customer or social-platform data.

Business metadata is stored only in ignored `agents/data/workspaces.json`. When missing, `agents/gui/workspaces.py` derives initial business groups from `jobs/*.json` offer-id prefixes. `active_business` is stored through the existing SQLite state table. `Tous les business` remains the global view.

The current repository does **not** yet provide a structured CRM, consolidated revenue/margin ledger, or native social-platform analytics. Full customer management, automated funnel execution and capital allocation therefore require future connectors/data sources; the GUI should expose the orchestration surface without pretending those integrations already exist.

Long operations must stay outside the Tk event loop. Cycles, workers, messages and browser commands use the existing subprocess launcher; doctor and Orca status/tasks use background threads. Do not move business logic into Tkinter.

Quick command bar supports `/business`, `/mission`, `/production`, `/agents`, `/human`, `/browser`, `/system`, `/doctor`; free text is sent to ORBIT and `@ROLE ...` targets an agent.

GUI specification: `docs/GUI.md`.

## Local machine policy

The local PC must stay lightweight. MiniMax H3 must NOT be downloaded or executed locally. Chatterbox, Remotion, FFmpeg, and other heavy video dependencies are cloud-first for the normal path.

Expected local stack:
- Python 3.11+
- `requirements-local.txt`
- Playwright + Chromium
- Docker Desktop
- OmniRoute container
- OCTOPUS GUI/control plane

Bootstrap:

```powershell
.\setup-local.ps1
```

Run `python -m agents.run doctor` before a real cycle.

Default local LLM routing is OmniRoute with:
- `OMNIROUTE_ENABLED=1`
- `OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1`
- `OMNIROUTE_MODEL=auto/free`
- `OMNIROUTE_API_KEY` kept only in the runtime environment, never committed

When OmniRoute is enabled and `OCTOPUS_PROFILE` is not explicitly set, the catalog resolves the default profile to `zero_cost`. An explicit `OCTOPUS_PROFILE=legacy` still restores historical direct routing. `zero_cost` must not fall back to paid DeepSeek.

## Browser requirements

The browser is Playwright/Chromium.

Public pages: ephemeral context.
Account pages: persistent profile for human-authenticated sessions.
Service workers: blocked in guarded contexts.
Network guard: navigation + redirects + fetch/XHR/EventSource/beacon are guarded; WebSockets use Playwright `route_web_socket()`; outbound URLs after an account read must not leak to public destinations.
Login/2FA/CAPTCHA/confirmation must use human handoff.

There are browser integration tests in `tests/test_browser_integration.py`, including account-page load exfiltration and public WebSocket blocking. The local dependency is pinned to Playwright `>=1.55`.

## Video requirements

Normal Podalux cycle: `PODALUX_VIDEO_RENDERER=cloud`.

Cloud worker contract:
- receives `VideoJob`
- executes the existing FORGE pipeline in an isolated workspace
- uploads `final.mp4`, QC JSON, frames, logs and compatibility artifacts to filesystem/S3-compatible storage
- returns a manifest/result compatible with OCTOPUS

RunPod Serverless adapter uses async `/run` submission + `/status/{id}` polling.

Idempotence is critical: stable job IDs + local render state prevent blind duplicate paid submissions. A `SUBMITTING` state without `remote_id` must not be automatically resubmitted.

MiniMax H3 is cloud-only. The H3 ComfyUI workflow should track the current official ComfyUI H3 node/workflow contract rather than copied opaque implementations.

RunPod deployment notes are in `docs/RUNPOD_SETUP.md`, including endpoint timeout and object-storage configuration.

## Orca development bridge

The integration is intentionally thin and optional:

- `agents/orca.py` calls the documented Orca CLI with argument vectors; it does not use Orca's internal SQLite database.
- `Run -> Task -> Worker` is created for explicit development tasks only.
- Worker lifecycle retains Orca's `live` / `unverifiable` / `exited` safety vocabulary. Loss of contact is never treated as proof of process exit.
- `python -m agents.run orca ...` exposes status, start, worker listing and event checks.
- `tests/test_orca.py` verifies the adapter without requiring Orca to be installed.

Do not move business agents or cloud rendering into Orca merely because Orca can supervise workers.

## Important files

- `agents/gui/workbench.py` — primary desktop Workbench
- `agents/gui/intelligence.py` — entrepreneurial Intelligence extension and entrypoint
- `agents/gui/strategy.py` — strategic mission templates
- `agents/gui/workspaces.py` — local Business Workspace registry
- `agents/gui/app.py` — compatibility entrypoint to Workbench
- `agents/gui/studio.py` — existing video studio window used by the Workbench
- `docs/GUI.md` — cockpit/workbench and Intelligence workflow
- `docs/HANDOFF-AUTONOMOUS-BUSINESS.md` — concrete implementation roadmap and acceptance criteria for the long-term autonomous-business layer
- `octopus/strategy.py` — persisted strategic loop (objectives ... evidence, links, reviews, mission context)
- `octopus/strategy_cli.py` — `python -m octopus strategy ...`
- `octopus/connectors.py` — external data sources, unavailable unless a real probe is registered
- `octopus/builtin_handlers.py` — engine tasks, including `strategy.review`
- `agents/task_handlers.py` — `podalux.*` tasks and generic `orbit.mission`
- `docs/AUTONOMOUS_SESSION_REPORT.md` / `docs/AUTONOMOUS_WORK_LOG.md` — last autonomous session, verified facts vs hypotheses
- `octopus/catalog.py` — dynamic OmniRoute overlay + safe default profile
- `octopus/llm.py` — LLM gateway
- `agents/browser.py` — Chromium/Playwright tool
- `agents/web_guard.py` — browser security boundary
- `agents/doctor.py` — preflight diagnostics
- `agents/orca.py` — optional Orca development bridge
- `octopus/media/handlers.py` — local WanGP vs cloud H3 routing
- `octopus/media/presets.py` — H3 cloud preset
- `octopus/video/service.py` — video facade + artifact integrity checks
- `octopus/video/renderers.py` — cloud/local renderer abstraction
- `octopus/video/runpod.py` — RunPod adapter
- `video_worker/executor.py` — FORGE cloud executor
- `video_worker/runpod_handler.py` — RunPod entry point
- `video_worker/Dockerfile` — worker image
- `tests/test_gui.py`
- `tests/test_gui_intelligence.py`
- `tests/test_browser_integration.py`
- `tests/test_doctor.py`
- `tests/test_omniroute.py`
- `tests/test_orca.py`
- `tests/test_minimax_h3_cloud.py`
- `tests/test_video_executor.py`
- `.github/workflows/video-foundation.yml`
- `docs/LOCAL_SETUP.md`
- `docs/OMNIROUTE_SETUP.md`
- `docs/ORCA_INTEGRATION.md`
- `docs/RUNPOD_SETUP.md`

## Next work order

1. Review and commit the uncommitted autonomous-business working tree (human decision), then let a fresh CI run finish and fix actual failures.
2. Run the GUI on Windows with real data and verify Business switching, business creation, Intelligence actions, mission launch, handoff replies and browser observation (an isolated automated smoke of every page passes; real clicks were not exercised).
3. Run the real local smoke test with OmniRoute + Chromium + `doctor` on Windows.
4. Strategy persistence, evidence, scheduled reviews and connector boundaries exist (see `docs/AUTONOMOUS-BUSINESS-IMPLEMENTATION.md` status). Next: GUI forms to create/advance strategic objects, make Intelligence actions attach to a selected objective/experiment (`orbit.mission`), then real CRM/finance/social probes.
5. Add real CRM/revenue/social data connectors before claiming full client-management, funnel execution or reinvestment automation.
6. Deploy the real RunPod worker + object storage and execute one paid-safe/idempotent end-to-end video test.
7. Only after that, run a real Podalux cycle and compare technical QC + visual QC.
8. Use the Orca bridge only for explicit repository-development tasks; keep business automation and video execution in Octopus.
9. Keep publication in dry-run until the whole pipeline is verified.

## Do not regress

- Do not restore DeepSeek as the default zero-cost path.
- Do not put H3 weights on the local PC.
- Do not create a second video engine alongside Remotion FORGE.
- Do not remove idempotency/state protection around remote rendering.
- Do not bypass `web_guard` for convenience.
- Do not turn Orca into a required runtime dependency.
- Do not move Podalux business agents or RunPod workloads into Orca.
- Do not move business logic into the Tkinter cockpit.
- Do not block the Tk main loop on network or long-running work.
- Do not invent client, revenue, margin or social-platform data that has not been connected.
- Do not claim a successful GUI smoke, real render, deployment, or green CI run unless it was actually observed.
