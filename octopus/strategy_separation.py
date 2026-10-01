"""Sépare la pertinence stratégique de l'exécutabilité.

Trois niveaux, jamais confondus :

1. stratégie — ce qui serait économiquement souhaitable, indépendamment des outils ;
2. exécutabilité — ce que le registre d'outils et les permissions actuelles permettent ;
3. exécution — uniquement une stratégie retenue dont l'exécutabilité est établie.

Une sortie de modèle peut proposer une stratégie qui exige une capacité absente.
Elle ne peut pas déclarer qu'une capacité existe : le registre, les exécuteurs déjà
enregistrés et les sondes runtime sont la seule source d'autorité. Ce module ne
découvre, n'installe et n'autorise aucune capacité.
"""
from __future__ import annotations

import copy
import json
import re
import unicodedata

from . import strategy, tasks

SCHEMA = "octopus.strategy_executability.v1"
ACTOR = "policy:executability"
SOURCE_TYPE = "strategy.executability"
RELATION = "executability"
ECONOMIC_CRITERIA = ("cash_received", "margin", "recurrence", "autonomy", "growth")
STRATEGIC_STATES = ("retained", "candidate", "invalidated")
EXECUTABILITY_STATES = (
    "executable", "missing_capability", "temporarily_unavailable",
    "permission_denied", "human_required", "not_established",
)
_MAX_PROPOSALS = 12
_ID_RE = re.compile(r"^[a-z0-9_:-]{1,64}$")
_CLAIM_KEYS = frozenset({
    "available", "availability", "executable", "executability", "tool_available",
    "capability_available", "capabilities_available", "missing_capabilities", "missing",
    "permission", "permissions", "permission_state", "strategic_state", "status",
    "tool_exists", "registry", "inventory", "may_execute", "authorized",
})
_CRITERIA_ALIASES = {
    "cash_received": "cash_received", "cash": "cash_received", "encaissement": "cash_received",
    "argent_encaisse": "cash_received", "argent": "cash_received",
    "margin": "margin", "marge": "margin",
    "recurrence": "recurrence", "autonomie": "autonomy", "autonomy": "autonomy",
    "growth": "growth", "croissance": "growth",
}

STRATEGY_SEPARATION_CLAUSE = (
    "STRATÉGIE ET CAPACITÉS SONT DISTINCTES. Envisage, compare et retiens la stratégie "
    "économiquement préférable même si l'outil nécessaire n'existe pas. Les capacités et "
    "permissions actuelles limitent seulement ce qui peut être exécuté, pas ce qui peut être "
    "envisagé. N'élimine pas une stratégie parce qu'une capacité manque. Ne la remplace pas par "
    "une stratégie moins pertinente seulement parce qu'un outil est déjà disponible. Une capacité "
    "absente n'est pas un échec économique, ni une permission refusée, ni une raison d'invalider "
    "la stratégie. Une permission absente n'est pas non plus une mauvaise stratégie. "
    "Ne déclare jamais qu'une capacité est disponible : le registre d'outils du système est la "
    "seule source d'autorité. Ordre économique, du plus important au moins important : "
    "argent encaissé, marge, récurrence, autonomie, croissance. "
    "Tu peux proposer des stratégies qui exigent une capacité inexistante, en nommant "
    "l'identifiant requis (par exemple phone_call), sans affirmer qu'elle existe."
)
STRATEGY_PROPOSAL_CLAUSE = (
    "Tu peux ajouter facultativement strategies : une liste comparée sur la valeur économique "
    "attendue, indépendamment des outils disponibles. Chaque élément contient statement, "
    "economic_justification, economic_criteria (parmi cash_received, margin, recurrence, autonomy, "
    "growth), economic_rank (entier utilisé seulement à critère économique égal), "
    "required_capabilities (identifiants, jamais une déclaration de disponibilité), evidence_ids, "
    "expected_signal, stop_criterion. N'inclus aucun champ available, executable ou de "
    "disponibilité. Ne classe pas une stratégie plus bas parce qu'une capacité manque, et ne "
    "remplace pas la meilleure stratégie non exécutable par une moins bonne seulement exécutable."
)


class Inventory:
    """Faits de disponibilité produits par le système, jamais par un modèle."""

    def __init__(self, *, present, executable, temporarily_unavailable, permission_denied,
                 human_required, authority="tool_registry"):
        self.present = frozenset(present)
        self.executable = frozenset(executable)
        self.temporarily_unavailable = frozenset(temporarily_unavailable)
        self.permission_denied = frozenset(permission_denied)
        self.human_required = frozenset(human_required)
        self.authority = authority

    def as_dict(self) -> dict:
        return {
            "authority": self.authority,
            "present": sorted(self.present),
            "executable": sorted(self.executable),
            "temporarily_unavailable": sorted(self.temporarily_unavailable),
            "permission_denied": sorted(self.permission_denied),
            "human_required": sorted(self.human_required),
        }


def _fold(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _ids(values) -> set[str]:
    found = set()
    for value in values or []:
        if isinstance(value, str):
            token = value.strip().casefold()
            if _ID_RE.match(token):
                found.add(token)
    return found


def _capability_id(value) -> str | None:
    raw = value
    if isinstance(value, dict):
        raw = value.get("id", value.get("identifier", value.get("capability")))
    if not isinstance(raw, str):
        return None
    token = raw.strip().casefold()
    return token if _ID_RE.match(token) else None


def _claims_ignored(raw) -> bool:
    if not isinstance(raw, dict):
        return False
    if _CLAIM_KEYS & set(raw):
        return True
    capabilities = raw.get("required_capabilities")
    if not isinstance(capabilities, list):
        return False
    for item in capabilities:
        if isinstance(item, dict) and set(item) - {"id", "identifier", "capability"}:
            return True
    return False


def _criteria(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    chosen = []
    for item in value:
        if not isinstance(item, str):
            continue
        key = _fold(item).replace("-", "_").replace(" ", "_")
        canonical = _CRITERIA_ALIASES.get(key)
        if canonical and canonical not in chosen:
            chosen.append(canonical)
    return chosen


def _rank(value):
    if type(value) is int and 1 <= value <= 1000:
        return value
    return None


def _evidence_ids(value) -> list[int]:
    if not isinstance(value, list):
        return []
    ids = []
    for item in value:
        if type(item) is int and item > 0 and item not in ids:
            ids.append(item)
        if len(ids) >= 20:
            break
    return ids


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def normalize_proposals(value) -> list[dict]:
    """Garde les propositions stratégiques et jette toute déclaration de disponibilité."""
    if not isinstance(value, list):
        return []
    proposals = []
    seen = set()
    for raw in value:
        if len(proposals) >= _MAX_PROPOSALS or not isinstance(raw, dict):
            continue
        statement = _text(raw.get("statement"), 1000)
        justification = _text(raw.get("economic_justification"), 900)
        if not statement or strategy._mentions_legacy_business(statement, justification):
            continue
        key = strategy._hypothesis_key(statement)
        if not key or key in seen:
            continue
        seen.add(key)
        capabilities = []
        declared = raw.get("required_capabilities")
        if isinstance(declared, list):
            for item in declared:
                identifier = _capability_id(item)
                if identifier and identifier not in capabilities:
                    capabilities.append(identifier)
                if len(capabilities) >= 12:
                    break
        proposals.append({
            "key": key,
            "statement": statement,
            "economic_justification": justification,
            "economic_criteria": _criteria(raw.get("economic_criteria")),
            "economic_rank": _rank(raw.get("economic_rank")),
            "required_capabilities": capabilities,
            "evidence_ids": _evidence_ids(raw.get("evidence_ids")),
            "expected_signal": _text(raw.get("expected_signal"), 500),
            "stop_criterion": _text(raw.get("stop_criterion"), 500),
            "llm_availability_claims_ignored": _claims_ignored(raw) or raw.get("llm_availability_claims_ignored") is True,
        })
    return proposals


def attach_strategies(choice: dict) -> None:
    """Normalise strategies sur place. Une liste invalide est écartée, pas la décision."""
    if not isinstance(choice, dict) or "strategies" not in choice:
        return
    normalized = normalize_proposals(choice.get("strategies"))
    if normalized:
        choice["strategies"] = normalized
    else:
        choice.pop("strategies", None)


def build_inventory(*, present_tools, allowed_execution, executors=(), temporarily_unavailable=(),
                    authority="tool_registry") -> Inventory:
    """Construit l'inventaire sans ajouter d'outil ni élargir l'ensemble autorisé.

    Un exécuteur déjà enregistré est une capacité présente, jamais une autorisation
    d'exécution. Une présence hors de l'ensemble autorisé est un refus de permission,
    pas une absence de capacité.
    """
    tools = _ids(present_tools)
    allowed = _ids(allowed_execution)
    registered = _ids(executors)
    present = tools | registered
    temporary = _ids(temporarily_unavailable) & present
    # Un identifiant d'exécuteur n'est jamais exécutable, même s'il figure aussi parmi les outils autorisés.
    executable = (tools & allowed) - temporary - registered
    human_required = registered - temporary
    # Hors de l'ensemble autorisé, le refus de permission reste stable : une sonde temporaire ne le masque pas.
    permission_denied = (tools - allowed) - human_required
    return Inventory(
        present=present, executable=executable, temporarily_unavailable=temporary,
        permission_denied=permission_denied, human_required=human_required, authority=authority,
    )


def executor_ids() -> set[str]:
    """Exécuteurs déjà enregistrés. Ne charge ni n'enregistre aucun connecteur."""
    from .actions import registered_executors
    return {f"{kind}:{action}" for kind, action in registered_executors()}


def browser_unavailable_tools(present_tools, *, ready: bool | None = None) -> set[str]:
    """Une sonde runtime peut rendre un outil enregistré temporairement indisponible.

    L'absence de sonde n'efface pas l'outil du registre : ce n'est pas une absence définitive.
    """
    if ready is None:
        try:
            from agents.agent_browser import availability
            ready = bool(availability().get("ready"))
        except Exception:
            ready = False
    if ready:
        return set()
    return {name for name in _ids(present_tools) if name == "browse" or name.startswith("browser_")}


def _one_capability(identifier: str, inventory: Inventory) -> str:
    if identifier not in inventory.present:
        return "missing_capability"
    if identifier in inventory.permission_denied:
        return "permission_denied"
    if identifier in inventory.temporarily_unavailable:
        return "temporarily_unavailable"
    if identifier in inventory.human_required:
        return "human_required"
    if identifier in inventory.executable:
        return "executable"
    return "permission_denied"


def classify_executability(required: list[str], inventory: Inventory) -> dict:
    """Annote l'exécutabilité. Ne modifie ni le rang économique ni un statut d'hypothèse."""
    if not required:
        return {"executability": "not_established", "missing_capabilities": [],
                "temporarily_unavailable": [], "permission_state": "none", "may_execute": False}
    buckets = {state: [] for state in (
        "missing_capability", "temporarily_unavailable", "permission_denied", "human_required", "executable")}
    for identifier in required:
        buckets[_one_capability(identifier, inventory)].append(identifier)
    if buckets["missing_capability"]:
        state = "missing_capability"
    elif buckets["temporarily_unavailable"]:
        state = "temporarily_unavailable"
    elif buckets["permission_denied"]:
        state = "permission_denied"
    elif buckets["human_required"]:
        state = "human_required"
    else:
        state = "executable"
    permission_state = {"permission_denied": "denied", "human_required": "human_required"}.get(state, "none")
    return {
        "executability": state,
        "missing_capabilities": buckets["missing_capability"],
        "temporarily_unavailable": buckets["temporarily_unavailable"],
        "permission_state": permission_state,
        "may_execute": state == "executable",
    }


def _criterion_index(criteria: list[str]) -> int:
    if not criteria:
        return len(ECONOMIC_CRITERIA)
    return min(ECONOMIC_CRITERIA.index(item) for item in criteria)


def _economic_sort_key(item: dict) -> tuple:
    # L'exécutabilité est absente de cette clé. Un outil disponible ne fait pas monter une stratégie.
    rank = item["economic_rank"]
    return (
        1 if item["economically_invalidated"] else 0,
        _criterion_index(item["economic_criteria"]),
        rank if rank is not None else 10**6,
        -len(item["evidence_ids"]),
        item["key"],
    )


def invalidated_keys(business: str) -> set[str]:
    from . import journal
    rows = journal.query(
        "SELECT statement FROM strategy_hypotheses WHERE business=? AND status='invalidated'",
        (strategy._business(business),))
    return {key for row in rows if (key := strategy._hypothesis_key(row["statement"]))}


def assess(proposals, inventory: Inventory, *, economically_invalidated: set[str] | None = None) -> dict:
    """Classe par valeur économique, puis annote l'exécutabilité sans réordonner."""
    invalidated = set(economically_invalidated or ())
    considered = []
    for proposal in normalize_proposals(proposals):
        item = dict(proposal)
        item["economically_invalidated"] = item["key"] in invalidated
        item.update(classify_executability(item["required_capabilities"], inventory))
        if item["economically_invalidated"]:
            item["may_execute"] = False
            item["strategic_state"] = "invalidated"
        considered.append(item)
    considered.sort(key=_economic_sort_key)
    retained = next((item for item in considered if not item["economically_invalidated"]), None)
    for item in considered:
        if item["economically_invalidated"]:
            item["strategic_state"] = "invalidated"
        elif retained is not None and item["key"] == retained["key"]:
            item["strategic_state"] = "retained"
        else:
            item["strategic_state"] = "candidate"
            item["may_execute"] = False
    if retained is not None:
        retained = next(item for item in considered if item["key"] == retained["key"])
    return {
        "schema": SCHEMA,
        "authority": inventory.authority,
        "retained": copy.deepcopy(retained) if retained else None,
        "considered": copy.deepcopy(considered),
        "substitution_blocked": False,
        "model_reason": None,
    }


def is_substitution(next_goal: str, assessment: dict) -> bool:
    retained = assessment.get("retained") if isinstance(assessment, dict) else None
    if not retained:
        return False
    goal = strategy._hypothesis_key(next_goal)
    retained_key = retained.get("key") or strategy._hypothesis_key(retained.get("statement", ""))
    if not goal or goal == retained_key:
        return False
    return any(goal == item.get("key") for item in assessment.get("considered") or []
               if item.get("strategic_state") != "retained")


def reasoning_goal(retained: dict) -> str:
    missing = ", ".join(retained.get("missing_capabilities") or []) or "aucune capacité nommée"
    return (
        f"Poursuivre l'observation sans exécuter. Stratégie retenue : {retained['statement']} "
        f"Exécutabilité : {retained['executability']}. Capacités manquantes : {missing}. "
        "Ne pas remplacer cette stratégie par une autre seulement parce qu'un outil existe."
    )[:900]


def reasoning_reason(retained: dict) -> str:
    missing = ", ".join(retained.get("missing_capabilities") or []) or "aucune"
    return (
        f"Stratégie retenue : {retained['statement']} "
        f"Exécutabilité : {retained['executability']} (capacités manquantes : {missing}). "
        "Ce n'est pas un échec économique. Aucune stratégie moins pertinente n'est substituée."
    )[:900]


def apply_pursuit_choice(choice, action, reason, permission, assessment, *,
                         execution_boundary: bool, rounds_left: bool) -> dict:
    """Ajuste la suite de pursuit sans confondre stratégie, capacité et permission.

    Une capacité absente ou temporairement indisponible ne devient pas une frontière humaine.
    Une stratégie non exécutable ne fait pas abandonner le raisonnement tant que le plafond
    de cycles n'est pas atteint et qu'aucune frontière d'exécution réelle n'est ouverte.
    Une alternative exécutable n'est jamais adoptée à la place de la stratégie retenue.
    """
    retained = assessment.get("retained") if isinstance(assessment, dict) else None
    next_goal = str((choice or {}).get("next_goal") or "") if isinstance(choice, dict) else ""
    model_reason = str(reason or "")
    substitution = bool(retained) and is_substitution(next_goal, assessment)
    new_action = action if action in {"continue", "pause", "request_permission"} else "pause"
    new_reason = model_reason
    new_permission = permission
    new_goal = reasoning_goal(retained) if retained and substitution else next_goal
    non_executable = bool(retained) and retained.get("executability") != "executable"
    if non_executable and not execution_boundary:
        if new_action == "request_permission":
            new_permission = None
            if rounds_left:
                new_action = "continue"
                new_reason = reasoning_reason(retained)
                new_goal = new_goal or reasoning_goal(retained)
            else:
                new_action = "pause"
                new_reason = "Limite de trois cycles atteinte. " + reasoning_reason(retained)
        elif new_action == "pause" and rounds_left:
            new_action = "continue"
            new_reason = reasoning_reason(retained)
            new_goal = new_goal or reasoning_goal(retained)
        elif substitution or (new_action == "continue" and not new_goal):
            new_reason = reasoning_reason(retained)
            new_goal = new_goal or reasoning_goal(retained)
    elif retained and retained.get("executability") == "executable" and substitution and new_action == "continue":
        new_goal = retained["statement"][:900]
    if new_action == "continue" and not new_goal and retained:
        new_goal = reasoning_goal(retained)
    return {
        "action": new_action, "reason": new_reason, "permission": new_permission,
        "next_goal": new_goal, "substitution_blocked": substitution, "model_reason": model_reason,
    }


def authorize_execution(assessment: dict) -> dict:
    """N'autorise que la stratégie retenue, et seulement si elle est exécutable.

    Une alternative exécutable n'est jamais choisie à la place d'une meilleure stratégie
    non exécutable. L'autorisation n'accorde aucune permission et ne déclenche rien.
    """
    retained = assessment.get("retained") if isinstance(assessment, dict) else None
    if not retained:
        return {"authorized": False, "substituted": False, "reason": "aucune stratégie retenue",
                "strategy_key": None, "missing_capabilities": []}
    missing = list(retained.get("missing_capabilities") or [])
    if (retained.get("strategic_state") == "invalidated" or retained.get("executability") != "executable"
            or not retained.get("may_execute")):
        return {"authorized": False, "substituted": False,
                "reason": retained.get("executability") or "not_executable",
                "strategy_key": retained.get("key"), "missing_capabilities": missing}
    return {"authorized": True, "substituted": False, "reason": "executable",
            "strategy_key": retained.get("key"), "missing_capabilities": []}


def dispatch_if_authorized(assessment: dict, trigger) -> dict:
    """Appelle trigger uniquement pour la stratégie retenue et exécutable."""
    decision = authorize_execution(assessment)
    if not decision["authorized"]:
        return {**decision, "triggered": False, "effect": None}
    effect = trigger(assessment["retained"])
    return {**decision, "triggered": True, "effect": effect, "substituted": False}


def _annotation_payload(item: dict, hypothesis_id: int) -> dict:
    return {
        "schema": SCHEMA,
        "hypothesis_id": hypothesis_id,
        "statement": item["statement"],
        "economic_justification": item.get("economic_justification") or "",
        "economic_criteria": list(item.get("economic_criteria") or []),
        "economic_rank": item.get("economic_rank"),
        "evidence_ids": list(item.get("evidence_ids") or []),
        "strategic_state": item["strategic_state"],
        "executability": item["executability"],
        "required_capabilities": list(item.get("required_capabilities") or []),
        "missing_capabilities": list(item.get("missing_capabilities") or []),
        "temporarily_unavailable": list(item.get("temporarily_unavailable") or []),
        "permission_state": item.get("permission_state") or "none",
        "may_execute": bool(item.get("may_execute")),
        "availability_authority": "tool_registry",
        "llm_availability_claims_ignored": bool(item.get("llm_availability_claims_ignored")),
    }


def _existing_invalidated(conn, business: str, key: str) -> int | None:
    rows = conn.execute(
        "SELECT id, statement FROM strategy_hypotheses WHERE business=? AND status='invalidated' ORDER BY id DESC",
        (business,)).fetchall()
    for row in rows:
        if strategy._hypothesis_key(row["statement"]) == key:
            return int(row["id"])
    return None


def _reuse_hypothesis(conn, business: str, objective_id: int, task_id: int, statement: str, key: str):
    rows = conn.execute(
        "SELECT id, statement, status FROM strategy_hypotheses WHERE business=? AND objective_id=? ORDER BY id",
        (business, objective_id)).fetchall()
    for row in rows:
        if row["status"] in {"proposed", "testing", "inconclusive"} and strategy._hypothesis_key(row["statement"]) == key:
            return int(row["id"]), False
    prior = conn.execute(
        "SELECT id, status FROM strategy_hypotheses WHERE business=? AND origin_task_id=? AND statement=? ORDER BY id LIMIT 1",
        (business, task_id, statement)).fetchone()
    if prior and prior["status"] not in strategy.KINDS["hypothesis"]["closed"]:
        return int(prior["id"]), False
    return None, False


def _link_cited(conn, business: str, hypothesis_id: int, evidence_ids: list[int], allowed: set[int]) -> list[int]:
    linked = []
    for evidence_id in evidence_ids:
        if evidence_id not in allowed:
            continue
        row = conn.execute(
            "SELECT id, experiment_id, nature, source_type, created_by, status FROM strategy_evidence "
            "WHERE id=? AND business=?", (evidence_id, business)).fetchone()
        if (not row or row["status"] != "active" or row["nature"] not in {"observed", "computed"}
                or row["source_type"] == SOURCE_TYPE or row["created_by"] == ACTOR):
            continue
        strategy.link(business, "hypothesis", hypothesis_id, "evidence", int(row["id"]), "considers", _conn=conn)
        if row["experiment_id"] is not None:
            strategy.link(business, "hypothesis", hypothesis_id, "experiment", int(row["experiment_id"]),
                          "learns_from", _conn=conn)
        linked.append(int(row["id"]))
    return linked


def _write_annotation(conn, business: str, task_id: int, hypothesis_id: int, item: dict) -> int:
    source_ref = f"strategy.executability#h{hypothesis_id}#t{task_id}"
    existing = conn.execute(
        "SELECT id FROM strategy_evidence WHERE business=? AND source_type=? AND source_ref=? "
        "AND status='active' AND created_by=? ORDER BY id LIMIT 1",
        (business, SOURCE_TYPE, source_ref, ACTOR)).fetchone()
    if existing:
        evidence_id = int(existing["id"])
    else:
        evidence_id = strategy.create(
            "evidence", business, f"Exécutabilité hypothèse #{hypothesis_id} : {item['executability']}",
            _conn=conn, created_by=ACTOR, origin_task_id=task_id, nature="computed",
            source_type=SOURCE_TYPE, source_ref=source_ref,
            observation=json.dumps(_annotation_payload(item, hypothesis_id), ensure_ascii=False, sort_keys=True))
    strategy.link(business, "hypothesis", hypothesis_id, "evidence", evidence_id, RELATION, _conn=conn)
    item["evidence_annotation_id"] = evidence_id
    return evidence_id


def _persist_item(conn, business: str, objective_id: int, task_id: int, item: dict, allowed: set[int]) -> bool:
    created = False
    invalidated_id = _existing_invalidated(conn, business, item["key"])
    if item.get("strategic_state") == "invalidated" or item.get("economically_invalidated") or invalidated_id:
        # Une hypothèse déjà invalidée économiquement n'est pas recréée ni rouverte.
        item["strategic_state"] = "invalidated"
        item["economically_invalidated"] = True
        item["may_execute"] = False
        if invalidated_id is None:
            item["hypothesis_id"] = None
            return False
        hypothesis_id = invalidated_id
    else:
        hypothesis_id, _ = _reuse_hypothesis(conn, business, objective_id, task_id, item["statement"], item["key"])
        if hypothesis_id is None:
            fields = {"statement": item["statement"],
                      "evidence_required": "Preuve observée ou résultat économique vérifiable; la sortie LLM seule n'est pas une preuve."}
            if item.get("expected_signal"):
                fields["expected_signal"] = item["expected_signal"]
            if item.get("stop_criterion"):
                fields["stop_criterion"] = item["stop_criterion"]
            hypothesis_id = strategy.create(
                "hypothesis", business, "Hypothèse stratégique proposée par pursuit; pertinence économique distincte de l'exécutabilité",
                _conn=conn, created_by="octopus:pursuit", parent_id=objective_id, origin_task_id=task_id, **fields)
            created = True
    item["cited_evidence_ids"] = list(item.get("evidence_ids") or [])
    item["evidence_ids"] = _link_cited(conn, business, hypothesis_id, item["cited_evidence_ids"], allowed)
    item["hypothesis_id"] = hypothesis_id
    _write_annotation(conn, business, task_id, hypothesis_id, item)
    return created


def persist(business: str, objective_id: int, task_id: int, assessment: dict, *,
            allowed_evidence_ids) -> dict:
    """Enregistre stratégie et exécutabilité séparément. Idempotent pour la même tâche."""
    stored = copy.deepcopy(assessment)
    allowed = {int(item) for item in allowed_evidence_ids or [] if type(item) is int and item > 0}
    with tasks._tx() as conn:
        business = strategy._business(business)
        objective = strategy.get("objective", objective_id, business, _conn=conn)
        if not objective or objective["status"] != "active":
            stored["status"] = "ignored"
            stored["hypothesis_id"] = None
            return stored
        created_retained = False
        for item in stored.get("considered") or []:
            created = _persist_item(conn, business, objective_id, task_id, item, allowed)
            if item.get("strategic_state") == "retained" and created:
                created_retained = True
        retained = stored.get("retained")
        retained_id = None
        if retained:
            match = next((item for item in stored["considered"] if item.get("key") == retained.get("key")), None)
            if match and match.get("strategic_state") != "invalidated":
                retained.update({key: match.get(key) for key in (
                    "hypothesis_id", "evidence_annotation_id", "evidence_ids", "cited_evidence_ids")})
                retained_id = match.get("hypothesis_id")
            else:
                stored["retained"] = None
                created_retained = False
        stored["hypothesis_id"] = retained_id
        stored["status"] = "created" if created_retained else ("reused" if retained_id else "no_retained")
        stored["reconsiders_hypothesis_id"] = None
    return stored


def _payload(row) -> dict | None:
    try:
        payload = json.loads(row["observation"])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return None
    if strategy._mentions_legacy_business(payload.get("statement"), payload.get("economic_justification")):
        return None
    payload = dict(payload)
    payload["hypothesis_status"] = row["hypothesis_status"]
    payload["evidence_id"] = int(row["evidence_id"])
    return payload


def recorded_strategies(business: str, objective_id: int) -> list[dict]:
    """Dernière annotation système par hypothèse. La reprise relit ces états séparés."""
    from . import journal
    rows = journal.query(
        "SELECT h.id AS hypothesis_id, h.status AS hypothesis_status, e.id AS evidence_id, e.observation "
        "FROM strategy_hypotheses h JOIN strategy_links l ON l.business=h.business AND l.from_type='hypothesis' "
        "AND l.from_id=h.id AND l.to_type='evidence' AND l.relation=? "
        "JOIN strategy_evidence e ON e.business=h.business AND e.id=l.to_id AND e.status='active' "
        "AND e.nature='computed' AND e.source_type=? AND e.created_by=? "
        "WHERE h.business=? AND h.objective_id=? ORDER BY e.id DESC",
        (RELATION, SOURCE_TYPE, ACTOR, strategy._business(business), objective_id))
    seen = set()
    found = []
    for row in rows:
        if row["hypothesis_id"] in seen:
            continue
        seen.add(row["hypothesis_id"])
        payload = _payload(row)
        if payload:
            found.append(payload)
    found.sort(key=lambda item: (0 if item.get("strategic_state") == "retained" else 1,
                                 item.get("economic_rank") if type(item.get("economic_rank")) is int else 10**6,
                                 item.get("statement") or ""))
    return found


def latest_annotation(business: str, hypothesis_id: int) -> dict | None:
    from . import journal
    rows = journal.query(
        "SELECT h.status AS hypothesis_status, e.id AS evidence_id, e.observation "
        "FROM strategy_hypotheses h JOIN strategy_links l ON l.business=h.business AND l.from_type='hypothesis' "
        "AND l.from_id=h.id AND l.to_type='evidence' AND l.relation=? "
        "JOIN strategy_evidence e ON e.business=h.business AND e.id=l.to_id AND e.status='active' "
        "AND e.nature='computed' AND e.source_type=? AND e.created_by=? "
        "WHERE h.business=? AND h.id=? ORDER BY e.id DESC LIMIT 1",
        (RELATION, SOURCE_TYPE, ACTOR, strategy._business(business), hypothesis_id))
    return _payload(rows[0]) if rows else None
