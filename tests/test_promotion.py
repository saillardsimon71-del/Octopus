from __future__ import annotations

import json
import subprocess

import pytest

from octopus import acceptance, promotion


BASE = "1" * 40
COMMIT = "2" * 40


def product_contract() -> dict:
    return {
        "version": 1,
        "id": "promotion-product-v1",
        "artifact_type": "code",
        "probe": {"kind": "none"},
        "must": [
            {
                "id": "tests_green",
                "fact": "tests.passed",
                "op": "equals",
                "expected": True,
            }
        ],
    }


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
                "id": 101,
                "status": "done",
                "input": {
                    "strict_repository_preflight": True,
                    "require_baseline_oracle": True,
                    "python_canary_ast": True,
                    "allow_declarative_fallback": False,
                    "allowed_paths": ["octopus/resources.py"],
                    "tests": [["python", "-m", "pytest", "-q", "tests/test_resources.py"]],
                    "max_files_changed": 1,
                    "max_lines_added": 20,
                    "max_lines_deleted": 20,
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


def good_product_report() -> dict:
    return {
        "run_id": "20260921-product-001",
        "policy": "product_ticket",
        "status": "backlog_complete",
        "base_head": BASE,
        "final_head": COMMIT,
        "tickets": [{
            "index": 1,
            "status": "done",
            "allowed_paths": ["octopus/resources.py", "octopus/config/catalog.json"],
            "night_head": COMMIT,
            "result": {
                "id": 202,
                "status": "done",
                "input": {
                    "strict_repository_preflight": True,
                    "require_baseline_oracle": True,
                    "python_canary_ast": False,
                    "allow_declarative_fallback": False,
                    "self_modification_policy": "product_ticket",
                    "allowed_paths": ["octopus/resources.py", "octopus/config/catalog.json"],
                    "tests": [["python", "-m", "pytest", "-q", "tests/test_resources.py"]],
                    "acceptance_contract": product_contract(),
                    "test_sandbox_image": "octopus-test-sandbox:py311",
                    "test_sandbox_image_id": "sha256:" + "a" * 64,
                    "max_files_changed": 2,
                    "max_lines_added": 1200,
                    "max_lines_deleted": 900,
                },
                "output": {
                    "backend": "kilo",
                    "self_policy": "product_ticket",
                    "commit": COMMIT,
                    "changed_paths": ["octopus/resources.py"],
                    "test_sandbox": "docker",
                    "test_sandbox_image": "sha256:" + "a" * 64,
                    "test_sandbox_image_ref": "octopus-test-sandbox:py311",
                    "tests_passed": True,
                    "baseline_oracle_runs": 2,
                    "oracle_tests": 17,
                    "post_oracle_tests": 17,
                    "acceptance_contract_hash": acceptance.contract_hash(product_contract()),
                    "gate_status": "ACCEPTED",
                    "evidence_path": "/tmp/octopus-evidence.json",
                    "evidence_sha256": "b" * 64,
                },
            },
        }],
    }


def test_build_manifest_accepts_product_ticket_with_human_review():
    manifest = promotion.build_manifest(good_product_report())

    assert manifest["policy"] == "product_ticket"
    assert manifest["commits"] == [COMMIT]
    assert manifest["changed_paths"] == ["octopus/resources.py"]
    assert manifest["requires_human_review"] is True
    assert manifest["auto_merge"] is False


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["tickets"][0]["result"]["output"].update(self_policy="scoped_kilo"), "self_policy"),
    (lambda r: r["tickets"][0]["result"]["input"].update(self_modification_policy="python_canary"),
     "self_modification_policy"),
    (lambda r: r["tickets"][0]["result"]["output"].update(test_sandbox_image="octopus-test-sandbox:py311"),
     "image sandbox résolue"),
    (lambda r: r["tickets"][0]["result"]["output"].update(tests_passed=False), "tests verts"),
    (lambda r: r["tickets"][0]["result"]["output"].update(baseline_oracle_runs=1), "baseline oracle"),
    (lambda r: r["tickets"][0]["result"]["output"].update(post_oracle_tests=16), "oracle final"),
    (lambda r: r["tickets"][0]["result"]["output"].update(gate_status="REJECTED"), "gate_status"),
    (lambda r: r["tickets"][0]["result"]["output"].update(acceptance_contract_hash="0" * 64), "acceptance_contract_hash"),
    (lambda r: (
        r["tickets"][0].update(allowed_paths=["agents/browser.py"]),
        r["tickets"][0]["result"]["output"].update(changed_paths=["agents/browser.py"]),
    ), "noyau product_ticket"),
    (lambda r: (
        r["tickets"][0].update(allowed_paths=["tests/test_resources.py"]),
        r["tickets"][0]["result"]["output"].update(changed_paths=["tests/test_resources.py"]),
    ), "modification des tests"),
    (lambda r: r["tickets"][0]["result"]["input"].update(max_files_changed=21), "max_files_changed"),
    (lambda r: r["tickets"][0]["result"]["input"].update(max_lines_added=5001), "max_lines_added"),
])
def test_build_manifest_rejects_invalid_product_ticket_proof(mutate, message):
    report = good_product_report()
    mutate(report)
    report["tickets"][0]["result"]["input"]["allowed_paths"] = report["tickets"][0]["allowed_paths"]

    with pytest.raises(promotion.PromotionError, match=message):
        promotion.build_manifest(report)


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
    (lambda r: r["tickets"][0]["result"]["input"].update(tests=[["python", "-m", "pytest", "-q", "tests/test_capabilities.py"]]), "oracle worker attendu"),
    (lambda r: r["tickets"][0]["result"]["input"].update(max_files_changed=2), "max_files_changed invalide"),
    (lambda r: r["tickets"][0]["result"]["input"].update(max_lines_added=81), "max_lines_added hors politique"),
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

    base_repo = tmp_path / "base"
    subprocess.run(["git", "clone", "-q", str(repo), str(base_repo)], check=True)
    subprocess.run(["git", "checkout", "-q", "--detach", base], cwd=base_repo, check=True)
    return base_repo, repo, base, final


def report_for_repo(base_repo, repo, base, final):
    report = good_report()
    report["base_head"] = base
    report["final_head"] = final
    report["base_repository"] = str(base_repo)
    report["night_worktree"] = str(repo)
    report["tickets"][0]["night_head"] = final
    report["tickets"][0]["result"]["output"]["commit"] = final
    return report


def test_verify_git_accepts_exact_night_history(tmp_path):
    base_repo, repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(base_repo, repo, base, final)
    manifest = promotion.build_manifest(report)

    verified = promotion.verify_git(report, manifest)

    assert verified["git_verified"] is True
    assert verified["base_repository"] == str(base_repo.resolve())
    assert verified["night_worktree"] == str(repo.resolve())
    assert verified["commits"] == [final]
    assert verified["changed_paths"] == ["octopus/resources.py"]


def product_report_for_repo(base_repo, repo, base, final):
    report = good_product_report()
    report["base_head"] = base
    report["final_head"] = final
    report["base_repository"] = str(base_repo)
    report["night_worktree"] = str(repo)
    report["tickets"][0]["night_head"] = final
    report["tickets"][0]["allowed_paths"] = ["octopus/resources.py"]
    result = report["tickets"][0]["result"]
    result["input"]["allowed_paths"] = ["octopus/resources.py"]
    result["input"]["max_files_changed"] = 1
    result["output"]["commit"] = final
    result["output"]["changed_paths"] = ["octopus/resources.py"]
    bundle = acceptance.build_evidence_bundle(
        task_id=result["id"],
        attempt=1,
        contract=result["input"]["acceptance_contract"],
        facts={
            "tests": {"passed": True},
            "git": {"changed_paths": ["octopus/resources.py"]},
        },
    )
    bundle["gate_decision"] = acceptance.evaluate_contract(
        result["input"]["acceptance_contract"], bundle,
    )
    evidence_path, evidence_sha = acceptance.persist_evidence(repo.parent, bundle)
    result["output"]["evidence_path"] = str(evidence_path)
    result["output"]["evidence_sha256"] = evidence_sha
    return report


def test_verify_git_requires_human_review_contract(tmp_path):
    base_repo, repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(base_repo, repo, base, final)
    manifest = promotion.build_manifest(report)
    manifest["requires_human_review"] = False

    with pytest.raises(promotion.PromotionError, match="revue humaine"):
        promotion.verify_git(report, manifest)


def test_verify_git_product_ticket_rechecks_exact_commit_and_tests(tmp_path, monkeypatch):
    from octopus import dev_worker

    base_repo, repo, base, final = _night_repo(tmp_path)
    report = product_report_for_repo(base_repo, repo, base, final)
    manifest = promotion.build_manifest(report)
    seen = {}

    def green(worktree, commands, *, sandbox, sandbox_image, expected_image_id=None):
        seen.update(
            worktree=worktree,
            commands=commands,
            sandbox=sandbox,
            sandbox_image=sandbox_image,
            expected_image_id=expected_image_id,
        )
        return ("1 passed", True)

    monkeypatch.setattr(dev_worker, "_run_tests", green)

    verified = promotion.verify_git(report, manifest)

    assert verified["git_verified"] is True
    assert seen["sandbox"] == "docker"
    assert seen["commands"] == [["python", "-m", "pytest", "-q", "tests/test_resources.py"]]
    assert seen["sandbox_image"] == "octopus-test-sandbox:py311"
    assert seen["expected_image_id"].startswith("sha256:")


def test_verify_git_product_ticket_fails_if_promotion_tests_are_red(tmp_path, monkeypatch):
    from octopus import dev_worker

    base_repo, repo, base, final = _night_repo(tmp_path)
    report = product_report_for_repo(base_repo, repo, base, final)
    manifest = promotion.build_manifest(report)
    monkeypatch.setattr(
        dev_worker, "_run_tests",
        lambda *args, **kwargs: ("FAILED tests/test_resources.py::test_x", False),
    )

    with pytest.raises(promotion.PromotionError, match="tests de promotion en échec"):
        promotion.verify_git(report, manifest)


def test_verify_git_product_ticket_enforces_real_diff_radius(tmp_path, monkeypatch):
    from octopus import dev_worker

    base_repo, repo, base, _ = _night_repo(tmp_path)
    subprocess.run(["git", "reset", "--hard", base], cwd=repo, check=True, capture_output=True)
    path = repo / "octopus" / "resources.py"
    path.write_text("VALUE = 3\nEXTRA = 1\nMORE = 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "wider"], cwd=repo, check=True)
    wider = _git(repo, "rev-parse", "HEAD")

    report = product_report_for_repo(base_repo, repo, base, wider)
    report["tickets"][0]["result"]["input"]["max_lines_added"] = 1
    report["tickets"][0]["result"]["input"]["max_lines_deleted"] = 1
    manifest = promotion.build_manifest(report)
    monkeypatch.setattr(dev_worker, "_run_tests", lambda *args, **kwargs: ("green", True))

    with pytest.raises(promotion.PromotionError, match="lignes ajoutées réelles"):
        promotion.verify_git(report, manifest)


def test_verify_git_rejects_reported_paths_that_do_not_match_diff(tmp_path):
    base_repo, repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(base_repo, repo, base, final)
    report["tickets"][0]["allowed_paths"] = ["octopus/capabilities.py"]
    report["tickets"][0]["result"]["input"]["allowed_paths"] = ["octopus/capabilities.py"]
    report["tickets"][0]["result"]["input"]["tests"] = [
        ["python", "-m", "pytest", "-q", "tests/test_capabilities.py"]
    ]
    report["tickets"][0]["result"]["output"]["changed_paths"] = ["octopus/capabilities.py"]
    manifest = promotion.build_manifest(report)

    with pytest.raises(promotion.PromotionError, match="diff Git différent du rapport"):
        promotion.verify_git(report, manifest)


def test_verify_git_rejects_dirty_or_advanced_worktree(tmp_path):
    base_repo, repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(base_repo, repo, base, final)
    manifest = promotion.build_manifest(report)

    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(promotion.PromotionError, match="non propre"):
        promotion.verify_git(report, manifest)


def test_verify_git_rejects_stale_base_repository(tmp_path):
    base_repo, repo, base, final = _night_repo(tmp_path)
    report = report_for_repo(base_repo, repo, base, final)
    manifest = promotion.build_manifest(report)

    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=base_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=base_repo, check=True)
    marker = base_repo / "stale.txt"
    marker.write_text("advance\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=base_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "advance"], cwd=base_repo, check=True)

    with pytest.raises(promotion.PromotionError, match="HEAD base_repository différent"):
        promotion.verify_git(report, manifest)
