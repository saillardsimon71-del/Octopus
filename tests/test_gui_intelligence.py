"""Tests sans ouverture de fenêtre pour la couche Intelligence du cockpit."""
from __future__ import annotations

from agents.gui.strategy import STRATEGIC_ACTIONS, build_objective
from agents.gui.workspaces import Business, DEFAULT_BUSINESS_ID


def test_strategic_actions_cover_business_loop():
    keys = [action.key for action in STRATEGIC_ACTIONS]
    assert keys == [
        "discover",
        "validate",
        "offer",
        "content",
        "funnel",
        "clients",
        "reinvest",
        "review",
    ]


def test_business_context_is_included_without_inventing_metrics():
    business = Business("creator_ai", "Creator AI", "Business de contenu", ["creator_pack"])
    objective = build_objective(STRATEGIC_ACTIONS[0], business)
    assert "Business : Creator AI" in objective
    assert "creator_pack" in objective
    assert "revenu" not in objective.lower()


def test_global_context_is_explicit():
    objective = build_objective(STRATEGIC_ACTIONS[-1], None, offers=["offer_a"])
    assert "portefeuille global" in objective
    assert "offer_a" in objective
    assert DEFAULT_BUSINESS_ID == "all"
