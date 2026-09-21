from __future__ import annotations

import json
import subprocess

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



def _git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _night_repo(tmp_path):
    repo = tmp_path / "night"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    path = repo / "octopus" / "resources.py"
    path.parent.mkdir()
    path.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = _git(repo, "rev-parse", "HEAD")
    path.write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "canary"], cwd=repo, check=True)
    final = _git(repo, "rev-parse", "HEAD")
    return repo, base, final


def report_for_repo(repo, base, final):
    report = good_report()
    report["base_head"] = base
    report["final_head"] = final
    report["night_worktree"] = str(repo)
    report["tickets"][0]["night_head"] = final
    report["tickets"][0]["result"]["output"]["commit"] = final
    return report


def test_verify_git_accepts_exact_night_history(tmp_path):
    repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(repo, base, final)
    manifest = promotion.build_manifest(report)

    verified = promotion.verify_git(report, manifest)

    assert verified["git_verified"] is True
    assert verified["night_worktree"] == str(repo.resolve())
    assert verified["commits"] == [final]
    assert verified["changed_paths"] == ["octopus/resources.py"]


def test_verify_git_rejects_reported_paths_that_do_not_match_diff(tmp_path):
    repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(repo, base, final)
    report["tickets"][0]["allowed_paths"] = ["octopus/capabilities.py"]
    report["tickets"][0]["result"]["output"]["changed_paths"] = ["octopus/capabilities.py"]
    manifest = promotion.build_manifest(report)

    with pytest.raises(promotion.PromotionError, match="diff Git différent du rapport"):
        promotion.verify_git(report, manifest)


def test_verify_git_rejects_dirty_or_advanced_worktree(tmp_path):
    repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(repo, base, final)
    manifest = promotion.build_manifest(report)

    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(promotion.PromotionError, match="non propre"):
        promotion.verify_git(report, manifest)
