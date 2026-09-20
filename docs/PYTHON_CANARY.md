# Python canary V1

This is **not** the default night shift. It is the first bounded executable-code canary.

The model still runs without shell, web, external-directory access, Git writes, push or merge authority. The extra risk comes from executing tests against model-written Python, so the oracle tests run in a disposable Docker container with:

- no network;
- read-only root filesystem;
- the task worktree mounted read-only;
- all Linux capabilities dropped;
- no-new-privileges;
- bounded memory, CPU and process count;
- only a temporary writable `/tmp`;
- no host secrets passed to the container.

The Python policy also refuses edits to existing tests and OCTOPUS trust-core files such as `dev_worker.py`, `night_shift.py`, `worker.py`, `tasks.py` and `paths.py`. It applies explicit file/line diff-radius limits and runs the oracle tests once **before** Step edits anything.

## Build the local sandbox image

From the repository root:

```powershell
docker build -f docker/dev-sandbox.Dockerfile -t octopus-test-sandbox:py311 .
```

The image intentionally contains only Python 3.11 and pytest. Therefore the first canary targets `octopus/capabilities.py`, whose current tests do not require the heavier OCTOPUS runtime dependencies.

## Dry-run

```powershell
python -m octopus night-shift --repo . --plan octopus/config/night_shift_python_canary.json --dry-run
```

## Supervised one-ticket canary

```powershell
python -m octopus night-shift --repo . --plan octopus/config/night_shift_python_canary.json --hours 1 --max-tasks 1 --max-failures 1
```

Do not promote Python canaries to a multi-hour unattended backlog merely because one task passes. The next promotion gate is repeated green canaries with no security violation, no out-of-scope diff and clean sandbox teardown.
