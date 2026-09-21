from __future__ import annotations

import json

import pytest

from octopus import acceptance


def gui_contract():
    return {
        "version": 1,
        "id": "gui-primary-nav-v1",
        "artifact_type": "desktop_gui",
        "probe": {
            "kind": "tk_navigation",
            "module": "agents.gui.intelligence",
            "class": "EntrepreneurialWorkbench",
            "attribute": "nav_buttons",
        },
        "must": [
            {
                "id": "runtime_launches",
                "fact": "runtime.launched",
                "op": "equals",
                "expected": True,
            },
            {
                "id": "primary_nav_exact",
                "fact": "ui.primary_nav",
                "op": "equals",
                "expected": ["Home", "Operate", "Build", "Review"],
            },
            {
                "id": "no_intelligence_primary",
                "fact": "ui.primary_nav",
                "op": "not_contains",
                "expected": "Intelligence",
            },
        ],
    }


def test_contract_hash_is_stable_under_key_order():
    contract = gui_contract()
    reordered = json.loads(json.dumps(contract))
    reordered["must"][0] = {
        "expected": True,
        "op": "equals",
        "fact": "runtime.launched",
        "id": "runtime_launches",
    }

    assert acceptance.contract_hash(contract) == acceptance.contract_hash(reordered)


def test_contract_rejects_unknown_probe_and_duplicate_must_ids():
    contract = gui_contract()
    contract["probe"]["kind"] = "shell"

    with pytest.raises(acceptance.AcceptanceError, match="probe non supporté"):
        acceptance.validate_contract(contract)

    contract = gui_contract()
    contract["must"][1]["id"] = contract["must"][0]["id"]
    with pytest.raises(acceptance.AcceptanceError, match="dupliqué"):
        acceptance.validate_contract(contract)


def test_contract_rejects_unknown_fields_instead_of_silently_ignoring_them():
    contract = gui_contract()
    contract["visual_reference"] = "this field is not supported in schema v1"
    with pytest.raises(acceptance.AcceptanceError, match="champs inconnus"):
        acceptance.validate_contract(contract)

    contract = gui_contract()
    contract["probe"]["timeout"] = 12
    with pytest.raises(acceptance.AcceptanceError, match="champs inconnus"):
        acceptance.validate_contract(contract)

    contract = gui_contract()
    contract["must"][0]["expectd"] = True
    with pytest.raises(acceptance.AcceptanceError, match="champs inconnus"):
        acceptance.validate_contract(contract)


def test_gate_rejects_the_gui_failure_we_observed():
    contract = gui_contract()
    bundle = acceptance.build_evidence_bundle(
        task_id=52,
        attempt=1,
        contract=contract,
        facts={
            "tests": {"passed": True},
            "runtime": {"launched": True, "exception": None},
            "ui": {
                "primary_nav": ["Home", "Operate", "Build", "Review", "Intelligence"],
            },
        },
    )

    decision = acceptance.evaluate_contract(contract, bundle)

    assert decision["status"] == "REJECTED"
    failed = {item["id"] for item in decision["criteria"] if item["passed"] is False}
    assert failed == {"primary_nav_exact", "no_intelligence_primary"}


def test_gate_rejects_runtime_crash_even_when_tests_are_green():
    contract = gui_contract()
    bundle = acceptance.build_evidence_bundle(
        task_id=52,
        attempt=1,
        contract=contract,
        facts={
            "tests": {"passed": True},
            "runtime": {
                "launched": False,
                "exception": 'TclError: bad event type or keysym "10"',
            },
            "ui": {"primary_nav": None},
        },
    )

    decision = acceptance.evaluate_contract(contract, bundle)

    assert decision["status"] == "REJECTED"
    assert next(item for item in decision["criteria"] if item["id"] == "runtime_launches")["passed"] is False


def test_gate_is_uncertain_when_required_evidence_is_missing():
    contract = gui_contract()
    bundle = acceptance.build_evidence_bundle(
        task_id=1,
        attempt=1,
        contract=contract,
        facts={"tests": {"passed": True}},
    )

    decision = acceptance.evaluate_contract(contract, bundle)

    assert decision["status"] == "UNCERTAIN"
    assert decision["reason"] == "evidence_incomplete"


def test_evidence_file_is_hash_bound_to_contract_and_task(tmp_path):
    contract = gui_contract()
    bundle = acceptance.build_evidence_bundle(
        task_id=7,
        attempt=2,
        contract=contract,
        facts={
            "runtime": {"launched": True},
            "ui": {"primary_nav": ["Home", "Operate", "Build", "Review"]},
        },
    )
    decision = acceptance.evaluate_contract(contract, bundle)
    bundle["gate_decision"] = decision

    path, sha = acceptance.persist_evidence(tmp_path, bundle)
    loaded = acceptance.verify_evidence_file(
        path,
        expected_sha256=sha,
        expected_contract_hash=acceptance.contract_hash(contract),
        expected_task_id=7,
    )

    assert loaded["gate_decision"]["status"] == "ACCEPTED"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(acceptance.AcceptanceError, match="checksum"):
        acceptance.verify_evidence_file(
            path,
            expected_sha256=sha,
            expected_contract_hash=acceptance.contract_hash(contract),
            expected_task_id=7,
        )


def test_evidence_persistence_is_content_addressed_and_non_overwriting(tmp_path):
    contract = gui_contract()
    first = acceptance.build_evidence_bundle(
        task_id=9,
        attempt=1,
        contract=contract,
        facts={
            "runtime": {"launched": True},
            "ui": {"primary_nav": ["Home", "Operate", "Build", "Review"]},
        },
    )
    first["gate_decision"] = acceptance.evaluate_contract(contract, first)

    path_a, sha_a = acceptance.persist_evidence(tmp_path, first)
    original = path_a.read_bytes()

    # Idempotent replay returns the same immutable record.
    path_a2, sha_a2 = acceptance.persist_evidence(tmp_path, first)
    assert path_a2 == path_a
    assert sha_a2 == sha_a
    assert path_a.read_bytes() == original

    # Different evidence for the same task/attempt/contract is appended under a
    # different content-addressed path; the previous record is never overwritten.
    second = acceptance.build_evidence_bundle(
        task_id=9,
        attempt=1,
        contract=contract,
        facts={
            "runtime": {"launched": False, "exception": "TclError: regression"},
            "ui": {"primary_nav": None},
        },
    )
    second["gate_decision"] = acceptance.evaluate_contract(contract, second)
    path_b, sha_b = acceptance.persist_evidence(tmp_path, second)

    assert path_b != path_a
    assert sha_b != sha_a
    assert path_a.read_bytes() == original


def test_verify_evidence_rejects_untrusted_producer_and_wrong_decision_contract(tmp_path):
    contract = gui_contract()
    bundle = acceptance.build_evidence_bundle(
        task_id=11,
        attempt=1,
        contract=contract,
        facts={
            "runtime": {"launched": True},
            "ui": {"primary_nav": ["Home", "Operate", "Build", "Review"]},
        },
    )
    bundle["gate_decision"] = acceptance.evaluate_contract(contract, bundle)

    forged = dict(bundle)
    forged["producer"] = "builder"
    path, sha = acceptance.persist_evidence(tmp_path, forged)
    with pytest.raises(acceptance.AcceptanceError, match="producteur evidence"):
        acceptance.verify_evidence_file(
            path,
            expected_sha256=sha,
            expected_contract_hash=acceptance.contract_hash(contract),
            expected_task_id=11,
        )

    wrong_decision = dict(bundle)
    wrong_decision["gate_decision"] = dict(bundle["gate_decision"])
    wrong_decision["gate_decision"]["contract_hash"] = "0" * 64
    path2, sha2 = acceptance.persist_evidence(tmp_path, wrong_decision)
    with pytest.raises(acceptance.AcceptanceError, match="autre contrat"):
        acceptance.verify_evidence_file(
            path2,
            expected_sha256=sha2,
            expected_contract_hash=acceptance.contract_hash(contract),
            expected_task_id=11,
        )


def test_probe_command_is_closed_world():
    command = acceptance.probe_command(gui_contract())

    assert command[:5] == ["xvfb-run", "-a", "python", "-m", "octopus.acceptance_probe"]
    assert "--module" in command
    assert "agents.gui.intelligence" in command

    contract = gui_contract()
    contract["probe"] = {"kind": "none"}
    assert acceptance.probe_command(contract) is None
