# Core runtime

This directory contains the shared infrastructure for autonomous businesses.

Structure:
- agent_runtime/: generic agent execution runtime
- task_manager/: task lifecycle and queues
- event_bus/: lightweight event notifications
- llm_router/: model selection and abstraction
- browser/: shared Playwright/browser service
- memory/: business and agent memory
- database/: persistence layer
- scheduler/: delayed and recurring execution
- tools/: generic tool registry
- human_interface/: human approval gates
- logging/: structured logs
- accounting/: cost and usage tracking
- config/: common settings

The short-video business is the first concrete implementation built on top of this core.
