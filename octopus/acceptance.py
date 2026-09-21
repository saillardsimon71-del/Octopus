"""Protected acceptance contracts, evidence bundles and deterministic gate decisions.

This module belongs to the self-development trust boundary. Product builders may
consume contracts and evidence, but must not be able to change the rules that turn
evidence into ACCEPTED/REJECTED decisions.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
GATE_STATUSES = frozenset({
    "ACCEPTED",
    "REJECTED",
    "UNCERTAIN",
    "WAITING_FOR_HUMAN",
    "WAITING_FOR_RESOURCE",
    "ABORTED_SAFE",
})
_ALLOWED_PROBES = frozenset({"none", "tk_navigation"})
_ALLOWED_OPS = frozenset({
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "set_equals",
    "truthy",
    "falsy",
})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_FACT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AcceptanceError(ValueError):
    pass


def _bounded_text(value: Any, field: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str):
        raise AcceptanceError(f"{field} doit être une chaîne")
    text = " ".join(value.split())
    if not text or len(text) > maximum:
        raise AcceptanceError(f"{field} invalide")
    return text


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise AcceptanceError("contrat non sérialisable en JSON") from exc


def validate_contract(raw: Any) -> dict:
    """Validate and normalize a versioned, immutable acceptance contract."""
    if not isinstance(raw, dict):
        raise AcceptanceError("acceptance_contract doit être un objet")
    if raw.get("version") != SCHEMA_VERSION:
        raise AcceptanceError(f"acceptance_contract.version doit valoir {SCHEMA_VERSION}")

    contract_id = _bounded_text(raw.get("id"), "acceptance_contract.id", maximum=128)
    artifact_type = _bounded_text(raw.get("artifact_type"), "acceptance_contract.artifact_type", maximum=64)

    probe_raw = raw.get("probe") or {"kind": "none"}
    if not isinstance(probe_raw, dict):
        raise AcceptanceError("acceptance_contract.probe doit être un objet")
    kind = str(probe_raw.get("kind") or "").strip()
    if kind not in _ALLOWED_PROBES:
        raise AcceptanceError(f"probe non supporté: {kind!r}")
    probe: dict[str, Any] = {"kind": kind}
    if kind == "tk_navigation":
        module = _bounded_text(probe_raw.get("module"), "probe.module", maximum=200)
        class_name = _bounded_text(probe_raw.get("class"), "probe.class", maximum=100)
        attribute = _bounded_text(
            probe_raw.get("attribute") or "nav_buttons", "probe.attribute", maximum=100,
        )
        if _MODULE_RE.fullmatch(module) is None:
            raise AcceptanceError("probe.module invalide")
        if _IDENTIFIER_RE.fullmatch(class_name) is None:
            raise AcceptanceError("probe.class invalide")
        if _IDENTIFIER_RE.fullmatch(attribute) is None:
            raise AcceptanceError("probe.attribute invalide")
        probe.update(module=module, **{"class": class_name}, attribute=attribute)

    must_raw = raw.get("must")
    if not isinstance(must_raw, list) or not must_raw:
        raise AcceptanceError("acceptance_contract.must doit être une liste non vide")
    if len(must_raw) > 32:
        raise AcceptanceError("acceptance_contract.must dépasse 32 critères")

    must = []
    seen_ids: set[str] = set()
    for index, item in enumerate(must_raw, start=1):
        if not isinstance(item, dict):
            raise AcceptanceError(f"critère MUST #{index}: objet attendu")
        rule_id = _bounded_text(item.get("id"), f"critère MUST #{index}.id", maximum=100)
        if rule_id in seen_ids:
            raise AcceptanceError(f"critère MUST dupliqué: {rule_id}")
        seen_ids.add(rule_id)
        fact = _bounded_text(item.get("fact"), f"critère MUST {rule_id}.fact", maximum=160)
        if _FACT_RE.fullmatch(fact) is None:
            raise AcceptanceError(f"critère MUST {rule_id}: fact invalide")
        op = str(item.get("op") or "").strip()
        if op not in _ALLOWED_OPS:
            raise AcceptanceError(f"critère MUST {rule_id}: opérateur non supporté: {op!r}")
        rule: dict[str, Any] = {"id": rule_id, "fact": fact, "op": op}
        if op not in {"truthy", "falsy"}:
            if "expected" not in item:
                raise AcceptanceError(f"critère MUST {rule_id}: expected requis")
            rule["expected"] = _json_copy(item["expected"])
        description = item.get("description")
        if description is not None:
            rule["description"] = _bounded_text(
                description, f"critère MUST {rule_id}.description", maximum=400,
            )
        must.append(rule)

    return {
        "version": SCHEMA_VERSION,
        "id": contract_id,
        "artifact_type": artifact_type,
        "probe": probe,
        "must": must,
    }


def canonical_contract(raw: Any) -> str:
    contract = validate_contract(raw)
    return json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def contract_hash(raw: Any) -> str:
    return hashlib.sha256(canonical_contract(raw).encode("utf-8")).hexdigest()


def contract_prompt_requirements(raw: Any) -> list[str]:
    contract = validate_contract(raw)
    out = []
    for rule in contract["must"]:
        expected = ""
        if "expected" in rule:
            expected = "=" + json.dumps(rule["expected"], ensure_ascii=False, sort_keys=True)
        label = rule.get("description") or f"{rule['fact']} {rule['op']} {expected}".strip()
        out.append(f"MUST[{rule['id']}] {label}")
    return out


def probe_command(raw: Any) -> list[str] | None:
    contract = validate_contract(raw)
    probe = contract["probe"]
    if probe["kind"] == "none":
        return None
    if probe["kind"] == "tk_navigation":
        return [
            "xvfb-run",
            "-a",
            "python",
            "-m",
            "octopus.acceptance_probe",
            "tk-navigation",
            "--module",
            probe["module"],
            "--class",
            probe["class"],
            "--attribute",
            probe["attribute"],
        ]
    raise AcceptanceError(f"probe non supporté: {probe['kind']}")


def build_evidence_bundle(
    *,
    task_id: int,
    attempt: int,
    contract: Any,
    facts: dict,
    artifacts: list[dict] | None = None,
) -> dict:
    normalized = validate_contract(contract)
    if not isinstance(task_id, int) or task_id < 0:
        raise AcceptanceError("task_id invalide")
    if not isinstance(attempt, int) or attempt < 0:
        raise AcceptanceError("attempt invalide")
    if not isinstance(facts, dict):
        raise AcceptanceError("facts doit être un objet")
    if artifacts is not None and not isinstance(artifacts, list):
        raise AcceptanceError("artifacts doit être une liste")
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "attempt": attempt,
        "contract_id": normalized["id"],
        "contract_hash": contract_hash(normalized),
        "facts": _json_copy(facts),
        "artifacts": _json_copy(artifacts or []),
    }


_MISSING = object()


def _fact_value(facts: dict, path: str):
    value: Any = facts
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _evaluate_rule(rule: dict, observed: Any) -> tuple[bool | None, str | None]:
    op = rule["op"]
    expected = rule.get("expected")
    if observed is _MISSING:
        return None, "fact_missing"
    try:
        if op == "equals":
            return observed == expected, None
        if op == "not_equals":
            return observed != expected, None
        if op == "contains":
            return expected in observed, None
        if op == "not_contains":
            return expected not in observed, None
        if op == "set_equals":
            if not isinstance(observed, list) or not isinstance(expected, list):
                return None, "set_equals_requires_lists"
            return set(observed) == set(expected), None
        if op == "truthy":
            return bool(observed), None
        if op == "falsy":
            return not bool(observed), None
    except (TypeError, ValueError):
        return None, "incompatible_fact_type"
    return None, "unsupported_operator"


def evaluate_contract(raw_contract: Any, bundle: dict) -> dict:
    """Deterministic L0 gate. Reviewer/model output is deliberately not trusted here."""
    contract = validate_contract(raw_contract)
    expected_hash = contract_hash(contract)
    if not isinstance(bundle, dict) or bundle.get("contract_hash") != expected_hash:
        return {
            "status": "UNCERTAIN",
            "contract_hash": expected_hash,
            "criteria": [],
            "reason": "evidence_contract_mismatch",
        }
    facts = bundle.get("facts")
    if not isinstance(facts, dict):
        return {
            "status": "UNCERTAIN",
            "contract_hash": expected_hash,
            "criteria": [],
            "reason": "evidence_facts_missing",
        }

    criteria = []
    failed = False
    uncertain = False
    for rule in contract["must"]:
        observed = _fact_value(facts, rule["fact"])
        passed, reason = _evaluate_rule(rule, observed)
        if passed is False:
            failed = True
        elif passed is None:
            uncertain = True
        criteria.append({
            "id": rule["id"],
            "fact": rule["fact"],
            "op": rule["op"],
            "expected": rule.get("expected"),
            "observed": None if observed is _MISSING else _json_copy(observed),
            "passed": passed,
            "reason": reason,
        })

    if failed:
        status = "REJECTED"
        reason = "must_failed"
    elif uncertain:
        status = "UNCERTAIN"
        reason = "evidence_incomplete"
    else:
        status = "ACCEPTED"
        reason = "all_must_satisfied"
    return {
        "status": status,
        "contract_hash": expected_hash,
        "criteria": criteria,
        "reason": reason,
    }


def gate_feedback(decision: dict) -> str:
    status = str(decision.get("status") or "UNCERTAIN")
    rows = []
    for item in decision.get("criteria") or []:
        if item.get("passed") is True:
            continue
        rows.append(
            f"{item.get('id')}: fact={item.get('fact')} observed={item.get('observed')!r} "
            f"expected={item.get('expected')!r} reason={item.get('reason') or 'mismatch'}"
        )
    detail = "; ".join(rows) or str(decision.get("reason") or "no detail")
    return f"{status}: {detail}"


def evidence_bytes(bundle: dict) -> bytes:
    return (json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def persist_evidence(root: Path, bundle: dict) -> tuple[Path, str]:
    root = Path(root).resolve()
    task_id = int(bundle["task_id"])
    attempt = int(bundle["attempt"])
    contract_sha = str(bundle["contract_hash"])
    if _SHA256_RE.fullmatch(contract_sha) is None:
        raise AcceptanceError("contract_hash invalide dans evidence bundle")
    directory = root / "acceptance-evidence" / f"task-{task_id}"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"attempt-{attempt}-{contract_sha[:12]}.json"
    payload = evidence_bytes(bundle)
    target.write_bytes(payload)
    return target, hashlib.sha256(payload).hexdigest()


def verify_evidence_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_contract_hash: str,
    expected_task_id: int | None = None,
) -> dict:
    target = Path(path)
    try:
        payload = target.read_bytes()
        bundle = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"evidence illisible: {exc}") from exc
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != expected_sha256:
        raise AcceptanceError("checksum evidence différent du rapport")
    if bundle.get("contract_hash") != expected_contract_hash:
        raise AcceptanceError("contract_hash evidence différent du rapport")
    if expected_task_id is not None and bundle.get("task_id") != expected_task_id:
        raise AcceptanceError("task_id evidence différent du rapport")
    decision = bundle.get("gate_decision")
    if not isinstance(decision, dict) or decision.get("status") not in GATE_STATUSES:
        raise AcceptanceError("gate_decision evidence invalide")
    return bundle
