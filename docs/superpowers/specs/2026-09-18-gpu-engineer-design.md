# GPU Engineer Design

## Objective

Run Qwen3-Coder on the GPU.ai instance as a repository engineer for OCTOPUS. The engineer must inspect the whole repository, produce an evidence-based audit, implement improvements in bounded batches, run tests, create local commits, and retain complete execution logs.

## Isolation

- The repository lives at `/workspace/octopus` on branch `gpu/deep-octopus`, based on commit `eae79182c8a50a132fe7115e3566d03c1fb08d8e`.
- The model and its subprocesses run as the unprivileged Linux user `octopus-agent`.
- GitHub credentials remain in the root account and are never available to the model.
- The runtime copy of the engineer is root-owned under `/opt/octopus-gpu-agent`; Qwen cannot alter the process controlling its tools.
- Logs live outside the repository under `/workspace/octopus-agent-state`.

## Tool Boundary

Qwen receives typed tools for repository summary, file listing, text search, bounded file reads, patch application, tests, Python compilation, Git status, Git diff, recent Git history, and local checkpoints. It never receives a generic shell, arbitrary subprocess execution, package installation, network tools, Git remote mutation, or `git push`.

Every path is resolved under `/workspace/octopus`. Access to `.git`, `ops/gpu_agent`, data directories, generated outputs, caches, environments, and secrets is refused. Patches are limited in size and file count, checked before application, and logged.

## Workflow

The first run is audit-only in intent: map the repository, run the full offline test suite, inspect architecture and high-risk code, and write `docs/audits/GPU_AUDIT_2026-09-18.md`. Findings must cite files and concrete evidence and be ranked P0 through P3. The audit ends with a roadmap of small independently testable batches.

Improvement runs take one roadmap batch at a time. Each run reads the audit, writes focused tests first where behavior changes, applies the smallest patch, runs targeted tests, runs the full suite, reviews the diff, and creates a local checkpoint only after all checks pass. Pushing and merging remain human-controlled.

## Failure Handling

Tool calls have timeouts and output caps. Failed commands return structured results to Qwen and are written to JSONL logs. A repeated invalid response is corrected through the conversation, while an unreachable model or exhausted step budget stops the run with a nonzero exit code. Existing repository changes are never discarded automatically.

## Verification

The controller has unit tests for path confinement, response parsing, forbidden patch targets, test argument validation, and commit gating. Installation verifies the source commit, active branch, unprivileged identity, vLLM health endpoint, controller tests, and OCTOPUS baseline tests before launching the audit.
