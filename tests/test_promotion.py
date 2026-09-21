from __future__ import annotations

import json

import pytest

from octopus import promotion


BASE = "1" * 40
COMMIT = "2" * 40


def good_report() -> dict:
    return {
        "run_id": "20260921-103347-903ec3",
        "policy": "python_canary",
        "status": "backlog_complete",
        "base_head": BASE,
        "final_head": COMMIT,
        "tickets": [{
            "index": 1,
            "status": "done",
            "allowed_paths": ["octopus/resources.py"],
            "night_head": COMMIT,
            "result": {
                "status": "done",
                "input": {
                    "strict_repository_preflight": True,
                    "require_baseline_oracle": True,
                    "python_canary_ast": True,
                    "allow_declarative_fallback": False,
                },
                "output": {
                    "backend": "kilo",
                    "commit": COMMIT,
                    "changed_paths": ["octopus/resources.py"],
                    "test_sandbox": "docker",
                    "oracle_tests": 17,
                },
            },
        }],
    }


def test_build_manifest_accepts_strict_python_canary():
    manifest = promotion.build_manifest(good_report())

    assert manifest == {
        "run_id": "20260921-103347-903ec3",
        "policy": "python_canary",
        "base_head": BASE,
        "final_head": COMMIT,
        "ticket_count": 1,
        "commits": [COMMIT],
        "changed_paths": ["octopus/resources.py"],
        "requires_human_review": True,
        "auto_merge": False,
    }


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r.update(status="consecutive_failures"), "non promouvable"),
    (lambda r: r["tickets"][0]["result"]["output"].update(backend="declarative"), "backend non Kilo"),
    (lambda r: r["tickets"][0]["result"]["output"].update(changed_paths=["octopus/economy.py"]), "hors périmètre"),
    (lambda r: r["tickets"][0]["result"]["output"].update(test_sandbox="host"), "sandbox Docker requis"),
    (lambda r: r["tickets"][0]["result"]["output"].update(oracle_tests=0), "oracle_tests positif"),
    (lambda r: r["tickets"][0]["result"]["input"].update(require_baseline_oracle=False), "require_baseline_oracle"),
    (lambda r: r.update(final_head="3" * 40), "dernier night_head"),
])
def test_build_manifest_rejects_unsafe_reports(mutate, message):
    report = good_report()
    mutate(report)

    with pytest.raises(promotion.PromotionError, match=message):
        promotion.build_manifest(report)


def test_load_report_rejects_non_object(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with pytest.raises(promotion.PromotionError, match="objet attendu"):
        promotion.load_report(path)
