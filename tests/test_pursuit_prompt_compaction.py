"""Prompt structure and execution boundaries, not model quality benchmarks."""
import hashlib
import re
from pathlib import Path

import pytest

from agents import runtime
from octopus import businesses, journal, supervisor
from octopus import strategy_separation as separation
from pursuit_prompt_capture import TASK, capture, metrics


def test_foundation_unchanged():
    content = (Path(__file__).resolve().parents[1] / "docs/FOUNDATION.md").read_bytes()
    digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    assert digest == "d910e0cfd901d70857d29fd28cf3847e0b17067b"


def test_cold_prompt_projection_and_unchanged_execution_limits():
    captured = capture()
    goal = captured["goal"]
    assert len(goal) < 2500
    for fact in ("performance économique réelle", "argent client réellement encaissé → marge → récurrence → autonomie → croissance",
                 "Choisis librement marchés, problèmes, acheteurs, offres, hypothèses et ordre d'exploration",
                 "limitent l'exécution, pas la réflexion stratégique", "hypothèse, inférence, observation sourcée, preuve",
                 "inconnue, jamais zéro ni preuve négative", "budget économique externe 0 EUR"):
        assert fact in goal
    preamble = goal.rsplit("\n", 1)[0]
    for name in ("Foundation", "OCTOPUS", "SiteQuiVend", "Agnes", "Hermes", "phone_call", "bpifrance.fr"):
        assert name not in preamble
    assert captured["state"]["origine"] == "octopus"  # Internal IDs are preserved.
    assert captured["state"]["expériences_antérieures"] == []
    assert captured["state"]["stratégies_enregistrées"] == []
    assert captured["state"]["écarts_de_capacités"] == []
    assert captured["kwargs"]["allowed_tools"] == supervisor.PURSUIT_TOOLS
    assert captured["kwargs"]["max_steps_per_agent"] == 6
    assert captured["kwargs"]["max_duration_s"] == 120
    assert captured["kwargs"]["profile"] == "economical"
    assert supervisor.PURSUIT_ROUNDS == 3
    assert supervisor.PURSUIT_LLM_BUDGET_USD == .20
    assert supervisor.PURSUIT_TOOLS == frozenset({"search", "browse", "resources_status", "economy_status",
        "browser_navigate", "browser_snapshot", "browser_scroll", "browser_back"})
    calls = {call["task"]: call["messages"] for call in captured["calls"]}
    planner = calls["planification"][0]["content"]
    assert "à exécuter maintenant" in planner
    assert "pas les marchés, offres ou stratégies" in planner
    assert "quoi observer ou analyser et pourquoi économiquement" in planner
    assert "sans preuve commerciale préalable" in planner
    assert calls["action"][1]["content"].startswith("Objectif : " + TASK)
    assert "MODE BUSINESS SIGNAL" in calls["action"][1]["content"]
    assert goal not in calls["action"][1]["content"]  # No new context propagation.
    synthesis = calls["determination"][0]["content"]
    assert "Continue si une observation gratuite permise" in synthesis
    assert "Pause si aucune exploration admissible et économiquement utile" in synthesis
    assert "Une proposition stratégique n'est pas une action externe" in synthesis
    for field in ("hypothesis", "strategies", "required_capabilities", "next_goal", "request_permission"):
        assert field in synthesis
    assert captured["result"]["determination"]["action"] == "continue"
    assert metrics(captured)["provider_calls"] == 0


@pytest.mark.parametrize("role", runtime.GENERIC_ROLES)
@pytest.mark.parametrize("conversational", [False, True])
def test_restricted_prompts_only_describe_exposed_tools(role, conversational):
    with journal.run("octopus", "agent"):
        prompt = runtime.build_prompts(role, "observer", conversational,
                                       set(supervisor.PURSUIT_TOOLS))[0]
    assert "OCTOPUS (business octopus)" not in prompt
    assert "groupe exploration économique" in prompt
    assert "bpifrance.fr" not in prompt
    for tool in set(runtime.TOOLS) - supervisor.PURSUIT_TOOLS:
        assert not re.search(r"\b" + re.escape(tool) + r"\b", prompt)
    for tool in supervisor.PURSUIT_TOOLS:
        assert "- " + tool + "(" in prompt


def test_record_observation_contract_is_present_when_exposed():
    with journal.run("octopus", "agent"):
        prompt = runtime.build_prompts("SOUT", "observer", allowed_tools={"record_observation"})[0]
    assert "- record_observation(" in prompt
    assert "CONTRAT record_observation" in prompt
    assert "NUMÉRIQUES" in prompt


def test_declared_business_name_survives_neutral_projection(monkeypatch):
    monkeypatch.setattr(businesses, "get", lambda business:
                        businesses.Business(id=business, name="Atelier humain"))
    with journal.run("octopus", "agent") as run:
        prompt = runtime.build_prompts("SOUT", "observer", allowed_tools={"search"})[0]
        assert run.business == "octopus"
    assert "Atelier humain" in prompt


def test_proposal_without_available_capability_remains_retained_but_blocked():
    inventory = supervisor.pursuit_capability_inventory()
    proposal = {"statement": "Offre nécessitant un entretien téléphonique",
                "economic_criteria": ["cash_received"], "economic_rank": 1,
                "required_capabilities": ["phone_call"]}
    assessment = separation.assess([proposal], inventory)
    assert assessment["retained"]["statement"] == proposal["statement"]
    assert assessment["retained"]["executability"] == "missing_capability"
    effects = []
    assert not separation.dispatch_if_authorized(assessment, effects.append)["triggered"]
    assert effects == []


@pytest.mark.parametrize("tool", ["record_observation", "publish", "send_message", "act_on_channel"])
def test_out_of_scope_tool_is_refused_before_any_effect(monkeypatch, tool):
    effects = []
    spec = dict(runtime.TOOLS[tool])
    spec["fn"] = lambda args: effects.append(args)
    monkeypatch.setitem(runtime.TOOLS, tool, spec)
    refusal, result = runtime.TOOLS.dispatch(tool, {}, set(supervisor.PURSUIT_TOOLS))
    assert refusal and result is None and effects == []
