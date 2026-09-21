"""Fail-closed validation for promoting a completed night-shift run.

This module never pushes, merges, or changes Git refs. It turns a night-shift JSON
report into a small review manifest only when every recorded ticket satisfies the
expected execution invariants.
"""
from __future__ import annotations

import json
import re
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
    if policy not in {"docs_only", "python_canary"}:
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
