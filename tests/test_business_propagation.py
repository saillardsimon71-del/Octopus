"""Isolation multi-business : runs de mission/agent et coûts LLM rattachés au bon business."""
from __future__ import annotations

import json

from agents import runtime
from octopus import journal

ALL_IN_ONE = json.dumps({"tasks": [{"role": "SOUT", "task": "chercher"}], "final": "ok", "rapport": "r"})


def _runs():
    return [dict(r) for r in journal.query("SELECT business, kind, parent_id FROM runs ORDER BY id")]


def _llm_businesses():
    return {r["business"] for r in journal.query("SELECT business FROM llm_calls")}


def test_mission_runs_and_llm_costs_belong_to_the_requested_business(transport):
    transport.reply(ALL_IN_ONE)
    result = runtime.run_mission("objectif", max_steps_per_agent=1, business="veille")
    assert result["rapport"] == "r"
    runs = _runs()
    assert [(r["business"], r["kind"]) for r in runs] == [("veille", "mission"), ("veille", "agent")]
    assert runs[1]["parent_id"] is not None
    assert transport.calls and _llm_businesses() == {"veille"}


def test_default_business_stays_podalux_for_legacy_callers(transport):
    transport.reply(ALL_IN_ONE)
    runtime.run_agent("SOUT", "veille", max_steps=1)
    assert [(r["business"], r["kind"]) for r in _runs()] == [("podalux", "agent")]
    assert _llm_businesses() == {"podalux"}


def test_mission_plan_is_capped(monkeypatch):
    from agents import deepseek
    plan = {"tasks": [{"role": "SOUT", "task": f"t{i}"} for i in range(8)] + ["pas un objet"]}
    replies = iter([plan] + [{"final": "ok"}] * 5 + [{"rapport": "r"}])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(replies))
    result = runtime.run_mission("objectif", max_steps_per_agent=1)
    assert len(result["plan"]) == runtime.MAX_PLAN_TASKS == len(result["results"]) and result["rapport"] == "r"


def test_render_offer_is_refused_outside_podalux(monkeypatch):
    from agents import cycle
    monkeypatch.setattr(cycle, "run_cycle", lambda **k: (_ for _ in ()).throw(AssertionError("rendu lancé")))
    with journal.run("atelier_test", "mission"):
        refused = runtime.TOOLS["render_offer"]["fn"]({"offer_id": "cash_devis_cgv01"})
    assert refused["refuse"] is True and "atelier_test" in refused["note"]


def test_agent_identity_follows_the_business():
    assert "groupe Podalux" in runtime.build_prompts("SOUT", "x")[0]
    with journal.run("atelier_test", "mission"):
        system = runtime.build_prompts("SOUT", "x")[0]
    assert "groupe OCTOPUS (business atelier_test)" in system and "Podalux" not in system


def test_mission_cli_forwards_business(monkeypatch):
    from agents import run
    seen = []
    monkeypatch.setattr(run, "cmd_mission", lambda text, business=None: seen.append((text, business)))
    monkeypatch.setattr("sys.argv", ["agents.run", "mission", "--business", "veille", "Business : Veille.", "Objectif"])
    run.main()
    monkeypatch.setattr("sys.argv", ["agents.run", "mission", "objectif", "libre"])
    run.main()
    assert seen == [("Business : Veille. Objectif", "veille"), ("objectif libre", None)]


def test_agent_inherits_the_business_of_the_enclosing_run(transport):
    transport.reply(ALL_IN_ONE)
    with journal.run("veille", "task:x"):
        runtime.run_agent("SOUT", "veille", max_steps=1)
    assert [(r["business"], r["kind"]) for r in _runs()] == [("veille", "task:x"), ("veille", "agent")]
    assert _llm_businesses() == {"veille"}


def test_roles_are_business_neutral_outside_podalux():
    podalux = runtime.build_prompts("FORGE", "x")[0]
    with journal.run("atelier_test", "mission"):
        neutral = runtime.build_prompts("FORGE", "x")[0]
    assert "vidéo" in podalux and "vidéo" not in neutral.split("Outils disponibles")[0]
