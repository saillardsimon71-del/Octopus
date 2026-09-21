"""Fail-closed validation for promoting a completed night-shift run.

This module never pushes, merges, or changes Git refs. It turns a night-shift JSON
report into a small review manifest only when every recorded ticket satisfies the
expected execution invariants.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PromotionError(ValueError):
    pass


def _sha(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(text):
        raise PromotionError(f"{field} doit être un SHA Git complet")
    return text


def load_report(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionError(f"rapport illisible: {exc}") from exc
    if not isinstance(raw, dict):
        raise PromotionError("rapport JSON objet attendu")
    return raw


def build_manifest(report: dict) -> dict:
    if not isinstance(report, dict):
        raise PromotionError("rapport JSON objet attendu")
    if report.get("status") != "backlog_complete":
        raise PromotionError(f"run non promouvable: status={report.get('status')!r}")

    run_id = str(report.get("run_id") or "").strip()
    if not run_id:
        raise PromotionError("run_id requis")
    policy = str(report.get("policy") or "").strip()
    if policy not in {"docs_only", "python_canary", "product_ticket"}:
        raise PromotionError(f"policy non promouvable: {policy!r}")

    base_head = _sha(report.get("base_head"), "base_head")
    final_head = _sha(report.get("final_head"), "final_head")
    tickets = report.get("tickets")
    if not isinstance(tickets, list) or not tickets:
        raise PromotionError("au moins un ticket est requis")

    commits: list[str] = []
    changed_paths: set[str] = set()
    last_night_head = base_head

    for index, entry in enumerate(tickets, start=1):
        if not isinstance(entry, dict):
            raise PromotionError(f"ticket #{index}: objet attendu")
        if entry.get("status") != "done":
            raise PromotionError(f"ticket #{index}: status non done")

        allowed = entry.get("allowed_paths")
        if not isinstance(allowed, list) or not allowed or not all(isinstance(path, str) and path for path in allowed):
            raise PromotionError(f"ticket #{index}: allowed_paths invalide")
        allowed_set = set(allowed)

        result = entry.get("result")
        if not isinstance(result, dict) or result.get("status") != "done":
            raise PromotionError(f"ticket #{index}: résultat worker non done")
        output = result.get("output")
        if not isinstance(output, dict):
            raise PromotionError(f"ticket #{index}: output worker manquant")
        if output.get("backend") != "kilo":
            raise PromotionError(f"ticket #{index}: backend non Kilo")

        paths = output.get("changed_paths") or []
        if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
            raise PromotionError(f"ticket #{index}: changed_paths invalide")
        outside = sorted(set(paths) - allowed_set)
        if outside:
            raise PromotionError(f"ticket #{index}: chemin hors périmètre: {outside}")

        noop = bool(output.get("noop"))
        if noop:
            if output.get("commit") not in {None, ""} or paths:
                raise PromotionError(f"ticket #{index}: noop incohérent")
        else:
            commit = _sha(output.get("commit"), f"ticket #{index} commit")
            if not paths:
                raise PromotionError(f"ticket #{index}: commit sans changed_paths")
            commits.append(commit)
            changed_paths.update(paths)

        if policy == "python_canary":
            from . import night_shift

            task_input = result.get("input") or {}
            required_flags = {
                "strict_repository_preflight": True,
                "require_baseline_oracle": True,
                "python_canary_ast": True,
                "allow_declarative_fallback": False,
            }
            for name, expected in required_flags.items():
                if task_input.get(name) is not expected:
                    raise PromotionError(f"ticket #{index}: garde {name} invalide")
            if output.get("test_sandbox") != "docker":
                raise PromotionError(f"ticket #{index}: sandbox Docker requis")
            oracle_tests = output.get("oracle_tests")
            if isinstance(oracle_tests, bool) or not isinstance(oracle_tests, int) or oracle_tests <= 0:
                raise PromotionError(f"ticket #{index}: oracle_tests positif requis")

            if len(allowed) != 1 or allowed[0] not in night_shift.PYTHON_CANARY_ORACLES:
                raise PromotionError(f"ticket #{index}: surface python_canary non approuvée")
            if task_input.get("allowed_paths") != allowed:
                raise PromotionError(f"ticket #{index}: allowed_paths worker incohérent")
            expected_targets = list(night_shift.PYTHON_CANARY_ORACLES[allowed[0]])
            commands = task_input.get("tests")
            if not isinstance(commands, list) or not commands:
                raise PromotionError(f"ticket #{index}: commandes oracle manquantes")
            actual_targets = [
                command[-1] if isinstance(command, list) and command else None
                for command in commands
            ]
            if actual_targets != expected_targets:
                raise PromotionError(
                    f"ticket #{index}: oracle worker attendu {expected_targets}, reçu {actual_targets}"
                )
            if task_input.get("max_files_changed") != 1:
                raise PromotionError(f"ticket #{index}: max_files_changed invalide")
            for field in ("max_lines_added", "max_lines_deleted"):
                value = task_input.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 80:
                    raise PromotionError(f"ticket #{index}: {field} hors politique actuelle")

        if policy == "product_ticket":
            from . import acceptance, dev_worker

            task_input = result.get("input") or {}
            required_flags = {
                "strict_repository_preflight": True,
                "require_baseline_oracle": True,
                "python_canary_ast": False,
                "allow_declarative_fallback": False,
            }
            for name, expected in required_flags.items():
                if task_input.get(name) is not expected:
                    raise PromotionError(f"ticket #{index}: garde {name} invalide")
            if task_input.get("self_modification_policy") != "product_ticket":
                raise PromotionError(f"ticket #{index}: self_modification_policy invalide")
            if output.get("self_policy") != "product_ticket":
                raise PromotionError(f"ticket #{index}: self_policy non product_ticket")
            raw_contract = task_input.get("acceptance_contract")
            try:
                contract = acceptance.validate_contract(raw_contract)
            except acceptance.AcceptanceError as exc:
                raise PromotionError(
                    f"ticket #{index}: acceptance_contract invalide: {exc}"
                ) from exc
            expected_contract_hash = acceptance.contract_hash(contract)
            if output.get("acceptance_contract_hash") != expected_contract_hash:
                raise PromotionError(f"ticket #{index}: acceptance_contract_hash incohérent")
            if output.get("gate_status") != "ACCEPTED":
                raise PromotionError(f"ticket #{index}: gate_status doit être ACCEPTED")
            evidence_path = str(output.get("evidence_path") or "").strip()
            evidence_sha = str(output.get("evidence_sha256") or "").strip().lower()
            artifact_fingerprint = str(output.get("artifact_fingerprint_sha256") or "").strip().lower()
            if not evidence_path:
                raise PromotionError(f"ticket #{index}: evidence_path requis")
            if re.fullmatch(r"[0-9a-f]{64}", evidence_sha) is None:
                raise PromotionError(f"ticket #{index}: evidence_sha256 invalide")
            if re.fullmatch(r"[0-9a-f]{64}", artifact_fingerprint) is None:
                raise PromotionError(f"ticket #{index}: artifact_fingerprint_sha256 invalide")
            if output.get("test_sandbox") != "docker":
                raise PromotionError(f"ticket #{index}: sandbox Docker requis")
            sandbox_image = str(output.get("test_sandbox_image") or "")
            if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", sandbox_image) is None:
                raise PromotionError(f"ticket #{index}: image sandbox résolue SHA256 requise")
            sandbox_ref = str(output.get("test_sandbox_image_ref") or "")
            if not sandbox_ref or sandbox_ref != str(task_input.get("test_sandbox_image") or ""):
                raise PromotionError(f"ticket #{index}: référence sandbox incohérente")
            if str(task_input.get("test_sandbox_image_id") or "") != sandbox_image:
                raise PromotionError(f"ticket #{index}: identité sandbox incohérente")
            if output.get("tests_passed") is not True:
                raise PromotionError(f"ticket #{index}: preuve tests verts manquante")
            if output.get("baseline_oracle_runs") != 2:
                raise PromotionError(f"ticket #{index}: baseline oracle double run requis")
            oracle_tests = output.get("oracle_tests")
            post_oracle_tests = output.get("post_oracle_tests")
            if isinstance(oracle_tests, bool) or not isinstance(oracle_tests, int) or oracle_tests <= 0:
                raise PromotionError(f"ticket #{index}: oracle_tests positif requis")
            if post_oracle_tests != oracle_tests:
                raise PromotionError(f"ticket #{index}: oracle final différent de la baseline")
            if task_input.get("allowed_paths") != allowed:
                raise PromotionError(f"ticket #{index}: allowed_paths worker incohérent")
            protected = sorted(set(allowed) & dev_worker.OCTOPUS_PRODUCT_PROTECTED_PATHS)
            if protected:
                raise PromotionError(f"ticket #{index}: noyau product_ticket protégé: {protected}")
            if any(path.startswith("tests/") or path.endswith("/conftest.py") or path == "conftest.py"
                   for path in allowed):
                raise PromotionError(f"ticket #{index}: modification des tests interdite")
            commands = task_input.get("tests")
            if not isinstance(commands, list) or not commands:
                raise PromotionError(f"ticket #{index}: commandes oracle manquantes")
            targets = [
                arg.split("::", 1)[0]
                for command in commands if isinstance(command, list)
                for arg in command[3:] if isinstance(arg, str) and not arg.startswith("-")
            ]
            if not targets or any(not target.startswith("tests/") or not target.endswith(".py") for target in targets):
                raise PromotionError(f"ticket #{index}: oracle product_ticket invalide")
            max_files = task_input.get("max_files_changed")
            if (isinstance(max_files, bool) or not isinstance(max_files, int)
                    or not 1 <= max_files <= min(dev_worker.PRODUCT_TICKET_MAX_FILES, len(allowed))):
                raise PromotionError(f"ticket #{index}: max_files_changed product_ticket invalide")
            for field in ("max_lines_added", "max_lines_deleted"):
                value = task_input.get(field)
                if (isinstance(value, bool) or not isinstance(value, int)
                        or not 1 <= value <= dev_worker.PRODUCT_TICKET_MAX_LINES):
                    raise PromotionError(f"ticket #{index}: {field} product_ticket invalide")
            protected_changed = sorted(set(paths) & dev_worker.OCTOPUS_PRODUCT_PROTECTED_PATHS)
            if protected_changed:
                raise PromotionError(f"ticket #{index}: changed_paths touche le noyau protégé: {protected_changed}")
            if any(path.startswith("tests/") or path.endswith("/conftest.py") or path == "conftest.py"
                   for path in paths):
                raise PromotionError(f"ticket #{index}: changed_paths modifie un test")

        night_head = entry.get("night_head")
        if night_head is not None:
            last_night_head = _sha(night_head, f"ticket #{index} night_head")

    if final_head != last_night_head:
        raise PromotionError("final_head ne correspond pas au dernier night_head")

    return {
        "run_id": run_id,
        "policy": policy,
        "base_head": base_head,
        "final_head": final_head,
        "ticket_count": len(tickets),
        "commits": commits,
        "changed_paths": sorted(changed_paths),
        "requires_human_review": True,
        "auto_merge": False,
    }



def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode:
        raise PromotionError((result.stderr or result.stdout or "git failed")[-2000:])
    return result


def _verify_repo(path_value: Any, expected_head: str, label: str) -> Path:
    raw = str(path_value or "").strip()
    if not raw:
        raise PromotionError(f"{label} requis pour la vérification Git")
    repo = Path(raw).resolve()
    if not repo.is_dir():
        raise PromotionError(f"{label} introuvable: {repo}")
    root = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if root != repo:
        raise PromotionError(f"{label} n'est pas une racine Git: {repo}")
    if _git(repo, "status", "--porcelain").stdout.strip():
        raise PromotionError(f"{label} non propre")
    head = _sha(_git(repo, "rev-parse", "HEAD").stdout.strip(), f"HEAD {label}")
    if head != expected_head:
        raise PromotionError(f"HEAD {label} différent du rapport")
    return repo


def verify_git(report: dict, manifest: dict) -> dict:
    if manifest.get("requires_human_review") is not True or manifest.get("auto_merge") is not False:
        raise PromotionError("promotion exige revue humaine et auto_merge=false")
    base_repository = _verify_repo(report.get("base_repository"), manifest["base_head"], "base_repository")
    worktree = _verify_repo(report.get("night_worktree"), manifest["final_head"], "night_worktree")
    if base_repository == worktree:
        raise PromotionError("base_repository et night_worktree doivent être distincts")

    ancestry = _git(
        worktree, "merge-base", "--is-ancestor", manifest["base_head"], manifest["final_head"], check=False
    )
    if ancestry.returncode != 0:
        raise PromotionError("final_head ne descend pas de base_head")

    actual_commits = [
        line.strip()
        for line in _git(
            worktree, "rev-list", "--reverse", f"{manifest['base_head']}..{manifest['final_head']}"
        ).stdout.splitlines()
        if line.strip()
    ]
    if actual_commits != manifest["commits"]:
        raise PromotionError(
            f"historique Git différent du rapport: attendu {manifest['commits']}, obtenu {actual_commits}"
        )

    actual_paths = sorted({
        line.strip()
        for line in _git(
            worktree, "diff", "--name-only", manifest["base_head"], manifest["final_head"], "--"
        ).stdout.splitlines()
        if line.strip()
    })
    if actual_paths != manifest["changed_paths"]:
        raise PromotionError(
            f"diff Git différent du rapport: attendu {manifest['changed_paths']}, obtenu {actual_paths}"
        )

    if manifest["policy"] == "product_ticket":
        from . import acceptance, dev_worker

        protected = sorted(set(actual_paths) & dev_worker.OCTOPUS_PRODUCT_PROTECTED_PATHS)
        if protected:
            raise PromotionError(f"diff product_ticket touche le noyau protégé: {protected}")
        test_paths = [
            path for path in actual_paths
            if path.startswith("tests/") or path.endswith("/conftest.py") or path == "conftest.py"
        ]
        if test_paths:
            raise PromotionError(f"diff product_ticket modifie des tests: {test_paths}")

        for index, entry in enumerate(report["tickets"], start=1):
            result = entry["result"]
            output = result["output"]
            task_input = result.get("input") or {}

            if not output.get("noop"):
                commit = _sha(output.get("commit"), f"ticket #{index} commit")
                allowed = set(entry["allowed_paths"])
                commit_paths = sorted({
                    line.strip()
                    for line in _git(
                        worktree, "diff", "--name-only", f"{commit}^", commit, "--"
                    ).stdout.splitlines()
                    if line.strip()
                })
                reported_paths = sorted(output.get("changed_paths") or [])
                if commit_paths != reported_paths:
                    raise PromotionError(
                        f"ticket #{index}: changed_paths différent du commit: "
                        f"attendu {reported_paths}, obtenu {commit_paths}"
                    )
                outside = sorted(set(commit_paths) - allowed)
                if outside:
                    raise PromotionError(f"ticket #{index}: commit hors allowed_paths: {outside}")
                protected_commit = sorted(set(commit_paths) & dev_worker.OCTOPUS_PRODUCT_PROTECTED_PATHS)
                if protected_commit:
                    raise PromotionError(f"ticket #{index}: commit touche le noyau protégé: {protected_commit}")
                if any(path.startswith("tests/") or path.endswith("/conftest.py") or path == "conftest.py"
                       for path in commit_paths):
                    raise PromotionError(f"ticket #{index}: commit modifie un test")

                numstat = _git(
                    worktree, "diff", "--numstat", f"{commit}^", commit, "--"
                ).stdout.splitlines()
                added = deleted = 0
                files = 0
                for line in numstat:
                    parts = line.split("\t", 2)
                    if len(parts) != 3:
                        continue
                    files += 1
                    try:
                        added += int(parts[0])
                        deleted += int(parts[1])
                    except ValueError:
                        raise PromotionError(f"ticket #{index}: diff binaire/non comptabilisable interdit") from None
                if files > task_input["max_files_changed"]:
                    raise PromotionError(
                        f"ticket #{index}: rayon fichiers réel {files}>{task_input['max_files_changed']}"
                    )
                if added > task_input["max_lines_added"]:
                    raise PromotionError(
                        f"ticket #{index}: lignes ajoutées réelles {added}>{task_input['max_lines_added']}"
                    )
                if deleted > task_input["max_lines_deleted"]:
                    raise PromotionError(
                        f"ticket #{index}: lignes supprimées réelles {deleted}>{task_input['max_lines_deleted']}"
                    )

            commands = task_input["tests"]
            try:
                test_output, green = dev_worker._run_tests(
                    worktree,
                    commands,
                    sandbox="docker",
                    sandbox_image=str(output.get("test_sandbox_image_ref") or ""),
                    expected_image_id=str(output.get("test_sandbox_image") or ""),
                )
            except (dev_worker.DevWorkerError, OSError, subprocess.SubprocessError) as exc:
                raise PromotionError(
                    f"ticket #{index}: vérification des tests impossible: {type(exc).__name__}: {exc}"
                ) from exc
            if not green:
                raise PromotionError(
                    f"ticket #{index}: tests de promotion en échec: {test_output[-2000:]}"
                )

            raw_contract = task_input.get("acceptance_contract")
            try:
                contract = acceptance.validate_contract(raw_contract)
                expected_contract_hash = acceptance.contract_hash(contract)
                acceptance.verify_evidence_file(
                    Path(str(output.get("evidence_path") or "")),
                    expected_sha256=str(output.get("evidence_sha256") or ""),
                    expected_contract_hash=expected_contract_hash,
                    expected_task_id=int(result.get("id") or 0),
                    expected_gate_status="ACCEPTED",
                    expected_artifact_fingerprint=str(
                        output.get("artifact_fingerprint_sha256") or ""
                    ),
                )
                rerun_gate = dev_worker._run_acceptance_gate(
                    worktree,
                    contract,
                    task_id=int(result.get("id") or 0),
                    attempt=0,
                    tests_passed=True,
                    changed_paths=list(output.get("changed_paths") or []),
                    sandbox_image=str(output.get("test_sandbox_image_ref") or ""),
                    expected_image_id=str(output.get("test_sandbox_image") or ""),
                    persist=False,
                )
            except (acceptance.AcceptanceError, dev_worker.DevWorkerError, OSError, subprocess.SubprocessError) as exc:
                raise PromotionError(
                    f"ticket #{index}: preuve acceptance impossible à vérifier: {type(exc).__name__}: {exc}"
                ) from exc
            if rerun_gate["contract_hash"] != expected_contract_hash:
                raise PromotionError(f"ticket #{index}: contract_hash gate rerun incohérent")
            if rerun_gate["artifact_fingerprint_sha256"] != str(
                output.get("artifact_fingerprint_sha256") or ""
            ):
                raise PromotionError(f"ticket #{index}: empreinte artefact différente lors de la promotion")
            if rerun_gate["decision"]["status"] != "ACCEPTED":
                raise PromotionError(
                    f"ticket #{index}: acceptance gate de promotion non ACCEPTED: "
                    f"{rerun_gate['decision']['status']}"
                )

    return {
        **manifest,
        "git_verified": True,
        "base_repository": str(base_repository),
        "night_worktree": str(worktree),
    }
