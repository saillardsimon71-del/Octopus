"""Cohérence des consignes de requête : fraîcheur générale vs stratégie business signal.

Run #63 : SOUT a produit `2026 freelance request data migration budget cloud`,
`freelance data migration quote 2026`, `reddit freelance client manual data entry painful 2026`,
`budget cloud migration PME 2026 France appel offres`. Deux consignes se contredisaient :

* `_freshness_context` : « privilégie {year} » ;
* `_business_signal_contract` : « n'ajoute pas l'année courante par défaut à la requête ».

Ces tests gèlent la résolution : la date reste connue du système, elle ne devient pas
un réflexe d'écriture de requête.
"""
from __future__ import annotations

from datetime import date

import pytest

from agents import deepseek, runtime

YEAR = date.today().isoformat()[:4]
TODAY = date.today().isoformat()

# Fragments de la consigne générale de fraîcheur (mode non business).
GENERAL_FRESHNESS = f"privilégie {YEAR}"
# Fragments attendus du mode business signal.
BUSINESS_YEAR_RULE = "n'ajoute pas l'année courante"
PROGRESSIVE_STRATEGY = "commence par une requête courte"
BUDGET_RULE = "n'exige PAS que le mot 'budget'"
SITE_RULE = "deuxième intention"


def _missions(monkeypatch, *, business_signal: bool, goal: str = "trouver des signaux") -> list[tuple]:
    """Exécute une mission et renvoie les appels LLM (agent, tâche, messages)."""
    calls: list[tuple] = []
    actions = [
        {"tasks": [{"role": "SOUT", "task": "collecter"}]},
        {"tool": "search", "args": {"query": "mission migration cloud"}},
        {"final": "ok"},
        {"rapport": "rapport", "business_signals": []} if business_signal else {"rapport": "rapport"},
    ]
    remaining = iter(actions)

    def call_json(agent, task, model, messages, **kwargs):
        calls.append((agent, task, messages))
        return next(remaining)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    runtime.run_mission(
        goal,
        max_steps_per_agent=2,
        business="octopus",
        allowed_tools={"search", "browse"},
        business_signal_focus=business_signal,
        business_signal_target=1,
    )
    return calls


def _planner_system(calls) -> str:
    for _agent, task, messages in calls:
        if task == "planification":
            return messages[0]["content"]
    raise AssertionError("aucun appel de planification capturé")


def _agent_prompt(calls, role: str = "SOUT") -> tuple[str, str]:
    for agent, task, messages in calls:
        if agent == role and task == "action":
            return messages[0]["content"], messages[1]["content"]
    raise AssertionError(f"aucun appel d'action capturé pour {role}")


def test_general_mode_keeps_the_general_freshness_rule(monkeypatch):
    calls = _missions(monkeypatch, business_signal=False)

    planner, agent_system = _planner_system(calls), _agent_prompt(calls)[0]

    assert f"DATE ACTUELLE : {TODAY}" in planner
    assert GENERAL_FRESHNESS in planner
    assert f"DATE ACTUELLE : {TODAY}" in agent_system
    assert GENERAL_FRESHNESS in agent_system


def test_business_planner_does_not_push_the_current_year(monkeypatch):
    calls = _missions(monkeypatch, business_signal=True)

    planner = _planner_system(calls)

    assert f"DATE ACTUELLE : {TODAY}" in planner
    assert GENERAL_FRESHNESS not in planner
    assert BUSINESS_YEAR_RULE in planner


def test_business_agent_does_not_push_the_current_year(monkeypatch):
    calls = _missions(monkeypatch, business_signal=True)

    agent_system, _ = _agent_prompt(calls)

    assert f"DATE ACTUELLE : {TODAY}" in agent_system
    assert GENERAL_FRESHNESS not in agent_system
    assert BUSINESS_YEAR_RULE in agent_system


def test_business_mode_explains_how_to_use_the_year(monkeypatch):
    calls = _missions(monkeypatch, business_signal=True)
    agent_system, task = _agent_prompt(calls)

    assert "année" in agent_system
    assert "discriminante" in agent_system
    # La règle reste énoncée côté contrat de tâche : elle n'a pas été supprimée.
    assert BUSINESS_YEAR_RULE in task
    assert "vérifie plutôt la fraîcheur dans la source ouverte" in task


def test_business_mode_keeps_the_progressive_search_strategy(monkeypatch):
    calls = _missions(monkeypatch, business_signal=True)
    planner, (agent_system, task) = _planner_system(calls), _agent_prompt(calls)

    assert PROGRESSIVE_STRATEGY in planner
    assert PROGRESSIVE_STRATEGY in task
    assert "2 à 5 termes discriminants" in task


def test_business_mode_does_not_require_the_word_budget(monkeypatch):
    calls = _missions(monkeypatch, business_signal=True)
    planner, (_, task) = _planner_system(calls), _agent_prompt(calls)

    assert BUDGET_RULE in planner
    assert BUDGET_RULE in task


def test_business_mode_treats_site_as_a_refinement(monkeypatch):
    calls = _missions(monkeypatch, business_signal=True)
    planner, agent_system, task = _planner_system(calls), *_agent_prompt(calls)

    assert SITE_RULE in planner
    assert SITE_RULE in task
    # L'ancienne incitation générale (« cibler un domaine » dès la première recherche) est absente.
    assert "Pour cibler un domaine" not in agent_system


def test_business_mode_still_mentions_a_recent_source_goal(monkeypatch):
    calls = _missions(monkeypatch, business_signal=True)

    agent_system = _agent_prompt(calls)[0]

    assert "récentes" in agent_system
    assert "source" in agent_system


def test_business_mode_is_scoped_to_the_mission(monkeypatch):
    """Hors mission business, la consigne générale est intacte (pas d'effet de bord global)."""
    _missions(monkeypatch, business_signal=True)

    system, _first_user, _done_label = runtime.build_prompts("SOUT", "veille")

    assert "privilégie" not in system  # run courant = activité historique : pas de contexte de date


@pytest.mark.parametrize("enabled", [True, False])
def test_freshness_context_never_loses_the_date(enabled):
    class Run:
        business = "octopus"

    text = runtime._freshness_context(Run(), business_signal=enabled)

    assert f"DATE ACTUELLE : {TODAY}" in text
