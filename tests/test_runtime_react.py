"""Audit M2 (boucle ReAct : historique et recherches repetees) et M9 (pannes de recherche visibles)."""
from __future__ import annotations

import json

import pytest

from agents import deepseek, runtime, search
from octopus import catalog, journal, llm

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
    monkeypatch.setattr(
        search,
        "web_search",
        lambda q, n=6, site=None: calls.append(q if site is None else f"{q} site:{site}") or f"- resultat pour {q}",
    )
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


def test_lockstep_forces_browse_of_first_returned_search_url(monkeypatch):
    seen_browse = []
    monkeypatch.setitem(
        runtime.TOOLS["search"],
        "fn",
        lambda args: (
            "Source A\nhttps://example.com/preuve-a\n"
            "Source B\nhttps://example.com/preuve-b"
        ),
    )
    monkeypatch.setitem(
        runtime.TOOLS["browse"],
        "fn",
        lambda args: seen_browse.append(args["url"]) or {
            "url": args["url"],
            "texte": "preuve exploitable " + ("x" * 200),
        },
    )
    calls = scripted(monkeypatch, [
        {"tool": "search", "args": {"query": "preuve PME France"}},
    ])

    result = runtime.run_agent(
        "SOUT",
        "collecter",
        max_steps=3,
        allowed_tools={"search", "browse"},
        search_browse_lockstep=True,
    )

    assert seen_browse == ["https://example.com/preuve-a"]
    assert [step["tool"] for step in result["steps"]] == ["search", "browse"]
    assert result["steps"][1]["args"]["url"] == "https://example.com/preuve-a"
    assert result["steps"][1]["lockstep_forced"] is True
    # Le browse imposé ne consomme pas un appel LLM de sélection d'action.
    assert len(calls) == 2


def test_evidence_selector_prefers_relevant_official_result_over_dictionary():
    result = (
        "- Définitions : problème - Dictionnaire Larousse\n"
        "  https://www.larousse.fr/dictionnaires/francais/probleme/64046\n"
        "  Définition générale du mot problème.\n"
        "- Délais de paiement des PME - INSEE\n"
        "  https://www.insee.fr/fr/statistiques/1234567\n"
        "  Statistiques sur les délais de paiement des PME et les créances clients.\n"
    )

    choice = runtime._select_search_browse_candidate(
        result,
        "délais de paiement PME France statistiques officielles",
        selector="evidence_relevance",
    )

    assert choice["url"] == "https://www.insee.fr/fr/statistiques/1234567"
    assert choice["rank"] == 2
    assert choice["official"] is True
    assert choice["low_evidence"] is False


def test_evidence_selector_respects_explicit_site_constraint():
    result = (
        "- Français — Wikipédia\n"
        "  https://fr.wikipedia.org/wiki/Fran%C3%A7ais\n"
        "  Article encyclopédique.\n"
    )

    choice = runtime._select_search_browse_candidate(
        result,
        "site:reddit.com difficulté freelance français clients",
        selector="evidence_relevance",
    )

    assert choice is None


def test_lockstep_can_force_later_relevant_result(monkeypatch):
    seen_browse = []
    monkeypatch.setitem(
        runtime.TOOLS["search"],
        "fn",
        lambda args: (
            "- Définitions : problème - Larousse\n"
            "  https://www.larousse.fr/dictionnaires/francais/probleme/64046\n"
            "  Définition générale.\n"
            "- Retards de paiement PME - Banque de France\n"
            "  https://www.banque-france.fr/fr/publications-et-statistiques/retards-paiement\n"
            "  Retards de paiement, trésorerie et PME.\n"
        ),
    )
    monkeypatch.setitem(
        runtime.TOOLS["browse"],
        "fn",
        lambda args: seen_browse.append(args["url"]) or {
            "url": args["url"],
            "texte": "preuve exploitable " + ("x" * 200),
        },
    )
    scripted(monkeypatch, [
        {"tool": "search", "args": {"query": "retards de paiement PME trésorerie France"}},
    ])

    result = runtime.run_agent(
        "SOUT",
        "collecter",
        max_steps=3,
        allowed_tools={"search", "browse"},
        search_browse_lockstep=True,
        search_browse_selector="evidence_relevance",
    )

    assert seen_browse == [
        "https://www.banque-france.fr/fr/publications-et-statistiques/retards-paiement"
    ]
    browse_step = result["steps"][1]
    assert browse_step["lockstep_forced"] is True
    assert browse_step["lockstep_selection"]["selector"] == "evidence_relevance"
    assert browse_step["lockstep_selection"]["rank"] == 2


def test_lockstep_does_not_invent_browse_when_search_returns_no_url(monkeypatch):
    monkeypatch.setitem(runtime.TOOLS["search"], "fn", lambda args: "aucun résultat exploitable")
    scripted(monkeypatch, [
        {"tool": "search", "args": {"query": "preuve absente"}},
        {"final": "aucune source"},
    ])

    result = runtime.run_agent(
        "SOUT",
        "collecter",
        max_steps=3,
        allowed_tools={"search", "browse"},
        search_browse_lockstep=True,
    )

    assert [step["tool"] for step in result["steps"]] == ["search"]
    assert "lockstep_forced" not in result["steps"][0]


def test_lockstep_search_failure_does_not_reuse_stale_result(monkeypatch):
    def fail(args):
        raise RuntimeError("moteur indisponible")

    monkeypatch.setitem(runtime.TOOLS["search"], "fn", fail)
    scripted(monkeypatch, [
        {"tool": "search", "args": {"query": "preuve"}},
        {"final": "échec explicite"},
    ])

    result = runtime.run_agent(
        "SOUT",
        "collecter",
        max_steps=3,
        allowed_tools={"search", "browse"},
        search_browse_lockstep=True,
    )

    assert result["steps"][0]["tool"] == "search"
    assert "moteur indisponible" in result["steps"][0]["result"]
    assert result["steps"][0]["result_urls"] == []
    assert all(step["tool"] != "browse" for step in result["steps"])


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
    assert "args" not in result["steps"][0]


def test_record_observation_ids_are_numeric_optional_database_ids():
    invalid = runtime._validate_tool_args(
        "record_observation",
        {
            "summary": "Contexte",
            "observation": "Fait",
            "experiment_id": "ORBIT",
            "channel_id": "FORGE",
        },
    )
    assert "argument experiment_id : type attendu int, reçu str" in invalid

    valid = runtime._validate_tool_args(
        "record_observation",
        {"summary": "Contexte", "observation": "Fait"},
    )
    assert valid is None

    with journal.run("octopus", "agent"):
        system, _, _ = runtime.build_prompts("SOUT", "collecter une preuve")
    assert "CONTRAT record_observation" in system
    assert "identifiants NUMÉRIQUES" in system
    assert "S'ils sont inconnus, omets ces champs" in system


def test_generic_agent_prompt_includes_current_date_and_search_freshness(monkeypatch):
    monkeypatch.setattr(runtime, "_today_iso", lambda: "2026-09-24")

    with journal.run("octopus", "agent"):
        system, _, _ = runtime.build_prompts(
            "SOUT",
            "collecter des preuves actuelles",
            allowed_tools={"search", "browse"},
        )

    assert "DATE ACTUELLE : 2026-09-24" in system
    assert "privilégie 2026" in system
    assert "n'utilise pas une année antérieure comme substitut implicite du présent" in system
    assert 'site="insee.fr"' in system


def test_legacy_podalux_prompt_does_not_change_with_search_freshness(monkeypatch):
    monkeypatch.setattr(runtime, "_today_iso", lambda: "2026-09-24")

    with journal.run("podalux", "agent"):
        system, _, _ = runtime.build_prompts("SOUT", "veille")

    assert "DATE ACTUELLE" not in system


def test_agent_profiles_have_omniroute_auto_free_fallback(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    cat = catalog.load()

    for task_name in ("agent.react_step", "agent.plan", "agent.synthesize"):
        task = cat.task(task_name)
        for profile_name in ("zero_cost", "low_cost", "flash_fallback"):
            assert task["candidates"][profile_name][:2] == [
                "omniroute/devworker-groq",
                "omniroute/auto-free",
            ]

    assert cat.model("kilo/auto-free")["api_model"] == "kilo-auto/free"
    assert cat.model("kilo/ling-3.0-flash-vl-free") is None
    assert cat.task("web.inspect_page")["needs"] == []


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


def test_mission_planner_receives_generic_role_contracts(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "_today_iso", lambda: "2026-09-24")
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "collecter une preuve"}]},
        {"final": "preuve"},
        {"rapport": "rapport"},
    ])

    def call_json(agent, task, model, messages, **kwargs):
        calls.append((task, messages))
        return next(actions)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    result = runtime.run_mission(
        "collecter des preuves économiques",
        max_steps_per_agent=5,
        business="octopus",
    )

    assert result["synthesis_status"] == "validated"
    planner_messages = next(messages for task, messages in calls if task == "planification")
    system = planner_messages[0]["content"]
    for role, description in runtime.GENERIC_ROLES.items():
        assert f"- {role}: {description}" in system
    assert "workers interchangeables" in system
    assert "Un même rôle peut recevoir plusieurs sous-tâches distinctes" in system
    assert "au plus 5 étapes" in system
    assert "artefacts utiles des étapes amont" in system
    assert "transmis automatiquement" in system
    assert "DATE ACTUELLE : 2026-09-24" in system
    assert "privilégie 2026" in system
    assert runtime.ROLES["FORGE"] not in system



def test_business_signal_contract_uses_progressive_search_and_realistic_target():
    contract = runtime._business_signal_contract(3)

    assert "cherche d'abord des URL candidates" in contract
    assert "n'exige PAS que le mot 'budget' apparaisse dans search" in contract
    assert "n'ajoute pas l'année courante par défaut" in contract
    assert "utilise site= en deuxième intention" in contract
    assert "zéro URL exploitable ou seulement une homepage générique" in contract
    assert "SEARCH découvre ; BROWSE vérifie ; le gate qualifie" in contract
    assert "objectif MINIMAL DE MISSION" in contract
    assert "demander 10 signaux quand la mission en demande 3" in contract
    assert "concentre la découverte web chez SOUT" in contract


def test_business_signal_task_context_separates_discovery_from_downstream_analysis():
    sout = runtime._business_signal_task_context(3, "SOUT")
    convert = runtime._business_signal_task_context(3, "CONVERT")

    assert "TON RÔLE ICI : découverte" in sout
    assert "Vise le seuil de mission" in sout
    assert "TON RÔLE ICI (CONVERT) : exploitation des preuves amont" in convert
    assert "Commence par les artefacts transmis" in convert
    assert "ne relance search que si un champ de preuve précis manque" in convert

def test_business_signal_gate_rejects_generic_and_unopened_candidates():
    opened = "https://example.com/brief"
    results = [{
        "role": "SOUT",
        "steps": [{
            "tool": "browse",
            "args": {"url": opened},
            "browse_meta": {
                "url": opened,
                "text_chars": 900,
                "blocked": False,
                "error": None,
            },
        }],
    }]
    strong = {
        "signal_type": "explicit_request",
        "buyer": "cabinet comptable de 5 à 20 salariés",
        "pain": "relances clients effectuées manuellement chaque semaine",
        "money_signal": "la source demande un prestataire et mentionne un budget mensuel",
        "evidence_url": opened,
        "evidence_summary": "demande explicite d'aide pour automatiser les relances",
        "test_channel": "réponse directe à la demande publiée",
        "test_offer": "audit + automatisation simple des relances",
        "next_test": "contacter 5 cabinets avec la même douleur",
    }
    generic = {
        **strong,
        "signal_type": "macro_trend",
        "evidence_url": opened,
        "buyer": "entreprises françaises",
        "pain": "inflation générale",
    }
    unopened = {
        **strong,
        "evidence_url": "https://example.com/non-ouvert",
    }

    accepted, rejected = runtime._qualify_business_signals(
        [strong, generic, unopened],
        results,
    )

    assert accepted == [{**strong, "action_fields_nature": "inferred"}]
    reasons = [reason for item in rejected for reason in item["reasons"]]
    assert "unsupported_signal_type" in reasons
    assert "evidence_url_not_opened" in reasons


def test_business_signal_url_normalization_accepts_requested_and_redirected_variants():
    requested = "https://www.example.com/offre/?utm_source=newsletter#section"
    final = "https://example.com/offre"
    results = [{
        "role": "SOUT",
        "steps": [{
            "tool": "browse",
            "args": {"url": requested},
            "browse_meta": {
                "url": final,
                "text_chars": 500,
                "blocked": False,
                "error": None,
            },
        }],
    }]
    raw = {
        "signal_type": "explicit_request",
        "buyer": "PME bâtiment",
        "pain": "relances devis manuelles",
        "money_signal": "demande de prestataire",
        "evidence_url": "https://example.com/offre?utm_campaign=test",
        "evidence_summary": "demande explicite",
        "test_channel": "contact direct",
        "test_offer": "automatisation relances",
        "next_test": "contacter 3 entreprises",
    }

    accepted, rejected = runtime._qualify_business_signals([raw], results)

    assert rejected == []
    assert accepted[0]["evidence_url"] == raw["evidence_url"]
    assert accepted[0]["action_fields_nature"] == "inferred"


def test_business_signal_url_normalization_keeps_meaningful_query_parameters():
    assert runtime._canonical_evidence_url(
        "https://example.com/offre?id=42&utm_source=x#details"
    ) == "https://example.com/offre?id=42"
    assert runtime._canonical_evidence_url(
        "https://example.com/offre?id=43"
    ) != runtime._canonical_evidence_url(
        "https://example.com/offre?id=42"
    )


def test_business_signal_focus_reaches_planner_agent_and_synthesis(monkeypatch):
    calls = []
    source_url = "https://example.com/mission-freelance"
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "trouver une demande achetable"}]},
        {"tool": "browse", "args": {"url": source_url}},
        {"final": "demande lue"},
        {
            "rapport": "Un signal qualifié.",
            "business_signals": [{
                "signal_type": "procurement",
                "buyer": "PME de services",
                "pain": "traitement manuel de factures",
                "money_signal": "mission publiée avec budget pour un prestataire",
                "evidence_url": source_url,
                "evidence_summary": "la publication cherche un prestataire pour automatiser le traitement",
                "test_channel": "répondre à la mission publiée",
                "test_offer": "prototype d'automatisation du traitement de factures",
                "next_test": "répondre manuellement à 3 missions similaires",
            }],
        },
    ])

    monkeypatch.setitem(
        runtime.TOOLS["browse"],
        "fn",
        lambda args: {
            "url": args["url"],
            "source": "web public non fiable",
            "texte": "Mission : recherche prestataire automatisation factures. Budget mensuel prévu. " * 8,
            "page": {
                "final_url": args["url"],
                "title": "Mission automatisation factures",
                "fetched_at": "2026-09-24T00:00:00+00:00",
                "http_status": 200,
                "content_type": "text/html",
                "extraction_method": "http:html_main",
                "rendered": False,
                "blocked": False,
                "main_text": "Mission : recherche prestataire automatisation factures. Budget mensuel prévu. " * 8,
                "text_chars": 640,
                "raw_chars": 900,
                "truncated": False,
                "error": None,
            },
        },
    )

    def call_json(agent, task, model, messages, **kwargs):
        calls.append((agent, task, messages))
        return next(actions)

    monkeypatch.setattr(deepseek, "call_json", call_json)

    result = runtime.run_mission(
        "identifier une opportunité vendable",
        max_steps_per_agent=3,
        business="octopus",
        allowed_tools={"browse"},
        business_signal_focus=True,
        business_signal_target=1,
    )

    assert result["synthesis_status"] == "validated"
    assert len(result["business_signals"]) == 1
    assert result["business_signals"][0]["buyer"] == "PME de services"
    assert result["business_signals"][0]["action_fields_nature"] == "inferred"
    assert result["business_signal_rejections"] == []

    planner = next(messages for _, task, messages in calls if task == "planification")
    assert "MODE BUSINESS SIGNAL" in planner[0]["content"]
    assert "À REJETER" in planner[0]["content"]

    action_messages = next(messages for _, task, messages in calls if task == "action")
    assert "MODE BUSINESS SIGNAL" in action_messages[1]["content"]
    assert "acheteur/segment identifiable" in action_messages[1]["content"]

    synthesis = next(messages for _, task, messages in calls if task == "synthese")
    assert "business_signals" in synthesis[0]["content"]
    assert "Les généralités macro" in synthesis[0]["content"]


def test_mission_handoff_keeps_upstream_artifacts_when_agent_hits_max_steps(monkeypatch):
    calls = []
    actions = iter([
        {
            "tasks": [
                {"role": "SOUT", "task": "trouver une source"},
                {"role": "CONVERT", "task": "exploiter la source trouvée"},
            ]
        },
        {"tool": "search", "args": {"query": "preuve PME France"}},
        {"final": "source réutilisée"},
        {"rapport": "rapport"},
    ])

    monkeypatch.setitem(
        runtime.TOOLS["search"],
        "fn",
        lambda args: "Résultat utile\nhttps://example.com/preuve-pme",
    )

    def call_json(agent, task, model, messages, **kwargs):
        calls.append((agent, task, messages))
        return next(actions)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    result = runtime.run_mission(
        "collecter puis exploiter une preuve",
        max_steps_per_agent=1,
        business="octopus",
    )

    assert result["results"][0]["final"] == "(max steps atteint)"
    downstream = next(
        messages
        for agent, task, messages in calls
        if agent == "CONVERT" and task == "action"
    )
    objective = downstream[1]["content"]
    assert "Contexte structuré des sous-tâches précédentes" in objective
    assert '"role": "SOUT"' in objective
    assert '"tool": "search"' in objective
    assert "https://example.com/preuve-pme" in objective
    assert "(max steps atteint)" in objective


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


def test_keyless_search_is_stable_web_then_news(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    calls = []
    monkeypatch.setattr(search, "_bing_web_items", lambda q, n: calls.append("web") or [
        search._item("bing_web", "Web", "https://example.com/web", "Bing Web", "", "preuve web")
    ])
    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: calls.append("news") or [
        search._item("bing_news", "News", "https://example.com/news", "Exemple", "2026-09-22", "preuve news")
    ])
    monkeypatch.setattr(search, "_gnews_items", lambda q, n: [])
    monkeypatch.setattr(search, "_wikipedia_items", lambda q, n: [])

    items, errors = search.search_items("besoin PME", max_results=6)

    assert errors == []
    assert calls[:2] == ["web", "news"]
    assert [item["url"] for item in items[:2]] == [
        "https://example.com/web",
        "https://example.com/news",
    ]


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


def test_keyless_search_uses_direct_results_before_google_wrappers(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: [])
    monkeypatch.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", f"Titre {i}", f"https://example.com/direct-{i}", "Bing Web", "", "preuve")
        for i in range(6)
    ])
    monkeypatch.setattr(search, "_gnews_items", lambda *a, **k: pytest.fail("Google News ne doit pas être consulté"))
    monkeypatch.setattr(search, "_wikipedia_items", lambda *a, **k: pytest.fail("Wikipedia ne doit pas être consulté"))

    out = search.web_search("besoin PME")

    assert "https://example.com/direct-0" in out
    assert "Fournisseurs de recherche : bing_web" in out


def test_search_site_parameter_is_applied_and_filtered(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    seen_queries = []

    def web_items(q, n):
        seen_queries.append(q)
        return [
            search._item("bing_web", "Bpifrance", "https://www.bpifrance.fr/barometre", "Bing Web", "", "preuve"),
            search._item("bing_web", "Hors site", "https://example.com/barometre", "Bing Web", "", "bruit"),
        ]

    monkeypatch.setattr(search, "_bing_web_items", web_items)
    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: [])
    monkeypatch.setattr(search, "_gnews_items", lambda *a, **k: pytest.fail("pas de fallback sous contrainte site"))
    monkeypatch.setattr(search, "_wikipedia_items", lambda *a, **k: pytest.fail("pas de Wikipedia sous contrainte site"))

    items, errors = search.search_items("baromètre trésorerie PME", site="bpifrance.fr")

    assert errors == []
    assert seen_queries == ["baromètre trésorerie PME site:bpifrance.fr"]
    assert [item["url"] for item in items] == ["https://www.bpifrance.fr/barometre"]


def test_embedded_site_operator_is_enforced_locally(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Cible", "https://stats.insee.fr/preuve", "Bing Web", "", "preuve"),
        search._item("bing_web", "Hors cible", "https://example.com/bruit", "Bing Web", "", "bruit"),
    ])
    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: [])

    items, _ = search.search_items("chômage France site:insee.fr")

    assert [item["url"] for item in items] == ["https://stats.insee.fr/preuve"]


def test_runtime_search_declares_and_forwards_site(monkeypatch):
    calls = []

    def fake_search(query, n=6, site=None):
        calls.append((query, site))
        return f"Requête effective : {query} site:{site}\n- preuve\n  https://{site}/preuve"

    monkeypatch.setattr(search, "web_search", fake_search)

    with runtime._search_cache():
        result = runtime._search({"query": "baromètre trésorerie PME", "site": "bpifrance.fr"})

    assert calls == [("baromètre trésorerie PME", "bpifrance.fr")]
    assert "site:bpifrance.fr" in result
    assert runtime.TOOLS["search"]["params"]["site"] == "str?"


def test_site_changes_search_cache_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(
        search,
        "web_search",
        lambda q, n=6, site=None: calls.append((q, site)) or f"- {site}",
    )

    with runtime._search_cache():
        runtime._search({"query": "baromètre PME", "site": "bpifrance.fr"})
        runtime._search({"query": "baromètre PME", "site": "insee.fr"})

    assert calls == [
        ("baromètre PME", "bpifrance.fr"),
        ("baromètre PME", "insee.fr"),
    ]


def test_invalid_site_is_rejected_before_network():
    with pytest.raises(ValueError, match="site invalide"):
        search.effective_query("preuve PME", site="bpifrance.fr OR evil.example")


def test_structured_site_replaces_stale_site_operator():
    assert search.effective_query(
        "baromètre PME site:ancien.example",
        site="bpifrance.fr",
    ) == "baromètre PME site:bpifrance.fr"


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
