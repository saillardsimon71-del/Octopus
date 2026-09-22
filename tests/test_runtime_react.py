"""Audit M2 (boucle ReAct : historique et recherches repetees) et M9 (pannes de recherche visibles)."""
from __future__ import annotations

import json

import pytest

from agents import deepseek, runtime, search
from octopus import journal, llm

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


def test_tool_gate_blocks_disallowed_tool_before_execution(monkeypatch):
    called, posts = [], []
    monkeypatch.setitem(runtime.TOOLS["ask_human"], "fn", lambda args: called.append(args) or "should not run")
    monkeypatch.setattr(runtime.db, "post", lambda agent, content, *a, **k: posts.append(content))
    scripted(monkeypatch, [
        {"tool": "ask_human", "args": {"question": "cash ?"}},
        {"final": "continue sans humain"},
    ])

    result = runtime.run_agent("ORBIT", "collecte", max_steps=3, allowed_tools={"search"})

    assert called == []
    assert '"refused": true' in result["steps"][0]["result"]
    assert "interdit par la politique" in result["steps"][0]["result"]
    assert any("refus outil ask_human" in message for message in posts)
    assert not any(message.startswith("action ask_human") for message in posts)


def test_tool_gate_rejects_missing_required_argument(monkeypatch):
    monkeypatch.setitem(runtime.TOOLS["recall"], "fn", lambda args: pytest.fail("recall ne doit pas être exécuté"))
    scripted(monkeypatch, [
        {"tool": "recall", "args": {}},
        {"final": "continue"},
    ])

    result = runtime.run_agent("GROWTH", "relire", max_steps=3, allowed_tools={"recall"})

    assert "argument obligatoire manquant : key" in result["steps"][0]["result"]


def test_tool_gate_rejects_wrong_argument_type(monkeypatch):
    monkeypatch.setitem(runtime.TOOLS["remember"], "fn", lambda args: pytest.fail("remember ne doit pas être exécuté"))
    scripted(monkeypatch, [
        {"tool": "remember", "args": {"key": "evidence_records", "value": [{"id": 1}]}},
        {"final": "continue"},
    ])

    result = runtime.run_agent("LEDGER", "mémoriser", max_steps=3, allowed_tools={"remember"})

    assert "type attendu str, reçu list" in result["steps"][0]["result"]


def test_mission_profile_is_inherited_by_all_llm_calls(monkeypatch):
    profiles = []
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "collecter"}]},
        {"final": "preuve"},
        {"rapport": "rapport"},
    ])

    def call_json(*args, **kwargs):
        current = journal.current_run()
        profiles.append(current.profile if current else None)
        return next(actions)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    result = runtime.run_mission("collecte", max_steps_per_agent=2, profile="flash_fallback")

    assert result["synthesis_status"] == "validated"
    assert profiles == ["flash_fallback", "flash_fallback", "flash_fallback"]


def test_mission_propagates_tool_allowlist_to_subagents(monkeypatch):
    called = []
    monkeypatch.setitem(runtime.TOOLS["ask_human"], "fn", lambda args: called.append(args) or "should not run")
    actions = iter([
        {"tasks": [{"role": "ORBIT", "task": "collecter sans humain"}]},
        {"tool": "ask_human", "args": {"question": "cash ?"}},
        {"final": "cash inconnu"},
        {"rapport": "aucun appel humain exécuté"},
    ])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    result = runtime.run_mission("collecte", max_steps_per_agent=3, allowed_tools={"search"})

    assert called == []
    assert "interdit par la politique" in result["results"][0]["steps"][0]["result"]
    assert result["synthesis_status"] == "validated"


def test_tool_gate_allows_declared_tool(monkeypatch, web):
    scripted(monkeypatch, [
        {"tool": "search", "args": {"query": "retards de paiement PME"}},
        {"final": "ok"},
    ])

    result = runtime.run_agent("SOUT", "chercher", max_steps=3, allowed_tools={"search"})

    assert web == ["retards de paiement PME"]
    assert result["steps"][0]["tool"] == "search"


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

    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: [])
    monkeypatch.setattr(search, "_bing_web_items", lambda q, n: [])
    monkeypatch.setattr(search, "_gnews_items", down)
    monkeypatch.setattr(search, "_wikipedia_items", lambda q, n: [search._item(
        "wikipedia", "Recouvrement de créances", "https://fr.wikipedia.org/wiki/Recouvrement", "Wikipédia", "", "procédure")])
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    out = search.web_search("recouvrement")
    assert "Recouvrement de créances" in out and "Sources en erreur : Google News : ConnectionError" in out


def test_bing_news_unwraps_publisher_url(monkeypatch):
    rss = """<rss><channel><item>
    <title>Facturation électronique des PME</title>
    <link>https://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3A%2F%2Fexample.com%2Farticle&amp;mkt=fr-fr</link>
    <description>Une preuve exploitable.</description>
    <pubDate>Tue, 22 Sep 2026 08:00:00 GMT</pubDate>
    <News:Source>Exemple</News:Source>
    </item></channel></rss>"""

    class Response:
        text = rss
        def raise_for_status(self):
            return None

    monkeypatch.setattr(search.requests, "get", lambda *a, **k: Response())
    items = search._bing_news_items("facturation électronique", 6)

    assert len(items) == 1
    assert items[0]["provider"] == "bing_news"
    assert items[0]["url"] == "https://example.com/article"
    assert items[0]["source"] == "Exemple"
    assert items[0]["date"] == "Tue, 22 Sep 2026 08:00:00 GMT"


def test_keyless_search_prefers_bing_direct_results(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: [
        search._item("bing_news", "Titre", "https://example.com/article", "Exemple", "2026-09-22", "preuve")
    ])
    monkeypatch.setattr(search, "_bing_web_items", lambda *a, **k: pytest.fail("Bing Web ne doit pas être consulté"))
    monkeypatch.setattr(search, "_gnews_items", lambda *a, **k: pytest.fail("Google News ne doit pas être consulté"))
    monkeypatch.setattr(search, "_wikipedia_items", lambda *a, **k: pytest.fail("Wikipedia ne doit pas être consulté"))

    out = search.web_search("besoin PME")

    assert "https://example.com/article" in out
    assert "Exemple" in out


def test_bing_web_rss_keeps_only_direct_external_urls(monkeypatch):
    rss = """<rss><channel>
    <item>
      <title>Retards de paiement des PME</title>
      <link>https://example.com/retards-paiement</link>
      <description>Données sur les délais de paiement.</description>
      <pubDate>Tue, 22 Sep 2026 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Wrapper Bing</title>
      <link>https://www.bing.com/ck/a?x=1</link>
      <description>À ignorer.</description>
    </item>
    </channel></rss>"""

    class Response:
        text = rss
        def raise_for_status(self):
            return None

    monkeypatch.setattr(search.requests, "get", lambda *a, **k: Response())
    items = search._bing_web_items("retards paiement PME", 6)

    assert len(items) == 1
    assert items[0]["provider"] == "bing_web"
    assert items[0]["url"] == "https://example.com/retards-paiement"


def test_keyless_search_uses_bing_web_before_google_wrappers(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: [])
    monkeypatch.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Titre", "https://example.com/direct", "Bing Web", "", "preuve")
    ])
    monkeypatch.setattr(search, "_gnews_items", lambda *a, **k: pytest.fail("Google News ne doit pas être consulté"))
    monkeypatch.setattr(search, "_wikipedia_items", lambda *a, **k: pytest.fail("Wikipedia ne doit pas être consulté"))

    out = search.web_search("besoin PME")

    assert "https://example.com/direct" in out


def test_google_news_wrappers_are_hints_not_browsable_urls():
    out = search.format_items([
        search._item(
            "google_news",
            "PME et facturation électronique",
            "https://news.google.com/rss/articles/example",
            "Exemple",
            "Tue, 22 Sep 2026 08:00:00 GMT",
        ),
        search._item(
            "wikipedia",
            "Micro-entrepreneur",
            "https://fr.wikipedia.org/wiki/Micro-entrepreneur",
            "Wikipédia",
            snippet="Régime français.",
        ),
    ])

    assert "https://news.google.com/rss/articles/example" not in out
    assert "wrapper non exploitable par browse" in out
    assert "PME et facturation électronique" in out
    assert "Exemple" in out
    assert "https://fr.wikipedia.org/wiki/Micro-entrepreneur" in out


def test_wikipedia_user_agent_is_identifiable():
    assert "Mozilla" not in search.WIKI_UA["User-Agent"] and "Podalux" in search.WIKI_UA["User-Agent"]


def test_mission_synthesis_receives_original_goal_constraints(monkeypatch):
    calls = []
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "collecter"}]},
        {"final": "aucune preuve suffisante"},
        {"rapport": "aucune recommandation"},
    ])

    def call_json(agent, task, model, messages, **kwargs):
        calls.append((task, messages))
        return next(actions)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    goal = "COLLECTE UNIQUEMENT. Ne recommande RIEN. Ne propose aucune action."
    result = runtime.run_mission(goal, max_steps_per_agent=2)

    assert result["synthesis_status"] == "validated"
    synthesis_messages = next(messages for task, messages in calls if task == "synthese")
    payload = json.loads(synthesis_messages[1]["content"])
    assert payload["objectif_original"] == goal
    assert payload["resultats_sous_taches"][0]["final"] == "aucune preuve suffisante"
    assert "Respecte aussi toutes les contraintes de l'objectif original" in synthesis_messages[0]["content"]


def test_mission_keeps_subagent_results_when_synthesis_gateway_fails(monkeypatch):
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "observer le marché"}]},
        {"final": "résultat sous-agent conservé"},
    ])

    def call_json(agent, task, model, messages, **kwargs):
        if task == "synthese":
            raise llm.NoEligibleModel("agent.synthesize", "zero_cost", [
                {"model": "omniroute/devworker-groq", "reason": "structured output failed"},
                {"model": "kilo/ling-3.0-flash-vl-free", "reason": "structured output failed"},
            ])
        return next(actions)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    result = runtime.run_mission("objectif économique", max_steps_per_agent=2)

    assert result["synthesis_status"] == "degraded"
    assert result["results"][0]["final"] == "résultat sous-agent conservé"
    assert "aucune synthèse factuelle validée" in result["rapport"]
    assert "NoEligibleModel" in result["synthesis_error"]


def test_mission_does_not_hide_non_gateway_synthesis_bug(monkeypatch):
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "observer"}]},
        {"final": "résultat"},
    ])

    def call_json(agent, task, model, messages, **kwargs):
        if task == "synthese":
            raise RuntimeError("bug de code synthèse")
        return next(actions)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    with pytest.raises(RuntimeError, match="bug de code synthèse"):
        runtime.run_mission("objectif", max_steps_per_agent=2)
