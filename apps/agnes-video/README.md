# Agnes Video integration

OCTOPUS does not implement its own Agnes video engine here.

Canonical replacement plan:
- `../../docs/migrations/AGNES_VIDEO_REPLACEMENT.md`

Pinned upstream:
- `lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b`
- MIT license

The first integration is a thin local HTTP adapter to the independently running Agnes Video Generator service. Do not vendor or rebuild the upstream application in this directory unless a later, explicit deployment decision requires it.
