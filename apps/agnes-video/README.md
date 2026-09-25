# Agnes Video App

V1 is a local/private operator tool. Do not deploy it publicly with a personal Agnes API key embedded or persisted in client-side code.

Target application replacing the legacy OCTOPUS video engine.

Implementation target:
- `apps/agnes-video/index.html`
- single self-contained HTML file;
- no framework;
- no CDN;
- no backend;
- no Remotion;
- no RunPod;
- no TTS/B-roll pipeline dependency.

Normative project contract:
- `../../docs/migrations/AGNES_VIDEO_REPLACEMENT.md`

Migration/removal plan:
- `../../docs/migrations/VIDEO_ENGINE_REMOVAL.md`

Do not couple this app back into the OCTOPUS core during the first migration phase.
