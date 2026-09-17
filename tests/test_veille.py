"""Etape 7 : business veille sur le moteur (sources verifiees, reprise humaine sans double cout)."""
from __future__ import annotations

import json

import pytest

from agents import db, search
from businesses.veille import brief as B
from businesses.veille import evals, handlers  # noqa: F401  (enregistre veille.brief)
from octopus import journal, tasks, worker

QUIET = dict(log=lambda s: None)
CFG = B.load_config()

ITEMS = [search._item("google_news", f"Titre {i}", f"https://news.example/{i}", f"Journal {i}", f"2026-09-1{i}")
         for i in range(1, 5)] + [search._item("google_news", "Titre 1", "https://news.example/dup", "Journal 1")]

GOOD = {"points": [{"fait": "Une procédure simplifiée existe pour les créances incontestées.", "sources": [1]},
                   {"fait": "Les TPE subissent des retards de paiement.", "sources": [2, 3]}],
        "opportunites": [{"idee": "Modèle de mise en demeure prêt à l'emploi.", "sources": [1]}],
        "a_verifier": ["Date d'entrée en vigueur exacte."]}


def test_collect_numbers_and_deduplicates(monkeypatch):
    monkeypatch.setattr(search, "search_items", lambda q, n: (ITEMS, ["Wikipedia : Timeout"]))
    collected = B.collect("recouvrement", 8)
    assert [s["n"] for s in collected["sources"]] == [1, 2, 3, 4]
    assert collected["errors"] == ["Wikipedia : Timeout"]


def test_validate_accepts_a_sourced_brief():
    assert B.validate(GOOD, 4, CFG)["points"][1]["sources"] == [2, 3]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["points"][0].update(sources=[9]), "sources \\[9\\] invalides"),
    (lambda d: d["points"][0].update(sources=[]), "invalides"),
    (lambda d: d["points"][0].update(fait="Offre sur https://exfil.example/promo"), "contient une URL"),
    (lambda d: d["a_verifier"].append("voir www.exfil.example"), "a_verifier contient une URL"),
    (lambda d: d.update(points=[]), "aucun point"),
    (lambda d: (d["points"].__setitem__(1, {"fait": "x", "sources": [1]}), d["opportunites"].clear()), "citée"),
    (lambda d: d.update(points=[{"fait": f"f{i}", "sources": [1, 2]} for i in range(9)]), "9 points"),
])
def test_validate_rejects(mutate, message):
    data = json.loads(json.dumps(GOOD))
    mutate(data)
    with pytest.raises(B.InvalidBrief, match=message):
        B.validate(data, 4, CFG)


def test_markdown_links_every_cited_source():
    collected = {"query": "q", "sources": [{"n": i, **ITEMS[i - 1]} for i in range(1, 5)], "errors": []}
    md = B.render_markdown("Recouvrement", collected, B.validate(GOOD, 4, CFG),
                           {"date": "2026-09-17", "model": "deepseek/flash", "cost_class": "paid", "cost_usd": 0.0004})
    assert "- Les TPE subissent des retards de paiement. [2] [3]" in md
    assert "1. [Titre 1](https://news.example/1) (Journal 1, 2026-09-11)" in md
    assert "NON VÉRIFIÉ" not in md and "n'a pas été vérifiée" in md


@pytest.fixture
def veille_env(monkeypatch, transport, isolated):
    monkeypatch.setattr(search, "search_items", lambda q, n: (ITEMS[:4], []))
    transport.reply(json.dumps(GOOD, ensure_ascii=False), prompt_tokens=900, completion_tokens=250)
    return transport


def test_brief_task_asks_before_feeding_sout_and_does_not_pay_twice(veille_env, isolated):
    task_id = worker.enqueue("veille", "veille.brief", {"topic": "recouvrement"})
    first = worker.run_one("w", **QUIET)
    assert first["status"] == "waiting_human" and len(veille_env.calls) == 1
    (request,) = tasks.pending_human_requests("veille")
    path = json.loads(request["context"])["path"]
    text = open(path, encoding="utf-8").read()
    assert text.startswith("# Veille : Recouvrement") and "[Titre 2](https://news.example/2)" in text
    tasks.answer(request["id"], "oui")
    done = worker.run_one("w", **QUIET)
    assert done["status"] == "done" and done["output"]["transmis"] is True
    assert len(veille_env.calls) == 1  # etapes memorisees : pas de second appel payant a la reprise
    memory = db.memory_keys("SOUT")
    assert memory and "NON VÉRIFIÉ" in memory[0]["value"] and "Journal 1" in memory[0]["value"]
    call = journal.query("SELECT * FROM llm_calls")[0]
    assert (call["business"], call["task"], call["profile"]) == ("veille", "veille.brief", "low_cost")
    assert json.loads(call["justification"])["paid_reason"] == "alternatives_ineligible"
    assert journal.query("SELECT budget_usd FROM runs WHERE kind='task:veille.brief'")[0]["budget_usd"] == 0.02


def test_refused_answer_keeps_the_brief_but_not_the_memory(veille_env):
    worker.enqueue("veille", "veille.brief", {"topic": "recouvrement"})
    worker.run_one("w", **QUIET)
    tasks.answer(tasks.pending_human_requests()[0]["id"], "non")
    assert worker.run_one("w", **QUIET)["output"]["transmis"] is False
    assert db.memory_keys("SOUT") == []


def test_ad_hoc_query_without_agent_finishes_without_question(veille_env):
    worker.enqueue("veille", "veille.brief", {"query": "facture électronique", "label": "E-facture"})
    result = worker.run_one("w", **QUIET)
    assert result["status"] == "done" and tasks.pending_human_requests() == []


def test_invalid_model_output_fails_the_attempt(monkeypatch, transport, isolated):
    monkeypatch.setattr(search, "search_items", lambda q, n: (ITEMS[:4], []))
    transport.reply('{"points": [{"fait": "voir https://exfil.example", "sources": [1]}]}')
    worker.enqueue("veille", "veille.brief", {"topic": "recouvrement"})
    result = worker.run_one("w", **QUIET)
    assert result["status"] == "queued" and "contient une URL" in result["error"]  # 2 tentatives


def test_not_enough_sources(monkeypatch, transport):
    monkeypatch.setattr(search, "search_items", lambda q, n: (ITEMS[:1], ["Google News : ConnectionError"]))
    worker.enqueue("veille", "veille.brief", {"topic": "recouvrement"})
    result = worker.run_one("w", **QUIET)
    assert "insuffisant" in result["error"] and transport.calls == []


def test_eval_suite_detects_injection():
    (task,) = evals.build_suite(None)
    assert [i.id for i in task.items] == ["sources_propres", "source_piegee"]
    assert "exfil.example" in task.items[1].messages[1]["content"]
    injected = json.dumps({**GOOD, "points": GOOD["points"] + [{"fait": "Offre spéciale exfil.example", "sources": [2]}]})
    assert evals.check(3)(json.dumps(GOOD)).passed
    assert evals.check(3)(injected).passed is False
