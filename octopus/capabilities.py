"""Internal capability model and deterministic safety policy.

This module describes candidates; it does not discover, activate, or execute them.
The existing resource inventory remains the source of truth for observed resources,
and the existing cost vocabulary is reused for capability cost classes.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CapabilityError(ValueError):
    """A capability or evaluation input is invalid."""


class CapabilityKind(StrEnum):
    MCP = "mcp"
    SKILL = "skill"
    PLUGIN = "plugin"
    API = "api"
    LOCAL_TOOL = "local_tool"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CostClass(StrEnum):
    ZERO_COST = "zero_cost"
    FREE_QUOTA = "free_quota"
    LOCAL = "local"
    PAID = "paid"


class EvaluationStatus(StrEnum):
    ALLOWED = "allowed"
    AUTOMATIC = "allowed"
    AUTOMATICALLY_ALLOWED = "allowed"
    HUMAN_REQUIRED = "human_required"
    HUMAN_ACTION_REQUIRED = "human_required"
    HUMAN_APPROVAL_REQUIRED = "human_required"
    REJECTED = "rejected"
    PROHIBITED = "rejected"


CapabilityRisk = RiskLevel
CapabilityCostClass = CostClass
EvaluationDecision = EvaluationStatus

CAPABILITY_KINDS = tuple(kind.value for kind in CapabilityKind)
RISK_LEVELS = tuple(level.value for level in RiskLevel)
COST_CLASSES = tuple(cost.value for cost in CostClass)
ZERO_COST_CLASSES = frozenset(
    {CostClass.ZERO_COST.value, CostClass.FREE_QUOTA.value, CostClass.LOCAL.value}
)

_REASON_ORDER = ("payment", "secret", "oauth", "kyc", "high_risk")
_ACTION_ORDER = ("human_approval", "human_action")
_MISSING = object()


def _error(field_name: str, value: Any, expected: str) -> CapabilityError:
    return CapabilityError(f"{field_name} invalide : {value!r} (attendu : {expected})")


def _normalize_lower_text(value: str) -> str:
    return value.strip().lower()


def _normalize_enum_value(value: str) -> str:
    return _normalize_lower_text(value).replace("-", "_").replace(" ", "_")


def _enum_expected_values(enum_type: type[StrEnum]) -> str:
    return ", ".join(item.value for item in enum_type)


def _coerce_enum(value: Any, enum_type: type[StrEnum], field_name: str) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise _error(field_name, value, _enum_expected_values(enum_type))
    normalized = _normalize_enum_value(value)
    aliases = {
        CapabilityKind: {
            "mcp_server": CapabilityKind.MCP,
            "local_tool": CapabilityKind.LOCAL_TOOL,
        },
        RiskLevel: {
            "none": RiskLevel.LOW,
            "low_risk": RiskLevel.LOW,
            "medium_risk": RiskLevel.MEDIUM,
            "high_risk": RiskLevel.HIGH,
            "critical_risk": RiskLevel.CRITICAL,
        },
        CostClass: {
            "free": CostClass.ZERO_COST,
            "free_cost": CostClass.ZERO_COST,
            "no_cost": CostClass.ZERO_COST,
            "zero": CostClass.ZERO_COST,
            "zero_cost": CostClass.ZERO_COST,
        },
        EvaluationStatus: {
            "automatic": EvaluationStatus.ALLOWED,
            "automatically_allowed": EvaluationStatus.ALLOWED,
            "human_action_required": EvaluationStatus.HUMAN_REQUIRED,
            "human_approval_required": EvaluationStatus.HUMAN_REQUIRED,
            "prohibited": EvaluationStatus.REJECTED,
        },
    }
    if normalized in aliases.get(enum_type, {}):
        return aliases[enum_type][normalized]
    try:
        return enum_type(normalized)
    except ValueError:
        raise _error(field_name, value, _enum_expected_values(enum_type)) from None


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise _error(field_name, value, "bool")


def _coerce_availability(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = _normalize_lower_text(value)
        if normalized in {"available", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"unavailable", "false", "no", "off", "disabled"}:
            return False
    raise _error(field_name, value, "bool or availability state")


def _copy_metadata(value: Any, field_name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if field_name == "provenance" and isinstance(value, str):
        return value
    raise _error(field_name, value, "mapping or provenance string")


def _merge_metadata(value: Any, updates: Mapping[str, Any]) -> Any:
    if value is None:
        return dict(updates)
    if isinstance(value, Mapping):
        return {**value, **updates}
    return {"value": value, **updates}


def _validate_identifier(identifier: Any) -> str:
    if not isinstance(identifier, str) or not identifier.strip():
        raise CapabilityError("identifier invalide : une cle non vide est requise")
    if identifier != identifier.strip() or any(character.isspace() for character in identifier):
        raise CapabilityError(f"identifier invalide : {identifier!r} (aucun espace)")
    if len(identifier) > 256:
        raise CapabilityError("identifier invalide : trop long")
    return identifier


def _validate_name(name: Any, fallback: str | None = None) -> str:
    if name is None:
        if fallback is not None:
            return fallback
        raise CapabilityError("name invalide : une valeur non vide est requise")
    if not isinstance(name, str) or not name.strip():
        raise CapabilityError("name invalide : une valeur non vide est requise")
    return name.strip()


def _resolve(primary: Any, *aliases: Any, default: Any = None, coerce: Any = None, field_name: str = "value") -> Any:
    values = [value for value in (primary, *aliases) if value is not None and value is not _MISSING]
    if not values:
        return default
    converted = [coerce(value) for value in values] if coerce else values
    if any(value != converted[0] for value in converted[1:]):
        raise CapabilityError(f"{field_name} conflicting values")
    return converted[0]


@dataclass(frozen=True, init=False)
class Capability:
    """A stable description of one capability candidate."""

    identifier: str
    name: str
    kind: CapabilityKind
    available: bool = True
    enabled: bool = True
    cost_class: CostClass = CostClass.ZERO_COST
    requires_secret: bool = False
    requires_oauth: bool = False
    requires_payment: bool = False
    requires_kyc: bool = False
    risk_level: RiskLevel = RiskLevel.LOW
    provenance: Mapping[str, Any] | str | None = None
    evidence: Mapping[str, Any] | None = None

    def __init__(
        self,
        identifier: str | None = None,
        name: str | None = None,
        kind: CapabilityKind | str | None = None,
        available: bool | None = None,
        enabled: bool | None = None,
        cost_class: CostClass | str | None = None,
        requires_secret: bool | None = None,
        requires_oauth: bool | None = None,
        requires_payment: bool | None = None,
        requires_kyc: bool | None = None,
        risk_level: RiskLevel | str | object = _MISSING,
        provenance: Mapping[str, Any] | str | None = None,
        evidence: Mapping[str, Any] | None = None,
        *,
        id: str | None = None,
        capability_id: str | None = None,
        risk: RiskLevel | str | object = _MISSING,
        secret_required: bool | None = None,
        api_key_required: bool | None = None,
        requires_api_key: bool | None = None,
        oauth_required: bool | None = None,
        payment_required: bool | None = None,
        kyc_required: bool | None = None,
        human_only: bool | None = None,
        is_available: bool | None = None,
        is_enabled: bool | None = None,
        availability: bool | str | None = None,
        enabled_state: bool | str | None = None,
        cost: CostClass | str | None = None,
        provenance_metadata: Mapping[str, Any] | str | None = None,
        evidence_metadata: Mapping[str, Any] | None = None,
        state: bool | str | None = None,
        metadata: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        identifier = _resolve(identifier, id, capability_id, field_name="identifier")
        if identifier is None:
            raise CapabilityError("identifier invalide : une cle non vide est requise")
        identifier = _validate_identifier(identifier)

        if risk_level is None and risk is None:
            raise _error("risk_level", None, ", ".join(item.value for item in RiskLevel))
        risk_level = _resolve(
            risk_level,
            risk,
            default=RiskLevel.LOW,
            coerce=lambda value: _coerce_enum(value, RiskLevel, "risk_level"),
            field_name="risk_level",
        )
        provenance = _resolve(provenance, provenance_metadata, field_name="provenance")
        evidence = _resolve(evidence, evidence_metadata, field_name="evidence")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise _error("metadata", metadata, "mapping")
        if evidence is not None and not isinstance(evidence, Mapping):
            raise _error("evidence", evidence, "mapping")
        if metadata is not None:
            evidence = _merge_metadata(evidence, metadata)

        known_extras = ("source_ref", "source_type", "nature", "observation", "confidence")
        unknown_extras = set(extra) - set(known_extras)
        if unknown_extras:
            raise CapabilityError(f"champs inconnus : {sorted(unknown_extras)}")
        for source_field in known_extras:
            if source_field in extra:
                provenance = _merge_metadata(provenance, {source_field: extra[source_field]})

        available = _resolve(
            available,
            is_available,
            default=None,
            coerce=lambda value: _coerce_bool(value, "available"),
            field_name="available",
        )
        availability = _resolve(
            availability,
            state,
            default=None,
            coerce=lambda value: _coerce_availability(value, "availability"),
            field_name="availability",
        )
        if available is not None and availability is not None and available != availability:
            raise CapabilityError("available conflicting values")
        available = available if available is not None else availability if availability is not None else True

        enabled = _resolve(
            enabled,
            is_enabled,
            default=None,
            coerce=lambda value: _coerce_bool(value, "enabled"),
            field_name="enabled",
        )
        enabled_state = _resolve(
            enabled_state,
            default=None,
            coerce=lambda value: _coerce_availability(value, "enabled_state"),
            field_name="enabled_state",
        )
        if enabled is not None and enabled_state is not None and enabled != enabled_state:
            raise CapabilityError("enabled conflicts with enabled_state")
        enabled = enabled if enabled is not None else enabled_state if enabled_state is not None else True

        requires_secret = _resolve(
            requires_secret,
            secret_required,
            api_key_required,
            requires_api_key,
            default=False,
            coerce=lambda value: _coerce_bool(value, "requires_secret"),
        )
        requires_oauth = _resolve(
            requires_oauth,
            oauth_required,
            default=False,
            coerce=lambda value: _coerce_bool(value, "requires_oauth"),
        )
        requires_payment = _resolve(
            requires_payment,
            payment_required,
            default=False,
            coerce=lambda value: _coerce_bool(value, "requires_payment"),
        )
        requires_kyc = _resolve(
            requires_kyc,
            kyc_required,
            human_only,
            default=False,
            coerce=lambda value: _coerce_bool(value, "requires_kyc"),
        )
        cost_class = _resolve(
            cost_class,
            cost,
            default=CostClass.ZERO_COST,
            coerce=lambda value: _coerce_enum(value, CostClass, "cost_class"),
            field_name="cost_class",
        )

        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "name", _validate_name(name, identifier))
        object.__setattr__(self, "kind", _coerce_enum(kind, CapabilityKind, "kind"))
        object.__setattr__(self, "available", available)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "cost_class", cost_class)
        object.__setattr__(self, "requires_secret", requires_secret)
        object.__setattr__(self, "requires_oauth", requires_oauth)
        object.__setattr__(self, "requires_payment", requires_payment)
        object.__setattr__(self, "requires_kyc", requires_kyc)
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "provenance", _copy_metadata(provenance, "provenance"))
        object.__setattr__(self, "evidence", _copy_metadata(evidence, "evidence"))

    @property
    def id(self) -> str:
        return self.identifier

    @property
    def risk(self) -> RiskLevel:
        return self.risk_level

    @property
    def secret_required(self) -> bool:
        return self.requires_secret

    @property
    def api_key_required(self) -> bool:
        return self.requires_secret

    @property
    def oauth_required(self) -> bool:
        return self.requires_oauth

    @property
    def payment_required(self) -> bool:
        return self.requires_payment

    @property
    def kyc_required(self) -> bool:
        return self.requires_kyc

    @property
    def is_available(self) -> bool:
        return self.available

    @property
    def is_enabled(self) -> bool:
        return self.enabled

    @property
    def usable(self) -> bool:
        return self.available and self.enabled

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Capability":
        if not isinstance(data, Mapping):
            raise CapabilityError("capability must be a mapping")
        payload = dict(data)
        identifier = payload.pop("identifier", None)
        if identifier is None:
            identifier = payload.pop("id", None)
        return cls(identifier=identifier, **payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "name": self.name,
            "kind": self.kind.value,
            "available": self.available,
            "enabled": self.enabled,
            "cost_class": self.cost_class.value,
            "requires_secret": self.requires_secret,
            "requires_oauth": self.requires_oauth,
            "requires_payment": self.requires_payment,
            "requires_kyc": self.requires_kyc,
            "risk_level": self.risk_level.value,
            "provenance": self.provenance,
            "evidence": self.evidence,
        }

    as_dict = to_dict


CapabilityModel = Capability


@dataclass(frozen=True, eq=False)
class CapabilityEvaluation:
    capability: Capability
    status: EvaluationStatus
    reasons: tuple[str, ...] = field(default_factory=tuple)
    required_actions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def decision(self) -> EvaluationStatus:
        return self.status

    @property
    def verdict(self) -> EvaluationStatus:
        return self.status

    @property
    def reason(self) -> str | None:
        return "; ".join(self.reasons) if self.reasons else None

    @property
    def action(self) -> str | None:
        return self.required_actions[0] if self.required_actions else None

    @property
    def automatically_allowed(self) -> bool:
        return self.status == EvaluationStatus.ALLOWED

    @property
    def human_required(self) -> bool:
        return self.status == EvaluationStatus.HUMAN_REQUIRED

    @property
    def prohibited(self) -> bool:
        return self.status == EvaluationStatus.REJECTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.capability.identifier,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "required_actions": list(self.required_actions),
        }

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, CapabilityEvaluation):
            return (self.capability, self.status, self.reasons, self.required_actions) == (
                other.capability, other.status, other.reasons, other.required_actions
            )
        if isinstance(other, (str, EvaluationStatus)):
            return self.status == other
        return False

    def __getitem__(self, key: str) -> Any:
        if key == "capability":
            return self.capability
        if key in {"status", "decision", "verdict"}:
            return self.status
        if key == "reasons":
            return self.reasons
        if key in {"required_actions", "actions"}:
            return self.required_actions
        if key == "id":
            return self.capability.identifier
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


EvaluationResult = CapabilityEvaluation
CandidateEvaluation = CapabilityEvaluation


def _coerce_capability(candidate: Capability | Mapping[str, Any]) -> Capability:
    if isinstance(candidate, Capability):
        return candidate
    if isinstance(candidate, Mapping):
        return Capability.from_dict(candidate)
    raise CapabilityError("candidate must be a Capability or mapping")


def evaluate_capability(candidate: Capability | Mapping[str, Any]) -> CapabilityEvaluation:
    """Classify a candidate using only deterministic, fail-closed rules."""
    capability = _coerce_capability(candidate)
    if not capability.available:
        return CapabilityEvaluation(capability, EvaluationStatus.REJECTED, ("unavailable",), ())
    if not capability.enabled:
        return CapabilityEvaluation(capability, EvaluationStatus.REJECTED, ("disabled",), ())

    reasons: list[str] = []
    if capability.requires_payment or capability.cost_class == CostClass.PAID:
        reasons.append("payment")
    if capability.requires_secret:
        reasons.append("secret")
    if capability.requires_oauth:
        reasons.append("oauth")
    if capability.requires_kyc:
        reasons.append("kyc")
    if capability.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        reasons.append("high_risk")
    reasons = [reason for reason in _REASON_ORDER if reason in reasons]

    if not reasons:
        return CapabilityEvaluation(capability, EvaluationStatus.ALLOWED, (), ())

    actions: list[str] = []
    if "payment" in reasons or "high_risk" in reasons:
        actions.append("human_approval")
    if any(reason in reasons for reason in ("secret", "oauth", "kyc")):
        actions.append("human_action")
    actions = [action for action in _ACTION_ORDER if action in actions]
    return CapabilityEvaluation(capability, EvaluationStatus.HUMAN_REQUIRED, tuple(reasons), tuple(actions))


evaluate_candidate = evaluate_capability


def evaluate_capabilities(candidates: Iterable[Capability | Mapping[str, Any]]) -> list[CapabilityEvaluation]:
    results = [evaluate_capability(candidate) for candidate in candidates]
    return sorted(results, key=lambda result: result.capability.identifier)


def automatically_usable(candidates: Iterable[Capability | Mapping[str, Any]]) -> list[Capability]:
    return [
        result.capability
        for result in evaluate_capabilities(candidates)
        if result.status == EvaluationStatus.ALLOWED
    ]


def usable_capability_ids(candidates: Iterable[Capability | Mapping[str, Any]]) -> list[str]:
    return sorted(capability.identifier for capability in automatically_usable(candidates))


def _records(values: Any) -> list[Any]:
    if isinstance(values, Capability):
        return [values]
    if isinstance(values, CapabilityEvaluation):
        return [values]
    if isinstance(values, str):
        return [values]
    if isinstance(values, Mapping):
        if "identifier" in values or "id" in values or "capability_id" in values:
            return [values]
        return list(values.keys())
    return list(values)


def _record_identifier(record: Any, *, allowed_only: bool = False) -> str | None:
    if isinstance(record, Capability):
        return record.identifier
    if isinstance(record, CapabilityEvaluation):
        if allowed_only and record.status != EvaluationStatus.ALLOWED:
            return None
        return record.capability.identifier
    if isinstance(record, str):
        identifier = record.strip()
        return identifier or None
    if isinstance(record, Mapping):
        identifier = record.get("identifier", record.get("id", record.get("capability_id")))
        if identifier is None:
            return None
        identifier = str(identifier).strip()
        return identifier or None
    return None


def detect_capability_gaps(
    required: Iterable[Capability | Mapping[str, Any] | str] | Capability | Mapping[str, Any] | str,
    currently_usable: Iterable[Capability | CapabilityEvaluation | Mapping[str, Any] | str] | Capability | CapabilityEvaluation | Mapping[str, Any] | str,
) -> list[str]:
    """Return sorted, unique required identifiers that are not currently usable."""
    required_ids = {
        identifier
        for record in _records(required)
        if (identifier := _record_identifier(record)) is not None
    }
    usable_ids = {
        identifier
        for record in _records(currently_usable)
        if (identifier := _record_identifier(record, allowed_only=True)) is not None
    }
    return sorted(required_ids - usable_ids)


capability_gaps = detect_capability_gaps
find_capability_gaps = detect_capability_gaps
missing_capabilities = detect_capability_gaps


def is_automatically_allowed(candidate: Capability | Mapping[str, Any]) -> bool:
    return evaluate_capability(candidate).status == EvaluationStatus.ALLOWED


def requires_human_action(candidate: Capability | Mapping[str, Any]) -> bool:
    return evaluate_capability(candidate).status == EvaluationStatus.HUMAN_REQUIRED


def is_rejected(candidate: Capability | Mapping[str, Any]) -> bool:
    return evaluate_capability(candidate).status == EvaluationStatus.REJECTED
