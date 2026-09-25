# Agnes Video Generator — replacement contract

Date: 2026-09-25

## Source of truth

The replacement engine is the existing open-source project:

- repository: `lcy362/agnes-video-generator`
- upstream branch observed: `master`
- pinned source for this migration: `a87162d6df73ffe72186838ca0ae9d461e68589b`
- license: MIT, Copyright (c) 2026 lcy362

Do **not** rebuild Agnes inside OCTOPUS.

The previous plan for a hand-written `apps/agnes-video/index.html` is superseded by this document.

## Why this changes the architecture

The upstream project already provides:

- Python/FastAPI backend;
- Agnes video API client;
- text-to-video, image-to-video and keyframe modes;
- multi-scene pipelines;
- retries and rate limiting;
- persisted task state and resume/stop;
- FFmpeg/moviepy composition;
- Edge TTS and subtitles;
- Vue UI;
- extensive tests;
- Agnes 2.0 and 2.5 model adaptation.

Reimplementing these facilities in OCTOPUS would recreate the legacy-video problem we are trying to remove.

## Target architecture

```text
OCTOPUS economic/control plane
        |
        | minimal explicit adapter
        v
Agnes Video Generator local service
(pinned external application)
        |
        v
Agnes AI + its own media pipeline
```

Ownership:

OCTOPUS owns:
- economic decision to request video work;
- task/experiment/evidence references;
- permission to trigger an external action;
- cost/time/result bookkeeping;
- verification of the returned artifact.

Agnes Video Generator owns:
- video-generation workflow;
- Agnes API protocol;
- rate limiting/retries;
- images/video/TTS/subtitles/composition;
- video-specific task state;
- video UI.

Do not copy Agnes's internal task manager, retry engine, model registry or media pipeline into OCTOPUS.

## Integration boundary

Prefer a local HTTP adapter against the upstream FastAPI API.

Upstream documented endpoints at the pinned revision include:

- `POST /api/tasks/simple`
- `POST /api/tasks/creative`
- `POST /api/tasks/manuscript`
- `POST /api/tasks/poetry`
- `POST /api/tasks/anchor`
- `GET /api/tasks/{task_id}` for progress polling
- `POST /api/tasks/{task_id}/stop`
- `POST /api/tasks/{task_id}/resume`
- `GET /api/video/{task_id}` for the final video
- `GET /api/tasks/{task_id}/artifacts`

Default service URL documented upstream: `http://localhost:8765`.

First OCTOPUS integration should use the smallest subset needed by the current product path.
Do not expose all Agnes modes merely because they exist.

## Deployment rule

Keep Agnes as an external/pinned application, not as a second OCTOPUS core.

**Bind it to loopback only.** At the pinned revision Agnes defaults its FastAPI host to `0.0.0.0`, while its local configuration endpoints can persist API keys. For the OCTOPUS workstation launch it with `HOST=127.0.0.1` (and port 8765 unless deliberately changed). Do not expose this service to the LAN/Internet.

Preferred first setup:
- obtain the exact pin with `scripts/fetch_pinned_upstreams.ps1 agnes` into ignored `cache/upstreams/`;
- prefer building a local Docker image **from that pinned source** rather than running upstream `start.bat` on the host or pulling an unverified moving image;
- publish container port 8765 only as `127.0.0.1:8765:8765`;
- inject `AGNES_API_KEY` at container/process runtime, never into Git and preferably not through the persisted Web config;
- health/probe the service from OCTOPUS;
- communicate only through the narrow adapter.

If Docker is unavailable and native execution is deliberately chosen, use an isolated virtual environment and force `HOST=127.0.0.1`.

Reproducibility limitation: the pinned upstream source is fixed, but its Dockerfile/requirements use version ranges and an unpinned `python:3.11-slim` base. Treat dependency resolution as a remaining supply-chain variable; do not claim a bit-reproducible build.

Do not vendor the entire Agnes repository into OCTOPUS in the first migration.
Do not add a Git submodule unless a concrete deployment constraint requires it.
Do not fork upstream code before a real incompatibility is observed.

If upstream source code is copied or substantially derived later, preserve its MIT notice.

## Security

The Agnes API key belongs to the Agnes process.

OCTOPUS should not persist or display it.
Browser/UI code in OCTOPUS should not receive it.

The upstream service already keeps its generation calls server-side; preserve that architecture.

Do not make real generation calls in the OCTOPUS test suite.

## Model/protocol ownership

Do not reproduce Agnes model protocol in OCTOPUS.

At the pinned revision, upstream already supports:
- default `agnes-video-v2.0`;
- Agnes 2.5 model branches;
- v2.0 image resolution including hosted-URL upload with Base64 fallback;
- submit retry/rate limiting;
- polling and returned video URL handling.

Therefore the earlier OCTOPUS-side assumptions about exact Data URI behavior, 2.0 payload details and adaptive polling are no longer an OCTOPUS implementation concern. They belong to the pinned Agnes engine.

## Replacement sequence

1. Remove OCTOPUS legacy video runtime according to `VIDEO_ENGINE_REMOVAL.md`.
2. Confirm shared OCTOPUS tests are green.
3. Validate the pinned Agnes project can start independently on this Windows machine.
4. Add one minimal OCTOPUS adapter/probe for the local Agnes service.
5. Add deterministic adapter tests with mocked HTTP responses.
6. Run one explicit human-authorized live smoke test only after the key/service is configured.
7. Record returned artifact evidence in OCTOPUS without importing Agnes internal state.

## Acceptance criteria

The migration succeeds when:

- legacy OCTOPUS Remotion/RunPod/TTS/B-roll code is no longer required;
- Agnes runs independently from its pinned upstream source;
- OCTOPUS does not contain a second video-generation pipeline;
- OCTOPUS can submit only the required Agnes job type through a narrow adapter;
- OCTOPUS can query status, stop a task and obtain the completed video/artifact reference;
- no Agnes secret is stored in OCTOPUS Git;
- deterministic tests make no external generation call;
- external side effects remain governed by OCTOPUS policy;
- upstream version/pin is visible and upgradeable deliberately.

## Not part of this migration

- rewriting Agnes UI;
- copying Agnes's pipeline into OCTOPUS;
- porting all six Agnes task types;
- changing Agnes's default video model;
- improving Agnes itself;
- merging its persistence with OCTOPUS SQLite;
- automatic upstream updates.

## Phase C implementation — 2026-09-25

`octopus.agnes` implements the pinned local HTTP boundary with Python's standard
library. It exposes `probe()`, `status(task_id)` and `video_reference(task_id)`;
each accepts an explicit loopback service URL (default `http://127.0.0.1:8765`).
Proxy environment settings and redirects are disabled. No Agnes credentials are
read, sent or persisted. The health response does not attest the deployed version:
`expected_pin` is the required source pin, not an observed server revision.

Mutations require explicit `agnes.register()` and the existing `actions.propose`
path. The channel kind is `agnes_video`, its locator is the service origin, and
the human must grant active `act` access. Capabilities are `agnes_submit` and
`agnes_stop`. Registration alone grants neither access nor budget.

- `submit`: payload exactly `{"prompt": "..."}`; calls `/api/tasks/simple` as a
  form with `mode=t2v`, leaving model, duration and media settings to Agnes.
  Requires a stable mission idempotency key, declared spend amount/currency and
  an existing allowance. No image upload, resume or alternative modes.
- `stop`: payload exactly `{"task_id": "<12 lowercase hex characters>"}`;
  requires its own stable idempotency key. A stop acknowledgement does not prove
  cancellation or absence of charges and does not release generation spend.
- A successful submission records the returned task ID and service reference in
  the existing action journal. Its evidence describes an HTTP acknowledgement,
  never completion, delivery, customer acceptance or an observed charge.
- `video_reference` requires a service-reported `completed` simple task and
  returns the fixed `/api/video/{task_id}` reference with `verified=False`.
  The pinned simple pipeline writes `final_video.mp4`; its `/artifacts` manifest
  has no simple-task definitions. No remote URLs or filesystem paths from the
  response are followed. Download, integrity and delivery checks remain human
  workflow responsibilities; this adapter does not claim artifact verification.

`actions.AmbiguousAction` preserves the existing spend reservation when a
mutation times out, returns an uncertain HTTP error or has an invalid response.
No retry is performed. The reservation is linked to the action before HTTP, so
a crash during submission retains both identity and budget. Reusing the same
key returns the journaled action, including after process restart; no new call
is made. A crash before acknowledgement persistence can leave status `proposed`
and no Agnes task ID. Reconcile in the independent service before any new key
or spend release; upstream creation has no client idempotency facility.

The declared reservation is not an upstream billing cap: Agnes owns model
configuration and its internal retries. Before live authorization the human must
bound those costs and later reconcile the observed charge through
`economy.record_cash(..., spend_request_id=...)`, or release a reservation only
with evidence that no charge occurred. There is no second ledger or automatic
settlement. Blocked identities are retained too; a newly authorized attempt
needs a new key only after reviewing the prior action.

Tests in `tests/test_agnes.py` mock HTTP and exercise the pinned wire contract,
permission/budget refusals, durable deduplication, concurrent identical requests,
crash and ambiguous outcomes, loopback restrictions, and status/video references.
No independent service launch or live generation was performed in phase C.
Next operational observation: verify the pinned service starts on loopback;
only after explicit human authorization, observe a real task and verify its
artifact and actual charge. Deployment and economic acceptance remain unproven.
