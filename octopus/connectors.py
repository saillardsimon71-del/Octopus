"""Sources de données externes d'un business (CRM, finance, réseaux sociaux, messagerie, publication).

Aucun connecteur réel n'existe encore : chaque domaine est déclaré « non configuré » tant qu'une sonde
n'est pas enregistrée. Une sonde doit dire ce qu'elle sait vraiment (disponibilité, période couverte,
provenance) et ne jamais fabriquer de valeur : les chiffres entrent ensuite comme preuves sourcées
(`octopus.strategy`, nature `observed`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

DOMAINS = {
    "crm": "clients et prospects",
    "finance": "revenus, coûts et marges",
    "social": "statistiques des réseaux sociaux",
    "messaging": "conversations et funnel",
    "publication": "publications réelles",
}


def _domain_description(domain: str) -> str:
    return DOMAINS[domain]


@dataclass(frozen=True)
class SourceStatus:
    domain: str
    available: bool
    reason: str
    provider: str | None = None
    period_start: float | None = None
    period_end: float | None = None
    provenance: str | None = None


_PROBES: dict[str, Callable[[str], SourceStatus]] = {}


def register(domain: str, probe: Callable[[str], SourceStatus]) -> None:
    """Déclare la sonde d'un domaine ; elle reçoit l'identifiant du business."""
    if domain not in DOMAINS:
        raise ValueError(f"domaine inconnu : {domain!r} (attendu : {sorted(DOMAINS)})")
    _PROBES[domain] = probe


def status(business: str) -> list[SourceStatus]:
    out = []
    for domain in DOMAINS:
        probe = _PROBES.get(domain)
        if probe is None:
            out.append(SourceStatus(domain, False, "non configuré"))
            continue
        try:
            result = probe(business)
        except Exception as exc:  # une source en panne est indisponible, jamais « zéro »
            result = SourceStatus(domain, False, f"erreur de la source : {type(exc).__name__}: {exc}"[:200])
        if result.domain != domain:
            result = SourceStatus(domain, False, f"sonde incohérente (domaine {result.domain!r})")
        out.append(result)
    return out


def summary_line(business: str) -> str:
    """Une ligne lisible : domaines connectés et domaines absents, sans aucune valeur métier."""
    states = status(business)
    missing = [_domain_description(s.domain) for s in states if not s.available]
    present = [f"{_domain_description(s.domain)} ({s.provider or 'source'})" for s in states if s.available]
    parts = []
    if present:
        parts.append("connectées : " + ", ".join(present))
    if missing:
        parts.append("non connectées, non évaluées : " + ", ".join(missing))
    return "Données externes " + " ; ".join(parts)
