"""Capacités requises, écarts et acquisition bornée.

Chaîne traitée, sans jamais confondre ses niveaux :

    stratégie retenue -> capacités requises -> inventaire réel -> écarts de capacité
      -> options d'acquisition -> coût / risque / valeur -> décision
      -> construction, connexion ou demande humaine -> validation déterministe
      -> inventaire recalculé

Un écart de capacité est une information opérationnelle, pas un échec stratégique : la
stratégie économiquement retenue reste retenue, aucune stratégie moins pertinente n'est
substituée, aucune permission humaine n'est inventée et aucune action réelle n'est exécutée
tant que la capacité n'est pas validée.

Autorité réelle (aucun second registre) :

- `agents.runtime.TOOLS` et `agents.tool_registry` pour les outils ;
- `octopus.actions.registered_executors` pour les exécuteurs de canaux ;
- `octopus.resources` et ses sondes (`octopus.resource_probes`) pour les ressources déclarées ;
- la politique d'autorisation du caller (`allowed_execution`), jamais élargie ici.

`octopus.strategy_separation` reste le module qui classe la stratégie et annote
l'exécutabilité ; ce module n'ajoute ni outil, ni exécuteur, ni permission, ni dépense.
Un modèle ne peut pas déclarer une capacité disponible : `available` est recalculé à chaque
lecture depuis les sources ci-dessus, jamais mémorisé. Une acquisition précédemment validée
dont la capacité a disparu redevient un écart (`stale_acquisition`).

Persistance : aucune nouvelle table. Les écarts et décisions d'étude sont des preuves
calculées (`strategy_evidence`, nature `computed`) liées à l'hypothèse, comme l'annotation
d'exécutabilité de #116. L'acquisition elle-même est une tâche durable existante
(`capability.acquire`) avec clef d'idempotence, mémoïsation d'étapes et demande humaine
réutilisant `tasks`/`worker`.

CAPABLE n'est pas AUTORISÉ : une capacité techniquement acquise hors de l'ensemble autorisé
reste non exécutable.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Callable

from . import journal, resources, strategy, tasks
from . import strategy_separation as separation

SCHEMA = "octopus.capability_acquisition.v1"
ACTOR = "policy:capability_acquisition"
SOURCE_TYPE = "strategy.capability_acquisition"
RELATION = "capability_acquisition"
TASK_KIND = "capability.acquire"

# État déterministe d'une capacité, recalculé depuis les registres réels.
CAPABILITY_STATES = ("available", "missing", "temporarily_unavailable", "permission_denied",
                     "human_required", "not_established")
_STATE_FROM_EXECUTABILITY = {
    "executable": "available",
    "missing_capability": "missing",
    "temporarily_unavailable": "temporarily_unavailable",
    "permission_denied": "permission_denied",
    "human_required": "human_required",
    "not_established": "not_established",
}
# Cycle de vie d'une acquisition. Il décrit une intention et son avancement ; il ne prouve
# jamais la disponibilité, seule la validation déterministe le fait.
ACQUISITION_STATES = ("none", "planned", "in_progress", "validation_required", "acquired",
                      "failed", "rejected", "deferred", "human_required", "not_needed")
DECISIONS = ("acquire", "defer", "reject", "human_required")
OPTION_KINDS = ("already_present", "local_build", "local_adapter", "human_frontier", "none")
RISK_LEVELS = ("low", "medium", "high", "critical")
COST_CLASSES = ("zero_cost", "free_quota", "local", "paid", "unknown")

# Frontières humaines réelles : le vocabulaire existant de l'inventaire des ressources.
HUMAN_FRONTIERS = frozenset(resources.HUMAN_NEEDS)
# Frontières qui engagent de l'argent ou du juridique : aucun double local ne peut les lever.
MONEY_LEGAL_FRONTIERS = frozenset({"payment_method", "legal", "kyc", "signature", "bank_validation"})

_MAX_CAPABILITIES = 12
_MAX_OPTIONS = 6

# --- modèle économique déterministe ---------------------------------------------------------------
# Priorité : argent encaissé, marge, récurrence, autonomie, croissance. Les valeurs sont des
# pondérations de décision interne, pas des mesures économiques.
CRITERION_SCORE = {"cash_received": 8.0, "margin": 6.0, "recurrence": 4.0, "autonomy": 3.0, "growth": 2.0}
RANK_SCORE = {1: 2.0, 2: 1.0}
PROOF_SCORE = 1.5
PROOF_SCORE_MAX = 3.0
REUSABILITY_SCORE = ((4, 2.5), (3, 2.0), (2, 1.5))
RISK_SCORE = {"low": 0.0, "medium": 1.5, "high": 4.0, "critical": 8.0}
COMPLEXITY_SCORE = 1.0
HUMAN_MINUTES_SCORE = 1.0 / 30.0
LLM_USD_SCORE = 20.0
ACQUIRE_THRESHOLD = 6.0
DEFER_THRESHOLD = 2.0
EXPENSIVE_WITHOUT_PROOF = 8.0
_DECISION_ORDER = {"acquire": 0, "human_required": 1, "defer": 2, "reject": 3}


CAPABILITY_ACQUISITION_CLAUSE = (
    "Les écarts de capacité fournis sont des informations opérationnelles recalculées par le système : "
    "un écart n'est ni un échec économique, ni une permission refusée, ni une raison d'abandonner ou de "
    "remplacer la stratégie retenue. Ils ne prouvent aucune disponibilité et n'autorisent aucune action. "
    "Ne déclare jamais une capacité disponible, acquise ou validée : seul le système le constate. "
    "Ne demande jamais à l'humain de réparer un timeout, un DNS, un endpoint cassé, du JSON ou "
    "d'installer une capacité constructible et testable localement : une demande humaine n'est "
    "admissible que pour une vraie frontière (authentification interactive, validation contractuelle, "
    "paiement, secret ou clef d'API inaccessible, action juridique, permission explicite, matériel "
    "physique).")


class CapabilityAcquisitionError(ValueError):
    """Une entrée d'acquisition est invalide."""


# --- définitions connues (jamais une preuve de disponibilité) --------------------------------------

# Traduction déterministe d'un identifiant de capacité vers les registres réels. Ce n'est pas
# un second registre : il ne déclare aucune disponibilité et n'accorde aucune permission.
CAPABILITY_TARGETS: dict[str, dict[str, tuple[str, ...]]] = {
    "email_send": {"executors": ("email:send",)},
    "phone_call": {"executors": ("phone:call",)},
    "sms_send": {"executors": ("sms:send",)},
    "whatsapp_message": {"executors": ("whatsapp:message",)},
    "linkedin_publish": {"executors": ("linkedin:publish",)},
    "browser_interaction": {"executors": ("browser_form:submit",), "tools": ("browse",),
                            "tool_prefixes": ("browser_",)},
    "payment_receive": {"executors": ("payment:receive",)},
    "video_create": {"executors": ("agnes_video:submit",),
                     "tools": ("agnes_submit_video", "agnes_generate_video_full")},
    "youtube_publish": {"executors": ("youtube:publish",)},
    "tiktok_publish": {"executors": ("tiktok:publish",)},
}

# Ce qu'une capacité exige pour exister. Coûts estimés, explicitement séparés des faits ;
# `None` signifie inconnu (jamais zéro). Une exigence inconnue est traitée en échec sûr.
CAPABILITY_REQUIREMENTS: dict[str, dict] = {
    "phone_call": {"needs": ("login", "payment_method"), "cost_class": "paid", "risk": "high",
                   "complexity": 4, "human_minutes": 45.0, "llm_usd": 0.0, "financial": 20.0,
                   "currency": "EUR", "recurrence_expected": True,
                   "description": "fournisseur de téléphonie : compte, clef d'API et moyen de paiement"},
    "sms_send": {"needs": ("login",), "cost_class": "paid", "risk": "medium", "complexity": 3,
                 "human_minutes": 30.0, "llm_usd": 0.0, "financial": 10.0, "currency": "EUR",
                 "recurrence_expected": True, "description": "API SMS : compte et clef d'API"},
    "whatsapp_message": {"needs": ("login", "oauth"), "cost_class": "paid", "risk": "medium",
                         "complexity": 4, "human_minutes": 60.0, "llm_usd": 0.0, "financial": 10.0,
                         "currency": "EUR", "recurrence_expected": True,
                         "description": "API WhatsApp Business : compte, OAuth et modèle approuvé"},
    "linkedin_publish": {"needs": ("login", "oauth"), "cost_class": "free_quota", "risk": "medium",
                         "complexity": 4, "human_minutes": 45.0, "llm_usd": 0.0, "financial": 0.0,
                         "currency": "EUR", "recurrence_expected": True,
                         "description": "API LinkedIn : authentification interactive et jeton"},
    "payment_receive": {"needs": ("kyc", "bank_validation", "legal"), "cost_class": "paid",
                        "risk": "critical", "complexity": 5, "human_minutes": 120.0, "llm_usd": 0.0,
                        "financial": None, "currency": "EUR", "recurrence_expected": True,
                        "description": "encaissement : KYC, validation bancaire et cadre juridique"},
    "youtube_publish": {"needs": ("login", "oauth"), "cost_class": "free_quota", "risk": "medium",
                        "complexity": 4, "human_minutes": 45.0, "llm_usd": 0.0, "financial": 0.0,
                        "currency": "EUR", "recurrence_expected": False,
                        "description": "API YouTube : compte Google et OAuth interactif"},
    "tiktok_publish": {"needs": ("login", "oauth"), "cost_class": "free_quota", "risk": "medium",
                       "complexity": 4, "human_minutes": 45.0, "llm_usd": 0.0, "financial": 0.0,
                       "currency": "EUR", "recurrence_expected": False,
                       "description": "API TikTok : compte développeur et OAuth interactif"},
    "email_send": {"needs": ("login",), "cost_class": "free_quota", "risk": "low", "complexity": 2,
                   "human_minutes": 15.0, "llm_usd": 0.0, "financial": 0.0, "currency": "EUR",
                   "recurrence_expected": True,
                   "description": "SMTP configuré (`octopus.smtp_executor`) : hôte et identifiants"},
    "browser_interaction": {"needs": (), "cost_class": "local", "risk": "low", "complexity": 2,
                            "human_minutes": 10.0, "llm_usd": 0.0, "financial": 0.0,
                            "currency": "EUR", "recurrence_expected": True,
                            "description": "espace de travail navigateur existant (Hermes agent-browser)"},
    "video_create": {"needs": (), "cost_class": "free_quota", "risk": "low", "complexity": 2,
                     "human_minutes": 15.0, "llm_usd": 0.0, "financial": 0.0, "currency": "EUR",
                     "recurrence_expected": False, "description": "service Agnes local sur boucle locale"},
}

_TARGETS: dict[str, dict[str, tuple[str, ...]]] = {key: dict(value) for key, value in CAPABILITY_TARGETS.items()}
_REQUIREMENTS: dict[str, dict] = {key: dict(value) for key, value in CAPABILITY_REQUIREMENTS.items()}


@dataclass(frozen=True)
class Builder:
    """Constructeur local d'une capacité. Aucun effet externe, aucune dépense, aucune permission."""

    capability: str
    build: Callable[[dict], dict]
    validate: Callable[[str], dict] | None = None
    financial: float | None = 0.0
    currency: str = "EUR"
    llm_usd: float = 0.0
    human_minutes: float = 0.0
    complexity: int = 1
    risk: str = "low"
    local_safe: bool = True
    description: str = ""

    def cost(self) -> dict:
        return _cost(self.financial, self.currency, self.llm_usd, self.human_minutes, self.complexity, self.risk)


_BUILDERS: dict[str, Builder] = {}
_VALIDATORS: dict[str, Callable[[str], dict]] = {}


def register_builder(capability: str, build: Callable[[dict], dict], *,
                     validate: Callable[[str], dict] | None = None, financial: float | None = 0.0,
                     currency: str = "EUR", llm_usd: float = 0.0, human_minutes: float = 0.0,
                     complexity: int = 1, risk: str = "low", local_safe: bool = True,
                     description: str = "") -> Builder:
    """Enregistre un constructeur local explicite. Aucune découverte, aucune installation implicite.

    Le constructeur ne peut ni élargir une permission, ni dépenser, ni produire un effet externe.
    Sa réussite déclarée ne vaut pas validation : l'état est toujours recalculé depuis les registres.
    """
    identifier = canonical(capability)
    if not identifier:
        raise CapabilityAcquisitionError(f"capacité invalide : {capability!r}")
    if not callable(build):
        raise CapabilityAcquisitionError("build doit être appelable")
    if validate is not None and not callable(validate):
        raise CapabilityAcquisitionError("validate doit être appelable")
    if risk not in RISK_LEVELS:
        raise CapabilityAcquisitionError(f"risque invalide : {risk!r} (attendu : {RISK_LEVELS})")
    if type(complexity) is not int or not 1 <= complexity <= 5:
        raise CapabilityAcquisitionError("complexity doit être un entier entre 1 et 5")
    for name, value in (("financial", financial), ("llm_usd", llm_usd), ("human_minutes", human_minutes)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or value < 0 or value != value or value in (float("inf"), float("-inf"))):
            raise CapabilityAcquisitionError(f"{name} doit être un nombre fini positif ou nul, ou None (inconnu)")
    builder = Builder(identifier, build, validate, None if financial is None else float(financial),
                      str(currency or "EUR").strip().upper(), float(llm_usd), float(human_minutes),
                      complexity, risk, bool(local_safe), str(description or "")[:300])
    _BUILDERS[identifier] = builder
    return builder


def unregister_builder(capability: str) -> None:
    identifier = canonical(capability)
    _BUILDERS.pop(identifier, None)
    _VALIDATORS.pop(identifier, None)


def builders() -> dict[str, Builder]:
    """Constructeurs locaux explicitement enregistrés. Vide par défaut : rien n'est découvert."""
    return dict(_BUILDERS)


def register_validator(capability: str, validate: Callable[[str], dict]) -> None:
    """Test contractuel déterministe hors production. `ok=True` exige une source vérifiable."""
    identifier = canonical(capability)
    if not identifier or not callable(validate):
        raise CapabilityAcquisitionError("validateur invalide")
    _VALIDATORS[identifier] = validate


def register_target(capability: str, *, executors=(), tools=(), tool_prefixes=()) -> None:
    """Nomme les entrées de registres réels correspondant à une capacité. N'accorde rien."""
    identifier = canonical(capability)
    if not identifier:
        raise CapabilityAcquisitionError(f"capacité invalide : {capability!r}")
    entry = dict(_TARGETS.get(identifier) or {})
    for key, values in (("executors", executors), ("tools", tools), ("tool_prefixes", tool_prefixes)):
        added = [str(value) for value in values or () if str(value).strip()]
        merged = tuple(dict.fromkeys([*(entry.get(key) or ()), *added]))
        if merged:
            entry[key] = merged
    _TARGETS[identifier] = entry


def register_requirement(capability: str, **fields) -> None:
    """Définition connue d'une capacité (exigences et coûts estimés). Jamais une disponibilité."""
    identifier = canonical(capability)
    if not identifier:
        raise CapabilityAcquisitionError(f"capacité invalide : {capability!r}")
    needs = tuple(str(n).strip().lower() for n in fields.get("needs") or ())
    unknown = sorted(set(needs) - HUMAN_FRONTIERS)
    if unknown:
        raise CapabilityAcquisitionError(f"frontière humaine inconnue : {unknown} (attendu : {sorted(HUMAN_FRONTIERS)})")
    risk = fields.get("risk", "low")
    if risk not in RISK_LEVELS:
        raise CapabilityAcquisitionError(f"risque invalide : {risk!r}")
    cost_class = fields.get("cost_class", "unknown")
    if cost_class not in COST_CLASSES:
        raise CapabilityAcquisitionError(f"classe de coût invalide : {cost_class!r}")
    entry = dict(_REQUIREMENTS.get(identifier) or {})
    entry.update({key: value for key, value in fields.items() if value is not None})
    entry.update(needs=needs, risk=risk, cost_class=cost_class)
    _REQUIREMENTS[identifier] = entry


def canonical(value) -> str | None:
    """Identifiant normalisé d'une capacité. `None` si l'identifiant est inutilisable."""
    return separation._capability_id(value)


def _canonical_set(values) -> set[str]:
    found = set()
    for value in values or ():
        identifier = canonical(value)
        if identifier:
            found.add(identifier)
    return found


def requirement(capability: str) -> dict:
    """Définition connue d'une capacité, ou exigences inconnues (traitées en échec sûr)."""
    identifier = canonical(capability) or ""
    entry = dict(_REQUIREMENTS.get(identifier) or {})
    entry.setdefault("needs", ())
    entry.setdefault("risk", "low")
    entry.setdefault("complexity", None)
    entry.setdefault("human_minutes", None)
    entry.setdefault("llm_usd", 0.0)
    entry.setdefault("financial", None)
    entry.setdefault("currency", "EUR")
    entry.setdefault("cost_class", "unknown")
    entry.setdefault("recurrence_expected", False)
    entry.setdefault("description", "")
    entry["known"] = identifier in _REQUIREMENTS
    return entry


def targets(capability: str) -> dict:
    """Entrées de registres réels attendues pour une capacité, y compris par dérivation.

    L'identifiant lui-même est toujours candidat : une capacité peut porter exactement le nom d'un
    outil (`search`) ou d'un exécuteur (`email:send`). Un identifiant composé donne aussi son
    exécuteur dérivé (`dummy_echo` -> `dummy:echo`). Rien ici ne déclare une disponibilité.
    """
    identifier = canonical(capability) or ""
    entry = {key: tuple(value) for key, value in (_TARGETS.get(identifier) or {}).items()}
    executors = list(entry.get("executors") or ())
    if "_" in identifier and ":" not in identifier:
        kind, _, action = identifier.partition("_")
        executors.append(f"{kind}:{action}")
    entry["executors"] = tuple(dict.fromkeys([*executors, identifier]))
    entry["tools"] = tuple(dict.fromkeys([identifier, *(entry.get("tools") or ())]))
    entry["tool_prefixes"] = tuple(entry.get("tool_prefixes") or ())
    return entry


# --- inventaire recalculé --------------------------------------------------------------------------

def system_inventory(allowed_execution=()) -> separation.Inventory:
    """Inventaire d'exécution recalculé depuis les sources réelles.

    N'ajoute aucun outil, n'enregistre aucun exécuteur et n'élargit aucune permission :
    `allowed_execution` est la politique du caller.
    """
    from agents.runtime import TOOLS
    present = set(TOOLS)
    return separation.build_inventory(
        present_tools=present, allowed_execution=_canonical_set(allowed_execution),
        executors=separation.executor_ids(),
        temporarily_unavailable=separation.browser_unavailable_tools(present))


def resource_facts(capability: str, *, limit: int = 12) -> list[dict]:
    """Ressources déclarées portant cette capacité. Lecture seule : aucune sonde n'est passée."""
    identifier = canonical(capability) or ""
    variants = {identifier, identifier.replace(":", "_"), identifier.replace("_", ":")}
    facts = []
    for row in resources.list_resources():
        if not (variants & set(row.get("capabilities") or ())):
            continue
        facts.append({"key": row["key"], "kind": row["kind"], "state": row["state"], "access": row["access"],
                      "nature": row["nature"], "needs": list(row.get("needs") or ()), "probe": row.get("probe"),
                      "last_check_ok": row.get("last_check_ok"), "last_check_detail": row.get("last_check_detail")})
        if len(facts) >= limit:
            break
    return facts


def resolve(capability: str, inventory: separation.Inventory, *, facts=None) -> dict:
    """Ce qui existe réellement pour une capacité, et ce qui n'existe pas.

    Les cibles sont des alternatives : une seule entrée réelle enregistrée suffit à rendre la
    capacité présente. L'inventaire passé en argument reste la seule source consultée.
    """
    identifier = canonical(capability) or ""
    wanted = targets(identifier)
    candidates = set(wanted["executors"]) | set(wanted["tools"])
    candidates |= {name for prefix in wanted["tool_prefixes"] for name in inventory.present
                   if name.startswith(prefix)}
    facts = resource_facts(identifier) if facts is None else list(facts)
    return {"capability": identifier, "targets": {key: list(value) for key, value in wanted.items()},
            "present_targets": sorted(name for name in candidates if name in inventory.present),
            "absent_targets": sorted(name for name in candidates if name not in inventory.present),
            "resources": facts}


def capability_state(capability: str, inventory: separation.Inventory, *, facts=None,
                     acquisition: dict | None = None) -> dict:
    """État déterministe d'une capacité : capable, autorisé, disponible, acquisition en cours.

    `available` ne vient jamais d'une acquisition mémorisée ni d'une déclaration de modèle :
    il est recalculé depuis l'inventaire passé en argument. Une cible réelle exécutable suffit ;
    sinon le blocage le plus strict des cibles présentes est conservé (permission et frontière
    humaines ne sont jamais requalifiées en absence technique).
    """
    identifier = canonical(capability) or ""
    resolution = resolve(identifier, inventory, facts=facts)
    alternatives = resolution["present_targets"]
    if any(separation.classify_executability([name], inventory)["executability"] == "executable"
           for name in alternatives):
        detected = "executable"
    elif alternatives:
        detected = separation.classify_executability(alternatives, inventory)["executability"]
    else:
        detected = "missing_capability"
    state = _STATE_FROM_EXECUTABILITY.get(detected, "not_established")
    capable = bool(alternatives)
    authorized = detected == "executable"
    acquired = str((acquisition or {}).get("state") or "none")
    stale = acquired == "acquired" and state != "available"
    if state == "available":
        effective = "available"
    elif acquired in ("planned", "in_progress"):
        effective = f"acquisition_{acquired}"
    elif acquired == "validation_required":
        effective = "validation_required"
    elif acquired == "human_required":
        effective = "acquisition_human_required"
    else:
        effective = state
    return {"capability": identifier, "state": state, "effective_state": effective,
            "detected_executability": detected, "capable": capable, "authorized": authorized,
            "available": state == "available", "acquisition_state": acquired,
            "stale_acquisition": stale, "authority": inventory.authority,
            "resolution": resolution}


def snapshot(capabilities, *, inventory: separation.Inventory | None = None,
             business: str | None = None, allowed_execution=()) -> dict:
    """Inventaire recalculé d'un ensemble de capacités : définition, acquisition, disponibilité."""
    inventory = system_inventory(allowed_execution) if inventory is None else inventory
    identifiers = sorted(_canonical_set(capabilities))[:_MAX_CAPABILITIES]
    states = []
    for identifier in identifiers:
        acquisition = (acquisition_state(business, identifier) if business else {"state": "none"})
        states.append(capability_state(identifier, inventory, acquisition=acquisition))
    return {"schema": SCHEMA, "authority": inventory.authority, "generated_at": time.time(),
            "recalculated": True, "inventory": inventory.as_dict(), "capabilities": states}


# --- écarts de capacité ----------------------------------------------------------------------------

_GAP_KINDS = {"missing": "capability_gap", "temporarily_unavailable": "outage",
              "permission_denied": "permission", "human_required": "human_boundary",
              "not_established": "requirements_incomplete", "available": "none"}


def gaps(assessment: dict, inventory: separation.Inventory, *, business: str | None = None,
         acquisition_states: dict | None = None) -> dict:
    """Écarts de la stratégie retenue. Aucune stratégie retenue : aucun écart inventé.

    Une panne temporaire, une permission refusée ou une frontière humaine ne sont pas des écarts
    de capacité : elles ne déclenchent aucune acquisition technique.
    """
    retained = (assessment or {}).get("retained") if isinstance(assessment, dict) else None
    if not retained or retained.get("strategic_state") != "retained":
        return {"status": "no_retained_strategy", "strategy_key": None, "hypothesis_id": None,
                "items": [], "capability_gaps": []}
    required = [identifier for identifier in (retained.get("required_capabilities") or [])
                if canonical(identifier)][:_MAX_CAPABILITIES]
    states = acquisition_states or {}
    # Des exigences incomplètes ou illisibles ne permettent pas d'inventer un écart ni une acquisition.
    complete = bool(retained.get("requirements_complete", True))
    items = []
    for identifier in required:
        identifier = canonical(identifier)
        state = capability_state(identifier, inventory, acquisition=states.get(identifier))
        kind = _GAP_KINDS.get(state["state"], "capability_gap") if complete else "requirements_incomplete"
        items.append({**state, "kind": kind,
                      "requires_acquisition": complete and kind == "capability_gap",
                      "strategy_key": retained.get("key"), "hypothesis_id": retained.get("hypothesis_id")})
    return {"status": "assessed", "strategy_key": retained.get("key"),
            "hypothesis_id": retained.get("hypothesis_id"),
            "statement": retained.get("statement"), "executability": retained.get("executability"),
            "requirements_complete": bool(retained.get("requirements_complete", True)),
            "items": items, "capability_gaps": [item for item in items if item["requires_acquisition"]]}


# --- options d'acquisition -------------------------------------------------------------------------

def _cost(financial, currency, llm_usd, human_minutes, complexity, risk, *, cost_class="unknown",
          nature="estimated") -> dict:
    return {"financial": None if financial is None else float(financial),
            "currency": str(currency or "EUR").strip().upper(), "cost_nature": nature,
            "cost_class": cost_class, "llm_usd": float(llm_usd or 0.0),
            "human_minutes": None if human_minutes is None else float(human_minutes),
            "complexity": None if complexity is None else int(complexity), "risk": risk}


def _option(capability: str, kind: str, description: str, cost: dict, *, needs=(), requires_human=False,
            local_safe=False, executable_here=False, builder=None, blocked_reason=None,
            source="known_requirement") -> dict:
    return {"option_id": f"{capability}#{kind}", "capability": capability, "kind": kind,
            "description": description[:400], "cost": cost, "needs": sorted(set(needs)),
            "human_frontier": bool(sorted(set(needs) & HUMAN_FRONTIERS)),
            "requires_human": bool(requires_human), "local_safe": bool(local_safe),
            "executable_here": bool(executable_here), "builder": builder,
            "blocked_reason": blocked_reason, "source": source, "deterministic": True}


def acquisition_options(capability: str, inventory: separation.Inventory, *, facts=None,
                        requirement_fields: dict | None = None, builders: dict | None = None) -> list[dict]:
    """Manières potentielles d'obtenir la capacité, produites déterministement.

    Aucune option n'élargit une permission : un outil déjà présent mais non autorisé est une
    question d'autorisation, pas une acquisition technique.
    """
    identifier = canonical(capability) or ""
    needed = requirement(identifier) if requirement_fields is None else dict(requirement_fields)
    facts = resource_facts(identifier) if facts is None else list(facts)
    present = resolve(identifier, inventory, facts=facts)["present_targets"]
    pool = _BUILDERS if builders is None else builders
    builder = pool.get(identifier)
    needs = sorted(set(needed.get("needs") or ())
                   | {need for fact in facts for need in fact.get("needs") or () if need in HUMAN_FRONTIERS})
    money_legal = sorted(set(needs) & MONEY_LEGAL_FRONTIERS)
    description = str(needed.get("description") or "")
    options: list[dict] = []
    if present:
        options.append(_option(
            identifier, "already_present",
            description or "déjà enregistré : " + ", ".join(present[:4]),
            _cost(0.0, needed["currency"], 0.0, 0.0, 1, "low", cost_class="local"),
            needs=needs, local_safe=False,
            blocked_reason="présent dans un registre réel : il manque une autorisation, jamais élargie ici",
            source="tool_registry"))
    if builder is not None:
        # Une exigence connue payante ou risquée ne disparaît pas parce qu'un constructeur local
        # existe : le coût retenu est le plus prudent des deux, jamais le plus optimiste.
        cost = builder.cost()
        if needed.get("known"):
            cost = {**cost,
                    "financial": (builder.financial if needed["financial"] is None else
                                  max(builder.financial or 0.0, float(needed["financial"]))),
                    "cost_class": ("paid" if (cost["financial"] or 0) > 0 else needed["cost_class"]),
                    "risk": (needed["risk"] if RISK_LEVELS.index(needed["risk"]) > RISK_LEVELS.index(builder.risk)
                             else builder.risk),
                    "complexity": max(builder.complexity, int(needed["complexity"] or builder.complexity))}
        options.append(_option(
            identifier, "local_build", builder.description or f"construction locale de {identifier}",
            cost, needs=needs, local_safe=builder.local_safe and not money_legal,
            executable_here=builder.local_safe and not money_legal, builder=identifier,
            blocked_reason=("frontière argent ou juridique : aucun double local ne peut la lever"
                            if money_legal else None), source="builder_registry"))
    usable = next((fact for fact in facts if fact["state"] in ("available", "degraded")), None)
    if usable is not None and builder is None:
        options.append(_option(
            identifier, "local_adapter",
            f"adaptateur autour de la ressource {usable['key']} (état {usable['state']})",
            _cost(needed["financial"], needed["currency"], needed["llm_usd"], needed["human_minutes"],
                  needed["complexity"], needed["risk"], cost_class=needed["cost_class"]),
            needs=needs, requires_human=bool(needs), local_safe=False,
            blocked_reason="adaptateur à construire : aucun constructeur local enregistré",
            source="resources"))
    if needs:
        options.append(_option(
            identifier, "human_frontier",
            description or f"frontière humaine réelle : {', '.join(needs)}",
            _cost(needed["financial"], needed["currency"], needed["llm_usd"], needed["human_minutes"],
                  needed["complexity"], needed["risk"], cost_class=needed["cost_class"]),
            needs=needs, requires_human=True, local_safe=False,
            source="known_requirement" if needed.get("known") else "resources"))
    if not options:
        options.append(_option(
            identifier, "none",
            "aucune option déterministe connue : exigences inconnues, aucun constructeur local",
            _cost(None, needed["currency"], 0.0, None, None, "low", cost_class="unknown"),
            needs=needs, local_safe=False, blocked_reason="exigences inconnues : rien n'est inventé",
            source="none"))
    return options[:_MAX_OPTIONS]


# --- évaluation économique -------------------------------------------------------------------------

def strategy_value(retained: dict, *, reusability: int = 1) -> dict:
    """Valeur économique potentielle de la stratégie retenue, lisible et déterministe."""
    criteria = [item for item in (retained or {}).get("economic_criteria") or [] if item in CRITERION_SCORE]
    criterion = min(criteria, key=lambda item: separation.ECONOMIC_CRITERIA.index(item)) if criteria else None
    rank = (retained or {}).get("economic_rank")
    proofs = len((retained or {}).get("evidence_ids") or [])
    reusability = max(1, int(reusability))
    reusability_score = next((score for minimum, score in REUSABILITY_SCORE if reusability >= minimum), 0.0)
    proof_score = min(PROOF_SCORE_MAX, PROOF_SCORE * proofs)
    total = ((CRITERION_SCORE.get(criterion, 0.0) if criterion else 0.0) + RANK_SCORE.get(rank, 0.0)
             + proof_score + reusability_score)
    return {"criterion": criterion, "criterion_score": CRITERION_SCORE.get(criterion, 0.0),
            "economic_rank": rank, "rank_score": RANK_SCORE.get(rank, 0.0), "proof_count": proofs,
            "proof_score": proof_score, "reusability": reusability,
            "reusability_score": reusability_score, "total": round(total, 4),
            "economically_invalidated": bool((retained or {}).get("economically_invalidated"))}


def evaluate_option(option: dict, value: dict, *, sunk_cost_usd: float = 0.0,
                    alternatives: int = 0) -> dict:
    """Décide si l'acquisition vaut la peine. Les coûts déjà dépensés ne pèsent jamais.

    Un coût financier, un risque critique ou une frontière humaine réelle ne sont jamais
    convertis en acquisition automatique : la décision devient `human_required`.
    """
    cost = dict(option.get("cost") or {})
    risk = cost.get("risk") if cost.get("risk") in RISK_SCORE else "low"
    complexity = cost.get("complexity")
    human_minutes = cost.get("human_minutes")
    financial = cost.get("financial")
    cost_score = (RISK_SCORE[risk]
                  + (COMPLEXITY_SCORE * complexity if complexity is not None else 0.0)
                  + (HUMAN_MINUTES_SCORE * human_minutes if human_minutes is not None else 0.0)
                  + LLM_USD_SCORE * float(cost.get("llm_usd") or 0.0))
    unknown_cost = complexity is None or human_minutes is None or financial is None
    net = round(float(value.get("total") or 0.0) - cost_score, 4)
    cost_score = round(cost_score, 4)
    proof_count = int(value.get("proof_count") or 0)
    needs = set(option.get("needs") or ())
    reasons: list[str] = []
    if value.get("economically_invalidated"):
        decision = "reject"
        reasons.append("stratégie économiquement invalidée : aucune acquisition automatique")
    elif option.get("kind") == "none":
        decision = "defer"
        reasons.append("aucune option déterministe connue ; exigences inconnues")
    elif needs & MONEY_LEGAL_FRONTIERS:
        decision = "human_required"
        reasons.append("frontière humaine réelle : " + ", ".join(sorted(needs & MONEY_LEGAL_FRONTIERS)))
    elif risk == "critical":
        decision = "human_required"
        reasons.append("risque critique : autorisation humaine requise")
    elif financial is None:
        decision = "defer"
        reasons.append("coût financier inconnu : inconnu n'est ni zéro ni autorisé")
    elif float(financial) > 0:
        decision = "human_required"
        reasons.append(f"coût financier {float(financial):g} {cost.get('currency') or 'EUR'} : "
                       "enveloppe et autorisation humaine requises")
    elif option.get("kind") == "already_present":
        decision = "human_required"
        reasons.append("capacité présente : il manque une autorisation explicite, pas une construction")
    elif not option.get("local_safe") or not option.get("executable_here"):
        decision = "defer"
        reasons.append(option.get("blocked_reason") or "aucune construction locale sûre disponible")
    elif cost_score >= EXPENSIVE_WITHOUT_PROOF and proof_count == 0:
        decision = "reject"
        reasons.append("coût élevé sans preuve économique liée")
    elif net >= ACQUIRE_THRESHOLD:
        decision = "acquire"
        reasons.append("valeur économique nette suffisante pour une construction locale sûre")
    elif net >= DEFER_THRESHOLD:
        decision = "defer"
        reasons.append("valeur économique nette insuffisante pour le coût ; à reconsidérer avec une preuve")
    else:
        decision = "reject"
        reasons.append("coût supérieur à la valeur économique potentielle")
    if needs and decision != "human_required":
        reasons.append("frontière humaine conservée : " + ", ".join(sorted(needs & HUMAN_FRONTIERS)))
    return {"option_id": option["option_id"], "capability": option["capability"],
            "kind": option["kind"], "decision": decision, "reason": "; ".join(reasons)[:600],
            "value_score": float(value.get("total") or 0.0), "cost_score": cost_score, "net_score": net,
            "value": dict(value), "cost": cost, "needs": sorted(needs),
            "alternatives_executable": int(alternatives),
            "sunk_cost_usd": float(sunk_cost_usd or 0.0), "sunk_cost_excluded": True,
            "unknown_cost": bool(unknown_cost)}


def choose(evaluations: list[dict]) -> dict | None:
    """Choix déterministe : meilleure décision, puis meilleur net, puis identifiant stable."""
    if not evaluations:
        return None
    return sorted(evaluations, key=lambda item: (_DECISION_ORDER.get(item["decision"], 9),
                                                 -float(item["net_score"]), item["option_id"]))[0]


def reusability(assessment: dict, capability: str) -> int:
    """Nombre de stratégies non invalidées de la même comparaison exigeant cette capacité."""
    identifier = canonical(capability)
    considered = (assessment or {}).get("considered") or []
    return sum(1 for item in considered if item.get("strategic_state") != "invalidated"
               and identifier in {canonical(name) for name in item.get("required_capabilities") or ()})


# --- plan d'acquisition ----------------------------------------------------------------------------

def plan_for(business: str, objective_id: int, task_id: int | None, assessment: dict, *,
             inventory: separation.Inventory | None = None, allowed_execution=(),
             persist: bool = True, sunk_costs: dict | None = None) -> dict:
    """Étape de planification : écarts, options, évaluation, décision. N'exécute rien.

    Idempotent pour la même tâche et le même contenu : une annotation identique est réutilisée,
    une modification en ajoute une nouvelle sans réécrire l'ancienne.
    """
    inventory = system_inventory(allowed_execution) if inventory is None else inventory
    retained = (assessment or {}).get("retained") or {}
    # Les écarts persistés ne dépendent que des registres réels : relire le cycle de vie dans
    # l'annotation précédente créerait une boucle et casserait l'idempotence de la reprise.
    found = gaps(assessment, inventory, business=business)
    considered = (assessment or {}).get("considered") or []
    alternatives = sum(1 for item in considered if item.get("strategic_state") != "retained"
                       and item.get("executability") == "executable")
    plans = []
    for gap in found["capability_gaps"]:
        identifier = gap["capability"]
        facts = gap["resolution"]["resources"]
        options = acquisition_options(identifier, inventory, facts=facts)
        value = strategy_value(retained, reusability=reusability(assessment, identifier))
        sunk = float((sunk_costs or {}).get(identifier) or 0.0)
        evaluations = [evaluate_option(option, value, sunk_cost_usd=sunk, alternatives=alternatives)
                       for option in options]
        chosen = choose(evaluations)
        plans.append({"capability": identifier, "state": gap["state"],
                      "effective_state": gap["effective_state"], "acquisition_state": gap["acquisition_state"],
                      "stale_acquisition": gap["stale_acquisition"], "options": options,
                      "evaluations": evaluations, "chosen_option_id": (chosen or {}).get("option_id"),
                      "decision": (chosen or {}).get("decision") or "defer",
                      "reason": (chosen or {}).get("reason") or "aucune option évaluée",
                      "value": value, "sunk_cost_usd": sunk, "sunk_cost_excluded": True,
                      "frontiers": sorted({need for option in options for need in option.get("needs") or ()
                                           if need in HUMAN_FRONTIERS})})
    record = {"schema": SCHEMA, "status": found["status"], "business": str(business),
              "objective_id": int(objective_id), "task_id": None if task_id is None else int(task_id),
              "hypothesis_id": found.get("hypothesis_id"), "strategy_key": found.get("strategy_key"),
              "statement": found.get("statement"), "strategic_state": retained.get("strategic_state"),
              "executability": retained.get("executability"), "authority": inventory.authority,
              "recalculated": True, "items": found["items"], "plans": plans,
              "capability_gaps": [gap["capability"] for gap in found["capability_gaps"]],
              "decisions": {plan["capability"]: plan["decision"] for plan in plans},
              "executed": False, "effects": [], "generated_at": time.time()}
    # Cycle de vie relu depuis les tâches durables. Rendu au caller, jamais réécrit dans
    # l'annotation : une acquisition en cours ne prouve rien sur la disponibilité.
    record["acquisition"] = {
        plan["capability"]: acquisition_state(business, plan["capability"], objective_id=objective_id,
                                               hypothesis_id=record["hypothesis_id"])
        for plan in plans}
    if persist and record["hypothesis_id"]:
        record["evidence_annotation_id"] = _persist_annotation(
            business, int(record["hypothesis_id"]), task_id, record)
    elif persist:
        # Sans hypothèse persistée il n'y a rien à rattacher : l'étude est rendue, jamais inventée.
        record["status"] = "not_persisted" if record["status"] == "assessed" else record["status"]
    return record


def _persist_annotation(business: str, hypothesis_id: int, task_id: int | None, record: dict) -> int:
    """Preuve calculée liée à l'hypothèse. Sous verrou d'écriture : aucune duplication concurrente."""
    payload = _annotation_payload(record, hypothesis_id)
    observation = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(observation.encode()).hexdigest()[:16]
    with tasks._tx() as conn:
        existing = conn.execute(
            "SELECT e.id, e.observation FROM strategy_evidence e JOIN strategy_links l "
            "ON l.business=e.business AND l.from_type='hypothesis' AND l.from_id=? "
            "AND l.to_type='evidence' AND l.to_id=e.id AND l.relation=? "
            "WHERE e.business=? AND e.source_type=? AND e.status='active' AND e.created_by=? "
            "ORDER BY e.id DESC LIMIT 1", (hypothesis_id, RELATION, business, SOURCE_TYPE, ACTOR)).fetchone()
        if existing and existing["observation"] == observation:
            return int(existing["id"])
        source_ref = f"{SOURCE_TYPE}#h{hypothesis_id}#t{task_id or 0}#{digest}"
        reusable = conn.execute(
            "SELECT id FROM strategy_evidence WHERE business=? AND source_type=? AND created_by=? "
            "AND source_ref=? AND status='active' LIMIT 1",
            (business, SOURCE_TYPE, ACTOR, source_ref)).fetchone()
        if reusable:
            evidence_id = int(reusable["id"])
        else:
            evidence_id = strategy.create(
                "evidence", business,
                f"Écarts de capacité et acquisition #{hypothesis_id} : "
                + (", ".join(record.get("capability_gaps") or []) or "aucun"),
                _conn=conn, created_by=ACTOR, origin_task_id=task_id, nature="computed",
                source_type=SOURCE_TYPE, source_ref=source_ref, observation=observation)
        strategy.link(business, "hypothesis", hypothesis_id, "evidence", evidence_id, RELATION, _conn=conn)
    return evidence_id


def _annotation_payload(record: dict, hypothesis_id: int) -> dict:
    # Sans horodatage ni identifiant de tâche : une étude inchangée est réutilisée à la reprise,
    # une étude modifiée ajoute une annotation sans réécrire la précédente.
    return {"schema": SCHEMA, "hypothesis_id": hypothesis_id, "objective_id": record.get("objective_id"),
            "strategy_key": record.get("strategy_key"),
            "statement": record.get("statement"), "strategic_state": record.get("strategic_state"),
            "executability": record.get("executability"), "authority": record.get("authority"),
            "items": record.get("items") or [], "plans": record.get("plans") or [],
            "capability_gaps": record.get("capability_gaps") or [],
            "decisions": record.get("decisions") or {}, "executed": False}


def _payload(row) -> dict | None:
    try:
        payload = json.loads(row["observation"])
    except (TypeError, ValueError, KeyError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return None
    payload = dict(payload)
    payload["hypothesis_status"] = row["hypothesis_status"]
    payload["evidence_id"] = int(row["evidence_id"])
    return payload


_ACQUISITION_QUERY = (
    "SELECT h.id AS hypothesis_id, h.status AS hypothesis_status, e.id AS evidence_id, e.observation "
    "FROM strategy_hypotheses h JOIN strategy_links l ON l.business=h.business AND l.from_type='hypothesis' "
    "AND l.from_id=h.id AND l.to_type='evidence' AND l.relation=? "
    "JOIN strategy_evidence e ON e.business=h.business AND e.id=l.to_id AND e.status='active' "
    "AND e.nature='computed' AND e.source_type=? AND e.created_by=? WHERE ")


def recorded(business: str, objective_id: int) -> list[dict]:
    """Dernière annotation d'acquisition par hypothèse. La reprise relit ces états persistés."""
    rows = journal.query(_ACQUISITION_QUERY + "h.business=? AND h.objective_id=? ORDER BY e.id DESC",
                         (RELATION, SOURCE_TYPE, ACTOR, strategy._business(business), int(objective_id)))
    seen, found = set(), []
    for row in rows:
        if row["hypothesis_id"] in seen:
            continue
        seen.add(row["hypothesis_id"])
        payload = _payload(row)
        if payload:
            found.append(payload)
    return found


def latest(business: str, hypothesis_id: int) -> dict | None:
    rows = journal.query(_ACQUISITION_QUERY + "h.business=? AND h.id=? ORDER BY e.id DESC LIMIT 1",
                         (RELATION, SOURCE_TYPE, ACTOR, strategy._business(business), int(hypothesis_id)))
    return _payload(rows[0]) if rows else None


def latest_for_capability(business: str, capability: str, *, objective_id: int | None = None,
                          limit: int = 20) -> dict | None:
    """Dernière étude persistée concernant une capacité (reprise, inventaire, compte rendu)."""
    identifier = canonical(capability)
    if objective_id is not None:
        records = recorded(business, objective_id)
    else:
        rows = journal.query("SELECT DISTINCT objective_id FROM strategy_hypotheses WHERE business=? "
                             "AND objective_id IS NOT NULL ORDER BY objective_id DESC LIMIT ?",
                             (strategy._business(business), int(limit)))
        records = [item for row in rows for item in recorded(business, int(row["objective_id"]))]
    matches = [record for record in records
               if any(plan.get("capability") == identifier for plan in record.get("plans") or [])]
    return max(matches, key=lambda record: record.get("evidence_id") or 0) if matches else None


# --- validation déterministe -----------------------------------------------------------------------

def validate(capability: str, *, inventory: separation.Inventory | None = None,
             probe: bool = False) -> dict:
    """Une capacité n'est confirmée que par une source déterministe. Jamais par une déclaration.

    Sources admises : exécuteur réellement enregistré, outil réellement enregistré, test
    contractuel enregistré, configuration vérifiée par une sonde (opt-in, hors réseau par défaut).
    """
    identifier = canonical(capability) or ""
    wanted = targets(identifier)
    from . import actions
    registered = {f"{kind}:{action}" for kind, action in actions.registered_executors()}
    try:
        from agents.runtime import TOOLS
        tools = set(TOOLS)
    except Exception:  # le registre d'outils reste l'autorité ; son absence est un constat
        tools = set()
    confirmed, sources = [], []
    executors = sorted(name for name in wanted["executors"] if name in registered)
    if executors:
        confirmed.append("executor_registered")
        sources += [f"actions.executor:{name}" for name in executors]
    present_tools = sorted({name for name in wanted["tools"] if name in tools}
                           | {name for prefix in wanted["tool_prefixes"] for name in tools
                              if name.startswith(prefix)})
    if present_tools:
        confirmed.append("tool_registered")
        sources += [f"tool_registry:{name}" for name in present_tools[:6]]
    validator = _VALIDATORS.get(identifier)
    contract = None
    if validator is not None:
        try:
            result = validator(identifier) or {}
        except Exception as exc:
            result = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}
        contract = {"ok": bool(result.get("ok")), "detail": str(result.get("detail") or "")[:300],
                    "source_ref": str(result.get("source_ref") or "")[:300]}
        if contract["ok"] and contract["source_ref"]:
            confirmed.append("contract_test")
            sources.append(contract["source_ref"])
        elif contract["ok"]:
            contract["ok"] = False
            contract["detail"] = "test contractuel sans source vérifiable"
    probed = []
    if probe:
        for fact in resource_facts(identifier):
            try:
                checked = resources.check(fact["key"])
            except Exception as exc:
                probed.append({"key": fact["key"], "ok": None, "detail": f"{type(exc).__name__}"[:200]})
                continue
            ok = checked["state"] in ("available", "degraded")
            probed.append({"key": checked["key"], "ok": ok, "state": checked["state"],
                           "detail": str(checked.get("last_check_detail") or "")[:200]})
            if ok and checked.get("source_ref"):
                confirmed.append("resource_probe")
                sources.append(f"resource:{checked['key']}:{checked['source_ref']}")
    capable = bool(confirmed)
    available = capable
    if inventory is not None:
        available = capable and identifier in inventory.executable
    return {"capability": identifier, "capable": capable, "available": available,
            "confirmed_by": sorted(set(confirmed)), "source_ref": sources[0] if sources else None,
            "sources": sources[:12], "contract": contract, "probes": probed,
            "authorized": None if inventory is None else identifier in inventory.executable,
            "validated_at": time.time(), "authority": "tool_registry+executors+probes"}


# --- état d'acquisition persistant -----------------------------------------------------------------

def acquisition_idempotency_key(business: str, capability: str, *, objective_id: int | None = None,
                                hypothesis_id: int | None = None) -> str:
    identifier = canonical(capability) or str(capability)
    scope = f"h{int(hypothesis_id)}" if hypothesis_id else (f"o{int(objective_id)}" if objective_id else "global")
    return f"capability:{strategy._business(business)}:{identifier}:{scope}"


def acquisition_keys(business: str, capability: str, *, objective_id: int | None = None,
                     hypothesis_id: int | None = None) -> list[str]:
    """Portées successives d'une acquisition : hypothèse, objectif, puis capacité seule."""
    identifier = canonical(capability) or str(capability)
    keys = []
    if hypothesis_id:
        keys.append(acquisition_idempotency_key(business, identifier, hypothesis_id=int(hypothesis_id)))
    if objective_id:
        keys.append(acquisition_idempotency_key(business, identifier, objective_id=int(objective_id)))
    keys.append(acquisition_idempotency_key(business, identifier))
    return list(dict.fromkeys(keys))


def _task_by_keys(keys: list[str]) -> dict | None:
    """Tâche d'acquisition la plus récente parmi les portées connues. Lecture seule."""
    rows = journal.query(
        f"SELECT * FROM tasks WHERE idempotency_key IN ({','.join('?' for _ in keys)}) "
        "ORDER BY id DESC LIMIT 1", tuple(keys))
    return tasks._row(rows[0]) if rows else None


def acquisition_state(business: str, capability: str, *, objective_id: int | None = None,
                      hypothesis_id: int | None = None) -> dict:
    """État d'acquisition relu depuis la tâche durable et l'annotation persistée. Jamais mémorisé."""
    identifier = canonical(capability) or ""
    keys = acquisition_keys(business, identifier, objective_id=objective_id, hypothesis_id=hypothesis_id)
    task = _task_by_keys(keys)
    key = str((task or {}).get("idempotency_key") or keys[0])
    state, detail, output = "none", None, {}
    if task is not None:
        output = task.get("output") if isinstance(task.get("output"), dict) else {}
        status = str(output.get("status") or "")
        built = tasks.step_value(int(task["id"]), "capability.build", None) is not None
        if task["status"] == "queued":
            # Reprise après crash : la construction mémoïsée existe, la validation reste à refaire.
            state = "validation_required" if built else "planned"
        elif task["status"] in ("running", "waiting_human"):
            state = "human_required" if task["status"] == "waiting_human" else (
                "validation_required" if built else "in_progress")
        elif task["status"] == "done":
            state = {"acquired": "acquired", "already_available": "not_needed",
                     "not_needed": "not_needed", "blocked": "failed", "failed": "failed",
                     "human_required": "human_required"}.get(status, "failed")
        elif task["status"] == "cancelled":
            state = "not_needed"
        else:
            state = "validation_required" if built else "failed"
        detail = str(output.get("reason") or task.get("error") or "")[:300] or None
    if state == "none":
        study = latest_for_capability(business, identifier, objective_id=objective_id)
        decision = (study or {}).get("decisions", {}).get(identifier)
        if decision == "acquire":
            state, detail = "planned", "acquisition décidée, tâche non démarrée"
        elif decision == "human_required":
            state, detail = "human_required", "frontière humaine réelle"
        elif decision == "defer":
            state, detail = "deferred", (study or {}).get("plans", [{}])[0].get("reason") if study else None
        elif decision == "reject":
            state, detail = "rejected", None
    return {"capability": identifier, "state": state, "task_id": int(task["id"]) if task else None,
            "task_status": task["status"] if task else None, "idempotency_key": key,
            "detail": detail, "output_status": str(output.get("status") or "") or None}


# --- démarrage et exécution d'une acquisition ------------------------------------------------------

def start_acquisition(business: str, capability: str, plan_record: dict, *, requested_by: str,
                      objective_id: int | None = None, hypothesis_id: int | None = None,
                      parent_task_id: int | None = None) -> dict:
    """Met en file l'acquisition décidée. Idempotent. N'exécute ni construction ni effet ici.

    Refusée si la décision n'est pas `acquire`, si l'option n'est pas locale et sûre, si un coût
    financier est engagé, si la stratégie est économiquement invalidée ou si la capacité est déjà
    réellement disponible.
    """
    business = strategy._business(business)
    identifier = canonical(capability)
    if not identifier:
        raise CapabilityAcquisitionError(f"capacité invalide : {capability!r}")
    requested_by = strategy._text(requested_by, "requested_by")
    plan = next((item for item in (plan_record or {}).get("plans") or []
                 if item.get("capability") == identifier), None)
    if plan is None:
        return {"status": "refused", "capability": identifier, "task_id": None,
                "reason": "aucun plan d'acquisition pour cette capacité"}
    if plan.get("decision") != "acquire":
        return {"status": "refused", "capability": identifier, "task_id": None,
                "reason": f"décision {plan.get('decision')} : {plan.get('reason')}"}
    option = next((item for item in plan.get("options") or []
                   if item.get("option_id") == plan.get("chosen_option_id")), None)
    if option is None or not option.get("local_safe") or not option.get("executable_here"):
        return {"status": "refused", "capability": identifier, "task_id": None,
                "reason": "option non locale ou non sûre : aucune exécution automatique"}
    if (option.get("cost") or {}).get("financial"):
        return {"status": "refused", "capability": identifier, "task_id": None,
                "reason": "coût financier : autorisation humaine et enveloppe requises"}
    if identifier not in _BUILDERS:
        return {"status": "refused", "capability": identifier, "task_id": None,
                "reason": "aucun constructeur local enregistré"}
    confirmed = validate(identifier)
    if confirmed["capable"]:
        # CAPABLE n'est pas AUTORISÉ : rien à acquérir, l'autorisation reste une décision humaine.
        return {"status": "already_available", "capability": identifier, "task_id": None,
                "confirmed_by": confirmed["confirmed_by"],
                "reason": "capacité déjà confirmée par une source déterministe ; "
                          "son autorisation d'exécution reste distincte"}
    if hypothesis_id and _invalidated(business, int(hypothesis_id)):
        return {"status": "not_needed", "capability": identifier, "task_id": None,
                "reason": "stratégie économiquement invalidée : acquisition non poursuivie"}
    task_id = tasks.enqueue(business, TASK_KIND,
                            {"capability": identifier, "objective_id": objective_id,
                             "hypothesis_id": hypothesis_id, "option": option,
                             "decision": plan.get("decision"), "value": plan.get("value"),
                             "reason": plan.get("reason"), "requested_by": requested_by,
                             "sunk_cost_usd": float(plan.get("sunk_cost_usd") or 0.0)},
                            max_attempts=2, parent_id=parent_task_id,
                            idempotency_key=acquisition_idempotency_key(
                                business, identifier, objective_id=objective_id, hypothesis_id=hypothesis_id))
    return {"status": "queued", "capability": identifier, "task_id": task_id,
            "reason": "acquisition en file ; exécution et validation par la tâche durable"}


def _invalidated(business: str, hypothesis_id: int) -> bool:
    hypothesis = strategy.get("hypothesis", hypothesis_id, business)
    return bool(hypothesis) and hypothesis["status"] == "invalidated"


def execute_acquisition(ctx) -> dict:
    """Corps de la tâche durable `capability.acquire`. Rejouable : aucune duplication après crash.

    Portes successives, toutes déterministes : disponibilité réelle, validité économique,
    frontière humaine, constructeur local, construction mémoïsée, validation séparée.
    """
    capability = canonical(ctx.input.get("capability"))
    if not capability:
        raise CapabilityAcquisitionError("capability invalide dans l'entrée de la tâche")
    objective_id = ctx.input.get("objective_id")
    hypothesis_id = ctx.input.get("hypothesis_id")
    option = ctx.input.get("option") if isinstance(ctx.input.get("option"), dict) else {}
    sunk_cost_usd = float(ctx.input.get("sunk_cost_usd") or 0.0)
    inventory = system_inventory()
    state = capability_state(capability, inventory)
    confirmed = validate(capability, inventory=inventory)

    def finish(status: str, reason: str, **extra) -> dict:
        record = {"capability": capability, "status": status, "reason": reason[:500],
                  "objective_id": objective_id, "hypothesis_id": hypothesis_id,
                  "option_id": option.get("option_id"), "capable": state["capable"],
                  "available": state["available"], "authorized": state["authorized"],
                  "sunk_cost_usd": sunk_cost_usd, "sunk_cost_excluded": True,
                  "executed_external_action": False, **extra}
        ctx.emit("capability.acquisition", {key: record[key] for key in
                                            ("capability", "status", "option_id", "available")})
        return record

    built = tasks.step_value(ctx.id, "capability.build", None)
    if confirmed["capable"]:
        if isinstance(built, dict):
            # Reprise après un crash entre construction et validation : la capacité est confirmée
            # par une source réelle, la preuve et la décision sont persistées sans reconstruire.
            decision_id = ctx.memo("capability.decision", lambda: _record_decision(
                ctx, capability, objective_id, hypothesis_id, option, confirmed, sunk_cost_usd))
            _persist_validation(ctx.business, capability, hypothesis_id, ctx.id, confirmed, built)
            return finish("acquired", "reprise : construction antérieure confirmée par une source déterministe",
                          validation=confirmed, build=built, decision_id=decision_id, state=state,
                          resumed=True)
        # CAPABLE n'est pas AUTORISÉ : une capacité confirmée hors de l'ensemble autorisé reste
        # non exécutable, et il n'y a rien à construire.
        return finish("already_available", "capacité déjà confirmée par une source déterministe ; "
                                           "son autorisation d'exécution reste distincte",
                      validation=confirmed, state=state)
    if hypothesis_id and _invalidated(ctx.business, int(hypothesis_id)):
        return finish("not_needed", "stratégie économiquement invalidée : acquisition arrêtée ; "
                                    "les coûts déjà dépensés ne motivent pas la poursuite")
    if ctx.input.get("decision") != "acquire":
        return finish("blocked", f"décision non exécutable : {ctx.input.get('decision')}")
    needs = sorted(set(option.get("needs") or ()) & HUMAN_FRONTIERS)
    if needs or not option.get("local_safe") or not option.get("executable_here"):
        need = needs[0] if needs else "create"
        question = (f"Capacité {capability} : {option.get('description') or 'acquisition requise'}. "
                    f"Frontière humaine : {', '.join(needs) or 'aucune construction locale sûre'}. "
                    "Une réponse seule n'accorde aucun droit et ne dépense rien.")
        answer = ctx.ask_human(f"capability:{capability}:{need}", question,
                               context={"capability": capability, "need": need, "objective_id": objective_id,
                                        "hypothesis_id": hypothesis_id,
                                        "requested_by": ctx.input.get("requested_by")})
        inventory = system_inventory()
        confirmed = validate(capability, inventory=inventory)
        state = capability_state(capability, inventory)
        return finish("acquired" if confirmed["capable"] else "human_required",
                      "frontière humaine traitée ; capacité "
                      + ("confirmée par une source déterministe" if confirmed["capable"]
                         else "toujours absente : aucune déclaration ne la remplace"),
                      answer=str(answer)[:200], validation=confirmed, state=state)
    builder = _BUILDERS.get(capability)
    if builder is None:
        return finish("blocked", "aucun constructeur local enregistré pour cette capacité")
    build = ctx.memo("capability.build", lambda: builder.build({"capability": capability,
                                                                "objective_id": objective_id,
                                                                "hypothesis_id": hypothesis_id,
                                                                "requested_by": ctx.input.get("requested_by")}))
    if not isinstance(build, dict):
        build = {"detail": str(build)[:200]}
    inventory = system_inventory()
    validation = validate(capability, inventory=inventory)
    state = capability_state(capability, inventory, acquisition={"state": "validation_required"})
    decision_id = ctx.memo("capability.decision", lambda: _record_decision(
        ctx, capability, objective_id, hypothesis_id, option, validation, sunk_cost_usd))
    if validation["capable"]:
        _persist_validation(ctx.business, capability, hypothesis_id, ctx.id, validation, build)
        return finish("acquired", "construction locale validée par une source déterministe",
                      validation=validation, build=build, decision_id=decision_id, state=state)
    return finish("failed", "construction réalisée mais aucune source déterministe ne confirme la capacité",
                  validation=validation, build=build, decision_id=decision_id, state=state)


def _record_decision(ctx, capability: str, objective_id, hypothesis_id, option: dict,
                     validation: dict, sunk_cost_usd: float) -> int:
    """Décision canonique d'acquisition (objet `decision` existant), créée une seule fois par tâche."""
    rationale = (f"Acquisition de {capability} via {option.get('kind')} : {option.get('description') or ''} "
                 f"Validation : {', '.join(validation.get('confirmed_by') or []) or 'aucune'}. "
                 f"Coût financier : {(option.get('cost') or {}).get('financial')} "
                 f"{(option.get('cost') or {}).get('currency') or 'EUR'} (estimé). "
                 f"Coûts déjà dépensés exclus de la décision : {sunk_cost_usd:g} USD.")[:900]
    decision_id = strategy.create(
        "decision", ctx.business, f"Acquisition de capacité : {capability}", created_by=str(
            ctx.input.get("requested_by") or "octopus"), origin_task_id=ctx.id,
        decision="acquire", rationale=rationale,
        resulting_action=f"capability.acquire {capability}")
    strategy.link(ctx.business, "decision", decision_id, "task", ctx.id, "executed_by")
    if objective_id:
        strategy.link(ctx.business, "decision", decision_id, "objective", int(objective_id), "supervises")
    if hypothesis_id:
        strategy.link(ctx.business, "decision", decision_id, "hypothesis", int(hypothesis_id), "advances")
    return decision_id


def _persist_validation(business: str, capability: str, hypothesis_id, task_id: int,
                        validation: dict, build: dict) -> int | None:
    """La validation est une preuve calculée distincte de l'annotation d'étude."""
    payload = {"schema": SCHEMA, "kind": "validation", "capability": capability,
               "capable": validation["capable"], "confirmed_by": validation["confirmed_by"],
               "sources": validation["sources"], "build": {key: build.get(key) for key in
                                                           ("detail", "source_ref", "artifacts")
                                                           if key in build},
               "validated_at": validation["validated_at"]}
    observation = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    source_ref = f"{SOURCE_TYPE}#validation#{capability}#{hashlib.sha256(observation.encode()).hexdigest()[:16]}"
    with tasks._tx() as conn:
        existing = conn.execute("SELECT id FROM strategy_evidence WHERE business=? AND source_type=? "
                                "AND created_by=? AND source_ref=? AND status='active' LIMIT 1",
                                (business, SOURCE_TYPE, ACTOR, source_ref)).fetchone()
        if existing:
            evidence_id = int(existing["id"])
        else:
            evidence_id = strategy.create(
                "evidence", business, f"Validation de capacité : {capability}", _conn=conn,
                created_by=ACTOR, origin_task_id=task_id, nature="computed", source_type=SOURCE_TYPE,
                source_ref=source_ref, observation=observation)
        if hypothesis_id:
            strategy.link(business, "hypothesis", int(hypothesis_id), "evidence", evidence_id,
                          RELATION, _conn=conn)
    return evidence_id


# --- compte rendu ----------------------------------------------------------------------------------

def study_context(record: dict) -> dict:
    """Projection compacte d'une étude persistée, pour le contexte de raisonnement.

    Contexte seulement : ce n'est ni une preuve de disponibilité, ni une autorisation, ni un ordre.
    L'état réel reste recalculé depuis les registres par `capability_state`.
    """
    plans = {}
    for plan in (record or {}).get("plans") or []:
        cost = next((option["cost"] for option in plan.get("options") or []
                     if option["option_id"] == plan.get("chosen_option_id")), None) or {}
        plans[plan["capability"]] = {
            "état": plan.get("effective_state"), "décision": plan.get("decision"),
            "raison": str(plan.get("reason") or "")[:240],
            "coût_financier": cost.get("financial"), "devise": cost.get("currency"),
            "nature_du_coût": cost.get("cost_nature"), "minutes_humaines": cost.get("human_minutes"),
            "risque": cost.get("risk"), "complexité": cost.get("complexity"),
            "frontières_humaines": list(plan.get("frontiers") or []),
        }
    return {"hypothesis_id": (record or {}).get("hypothesis_id"),
            "strategy_key": (record or {}).get("strategy_key"),
            "executability": (record or {}).get("executability"),
            "autorité": (record or {}).get("authority"), "acquisition": plans,
            "aucune_exécution": True, "coûts_déjà_dépensés_exclus": True}


def render(record: dict) -> str:
    """Compte rendu lisible : ce qui manque, ce que cela exigerait, ce que cela vaut, la décision."""
    if not record or record.get("status") == "no_retained_strategy":
        return "aucune stratégie retenue : aucun écart de capacité inventé"
    lines = [f"Stratégie retenue : {record.get('statement') or record.get('strategy_key')}",
             f"Exécutabilité : {record.get('executability')} (autorité : {record.get('authority')})"]
    for item in record.get("items") or []:
        lines.append(f"- {item['capability']} : {item['effective_state']} ({item['kind']})")
    for plan in record.get("plans") or []:
        cost = (next((option["cost"] for option in plan["options"]
                      if option["option_id"] == plan.get("chosen_option_id")), {}) or {})
        lines.append(
            f"  {plan['capability']} -> décision {plan['decision']} : {plan['reason']}\n"
            f"    valeur {plan['value']['total']:g} (critère {plan['value']['criterion'] or 'aucun'}, "
            f"preuves {plan['value']['proof_count']}, réutilisations {plan['value']['reusability']}) ; "
            f"coût financier {cost.get('financial')} {cost.get('currency') or ''} ({cost.get('cost_nature')}) ; "
            f"minutes humaines {cost.get('human_minutes')} ; risque {cost.get('risk')} ; "
            f"complexité {cost.get('complexity')}\n"
            f"    options : " + ", ".join(f"{option['kind']}({option['option_id']})"
                                          for option in plan["options"]))
    if not record.get("plans"):
        lines.append("aucun écart de capacité : rien à acquérir")
    return "\n".join(lines)
