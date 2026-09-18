# GPU Engineer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide Qwen3-Coder with a confined, logged engineering loop for auditing and improving OCTOPUS on GPU.ai.

**Architecture:** A standard-library Python controller calls the local OpenAI-compatible vLLM endpoint and accepts one JSON action per turn. A typed `Toolbox` confines all reads and mutations to the repository, exposes fixed test and Git operations, and gates commits on a fresh successful full test run.

**Tech Stack:** Python 3.10, OpenAI Python client already installed with vLLM, Git, pytest, Qwen3-Coder through vLLM.

**Spec:** `docs/superpowers/specs/2026-09-18-gpu-engineer-design.md`

## Global Constraints

- Work only in `/workspace/octopus` on `gpu/deep-octopus`.
- Run as `octopus-agent` without sudo or GitHub credentials.
- Never expose a generic shell or remote Git mutation to Qwen.
- Keep controller logs outside the repository in `/workspace/octopus-agent-state`.
- Require `git diff --check` and a fresh full test pass before every agent-created commit.

---

### Task 1: Confined repository tools

**Files:**
- Create: `ops/gpu_agent/__init__.py`
- Create: `ops/gpu_agent/agent.py`
- Create: `tests/test_gpu_agent.py`

**Interfaces:**
- Produces: `Toolbox(repo: Path)`, `Toolbox.execute(name: str, args: dict) -> dict`, `parse_action(text: str) -> dict`.

- [ ] Write tests proving traversal, `.git`, runtime-source, test flags, and forbidden patch targets are rejected.
- [ ] Run `python3 -m pytest tests/test_gpu_agent.py -q` and verify the new tests fail before implementation.
- [ ] Implement repository summary, list, search, read, patch, checks, Git inspection, and checkpoint tools using argument arrays without a shell.
- [ ] Run `python3 -m pytest tests/test_gpu_agent.py -q` and verify all controller tests pass.
- [ ] Commit with `git commit -m "feat: add confined GPU engineer tools"`.

### Task 2: Qwen control loop and audit prompt

**Files:**
- Modify: `ops/gpu_agent/agent.py`
- Modify: `tests/test_gpu_agent.py`

**Interfaces:**
- Produces: `run_agent(mode: str, goal: str, max_steps: int) -> int` and CLI arguments `--mode`, `--goal`, `--max-steps`, `--repo`, `--state-dir`.

- [ ] Write tests for plain and fenced JSON actions, malformed actions, history trimming, and JSONL redaction.
- [ ] Run the focused test file and verify failures cover the missing behavior.
- [ ] Implement the OpenAI-compatible request loop, one-action protocol, bounded history, audit prompt, improvement prompt, and JSONL transcript.
- [ ] Run the focused test file and the full OCTOPUS suite.
- [ ] Commit with `git commit -m "feat: add logged Qwen engineering loop"`.

### Task 3: Root-owned deployment

**Files:**
- Runtime copy: `/opt/octopus-gpu-agent`
- State directory: `/workspace/octopus-agent-state`

**Interfaces:**
- Consumes: `python3 -m ops.gpu_agent.agent`.
- Produces: a root-owned controller invoked as `octopus-agent` with repository and state arguments.

- [ ] Copy `ops/gpu_agent` to `/opt/octopus-gpu-agent`, owned by root and read-only to `octopus-agent`.
- [ ] Create `/workspace/octopus-agent-state`, owned by `octopus-agent`, with mode `0700`.
- [ ] Verify `octopus-agent` cannot modify `/opt/octopus-gpu-agent` and can modify `/workspace/octopus` and its state directory.
- [ ] Verify `curl http://127.0.0.1:8000/v1/models` returns `QuantTrio/Qwen3-Coder-30B-A3B-Instruct-AWQ`.

### Task 4: Massive audit run

**Files:**
- Create through the agent: `docs/audits/GPU_AUDIT_2026-09-18.md`
- Create outside Git: `/workspace/octopus-agent-state/audit-*.jsonl` and `audit-console.log`.

**Interfaces:**
- Consumes: `run_agent(mode="audit", goal=..., max_steps=80)`.
- Produces: cited audit, ranked roadmap, full logs, and a local checkpoint when tests pass.

- [ ] Launch the audit with `nohup` as `octopus-agent` and record its PID.
- [ ] Follow the console log until the first repository reads and baseline test invocation are visible.
- [ ] On completion, verify the audit document exists, inspect `git status` and `git diff`, and rerun the full suite independently.
- [ ] Review the first recommended improvement batch before starting implementation work.
