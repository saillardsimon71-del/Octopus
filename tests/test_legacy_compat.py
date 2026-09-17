"""Non-regression : requetes et prompts identiques au code d'avant OCTOPUS (commit 4f36417).

Les fixtures ont ete capturees sur le code historique. Avec le profil `legacy` (defaut),
la passerelle doit envoyer exactement les memes requetes et journaliser leur cout.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents import agents as ag
from agents import config, db, deepseek, runtime
from octopus import journal

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "legacy_prompts.json").read_text(encoding="utf-8"))
FAKE_JPEG = b"\xff\xd8\xff\xe0jpegfake"


def _frame(tmp_path) -> str:
    path = tmp_path / "frame.jpg"
    path.write_bytes(FAKE_JPEG)
    return str(path)


REQUEST_CASES = {
    "flash_json": lambda f: deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH,
                                               [{"role": "user", "content": "x"}]),
    "pro_reasoning_json": lambda f: deepseek.call_json("ORBIT", "arbitrage", config.MODEL_PRO,
                                                       [{"role": "user", "content": "x"}], reasoning="high"),
    "flash_text": lambda f: deepseek.call("SOUT", "veille_synthese", config.MODEL_FLASH,
                                          [{"role": "user", "content": "x"}], max_tokens=300),
    "vision_json": lambda f: deepseek.vision("GROWTH", "qc_vision", [f], "narration", "PROMPT VISION"),
    "vision_text": lambda f: deepseek.vision_text("SOUT", "voir_page", [f], "Décris"),
}


@pytest.mark.parametrize("name", sorted(REQUEST_CASES))
def test_gateway_sends_the_historical_request(name, transport, tmp_path):
    transport.reply('{"decision": "done"}', prompt_tokens=1000, completion_tokens=100, cache_hit_tokens=800)
    REQUEST_CASES[name](_frame(tmp_path))
    assert len(transport.calls) == 1
    provider, request = transport.calls[0]
    assert request == FIXTURE["requests"][name]
    assert provider["base_url"] == config.BASE_URL


def test_each_call_is_journaled_with_official_cost_and_legacy_table_kept(transport):
    transport.reply('{"a": 1}', prompt_tokens=1000, completion_tokens=100, cache_hit_tokens=800)
    deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, [{"role": "user", "content": "x"}])
    row = journal.query("SELECT * FROM llm_calls")[0]
    expected = (800 * 0.003 + 200 * 0.15 + 100 * 0.60) / 1_000_000
    assert row["task"] == "podalux.write_job" and row["agent"] == "CONVERT"
    assert row["model"] == "deepseek/flash" and row["status"] == "ok" and row["profile"] == "legacy"
    assert row["cost_usd"] == pytest.approx(expected)
    assert (row["cache_hit_tokens"], row["prompt_tokens"], row["completion_tokens"]) == (800, 1000, 100)
    assert json.loads(row["justification"])["paid_reason"] == "legacy_pin"
    legacy = db._conn().execute("SELECT model, cost_usd FROM costs").fetchall()
    assert [(r["model"], r["cost_usd"]) for r in legacy] == [(config.MODEL_FLASH, pytest.approx(expected, abs=1e-6))]  # arrondi a 6 decimales dans la table historique


# --- prompts des agents ---------------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    def call_json(agent, task, model, messages, max_tokens=2000, reasoning=None):
        calls.append({"agent": agent, "task": task, "model": model, "messages": messages,
                      "max_tokens": max_tokens, "reasoning": reasoning})
        return {"offer_id": "cash_devis_cgv01", "angle": "a", "decision": "done", "final": "ok",
                "titre": "t", "hook": "h", "cta": "37 €, lien en description", "keywords": ["devis"],
                "narration": [{"role": r, "texte": "texte"} for r in ag.ROLES_ORDER]}

    def call(agent, task, model, messages, max_tokens=1200, reasoning=None, json_mode=False):
        calls.append({"agent": agent, "task": task, "model": model, "messages": messages, "max_tokens": max_tokens})
        return "synthese"

    monkeypatch.setattr(deepseek, "call_json", call_json)
    monkeypatch.setattr(deepseek, "call", call)
    return calls


def _same(captured_call: dict, fixture: dict) -> None:
    assert {k: captured_call[k] for k in fixture} == fixture


def test_sout_selection_prompt(captured):
    ag.SOUT.run(["cash_impayes_relance01"])
    _same(captured[0], FIXTURE["prompts"]["sout_select"])


def test_convert_prompts(captured):
    ag.CONVERT.run("cash_devis_cgv01", "passer pour un pro")
    ag.CONVERT.run("cash_impayes_relance01", "le cash qui ne rentre pas",
                   fixes=["Ajouter un léger fondu au noir entre les scènes.", "Intégrer le prix dès la première mention."])
    _same(captured[0], FIXTURE["prompts"]["convert_cash_devis_cgv01_nofix"])
    _same(captured[1], FIXTURE["prompts"]["convert_cash_impayes_relance01_fixes"])


def test_growth_prompt(monkeypatch):
    seen = {}

    def vision(agent, task, frames, narration, prompt):
        seen["prompt"] = prompt
        return {k: 1 for k in ag.AXES} | {"humanite": 3}

    monkeypatch.setattr(deepseek, "vision", vision)
    job = {"narration": [{"texte": "Facture impayée ?"}, {"texte": "9 €, lien en description."}]}
    ag.GROWTH.run("cash_impayes_relance01", job, {"frames": ["a.jpg", "b.jpg"]})
    assert seen["prompt"] == FIXTURE["prompts"]["growth_prompt"]


def test_orbit_prompt_with_human_message(captured, monkeypatch):
    monkeypatch.setattr(config, "ORBIT_DECISION", "llm")  # arbitrage LLM historique, toujours disponible
    db.post("HUMAN", "vise 30/35")
    ag.ORBIT.run("cash_impayes_relance01", {"score": 27, "humanite": 4, "warm_pass": True, "cost_usd": 0.01, "go": True})
    _same(captured[0], FIXTURE["prompts"]["orbit_with_human"])


def test_sout_research_prompt(captured, monkeypatch):
    import agents.search
    monkeypatch.setattr(agents.search, "web_search", lambda query, n=5: "- resultat 1\n- resultat 2")
    ag.SOUT.research("factures impayees")
    _same(captured[0], FIXTURE["prompts"]["sout_research"])


def test_runtime_prompts(captured):
    runtime.run_agent("SOUT", "fais une veille", max_steps=1)
    runtime.run_agent("ORBIT", "bonjour", max_steps=1, conversational=True)
    _same(captured[0], FIXTURE["prompts"]["runtime_goal"])
    _same(captured[1], FIXTURE["prompts"]["runtime_conversational"])
