# CLAUDE — handoff for `feat/cloud-video-foundation`

## Current state

This branch is the active cloud-video migration branch. Do not restart the architecture from scratch and do not rewrite `agents/runtime.py` unless there is a concrete regression requiring it.

The branch includes the cloud-first control-plane/video foundation, the integrated Chromium browser guard, OmniRoute routing, MiniMax H3 cloud routing, local diagnostics, one-command Windows bootstrap, artifact checksum verification, CI coverage, and an **optional Orca development bridge**. Recent fixes also make OmniRoute `zero_cost` the default when enabled and guard WebSocket connections. Do not claim CI is green until a fresh run passes.

Orca is deliberately scoped to repository-development work: it is invoked only through its public CLI and remains disabled unless `OCTOPUS_ORCA_ENABLED=1` is set. It must not replace the Podalux business runtime, `octopus.tasks`, or RunPod video execution.

## Architecture to preserve

```text
OCTOPUS control plane
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
- `tests/test_browser_integration.py`
- `tests/test_doctor.py`
- `tests/test_omniroute.py`
- `tests/test_minimax_h3_cloud.py`
- `tests/test_orca.py`
- `tests/test_video_executor.py`
- `.github/workflows/video-foundation.yml`
- `docs/LOCAL_SETUP.md`
- `docs/OMNIROUTE_SETUP.md`
- `docs/ORCA_INTEGRATION.md`
- `docs/RUNPOD_SETUP.md`
- `setup-local.ps1`

## Next work order

1. Inspect current branch head and recent commits before changing anything further.
2. Inspect fresh GitHub Actions for the current HEAD; fix actual test failures rather than assuming.
3. Run the real local smoke test with OmniRoute + Chromium + `doctor` on Windows.
4. Deploy the real RunPod worker + object storage and execute one paid-safe/idempotent end-to-end video test.
5. Only after that, run a real Podalux cycle and compare technical QC + visual QC.
6. Use the Orca bridge only for explicit repository-development tasks; keep business automation and video execution in Octopus.
7. Keep publication in dry-run until the whole pipeline is verified.

## Do not regress

- Do not restore DeepSeek as the default zero-cost path.
- Do not put H3 weights on the local PC.
- Do not create a second video engine alongside Remotion FORGE.
- Do not remove idempotency/state protection around remote rendering.
- Do not bypass `web_guard` for convenience.
- Do not turn Orca into a required runtime dependency.
- Do not move Podalux business agents or RunPod workloads into Orca.
- Do not claim a successful real render, deployment, or green CI run unless it was actually observed.
