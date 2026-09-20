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


## Pre-launch hardening after hostile review

The first Python canary is blocked until the hardened dry-run succeeds on the real Windows + Docker Desktop host.

Before Step is started, OCTOPUS now:

- creates the task in an **independent Git clone outside the source checkout**, not a linked worktree;
- removes the clone remote and disables Git hooks;
- refuses tracked secret-like files, tracked symlinks, skip-worktree/assume-unchanged index flags and a dirty/ignored task clone;
- probes the real Docker engine and requires Linux containers;
- resolves the sandbox tag to an immutable local image ID for the run;
- executes a real sandbox probe on the host path and proves read-only workspace, no network, non-root execution, no Docker socket and no inherited host secret;
- runs the oracle twice inside the exact task clone before Kilo starts and refuses a flaky or empty baseline;
- requires the post-edit `PASSED` node signature to match that baseline;
- rejects new imports, dangerous calls and new module-level call side effects for `python_canary`;
- imports the successful commit explicitly from the isolated clone before a fast-forward of the night branch.

The Kilo process itself still runs on the host, so the first model-written Python change remains a **supervised canary**, not an unattended night backlog.

A dry-run is no longer purely syntactic for `python_canary`: it deliberately performs the real Docker sandbox probe. If that probe does not pass on Windows, do not run Step.
