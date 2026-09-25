#!/usr/bin/env python3
"""Fair, zero-cost Kilo worker benchmark on historical OCTOPUS regressions.

The workflow runs this script once per model in separate GitHub-hosted runners.
Each case injects a known historical regression into exactly one production file,
proves the current regression tests fail, lets the worker repair it, then reruns
those tests and audits the diff boundary.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = ROOT / "benchmark-results"

CASES = [
    {
        "name": "provider-cooldown",
        "fix_commit": "b431835a411f4db6cbb37b6b49ed125a7cab57fe",
        "path": "octopus/llm.py",
        "test": [sys.executable, "-m", "pytest", "-q", "tests/test_provider_cooldown.py"],
        "prompt": (
            "OCTOPUS benchmark ticket. A historical regression has deliberately been "
            "injected into octopus/llm.py while the current regression tests were kept. "
            "Diagnose and repair the production code. You may modify ONLY octopus/llm.py. "
            "Do not modify tests, configuration, dependencies, benchmark files, Git metadata, "
            "or any other path. OCTOPUS will run the tests; do not use shell or Git. "
            "Acceptance: a connection failure shared by a provider must make later models on "
            "that same provider temporarily ineligible, while model-level 429s, authentication "
            "errors, read timeouts and invalid/structured output failures must NOT poison the "
            "whole provider; expiry and successful recovery must work. Preserve existing "
            "rate-limit semantics and fallback behavior. Make the smallest coherent fix and "
            "finish with a concise report of what you changed."
        ),
    },
    {
        "name": "business-replan",
        "fix_commit": "5301a27b8f400041e38eef8b2f0afd73a7b23e6f",
        "path": "agents/runtime.py",
        "test": [sys.executable, "-m", "pytest", "-q", "tests/test_business_signal_evidence.py"],
        "prompt": (
            "OCTOPUS benchmark ticket. A historical regression has deliberately been "
            "injected into agents/runtime.py while the current regression tests were kept. "
            "Diagnose and repair the production code. You may modify ONLY agents/runtime.py. "
            "Do not modify tests, configuration, dependencies, benchmark files, Git metadata, "
            "or any other path. OCTOPUS will run the tests; do not use shell or Git. "
            "Acceptance: when business-signal discovery targets multiple signals but the first "
            "planner response contains only one task, perform exactly one additional planning "
            "pass because one agent has a bounded step budget. Do not replan an already "
            "decomposed plan, a single-signal target, or a non-business-signal mission. Accept "
            "the second plan as-is even if it still has one task, preserve MAX_PLAN_TASKS, and "
            "preserve structured handoff between resulting subtasks. The feedback may discuss "
            "granularity/budget only; it must not prescribe sources, queries, segments, roles "
            "or search strategy. Make the smallest coherent fix and finish with a concise "
            "report of what you changed."
        ),
    },
]


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
        timeout: int | None = None, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def git(*args: str, check: bool = True) -> str:
    p = run(["git", *args])
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-4000:])
    return p.stdout


def safe_model_name(model: str) -> str:
    return model.replace("/", "__").replace(":", "_")


def kilo_permissions(path: str) -> dict:
    sensitive = [
        ".env", ".env.*", "**/.env", "**/.env.*", "**/*.pem", "**/*.key",
        "**/credentials*", "**/secrets*",
    ]
    protected = [
        ".git/**", "**/.git/**", ".github/**", "benchmarks/**",
        "AGENTS.md", "**/AGENTS.md", "kilo.json", "kilo.jsonc",
        "**/kilo.json", "**/kilo.jsonc",
    ]
    read = {"*": "allow"}
    edit = {"*": "deny", path: "allow"}
    write = {"*": "deny", path: "allow"}
    for pattern in sensitive:
        read[pattern] = "deny"
        edit[pattern] = "deny"
        write[pattern] = "deny"
    for pattern in protected:
        edit[pattern] = "deny"
        write[pattern] = "deny"
    permissions = {
        "*": "deny",
        "read": read,
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "edit": edit,
        "write": write,
    }
    for name in (
        "bash", "task", "agent_manager", "skill", "websearch", "webfetch",
        "external_directory", "lsp", "question", "todowrite", "todoread",
        "doom_loop", "mcp",
    ):
        permissions[name] = "deny"
    return permissions


def scrubbed_kilo_env(state_dir: Path, model: str, path: str) -> dict[str, str]:
    keep = {
        "PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    }
    env = {k: v for k, v in os.environ.items() if k in keep and v}
    config = {
        "plugin": [],
        "mcp": {},
        "small_model": model,
        "compaction": {"auto": True, "prune": True, "threshold_percent": 65},
        "agent": {
            "octopus-bench-worker": {
                "description": "Restricted zero-cost OCTOPUS benchmark worker",
                "mode": "primary",
                "model": model,
                "steps": 24,
                "permission": kilo_permissions(path),
            },
        },
    }
    env.update({
        "CI": "1",
        "KILO_TELEMETRY_LEVEL": "off",
        "KILO_DISABLE_PROJECT_CONFIG": "1",
        "KILO_PURE": "1",
        "KILO_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
        "XDG_CONFIG_HOME": str(state_dir / "config"),
        "XDG_DATA_HOME": str(state_dir / "data"),
        "XDG_CACHE_HOME": str(state_dir / "cache"),
        "XDG_STATE_HOME": str(state_dir / "state"),
    })
    # No KILO_API_KEY, GitHub token, provider key, or other credential is exposed.
    return env


def status_paths() -> list[str]:
    out = git("status", "--porcelain=v1", "--untracked-files=all", check=False)
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        value = line[3:]
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.append(value.replace("\\", "/"))
    return sorted(set(paths))


def prepare_case(case: dict) -> tuple[str, str]:
    # Reset all tracked files to the benchmark branch, then reverse ONLY the historical
    # production fix. This preserves every later unrelated OCTOPUS change while recreating
    # the exact regression that the current oracle should detect.
    git("reset", "--hard", "HEAD")
    run(["git", "clean", "-fd", "-e", "benchmark-results/"])
    current = git("show", f"HEAD:{case['path']}")
    patch = run([
        "git", "show", "--format=", "--binary", case["fix_commit"], "--", case["path"]
    ])
    if patch.returncode or not patch.stdout.strip():
        raise RuntimeError(
            f"cannot read historical fix patch {case['name']}: {(patch.stderr or patch.stdout)[-2000:]}"
        )
    applied = run(["git", "apply", "--reverse", "--whitespace=nowarn", "-"], input_text=patch.stdout)
    if applied.returncode:
        raise RuntimeError(
            f"cannot inject historical regression {case['name']}: {(applied.stderr or applied.stdout)[-3000:]}"
        )
    target = ROOT / case["path"]
    baseline = target.read_text(encoding="utf-8")
    return baseline, current


def unified_delta(before: str, after: str, path: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{path}.injected",
        tofile=f"{path}.worker",
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    model = args.model
    model_dir = RESULT_ROOT / safe_model_name(model)
    model_dir.mkdir(parents=True, exist_ok=True)

    overall = {
        "model": model,
        "cost_policy": "explicit-free-model-only; no Kilo API key; no Codex worker",
        "cases": [],
    }

    for case in CASES:
        case_dir = model_dir / case["name"]
        case_dir.mkdir(parents=True, exist_ok=True)
        baseline_text, current_text = prepare_case(case)

        baseline_test = run(case["test"], timeout=600)
        (case_dir / "baseline-test.txt").write_text(
            baseline_test.stdout + "\n--- STDERR ---\n" + baseline_test.stderr,
            encoding="utf-8",
        )
        baseline_failed = baseline_test.returncode != 0

        if not baseline_failed:
            result = {
                "name": case["name"],
                "valid": False,
                "reason": "injected historical regression did not fail its current oracle",
                "baseline_test_exit": baseline_test.returncode,
            }
            overall["cases"].append(result)
            (case_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            continue

        injected_status = status_paths()
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix=f"kilo-bench-{case['name']}-") as tmp:
            env = scrubbed_kilo_env(Path(tmp), model, case["path"])
            try:
                kilo = run(
                    [
                        "kilo", "run", "--auto",
                        "--model", model,
                        "--agent", "octopus-bench-worker",
                        "--format", "json",
                        case["prompt"],
                    ],
                    env=env,
                    timeout=900,
                )
                kilo_timeout = False
            except subprocess.TimeoutExpired as exc:
                kilo = subprocess.CompletedProcess(
                    exc.cmd, 124,
                    (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                )
                kilo_timeout = True
        elapsed = time.monotonic() - started

        (case_dir / "kilo.stdout.jsonl").write_text(kilo.stdout or "", encoding="utf-8")
        (case_dir / "kilo.stderr.txt").write_text(kilo.stderr or "", encoding="utf-8")

        after_text = (ROOT / case["path"]).read_text(encoding="utf-8")
        (case_dir / "worker.patch").write_text(
            unified_delta(baseline_text, after_text, case["path"]),
            encoding="utf-8",
        )
        (case_dir / "vs-current.patch").write_text(
            unified_delta(current_text, after_text, case["path"]),
            encoding="utf-8",
        )

        changed = status_paths()
        ignored_prefixes = (
            "benchmark-results/",
        )
        out_of_scope = [
            p for p in changed
            if p != case["path"] and not p.startswith(ignored_prefixes)
        ]

        compile_check = run([sys.executable, "-m", "py_compile", case["path"]], timeout=120)
        final_test = run(case["test"], timeout=600)
        diff_check = run(["git", "diff", "--check"], timeout=120)
        exact_current = after_text == current_text

        (case_dir / "final-test.txt").write_text(
            final_test.stdout + "\n--- STDERR ---\n" + final_test.stderr,
            encoding="utf-8",
        )
        (case_dir / "compile.txt").write_text(
            compile_check.stdout + "\n" + compile_check.stderr,
            encoding="utf-8",
        )
        (case_dir / "diff-check.txt").write_text(
            diff_check.stdout + "\n" + diff_check.stderr,
            encoding="utf-8",
        )

        result = {
            "name": case["name"],
            "valid": True,
            "baseline_test_exit": baseline_test.returncode,
            "injected_status": injected_status,
            "kilo_exit": kilo.returncode,
            "kilo_timeout": kilo_timeout,
            "wall_seconds": round(elapsed, 3),
            "compile_exit": compile_check.returncode,
            "final_test_exit": final_test.returncode,
            "diff_check_exit": diff_check.returncode,
            "changed_paths": changed,
            "out_of_scope_paths": out_of_scope,
            "exact_match_current_main": exact_current,
            "pass": (
                final_test.returncode == 0
                and compile_check.returncode == 0
                and diff_check.returncode == 0
                and not out_of_scope
            ),
        }
        overall["cases"].append(result)
        (case_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    overall["passed_cases"] = sum(1 for c in overall["cases"] if c.get("pass"))
    overall["valid_cases"] = sum(1 for c in overall["cases"] if c.get("valid"))
    overall["all_valid_cases_passed"] = (
        overall["valid_cases"] > 0 and overall["passed_cases"] == overall["valid_cases"]
    )
    summary_path = model_dir / "summary.json"
    summary_path.write_text(json.dumps(overall, indent=2), encoding="utf-8")

    print("BENCHMARK_SUMMARY_START")
    print(json.dumps(overall, indent=2))
    print("BENCHMARK_SUMMARY_END")
    # Do not fail the matrix job merely because the model failed a benchmark:
    # failed model attempts are benchmark data and must still upload artifacts.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
