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
- obtain the exact pin with `scripts/fetch_pinned_upstreams.ps1 agnes` into the ignored `cache/upstreams/` location;
- configure `AGNES_API_KEY` in Agnes's process environment, never in Git and preferably not through the persisted Web config;
- set `HOST=127.0.0.1` and start Agnes independently;
- health/probe it from OCTOPUS;
- communicate only through the adapter.

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
