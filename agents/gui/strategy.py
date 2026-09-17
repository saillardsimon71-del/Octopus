"""Actions stratégiques prêtes à déléguer au moteur de missions OCTOPUS.

Ce module ne lance rien et ne stocke rien : il compose seulement des objectifs
structurés pour le runtime existant. Le cockpit reste ainsi une couche de pilotage.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .workspaces import Business, DEFAULT_BUSINESS_ID


@dataclass(frozen=True, slots=True)
class StrategicAction:
    key: str
    title: str
    phase: str
    description: str
    objective: str


STRATEGIC_ACTIONS: tuple[StrategicAction, ...] = (
    StrategicAction(
        "discover", "Découvrir", "1 · Découverte",
        "Chercher des marchés, besoins, niches, concurrents et opportunités nouvelles.",
        "Cherche activement de nouvelles opportunités business pertinentes. Explore le marché, les tendances, les concurrents, les problèmes récurrents et les canaux d'acquisition. Compare plusieurs pistes et propose des expériences concrètes à faible coût pour valider les meilleures.",
    ),
    StrategicAction(
        "validate", "Valider", "2 · Validation",
        "Transformer une opportunité en hypothèse testable avant de surinvestir.",
        "Prends les opportunités disponibles et construis un plan de validation. Identifie cible, problème, proposition de valeur, preuve à obtenir, offre minimale, canal de test, coût attendu, délai et critères d'arrêt. Priorise les expériences réversibles et mesurables.",
    ),
    StrategicAction(
        "offer", "Construire l'offre", "3 · Offre",
        "Créer une offre claire, monétisable et exploitable par les agents.",
        "Conçois ou améliore l'offre du business : cible, promesse, packaging, prix, preuve, objections, CTA, livraison et différenciation. Cherche une version simple à vendre et à automatiser avant d'ajouter de la complexité.",
    ),
    StrategicAction(
        "content", "Moteur contenu", "4 · Distribution",
        "Planifier une machine de contenu durable pour les réseaux.",
        "Conçois une stratégie de contenu durable pour ce business : angles éditoriaux, formats courts, cadence, réutilisation multi-plateforme, tests de hooks, preuves sociales et boucles d'apprentissage. Relie chaque contenu à une hypothèse business mesurable.",
    ),
    StrategicAction(
        "funnel", "Construire le funnel", "5 · Conversion",
        "Concevoir les parcours contenu → conversation → offre → vente.",
        "Conçois un funnel simple et mesurable depuis le contenu ou les réseaux vers la conversion. Examine notamment les CTA du type commentaire/mot-clé, les messages automatiques ou manuels, la capture de demande, la qualification, le suivi et l'offre finale. Signale clairement ce qui nécessite une intégration technique ou humaine.",
    ),
    StrategicAction(
        "clients", "Boucle client", "6 · Clients",
        "Améliorer acquisition, onboarding, satisfaction, rétention et expansion.",
        "Analyse le parcours client disponible pour ce business. Propose des actions concrètes pour acquisition, qualification, onboarding, suivi, satisfaction, rétention, réactivation et ventes additionnelles. Distingue ce qui peut être automatisé de ce qui doit rester humain et demande des données manquantes plutôt que de les inventer.",
    ),
    StrategicAction(
        "reinvest", "Réinvestir", "7 · Capital",
        "Décider où réinvestir les gains avec une logique expérimentale.",
        "Construis une stratégie de réinvestissement disciplinée à partir des revenus, coûts, marges et résultats réellement disponibles. Sépare réserve de sécurité, maintien des opérations, expérimentation, acquisition, contenu, infrastructure et nouveaux business. Propose des seuils de décision et des règles de réallocation plutôt qu'un plan fondé sur des chiffres inventés.",
    ),
    StrategicAction(
        "review", "Revue stratégique", "8 · Boucle longue",
        "Faire une revue périodique du portefeuille et décider de continuer, corriger ou arrêter.",
        "Fais une revue stratégique du business : progrès vers les objectifs, preuves obtenues, goulots d'étranglement, coûts, qualité, apprentissages, risques, prochaines expériences et décisions à prendre. Termine par un plan d'action court terme et une liste de décisions à revoir périodiquement.",
    ),
)


def build_objective(action: StrategicAction, business: Business | None, *, offers: list[str] | None = None) -> str:
    """Compose une mission ORBIT sans inventer de données business."""
    if business is None or business.id == DEFAULT_BUSINESS_ID:
        context = "Contexte : portefeuille global OCTOPUS."
    else:
        context = f"Business : {business.label()}."
        if business.description:
            context += f" Description : {business.description}."
    selected_offers = list(offers or (business.offers if business else []))
    if selected_offers:
        context += " Offres connues : " + ", ".join(selected_offers[:12]) + "."
    return f"{context}\n\nPhase : {action.phase}\nObjectif : {action.objective}"


NATURE_LABELS = {"observed": "observé", "computed": "calculé", "inferred": "inféré", "hypothesis": "hypothèse",
                 "unverified": "non vérifié"}


def overview_lines(state: dict) -> list[str]:
    """Texte d'affichage de `octopus.strategy.overview` : aucune donnée ajoutée, aucun chiffre inventé."""
    def section(title: str, rows: list[dict], fmt) -> list[str]:
        return [f"{title} ({len(rows)})"] + ([f"  {fmt(r)}" for r in rows] or ["  aucun"])

    def when(ts) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "sans échéance"

    lines = section("Objectifs actifs", state["objectives"], lambda r: f"#{r['id']} {r['summary']}")
    lines += section("Hypothèses en test", state["hypotheses"], lambda r: f"#{r['id']} {r['summary']}")
    lines += section("Expériences ouvertes", state["experiments"],
                     lambda r: f"#{r['id']} [{r['status']}] {r['summary']} · {when(r['deadline_at'])}")
    lines += section("Preuves récentes", state["evidence"],
                     lambda r: f"#{r['id']} [{NATURE_LABELS.get(r['nature'], r['nature'])}] {r['summary']}")
    lines += section("Décisions à valider par un humain", state["decisions"], lambda r: f"#{r['id']} {r['summary']}")
    lines += section("Revues planifiées", state["reviews"], lambda r: f"#{r['id']} {r['summary']} · {when(r['due_at'])}")
    lines.append(state.get("sources") or "Données externes : état inconnu")
    return lines


def portfolio_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return ["Aucun objet stratégique enregistré.",
                "Créer : python -m octopus strategy add objective <business> \"résumé\" --by human --set statement=\"...\""]
    return [f"{r['business']} · objectifs actifs {r['objectives']} · expériences ouvertes {r['experiments']} · "
            f"décisions en attente {r['decisions']}" for r in rows]


def get_action(key: str) -> StrategicAction:
    for action in STRATEGIC_ACTIONS:
        if action.key == key:
            return action
    raise KeyError(key)
