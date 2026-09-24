"""Frontière « contraindre les conséquences, pas l'intelligence ».

Ces tests protègent la séparation des responsabilités établie par la remise à plat
architecturale :

* le LLM décide QUOI chercher, COMMENT chercher, quand changer d'angle ;
* le code décide ce qui est autorisé, ce qui coûte, ce qui compte comme preuve.

Un échec ici signale une régression vers le micro-management cognitif (prompt qui
réapprend à chercher) ou vers un affaiblissement des invariants (preuve sans
acquisition, dépense sans autorisation).

Ne simule aucune performance live : tout est hors ligne et déterministe.
"""
from __future__ import annotations

import pytest

from agents import deepseek, runtime, search
from octopus import journal

TODAY = "2026-09-24"


@pytest.fixture
def web(monkeypatch):
    """SEARCH est structuré : le seam de test est l'enveloppe, pas le texte."""
    calls = []

    def fake_envelope(query, max_results=6, site=None, purpose="general"):
        calls.append(query if site is None else f"{query} site:{site}")
        return {
            "query": query,
            "effective_query": query if site is None else f"{query} site:{site}",
            "purpose": purpose,
            "items": [{"title": f"resultat pour {query}", "url": "https://example.com/preuve",
                       "source": "Bing Web", "date": "", "snippet": "extrait", "provider": "bing_web"}],
            "errors": [],
        }

    monkeypatch.setattr(search, "search_envelope", fake_envelope)
    return calls


def scripted(monkeypatch, actions):
    seen = []
    it = iter(actions + [{"final": "fin"}])

    def call_json(agent, task, model, messages, **kw):
        seen.append(messages)
        return next(it)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    return seen


# ─────────────────────────────────────────────────────────────────────────────
# A. LE PROMPT BUSINESS NE PRESCRIT PLUS COMMENT CHERCHER
# ─────────────────────────────────────────────────────────────────────────────
def _business_prompt(monkeypatch) -> tuple[str, str]:
    """(prompt système, contexte de tâche) d'un sous-agent SOUT en mode business signal."""
    with journal.run("octopus", "agent"):
        system, _first, _done = runtime.build_prompts(
            "SOUT", "trouver des opportunites", allowed_tools={"search", "browse"})
    return system, runtime._business_signal_task_context(3, "SOUT")


# Prescriptions de recherche interdites dans le contrat de mission.
_SEARCH_PRESCRIPTIONS = [
    "2 à 5 termes",          # longueur de requête imposée
    "3 mots",
    "requête courte",
    "budget",                # mot imposé ou interdit
    "année courante",        # année ajoutée puis retirée
    "retire l'année",
    "deuxième intention",    # quand utiliser site:
    "guillemets",            # ponctuation de requête
    "retire site",           # séquence conditionnelle de reformulation
    "change d'angle",        # séquence conditionnelle de reformulation
    "change de segment",
    "SEARCH découvre",       # séquence cognitive figée
    "BROWSE vérifie",
    "commence par une requête",
    "élargis immédiatement",
    "si search renvoie",     # branchement conditionnel
]

# Sous-ensemble applicable au prompt système, qui décrit légitimement les outils
# (`propose_experiment` déclare `budget_limit`, `request_spend` déclare une dépense).
_SYSTEM_PRESCRIPTIONS = [p for p in _SEARCH_PRESCRIPTIONS if p != "budget"]


@pytest.mark.parametrize("prescription", _SEARCH_PRESCRIPTIONS)
def test_business_mission_context_has_no_search_prescription(monkeypatch, prescription):
    _system, task = _business_prompt(monkeypatch)
    assert prescription not in task, f"prescription de recherche reintroduite : {prescription}"


@pytest.mark.parametrize("prescription", _SYSTEM_PRESCRIPTIONS)
def test_business_system_prompt_has_no_search_prescription(monkeypatch, prescription):
    system, _task = _business_prompt(monkeypatch)
    assert prescription not in system, f"prescription de recherche reintroduite : {prescription}"


def test_business_prompt_keeps_the_objective_and_result_criteria():
    """Supprimer le micro-management ne supprime pas l'objectif économique."""
    task = runtime._business_signal_task_context(3, "SOUT")

    for criterion in (
        "acheteur/segment identifiable",
        "douleur, tâche manuelle, obligation ou demande concrète",
        "source réellement ouverte pendant cette mission",
        "signal monétaire ou d'urgence",
        "canal réaliste",
        "offre minimale et un prochain test",
    ):
        assert criterion in task
    # La liberté de recherche est explicite.
    assert "Cherche librement" in task
    assert "adapte ton approche aux résultats" in task
    assert "n'invente aucune preuve" in task


def test_business_prompt_stays_small():
    """Garde-fou anti-accumulation : un contrat qui gonfle redevient une procédure."""
    contract = runtime._business_signal_contract(3)
    assert len(contract) < 2200, (
        f"contrat business signal = {len(contract)} caractères ; "
        "au-delà de 2200 il risque de contenir une procédure de recherche"
    )


def test_current_date_is_a_fact_not_a_query_instruction(monkeypatch):
    monkeypatch.setattr(runtime, "_today_iso", lambda: TODAY)
    with journal.run("octopus", "agent"):
        system, _first, _done = runtime.build_prompts(
            "SOUT", "veille", allowed_tools={"search", "browse"})

    assert f"DATE ACTUELLE : {TODAY}" in system
    # La date ne devient jamais une consigne d'écriture de requête.
    assert "privilégie" not in system
    assert "année" not in system


def test_date_context_is_identical_in_every_mode(monkeypatch):
    """Plus de variante « business » du contexte de date : une seule source, aucune contradiction."""
    monkeypatch.setattr(runtime, "_today_iso", lambda: TODAY)

    class Run:
        business = "octopus"

    text = runtime._freshness_context(Run())
    assert text.strip() == f"DATE ACTUELLE : {TODAY}."
    # Une seule fonction, un seul argument : aucun paramètre de mode à faire diverger.
    assert text == runtime._freshness_context(Run())


# ─────────────────────────────────────────────────────────────────────────────
# B. INVARIANTS DÉTERMINISTES — INTACTS
# ─────────────────────────────────────────────────────────────────────────────
def test_evidence_gate_still_fails_closed():
    """#94 : aucune preuve sans acquisition réelle + citations littérales de la même page."""
    url = "https://example.org/brief"
    text = ("Cabinet comptable. Relances de factures faites manuellement chaque semaine. "
            "Nous cherchons un prestataire pour automatiser les relances, budget mensuel prévu.")
    page = {"requested_url": url, "final_url": url, "main_text": text, "text_chars": len(text),
            "blocked": False, "error": None, "http_status": 200,
            "extraction_method": "http:html_main", "rendered": False,
            "fetched_at": "2026-09-24T00:00:00+00:00"}
    data = {"url": url, "page": page}
    step = {"tool": "browse", "args": {"url": url}, "result_data": data,
            "browse_meta": runtime._browse_result_meta(data)}
    results = [{"role": "SOUT", "steps": [step]}]

    def signal(**over):
        base = {
            "signal_type": "explicit_request", "buyer": "Cabinet comptable",
            "pain": "relances manuelles", "money_signal": "budget mensuel",
            "evidence_url": url, "evidence_summary": "demande de prestataire",
            "buyer_evidence": "Cabinet comptable",
            "pain_evidence": "Relances de factures faites manuellement chaque semaine",
            "money_evidence": "budget mensuel prévu",
            "summary_evidence": "Nous cherchons un prestataire",
            "test_channel": "réponse", "test_offer": "prototype", "next_test": "essai",
        }
        base.update(over)
        return base

    # URL jamais ouverte.
    assert runtime._qualify_business_signals([signal()], [])[0] == []
    # Citation absente du texte acquis.
    assert runtime._qualify_business_signals([signal(summary_evidence="texte inventé")], results)[0] == []
    # Acquisition bloquée.
    blocked = [{"role": "SOUT", "steps": [
        {**step, "browse_meta": {**step["browse_meta"], "blocked": True}}]}]
    assert runtime._qualify_business_signals([signal()], blocked)[0] == []
    # Citations provenant de pages différentes.
    other = [{"role": "SOUT", "steps": [
        {"tool": "browse", "args": {"url": url}, "result_data": data,
         "browse_meta": runtime._browse_result_meta(data)},
        {"tool": "browse", "args": {"url": "https://example.org/autre"},
         "result_data": {"url": "https://example.org/autre", "page": {
             **page, "final_url": "https://example.org/autre",
             "requested_url": "https://example.org/autre",
             "main_text": "Autre page totalement différente." * 3,
             "text_chars": 120}},
         "browse_meta": runtime._browse_result_meta({
             "url": "https://example.org/autre", "page": {
                 **page, "final_url": "https://example.org/autre",
                 "requested_url": "https://example.org/autre",
                 "main_text": "Autre page totalement différente." * 3,
                 "text_chars": 120}})},
    ]}]
    split = signal(summary_evidence="Autre page totalement différente")
    accepted, rejected = runtime._qualify_business_signals([split], other)
    assert accepted == [] and "quotes_not_in_same_acquisition" in rejected[0]["reasons"]

    # Provenance calculée depuis l'outil, jamais depuis un champ proposé par le LLM.
    accepted, _ = runtime._qualify_business_signals([signal()], results)
    assert len(accepted) == 1
    assert accepted[0]["evidence_acquisition"]["final_url"] == url
    assert accepted[0]["evidence_acquisition"]["fetched_at"] == "2026-09-24T00:00:00+00:00"
    assert accepted[0]["action_fields_nature"] == "inferred"


def test_tool_allowlist_still_gates_execution(monkeypatch):
    called = []
    monkeypatch.setitem(runtime.TOOLS["act_on_channel"], "fn",
                        lambda args: called.append(args) or {"ok": True})
    scripted(monkeypatch, [{"tool": "act_on_channel", "args": {
        "channel_id": 1, "action": "publier", "payload": {}}}])

    runtime.run_agent("GROWTH", "publier", max_steps=2,
                      allowed_tools={"search", "browse"})

    assert called == []


def test_agent_still_stops_on_budget(monkeypatch):
    monkeypatch.setattr(runtime, "_budget_exhausted", lambda: True)
    result = runtime.run_agent("SOUT", "chercher", max_steps=5)
    assert result["final"] == "(budget dépassé)"
    assert result["steps"] == []


def test_search_cost_class_still_blocks_paid_quota(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "secret")
    monkeypatch.setenv("OCTOPUS_SEARCH_BRAVE_COST_CLASS", "paid")
    monkeypatch.setattr(search.requests, "get",
                        lambda *a, **k: pytest.fail("aucun appel réseau attendu"))
    with pytest.raises(RuntimeError, match="paid.*bloquée"):
        search._brave_items("test", 1)


# ─────────────────────────────────────────────────────────────────────────────
# C. AUTONOMIE — LE LLM RESTE LIBRE
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("query", [
    "2026 freelance request data migration budget cloud",   # requête du run #63
    "appel d'offres migration cloud 2026 budget",
    "reddit freelance client manual data entry painful 2026",
    "a",
    "un mot",
    "une requête très longue " + " ".join(["terme"] * 40),
    "site:freelancer.com",
])
def test_search_accepts_any_free_query(monkeypatch, query):
    """SEARCH ne valide ni la longueur, ni les mots, ni l'année : c'est un outil, pas un tuteur."""
    seen = []

    def fake_envelope(q, max_results=6, site=None, purpose="general"):
        seen.append(q)
        return {"query": q, "effective_query": q, "purpose": purpose, "items": [], "errors": []}

    monkeypatch.setattr(search, "search_envelope", fake_envelope)
    with runtime._search_cache():
        runtime._search({"query": query})
    assert seen == [query]


def test_selector_does_not_block_an_ambiguous_business_word():
    """« appel d'offres » ne doit pas faire rejeter une URL pourtant pertinente.

    L'ancien sélecteur possédait une liste de mots ambigus (`appel`, `mission`, `free`,
    `work`) héritée d'un run. Un résultat réellement lié à la requête reste sélectionnable.
    """
    result = (
        "- Appel d'offres automatisation reporting data\n"
        "  https://example.org/ao/42\n"
        "  Marché public d'automatisation du reporting data, budget annexé.\n"
    )
    choice = runtime._select_search_browse_candidate(
        result, "appel d'offres automatisation reporting data", "business_signal_relevance")
    assert choice is not None
    assert choice["url"] == "https://example.org/ao/42"


def test_selector_keeps_rejecting_single_word_collisions():
    """Le plancher de pertinence général remplace les listes de bruit métier.

    Apple pour « appel », AlloCiné pour « mission » : un seul mot partagé ne suffit plus
    à forcer une ouverture. Aucun vocabulaire métier n'est nécessaire pour ce résultat.
    """
    apple = ("- Apple (France)\n  https://www.apple.com/fr/\n  Tout l'univers Apple.\n"
             "- App Téléphone\n  https://apps.apple.com/fr/app/telephone/id1\n"
             "  Pour un appel entrant, répondez sur l'iPhone.")
    assert runtime._select_search_browse_candidate(
        apple, "appel d'offres automatisation reporting 2026",
        "business_signal_relevance") is None

    allocine = ("- Mission (film) — Wikipédia\n  https://fr.wikipedia.org/wiki/Mission_(film)\n"
                "  Film historique.\n- Mission - Film 1986 - AlloCiné\n"
                "  https://www.allocine.fr/film/fichefilm_gen_cfilm=2152.html\n"
                "  Film de Roland Joffé.\n- Définitions : mission - Larousse\n"
                "  https://www.larousse.fr/dictionnaires/francais/mission/51785\n  Définition générale.")
    assert runtime._select_search_browse_candidate(
        allocine, "mission freelance data analyst reporting automatisation",
        "business_signal_relevance") is None


def test_no_coded_strategy_parser_exists():
    """Aucun parseur de stratégie de recherche ne doit réapparaître dans le runtime."""
    forbidden = ("_search_strategy", "_query_strategy", "_parse_strategy",
                 "_search_phase", "_reformulation", "_relax_query")
    for name in dir(runtime):
        assert name not in forbidden, f"analyseur de stratégie reintroduit : {name}"


def test_agent_can_change_angle_without_any_coded_transition(monkeypatch, web):
    """Deux recherches d'angles différents s'enchaînent librement, sans règle codée."""
    scripted(monkeypatch, [
        {"tool": "search", "args": {"query": "freelance automatisation factures PME"}},
        {"tool": "search", "args": {"query": "offre emploi comptable relance clients"}},
        {"final": "deux angles explorés"},
    ])
    result = runtime.run_agent("SOUT", "explorer", max_steps=5,
                               allowed_tools={"search"})
    assert [s["tool"] for s in result["steps"]] == ["search", "search"]
    assert len(web) == 2


# ─────────────────────────────────────────────────────────────────────────────
# D. COMPATIBILITÉ — LES CHEMINS EXISTANTS FONCTIONNENT
# ─────────────────────────────────────────────────────────────────────────────
def test_search_tool_contract_returns_structured_items(monkeypatch):
    """La vue texte de l'outil garde URL et requête effective ; la structure garde six champs."""
    items = [{"provider": "brave", "title": "T", "url": "https://example.org/a",
              "source": "S", "date": "2026-09-24", "snippet": "extrait"}]
    monkeypatch.setattr(
        search, "search_envelope",
        lambda q, max_results=6, site=None, purpose="general": {
            "query": q, "effective_query": q, "purpose": purpose, "items": items, "errors": []})
    text = search.web_search("requete", 6)
    assert "https://example.org/a" in text
    assert "Requête effective : requete" in text
    for field in ("provider", "title", "url", "source", "date", "snippet"):
        assert field in items[0]


def test_general_mission_still_runs_end_to_end(monkeypatch, web):
    """Mission générique (hors business signal) : planners, agents, synthèse, gate intacts."""
    scripted(monkeypatch, [
        {"tasks": [{"role": "SOUT", "task": "collecter"}]},
        {"tool": "search", "args": {"query": "veille concurrentielle"}},
        {"final": "collecte faite"},
        {"rapport": "rapport de synthèse"},
    ])
    result = runtime.run_mission("faire une veille", max_steps_per_agent=3)
    assert result["synthesis_status"] == "validated"
    assert result["rapport"] == "rapport de synthèse"
    assert "business_signals" not in result
    assert web == ["veille concurrentielle"]


def test_veille_consumer_still_uses_search(monkeypatch):
    """Le consommateur historique (veille/brief.collect) passe toujours par web_search."""
    from agents import task_handlers  # noqa: F401  (import guard)

    calls = []
    monkeypatch.setattr(search, "web_search",
                        lambda q, n=6, site=None: calls.append(q) or "- resultat")
    assert search.web_search("brief", 3) == "- resultat"
    assert calls == ["brief"]
