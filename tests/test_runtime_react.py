"""Audit M2 (boucle ReAct : historique et recherches repetees) et M9 (pannes de recherche visibles)."""
from __future__ import annotations

import json

import pytest

from agents import deepseek, runtime, search

REAL_QUERIES = [  # run ORBIT du 16/09, 14:07-14:10
    "meilleures pratiques relance factures impayées modèles email",
    "relance facture impayée meilleure pratique modèle email",
    "Meilleures pratiques relance factures impayées modèle email",
]


def test_equivalent_queries_share_a_key():
    assert len({runtime.query_key(q) for q in REAL_QUERIES}) == 1
    assert runtime.query_key("relance facture impayée") != runtime.query_key("injonction de payer")


@pytest.fixture
def web(monkeypatch):
    calls = []
    monkeypatch.setattr(search, "web_search", lambda q, n=6: calls.append(q) or f"- resultat pour {q}")
    return calls


def scripted(monkeypatch, actions):
    seen = []
    it = iter(actions + [{"final": "fin"}])

    def call_json(agent, task, model, messages, **kw):
        seen.append(messages)
        return next(it)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    return seen


def test_repeated_search_is_served_from_cache(monkeypatch, web):
    scripted(monkeypatch, [{"tool": "search", "args": {"query": q}} for q in REAL_QUERIES])
    result = runtime.run_agent("ORBIT", "veille", max_steps=5)
    assert web == [REAL_QUERIES[0]]
    assert "deja_cherche" in result["steps"][1]["result"] and "deja_cherche" in result["steps"][2]["result"]


def test_mission_subagents_share_the_search_cache(monkeypatch, web):
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "a"}, {"role": "ORBIT", "task": "b"}]},
        {"tool": "search", "args": {"query": REAL_QUERIES[0]}}, {"final": "1"},
        {"tool": "search", "args": {"query": REAL_QUERIES[1]}}, {"final": "2"},
        {"rapport": "r"},
    ])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))
    runtime.run_mission("objectif")
    assert web == [REAL_QUERIES[0]]


def test_new_run_has_a_fresh_cache(monkeypatch, web):
    scripted(monkeypatch, [{"tool": "search", "args": {"query": REAL_QUERIES[0]}}])
    runtime.run_agent("SOUT", "veille", max_steps=2)
    scripted(monkeypatch, [{"tool": "search", "args": {"query": REAL_QUERIES[0]}}])
    runtime.run_agent("SOUT", "veille", max_steps=2)
    assert len(web) == 2


def test_history_contains_the_actions(monkeypatch, web):
    seen = scripted(monkeypatch, [{"tool": "search", "args": {"query": "relance"}}])
    runtime.run_agent("SOUT", "veille", max_steps=3)
    second_call = seen[1]
    roles = [m["role"] for m in second_call]
    assert roles == ["system", "user", "assistant", "user", "user"]
    assert json.loads(second_call[2]["content"]) == {"tool": "search", "args": {"query": "relance"}}


def test_search_failures_are_reported(monkeypatch):
    def down(query, n):
        raise ConnectionError("proxy refuse")

    monkeypatch.setattr(search, "_gnews", down)
    monkeypatch.setattr(search, "_wikipedia", lambda q, n: "- Recouvrement de créances\n  procédure")
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    out = search.web_search("recouvrement")
    assert "Recouvrement de créances" in out and "Sources en erreur : Google News : ConnectionError" in out


def test_wikipedia_user_agent_is_identifiable():
    assert "Mozilla" not in search.WIKI_UA["User-Agent"] and "Podalux" in search.WIKI_UA["User-Agent"]
