"""Capability model, validation and deterministic safety policy."""
from __future__ import annotations

import pytest

from octopus.capabilities import (
    CAPABILITY_KINDS,
    COST_CLASSES,
    RISK_LEVELS,
    ZERO_COST_CLASSES,
    Capability,
    CapabilityError,
    CapabilityEvaluation,
    CapabilityKind,
    CapabilityModel,
    CandidateEvaluation,
    CostClass,
    EvaluationResult,
    EvaluationStatus,
    RiskLevel,
    capability_gaps,
    detect_capability_gaps,
    automatically_usable,
    evaluate_capabilities,
    evaluate_candidate,
    evaluate_capability,
    find_capability_gaps,
    is_automatically_allowed,
    is_rejected,
    missing_capabilities,
    requires_human_action,
    usable_capability_ids,
)


def test_zero_cost_low_risk_is_automatic():
    cap = Capability(
        identifier="free-low",
        name="Free Low Risk",
        kind=CapabilityKind.API,
        cost_class=CostClass.ZERO_COST,
        risk_level=RiskLevel.LOW,
    )
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.ALLOWED
    assert result.reasons == ()
    assert result.required_actions == ()
    assert result.automatically_allowed
    assert not result.human_required
    assert not result.prohibited


def test_free_quota_and_local_are_automatic():
    cap = Capability(identifier="free-quota", kind=CapabilityKind.API, cost_class=CostClass.FREE_QUOTA)
    assert evaluate_capability(cap).status == EvaluationStatus.ALLOWED

    cap = Capability(identifier="local", kind=CapabilityKind.API, cost_class=CostClass.LOCAL)
    assert evaluate_capability(cap).status == EvaluationStatus.ALLOWED


def test_secret_requirement_triggers_human_action():
    cap = Capability(identifier="secret-req", kind=CapabilityKind.API, requires_secret=True)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("secret",)
    assert result.required_actions == ("human_action",)
    assert result.human_required


def test_oauth_requirement_triggers_human_action():
    cap = Capability(identifier="oauth-req", kind=CapabilityKind.API, requires_oauth=True)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("oauth",)
    assert result.required_actions == ("human_action",)


def test_payment_requirement_triggers_human_approval():
    cap = Capability(identifier="paid", kind=CapabilityKind.API, requires_payment=True)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("payment",)
    assert result.required_actions == ("human_approval",)


def test_kyc_requirement_triggers_human_action():
    cap = Capability(identifier="kyc-req", kind=CapabilityKind.API, requires_kyc=True)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("kyc",)
    assert result.required_actions == ("human_action",)


def test_high_risk_triggers_human_approval():
    cap = Capability(identifier="high-risk", kind=CapabilityKind.API, risk_level=RiskLevel.HIGH)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("high_risk",)
    assert result.required_actions == ("human_approval",)


def test_critical_risk_triggers_human_approval():
    cap = Capability(identifier="critical", kind=CapabilityKind.API, risk_level=RiskLevel.CRITICAL)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("high_risk",)
    assert result.required_actions == ("human_approval",)


def test_paid_cost_class_triggers_payment_reason():
    cap = Capability(identifier="paid-cost", kind=CapabilityKind.API, cost_class=CostClass.PAID)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("payment",)
    assert result.required_actions == ("human_approval",)


def test_zero_cost_with_requirements_still_needs_human():
    cap = Capability(
        identifier="zero-but-secret",
        kind=CapabilityKind.API,
        cost_class=CostClass.ZERO_COST,
        requires_secret=True,
    )
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("secret",)
    assert result.required_actions == ("human_action",)


def test_unavailable_and_disabled_are_rejected():
    cap = Capability(identifier="unavail", kind=CapabilityKind.API, available=False)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.REJECTED
    assert result.reasons == ("unavailable",)
    assert result.prohibited

    cap = Capability(identifier="dis", kind=CapabilityKind.API, enabled=False)
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.REJECTED
    assert result.reasons == ("disabled",)
    assert result.prohibited


def test_deterministic_ordering_of_evaluations():
    caps = [
        Capability(identifier="z", kind=CapabilityKind.API, risk_level=RiskLevel.HIGH),
        Capability(identifier="a", kind=CapabilityKind.API, requires_secret=True),
        Capability(identifier="m", kind=CapabilityKind.API, requires_payment=True),
        Capability(identifier="a", kind=CapabilityKind.API, risk_level=RiskLevel.LOW),
    ]
    results = evaluate_capabilities(caps)
    assert [r.capability.identifier for r in results] == ["a", "a", "m", "z"]
    assert results[0].reasons == ("secret",)
    assert results[2].reasons == ("payment",)
    assert results[3].reasons == ("high_risk",)


def test_deterministic_reason_and_action_ordering():
    cap = Capability(
        identifier="multi",
        kind=CapabilityKind.API,
        requires_payment=True,
        requires_secret=True,
        requires_oauth=True,
        requires_kyc=True,
        risk_level=RiskLevel.HIGH,
    )
    result = evaluate_capability(cap)
    assert result.status == EvaluationStatus.HUMAN_REQUIRED
    assert result.reasons == ("payment", "secret", "oauth", "kyc", "high_risk")
    assert result.required_actions == ("human_approval", "human_action")


def test_capability_gap_detection():
    required = [
        {"identifier": "a", "kind": "api"},
        {"identifier": "b", "kind": "api"},
        "c",
    ]
    usable = [
        Capability(identifier="a", kind=CapabilityKind.API),
    ]
    gaps = detect_capability_gaps(required, usable)
    assert gaps == ["b", "c"]


def test_capability_gap_detection_with_evaluations():
    required = ["a", "b", "c"]
    usable = [
        CapabilityEvaluation(
            Capability(identifier="a", kind=CapabilityKind.API),
            EvaluationStatus.ALLOWED,
        ),
        CapabilityEvaluation(
            Capability(identifier="b", kind=CapabilityKind.API, requires_secret=True),
            EvaluationStatus.HUMAN_REQUIRED,
        ),
    ]
    gaps = detect_capability_gaps(required, usable)
    assert gaps == ["b", "c"]


def test_enum_normalization_preserves_historical_separator_semantics():
    cap = Capability(identifier="free-quota", kind="local_tool", cost_class="  FREE-QUOTA  ")
    assert cap.cost_class == CostClass.FREE_QUOTA

    for value in ("free  quota", "free\tquota", "free\u00a0quota", "free--quota"):
        with pytest.raises(CapabilityError, match="cost_class invalide"):
            Capability(identifier="bad-normalization", cost_class=value)


def test_invalid_enum_values_raise():
    with pytest.raises(CapabilityError, match="kind invalide"):
        Capability(identifier="bad", kind="invalid_kind")
    with pytest.raises(CapabilityError, match="risk_level invalide"):
        Capability(identifier="bad", risk_level="invalid")
    with pytest.raises(CapabilityError, match="cost_class invalide"):
        Capability(identifier="bad", cost_class="invalid")
    with pytest.raises(CapabilityError, match="available invalide"):
        Capability(identifier="bad", available="invalid")


def test_invalid_bool_values_raise():
    with pytest.raises(CapabilityError, match="requires_secret invalide"):
        Capability(identifier="bad", requires_secret="yes")
    with pytest.raises(CapabilityError, match="requires_oauth invalide"):
        Capability(identifier="bad", requires_oauth="no")
    with pytest.raises(CapabilityError, match="requires_payment invalide"):
        Capability(identifier="bad", requires_payment="true")
    with pytest.raises(CapabilityError, match="requires_kyc invalide"):
        Capability(identifier="bad", requires_kyc="false")


def test_risk_level_none_handling():
    with pytest.raises(CapabilityError, match="risk_level invalide"):
        Capability(identifier="x", kind=CapabilityKind.API, risk_level=None, risk=None)

    cap = Capability(identifier="x2", kind=CapabilityKind.API, risk_level=None, risk="high")
    assert cap.risk_level == RiskLevel.HIGH

    cap = Capability(identifier="x3", kind=CapabilityKind.API, risk_level="medium", risk=None)
    assert cap.risk_level == RiskLevel.MEDIUM


def test_conflicting_aliases_raise():
    with pytest.raises(CapabilityError, match="cost_class conflicting values"):
        Capability(identifier="x", kind=CapabilityKind.API, cost="free", cost_class="paid")

    with pytest.raises(CapabilityError, match="risk_level conflicting values"):
        Capability(identifier="x", kind=CapabilityKind.API, risk="high", risk_level="low")

    with pytest.raises(CapabilityError, match="available conflicting values"):
        Capability(identifier="x", kind=CapabilityKind.API, available=True, availability=False)

    with pytest.raises(CapabilityError, match="enabled conflicts with enabled_state"):
        Capability(identifier="x", kind=CapabilityKind.API, enabled=True, enabled_state=False)


def test_unknown_extra_kwargs_raise():
    with pytest.raises(CapabilityError, match="champs inconnus"):
        Capability(identifier="x", kind=CapabilityKind.API, unknown_field="value")


def test_known_extras_merge_into_provenance():
    cap = Capability(identifier="x", kind=CapabilityKind.API, source_ref="src", confidence=0.9)
    assert cap.provenance.get("source_ref") == "src"
    assert cap.provenance.get("confidence") == 0.9


def test_enum_string_coercion_and_aliases():
    cap = Capability(identifier="coerce", kind="mcp_server", risk_level="high_risk", cost_class="free")
    assert cap.kind == CapabilityKind.MCP
    assert cap.risk_level == RiskLevel.HIGH
    assert cap.cost_class == CostClass.ZERO_COST


def test_available_and_enabled_aliases():
    cap = Capability(identifier="avail", kind=CapabilityKind.API, is_available=False)
    assert not cap.available
    assert not cap.is_available

    cap = Capability(identifier="enabled", kind=CapabilityKind.API, is_enabled=False)
    assert not cap.enabled
    assert not cap.is_enabled


def test_compatibility_properties():
    cap = Capability(identifier="x", kind=CapabilityKind.API, secret_required=True)
    assert cap.secret_required is True
    assert cap.api_key_required is True

    cap = Capability(identifier="x", kind=CapabilityKind.API, payment_required=True)
    assert cap.payment_required is True

    cap = Capability(identifier="x", kind=CapabilityKind.API, oauth_required=True)
    assert cap.oauth_required is True

    cap = Capability(identifier="x", kind=CapabilityKind.API, kyc_required=True)
    assert cap.kyc_required is True


def test_human_only_alias_maps_to_kyc():
    cap = Capability(identifier="human-only", kind=CapabilityKind.API, human_only=True)
    assert cap.requires_kyc is True


def test_from_dict_and_to_dict_roundtrip():
    data = {
        "identifier": "roundtrip",
        "name": "Roundtrip",
        "kind": "api",
        "available": True,
        "enabled": True,
        "cost_class": "zero_cost",
        "requires_secret": False,
        "requires_oauth": True,
        "requires_payment": False,
        "requires_kyc": False,
        "risk_level": "low",
    }
    cap = Capability.from_dict(data)
    assert cap.identifier == "roundtrip"
    assert cap.requires_oauth is True
    result = cap.to_dict()
    assert result["id"] == "roundtrip"
    assert result["requires_oauth"] is True
    assert cap.as_dict() == result


def test_from_dict_with_aliases():
    data = {
        "id": "from-dict-alias",
        "kind": "mcp",
        "secret_required": True,
        "oauth_required": True,
        "payment_required": True,
        "kyc_required": True,
        "api_key_required": True,
        "cost": "paid",
        "risk": "critical",
    }
    cap = Capability.from_dict(data)
    assert cap.identifier == "from-dict-alias"
    assert cap.requires_secret is True
    assert cap.requires_oauth is True
    assert cap.requires_payment is True
    assert cap.requires_kyc is True
    assert cap.cost_class == CostClass.PAID
    assert cap.risk_level == RiskLevel.CRITICAL


def test_evaluation_properties_and_getitem():
    cap = Capability(identifier="x", kind=CapabilityKind.API)
    ev = CapabilityEvaluation(cap, EvaluationStatus.ALLOWED, ("secret",), ("human_action",))
    assert ev.decision == EvaluationStatus.ALLOWED
    assert ev.verdict == EvaluationStatus.ALLOWED
    assert ev.reason == "secret"
    assert ev.action == "human_action"
    assert ev.automatically_allowed
    assert not ev.human_required
    assert not ev.prohibited
    assert ev["capability"] is cap
    assert ev["status"] == EvaluationStatus.ALLOWED
    assert ev["decision"] == EvaluationStatus.ALLOWED
    assert ev["verdict"] == EvaluationStatus.ALLOWED
    assert ev["reasons"] == ("secret",)
    assert ev["required_actions"] == ("human_action",)
    assert ev["actions"] == ("human_action",)
    assert ev["id"] == "x"
    with pytest.raises(KeyError):
        ev["unknown"]
    assert ev.get("unknown", "default") == "default"


def test_evaluation_equality():
    cap = Capability(identifier="x", kind=CapabilityKind.API)
    ev1 = CapabilityEvaluation(cap, EvaluationStatus.ALLOWED)
    ev2 = CapabilityEvaluation(cap, EvaluationStatus.ALLOWED)
    assert ev1 == ev2
    assert ev1 == EvaluationStatus.ALLOWED
    assert ev1 != EvaluationStatus.HUMAN_REQUIRED


def test_evaluation_as_dict():
    cap = Capability(identifier="x", kind=CapabilityKind.API)
    ev = CapabilityEvaluation(cap, EvaluationStatus.HUMAN_REQUIRED, ("payment",), ("human_approval",))
    d = ev.as_dict()
    assert d == {
        "id": "x",
        "status": "human_required",
        "reasons": ["payment"],
        "required_actions": ["human_approval"],
    }


def test_compatibility_aliases():
    assert CapabilityModel is Capability
    assert EvaluationResult is CapabilityEvaluation
    assert CandidateEvaluation is CapabilityEvaluation
    assert capability_gaps is detect_capability_gaps
    assert find_capability_gaps is detect_capability_gaps
    assert missing_capabilities is detect_capability_gaps
    assert evaluate_candidate is evaluate_capability
    assert is_automatically_allowed(Capability(identifier="a", kind=CapabilityKind.API)) is True
    assert requires_human_action(Capability(identifier="b", kind=CapabilityKind.API, requires_secret=True)) is True
    assert is_rejected(Capability(identifier="c", kind=CapabilityKind.API, available=False)) is True


def test_usable_capability_ids():
    caps = [
        {"identifier": "z", "kind": "api", "risk_level": "high"},
        {"identifier": "a", "kind": "api"},
    ]
    assert usable_capability_ids(caps) == ["a"]


def test_automatically_usable():
    caps = [
        {"identifier": "z", "kind": "api", "risk_level": "high"},
        {"identifier": "a", "kind": "api"},
    ]
    auto = automatically_usable(caps)
    assert len(auto) == 1
    assert auto[0].identifier == "a"


def test_evaluate_capabilities_with_mappings():
    results = evaluate_capabilities([
        {"identifier": "z", "kind": "api", "risk_level": "high"},
        {"identifier": "a", "kind": "api", "requires_secret": True},
    ])
    assert [r.capability.identifier for r in results] == ["a", "z"]


def test_zero_cost_classes_constant():
    assert ZERO_COST_CLASSES == frozenset({"zero_cost", "free_quota", "local"})
    assert CAPABILITY_KINDS == ("mcp", "skill", "plugin", "api", "local_tool")
    assert RISK_LEVELS == ("low", "medium", "high", "critical")
    assert COST_CLASSES == ("zero_cost", "free_quota", "local", "paid")
