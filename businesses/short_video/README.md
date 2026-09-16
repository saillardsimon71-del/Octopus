# Short video business

This is the first concrete business using the shared autonomous business engine.
It keeps the business-specific logic under this folder and relies on the shared runtime under /core.

Structure:
- config/: business settings, goals and target KPIs
- agents/: per-agent definitions and routing
- memory/: business memory and agent memory
- data/: content sources, offers, materials and cached assets
- tasks/: task definitions and workflow stages
- outputs/: generated final files, exports and metadata
- logs/: operational logs

The business-specific implementation should remain thin and re-use the shared engine components.
