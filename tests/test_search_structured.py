"""SEARCH Commodity Reset : la structure traverse le runtime, le texte n'est qu'une vue.

Ces tests vérifient la frontière : le provider livre des items, le runtime les transporte
tels quels jusqu'au selector, et le texte n'est produit qu'ensuite pour le LLM ou l'humain.
Aucun chemin machine ne doit refaire STRUCTURE -> TEXTE -> REGEX -> STRUCTURE.
"""
from __future__ import annotations

import copy

import pytest

from agents import browser, runtime, search
from agents import agents as legacy_agents


def item(url="https://example.org/offre/1", title="Offre", **fields) -> dict:
    base = {"title": title, "url": url, "source": "Source", "date": "2026-09-22",
            "snippet": "Extrait exploitable.", "provider": "bing_web"}
    base.update(fields)
    return base


def envelope(items=(), **fields) -> dict:
    base = {"query": "retards paiement PME", "effective_query": "retards paiement PME",
            "purpose": search.SEARCH_PURPOSE_GENERAL, "items": list(items), "errors": []}
    base.update(fields)
    return base


@pytest.fixture
def keyless(monkeypatch):
    """Aucune clé d'API : SEARCH part directement sur les fournisseurs keyless."""
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(search, "_bing_web_items", lambda q, n: [])
    monkeypatch.setattr(search, "_bing_news_items", lambda q, n: [])
    monkeypatch.setattr(search, "_gnews_items", lambda q, n: [])
    monkeypatch.setattr(search, "_wikipedia_items", lambda q, n: [])
    return monkeypatch


def blow_up(name):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"{name} ne doit pas être appelé")

    return fail


# --- 1. Contrat d'enveloppe -------------------------------------------------------------

def test_envelope_shape_is_stable(keyless):
    result = search.search_envelope("retards paiement PME")

    assert list(result) == ["query", "effective_query", "purpose", "items", "errors"]
    assert result["query"] == "retards paiement PME"
    assert result["effective_query"] == "retards paiement PME"
    assert result["purpose"] == search.SEARCH_PURPOSE_GENERAL
    assert result["items"] == [] and result["errors"] == []


def test_items_keep_the_six_provider_fields(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        {"provider": "bing_web", "title": "Retards de paiement", "url": "https://example.org/retards",
         "source": "Bing Web", "date": "Tue, 22 Sep 2026 08:00:00 GMT", "snippet": "Délais constatés."},
    ])

    result = search.search_envelope("retards paiement PME")

    assert list(result["items"][0]) == ["title", "url", "source", "date", "snippet", "provider"]
    assert result["items"][0] == {
        "title": "Retards de paiement", "url": "https://example.org/retards", "source": "Bing Web",
        "date": "Tue, 22 Sep 2026 08:00:00 GMT", "snippet": "Délais constatés.", "provider": "bing_web",
    }


def test_unknown_purpose_is_rejected(keyless):
    with pytest.raises(ValueError, match="purpose"):
        search.search_envelope("retards PME", purpose="marketing")


def test_provider_error_is_not_an_empty_market(keyless):
    def fail(q, n):
        raise ConnectionError("TLS handshake failed")

    keyless.setattr(search, "_bing_web_items", fail)
    keyless.setattr(search, "_bing_news_items", fail)

    result = search.search_envelope("retards paiement PME")

    assert result["items"] == []
    assert result["errors"] and "ConnectionError" in result["errors"][0]
    assert "Aucun résultat exploitable." in search.render_envelope(result)
    assert "Sources en erreur" in search.render_envelope(result)


def test_zero_result_is_not_reported_as_a_failure(keyless):
    result = search.search_envelope("retards paiement PME")

    assert result["errors"] == []
    assert search.render_envelope(result).endswith("Aucun résultat exploitable.")


def test_timeout_and_429_are_recorded_as_errors(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: (_ for _ in ()).throw(TimeoutError("read timeout")))
    keyless.setattr(search, "_bing_news_items", lambda q, n: [])

    result = search.search_envelope("retards paiement PME")

    assert result["items"] == []
    assert "TimeoutError" in result["errors"][0]


def test_http_429_is_an_error_not_a_missing_market(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: (_ for _ in ()).throw(
        RuntimeError("429 Client Error: Too Many Requests")))
    keyless.setattr(search, "_bing_news_items", lambda q, n: [])

    result = search.search_envelope("retards paiement PME")

    assert result["items"] == []
    assert "429" in result["errors"][0]
    assert result["errors"] != ["Aucun résultat exploitable."]


# --- 2. Politique business / general -----------------------------------------------------

def test_business_purpose_stops_after_bing_web(keyless):
    keyless.setattr(search, "_gnews_items", blow_up("Google News"))
    keyless.setattr(search, "_wikipedia_items", blow_up("Wikipedia"))
    keyless.setattr(search, "_bing_news_items", blow_up("Bing News"))
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Appel d'offres", "https://example.org/ao", "Bing Web", snippet="preuve"),
    ])

    result = search.search_envelope("appel d'offres PME", purpose=search.SEARCH_PURPOSE_BUSINESS)

    assert [entry["url"] for entry in result["items"]] == ["https://example.org/ao"]
    assert result["purpose"] == search.SEARCH_PURPOSE_BUSINESS


def test_business_purpose_accepts_brave_then_tavily(monkeypatch):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "")
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "key")
    monkeypatch.setenv("OCTOPUS_SEARCH_TAVILY_COST_CLASS", "free_quota")
    monkeypatch.setattr(search, "_tavily_items", lambda q, n: [
        search._item("tavily", "Mission freelance", "https://example.org/mission", snippet="preuve"),
    ])
    monkeypatch.setattr(search, "_bing_web_items", blow_up("Bing Web"))

    result = search.search_envelope("mission freelance", purpose=search.SEARCH_PURPOSE_BUSINESS)

    assert result["items"][0]["provider"] == "tavily"


def test_business_purpose_falls_back_to_bing_web_when_keys_are_absent(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Devis", "https://example.org/devis", snippet="preuve"),
    ])

    result = search.search_envelope("devis PME", purpose=search.SEARCH_PURPOSE_BUSINESS)

    assert [entry["provider"] for entry in result["items"]] == ["bing_web"]


def test_business_purpose_does_not_complete_a_partial_bing_web(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Seul résultat", "https://example.org/seul", snippet="preuve"),
    ])
    keyless.setattr(search, "_bing_news_items", blow_up("Bing News"))
    keyless.setattr(search, "_gnews_items", blow_up("Google News"))
    keyless.setattr(search, "_wikipedia_items", blow_up("Wikipedia"))

    result = search.search_envelope("devis PME", max_results=6, purpose=search.SEARCH_PURPOSE_BUSINESS)

    assert len(result["items"]) == 1


def test_general_purpose_keeps_the_historical_fallbacks(keyless):
    calls = []
    keyless.setattr(search, "_bing_web_items", lambda q, n: calls.append("web") or [])
    keyless.setattr(search, "_bing_news_items", lambda q, n: calls.append("news") or [])
    keyless.setattr(search, "_gnews_items", lambda q, n: calls.append("gnews") or [])
    keyless.setattr(search, "_wikipedia_items", lambda q, n: calls.append("wiki") or [
        search._item("wikipedia", "Micro-entrepreneur", "https://fr.wikipedia.org/wiki/Micro-entrepreneur",
                     "Wikipédia", snippet="Régime."),
    ])

    result = search.search_envelope("micro entrepreneur")

    assert calls == ["web", "news", "gnews", "wiki"]
    assert result["items"][0]["provider"] == "wikipedia"


def test_general_purpose_stops_before_wrappers_under_site_constraint(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [])
    keyless.setattr(search, "_bing_news_items", lambda q, n: [])
    keyless.setattr(search, "_gnews_items", blow_up("Google News"))
    keyless.setattr(search, "_wikipedia_items", blow_up("Wikipedia"))

    result = search.search_envelope("baromètre PME", site="bpifrance.fr")

    assert result["effective_query"] == "baromètre PME site:bpifrance.fr"
    assert result["items"] == [] and result["errors"] == []


def test_missing_cost_class_is_an_error_not_a_silent_skip(monkeypatch, keyless):
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "key")
    monkeypatch.delenv("OCTOPUS_SEARCH_BRAVE_COST_CLASS", raising=False)

    result = search.search_envelope("devis PME", purpose=search.SEARCH_PURPOSE_BUSINESS)

    assert "cost class" in result["errors"][0]
    assert result["items"] == []


def test_paid_cost_class_never_reaches_the_provider(monkeypatch, keyless):
    monkeypatch.setattr(search.config, "TAVILY_API_KEY", "key")
    monkeypatch.setenv("OCTOPUS_SEARCH_TAVILY_COST_CLASS", "paid")
    monkeypatch.setattr(search.requests, "post", blow_up("appel réseau payant"))

    result = search.search_envelope("devis PME", purpose=search.SEARCH_PURPOSE_BUSINESS)

    assert "paid" in result["errors"][0]
    assert result["items"] == []


def test_mediocre_but_non_empty_provider_result_is_kept(monkeypatch, keyless):
    """Aucun benchmark live : un résultat faible n'autorise pas un changement de provider."""
    monkeypatch.setattr(search.config, "BRAVE_API_KEY", "key")
    monkeypatch.setenv("OCTOPUS_SEARCH_BRAVE_COST_CLASS", "free_quota")
    monkeypatch.setattr(search, "_brave_items", lambda q, n: [
        search._item("brave", "Page générique sans rapport", "https://example.org/generique", snippet="peu pertinent"),
    ])
    monkeypatch.setattr(search, "_tavily_items", blow_up("Tavily"))

    result = search.search_envelope("devis PME", purpose=search.SEARCH_PURPOSE_BUSINESS)

    assert [entry["url"] for entry in result["items"]] == ["https://example.org/generique"]
    assert result["errors"] == []


def test_search_items_defaults_to_the_historical_general_policy(keyless):
    calls = []
    keyless.setattr(search, "_bing_web_items", lambda q, n: calls.append("web") or [])
    keyless.setattr(search, "_bing_news_items", lambda q, n: calls.append("news") or [])
    keyless.setattr(search, "_gnews_items", lambda q, n: calls.append("gnews") or [])
    keyless.setattr(search, "_wikipedia_items", lambda q, n: calls.append("wiki") or [])

    items, errors = search.search_items("micro entreprise")

    assert (items, errors) == ([], [])
    assert calls == ["web", "news", "gnews", "wiki"]


# --- 3. Robustesse des items -------------------------------------------------------------

def test_malformed_provider_item_is_dropped(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        "pas un objet", None, 42, {"title": "sans url"},
        {"title": "valide", "url": "https://example.org/valide"},
    ])

    result = search.search_envelope("devis PME")

    assert [entry["url"] for entry in result["items"]] == ["https://example.org/valide"]


@pytest.mark.parametrize("url", ["", "   ", "ftp://example.org/f", "javascript:alert(1)",
                                 "//example.org/sans-scheme", "https://utilisateur:mdp@example.org/fuite",
                                 "https://example.org/espace dans l'url", "https://example.org/retour\nligne"])
def test_unbrowsable_urls_are_not_results(keyless, url):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [{"title": "x", "url": url}])

    result = search.search_envelope("devis PME")

    assert result["items"] == []


def test_url_with_parentheses_survives_intact(keyless):
    url = "https://example.org/offre/(42)?utm_source=news&id=7#details"
    keyless.setattr(search, "_bing_web_items", lambda q, n: [{"title": "Offre (42)", "url": url}])

    result = search.search_envelope("devis PME")

    assert result["items"][0]["url"] == url
    assert runtime._search_result_urls(result) == [url]
    assert url in search.render_envelope(result)


def test_unicode_and_query_params_survive(keyless):
    url = "https://exemple.fr/offres/d%C3%A9m%C3%A9nagement-é?ville=Clich%C3%BD&rayon=5+km"
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        {"title": "Déménagement — Clichy", "url": url},
    ])

    result = search.search_envelope("déménagement Clichy")

    assert result["items"][0]["url"] == url
    assert runtime._search_result_urls(result) == [url]


def test_duplicate_urls_are_collapsed_once(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        {"title": "Offre", "url": "https://example.org/offre"},
        {"title": "Offre (copie)", "url": "https://example.org/offre"},
        {"title": "Autre", "url": "https://example.org/autre"},
    ])

    result = search.search_envelope("devis PME")

    assert [entry["url"] for entry in result["items"]] == [
        "https://example.org/offre", "https://example.org/autre",
    ]


def test_missing_snippet_and_date_are_tolerated(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [{"title": "Offre", "url": "https://example.org/o"}])

    result = search.search_envelope("devis PME")

    assert result["items"][0]["snippet"] == "" and result["items"][0]["date"] == ""


def test_huge_snippet_stays_complete_in_structure_and_bounded_in_view(keyless):
    snippet = "x" * 20000
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        {"title": "Offre", "url": "https://example.org/o", "snippet": snippet},
    ])

    result = search.search_envelope("devis PME")
    view = search.render_envelope(result)

    assert result["items"][0]["snippet"] == snippet
    assert len(view) < 20000 and "https://example.org/o" in view


def test_url_only_present_in_a_snippet_is_not_a_result(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        {"title": "Offre", "url": "", "snippet": "voir https://invente.example.org/faux"},
    ])

    result = search.search_envelope("devis PME")

    assert result["items"] == []
    assert runtime._search_result_urls(result) == []
    assert runtime._search_result_candidates(result) == []


def test_url_only_present_in_an_error_is_not_a_result(keyless):
    def fail(q, n):
        raise ConnectionError("échec vers https://www.bing.com/search?q=devis")

    keyless.setattr(search, "_bing_web_items", fail)
    keyless.setattr(search, "_bing_news_items", lambda q, n: [])

    result = search.search_envelope("devis PME")

    assert runtime._search_result_urls(result) == []
    assert "https://www.bing.com/search?q=devis" in search.render_envelope(result)


def test_metadata_is_compacted_without_losing_words(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        {"title": " Offre  de\nmission ", "url": "https://example.org/o", "source": " Bing\nWeb ",
         "date": " 2026-09-22 ", "provider": "bing_web"},
    ])

    result = search.search_envelope("devis PME")

    assert result["items"][0]["title"] == "Offre de mission"
    assert result["items"][0]["source"] == "Bing Web"
    assert result["items"][0]["date"] == "2026-09-22"


def test_site_constraint_still_filters_provider_items(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        {"title": "Cible", "url": "https://www.bpifrance.fr/barometre"},
        {"title": "Hors cible", "url": "https://example.org/bruit"},
        {"title": "Faux sous-domaine", "url": "https://bpifrance.fr.evil.example/faux"},
    ])

    result = search.search_envelope("baromètre", site="bpifrance.fr")

    assert [entry["url"] for entry in result["items"]] == ["https://www.bpifrance.fr/barometre"]


def test_invalid_site_is_still_rejected_before_any_call(keyless):
    with pytest.raises(ValueError, match="site invalide"):
        search.search_envelope("baromètre", site="bpifrance.fr OR evil.example")


# --- 4. Cache ----------------------------------------------------------------------------

def test_cache_keeps_the_complete_structure(monkeypatch):
    monkeypatch.setattr(search, "search_envelope", lambda *a, **k: envelope([item()]))
    with runtime._search_cache() as cache:
        runtime._search({"query": "retards paiement PME"})

    assert cache[runtime._search_cache_key("retards paiement PME", search.SEARCH_PURPOSE_GENERAL)][
        "envelope"]["items"][0]["snippet"] == "Extrait exploitable."


def test_consumer_mutation_does_not_corrupt_the_cache(monkeypatch):
    monkeypatch.setattr(search, "search_envelope", lambda *a, **k: envelope([item()]))
    with runtime._search_cache():
        first = runtime._search({"query": "retards paiement PME"})
        first["items"].clear()
        first["errors"].append("pollution")
        second = runtime._search({"query": "retards paiement PME"})

    assert "deja_cherche" in second
    assert [entry["url"] for entry in second["items"]] == ["https://example.org/offre/1"]
    assert second["errors"] == []


def test_business_and_general_do_not_share_the_cache(monkeypatch):
    calls = []

    def fake(query, max_results=6, site=None, purpose=search.SEARCH_PURPOSE_GENERAL):
        calls.append(purpose)
        return envelope([item()], purpose=purpose)

    monkeypatch.setattr(search, "search_envelope", fake)
    with runtime._search_cache():
        with runtime.search_purpose(search.SEARCH_PURPOSE_GENERAL):
            runtime._search({"query": "retards paiement PME"})
        with runtime.search_purpose(search.SEARCH_PURPOSE_BUSINESS):
            business = runtime._search({"query": "retards paiement PME"})

    assert calls == [search.SEARCH_PURPOSE_GENERAL, search.SEARCH_PURPOSE_BUSINESS]
    assert "deja_cherche" not in business
    assert business["purpose"] == search.SEARCH_PURPOSE_BUSINESS


def test_cache_hit_carries_native_items_and_the_marker(monkeypatch):
    monkeypatch.setattr(search, "search_envelope", lambda *a, **k: envelope([item(), item("https://example.org/2")]))
    with runtime._search_cache():
        runtime._search({"query": "retards paiement PME"})
        hit = runtime._search({"query": "retards paiement PME"})

    assert hit["deja_cherche"] is True
    assert [entry["url"] for entry in hit["items"]] == [
        "https://example.org/offre/1", "https://example.org/2",
    ]
    assert runtime._search_result_urls(hit) == ["https://example.org/offre/1", "https://example.org/2"]
    assert "deja_cherche" in runtime._search_view(hit)
    assert len(runtime._search_view(hit)) < 1200


def test_business_mission_uses_the_business_purpose(monkeypatch):
    seen = []

    def fake_envelope(query, max_results=6, site=None, purpose=search.SEARCH_PURPOSE_GENERAL):
        seen.append(purpose)
        return envelope([item()], purpose=purpose)

    monkeypatch.setattr(search, "search_envelope", fake_envelope)
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "collecter"}]},
        {"tool": "search", "args": {"query": "mission freelance PME"}},
        {"final": "ok"},
        {"rapport": "rapport", "business_signals": []},
    ])
    from agents import deepseek
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    runtime.run_mission("signaux", max_steps_per_agent=2, business_signal_focus=True,
                        business_signal_target=1, business="octopus")

    assert seen == [search.SEARCH_PURPOSE_BUSINESS]


def test_general_mission_keeps_the_general_purpose(monkeypatch):
    seen = []

    def fake_envelope(query, max_results=6, site=None, purpose=search.SEARCH_PURPOSE_GENERAL):
        seen.append(purpose)
        return envelope([item()], purpose=purpose)

    monkeypatch.setattr(search, "search_envelope", fake_envelope)
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "collecter"}]},
        {"tool": "search", "args": {"query": "veille PME"}},
        {"final": "ok"},
        {"rapport": "rapport"},
    ])
    from agents import deepseek
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    runtime.run_mission("veille", max_steps_per_agent=2, business="octopus")

    assert seen == [search.SEARCH_PURPOSE_GENERAL]


# --- 5. Selector sur items natifs --------------------------------------------------------

def test_selector_reads_native_items_not_a_rendered_text(monkeypatch):
    monkeypatch.setattr(runtime, "_search_result_candidates_from_text", blow_up("parser texte legacy"))
    structured = envelope([
        item("https://www.larousse.fr/dictionnaires/francais/probleme", title="Définition : problème",
             snippet="Définition générale.", provider="wikipedia"),
        item("https://www.insee.fr/fr/statistiques/1234567", title="Délais de paiement des PME",
             snippet="Statistiques sur les délais de paiement des PME et les créances clients."),
    ])

    choice = runtime._select_search_browse_candidate(
        structured, "délais de paiement PME France statistiques", selector="evidence_relevance")

    assert choice["url"] == "https://www.insee.fr/fr/statistiques/1234567"
    # Le selector ne priorise ni ne pénalise aucun hôte : il classe par recouvrement
    # lexical avec la requête, rien de plus (cf. docs/ARCHITECTURE_BOUNDARY.md).
    assert choice["rank"] == 2 and choice["score"] > 0
    assert "official" not in choice and "low_evidence" not in choice


def test_selector_keeps_a_parenthesised_url_intact(monkeypatch):
    monkeypatch.setattr(runtime, "_search_result_candidates_from_text", blow_up("parser texte legacy"))
    url = "https://example.org/offre/(42)?src=rss"
    structured = envelope([item(url, title="Offre de mission", snippet="Mission de déménagement à Clichy.")])

    choice = runtime._select_search_browse_candidate(structured, "mission déménagement", selector="first")

    assert choice["url"] == url


def test_selector_does_not_blacklist_business_ambiguous_items(monkeypatch):
    """Le selector ne juge pas la nature d'une source : c'est le travail du LLM.

    Un temps, le selector portait des listes (hôtes encyclopédiques, mots de bruit
    « film », « dictionnaire »). Elles sont supprimées : un résultat qui partage les
    mots de la requête reste sélectionnable, et son inutilité économique est jugée par
    le LLM au moment de conclure. La vraie défense est du côté des providers : la
    purpose `business_signal` ne complète jamais avec Wikipédia ou Google News
    (cf. test_business_purpose_stops_after_bing_web).
    """
    monkeypatch.setattr(runtime, "_search_result_candidates_from_text", blow_up("parser texte legacy"))
    structured = envelope([item("https://fr.wikipedia.org/wiki/Micro-entrepreneur",
                                title="Micro-entrepreneur — Wikipédia", snippet="Régime français.")])

    choice = runtime._select_search_browse_candidate(
        structured, "micro entrepreneur facturation", selector="business_signal_relevance")

    assert choice is not None
    assert choice["url"] == "https://fr.wikipedia.org/wiki/Micro-entrepreneur"

    # Mot jadis « ambigu » : « appel » au sens appel d'offres reste sélectionnable,
    # sans aucune liste lexicale pour le autoriser.
    ao = envelope([item("https://example.org/ao/(42)", title="Appel d'offres automatisation reporting",
                        snippet="Marché public d'automatisation du reporting data, budget annexé.")])
    choice = runtime._select_search_browse_candidate(
        ao, "appel d'offres automatisation reporting", selector="business_signal_relevance")
    assert choice is not None and choice["url"] == "https://example.org/ao/(42)"


def test_business_selector_rejects_wrapper_hosts_on_native_items(monkeypatch):
    """Propriété de l'outil, pas jugement métier : un hôte de redirection ne sert pas la page."""
    monkeypatch.setattr(runtime, "_search_result_candidates_from_text", blow_up("parser texte legacy"))
    structured = envelope([item("https://news.google.com/rss/articles/x", title="Actualité automatisation",
                                snippet="Les PME automatisent leurs factures et relances.")])

    choice = runtime._select_search_browse_candidate(
        structured, "automatisation factures PME", selector="business_signal_relevance")

    assert choice is None


def test_business_selector_enforces_an_explicit_site(monkeypatch):
    monkeypatch.setattr(runtime, "_search_result_candidates_from_text", blow_up("parser texte legacy"))
    structured = envelope([item("https://example.org/ailleurs", title="Ailleurs", snippet="Sans rapport.")])

    choice = runtime._select_search_browse_candidate(
        structured, "baromètre PME site:bpifrance.fr", selector="business_signal_relevance")

    assert choice is None


def test_selector_ignores_urls_written_only_in_snippets(monkeypatch):
    monkeypatch.setattr(runtime, "_search_result_candidates_from_text", blow_up("parser texte legacy"))
    structured = envelope([item("https://example.org/officiel", title="Officiel",
                                snippet="citation de https://invente.example.org/piège")])

    choice = runtime._select_search_browse_candidate(structured, "preuve officielle", selector="first")

    assert choice["url"] == "https://example.org/officiel"


def test_legacy_text_results_are_still_understood():
    legacy = ("- Définitions : problème - Dictionnaire Larousse\n"
              "  https://www.larousse.fr/dictionnaires/francais/probleme/64046\n"
              "  Définition générale du mot problème.\n"
              "- Délais de paiement des PME - INSEE\n"
              "  https://www.insee.fr/fr/statistiques/1234567\n"
              "  Statistiques sur les délais de paiement des PME.\n")

    choice = runtime._select_search_browse_candidate(
        legacy, "délais de paiement PME France statistiques", selector="evidence_relevance")

    assert choice["url"] == "https://www.insee.fr/fr/statistiques/1234567"


# --- 6. Runtime : l'enveloppe descend jusqu'aux étapes -----------------------------------

def test_step_result_data_keeps_the_six_fields(monkeypatch):
    monkeypatch.setattr(search, "search_envelope", lambda *a, **k: envelope([
        item("https://example.org/offre/(42)", title="Offre de mission", source="Bing Web",
             date="2026-09-22", snippet="Mission de déménagement à Clichy.", provider="bing_web"),
    ]))
    from agents import deepseek
    actions = iter([{"tool": "search", "args": {"query": "mission déménagement"}}, {"final": "ok"}])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    result = runtime.run_agent("SOUT", "chercher", max_steps=3, allowed_tools={"search"})
    step = result["steps"][0]

    assert step["result_data"]["items"][0] == {
        "title": "Offre de mission", "url": "https://example.org/offre/(42)", "source": "Bing Web",
        "date": "2026-09-22", "snippet": "Mission de déménagement à Clichy.", "provider": "bing_web",
    }
    assert step["result_urls"] == ["https://example.org/offre/(42)"]
    assert "https://example.org/offre/(42)" in step["result"]


def test_prompt_view_is_bounded_but_keeps_urls(monkeypatch):
    long_snippet = "contexte " * 4000
    monkeypatch.setattr(search, "search_envelope", lambda *a, **k: envelope([
        item("https://example.org/offre/(42)", snippet=long_snippet),
    ]))
    from agents import deepseek
    actions = iter([{"tool": "search", "args": {"query": "mission déménagement"}}, {"final": "ok"}])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    result = runtime.run_agent("SOUT", "chercher", max_steps=3, allowed_tools={"search"})
    step = result["steps"][0]

    assert len(step["result"]) < 2000
    assert "https://example.org/offre/(42)" in step["result"]
    assert len(step["result_data"]["items"][0]["snippet"]) == len(long_snippet)


def test_lockstep_browses_a_native_url(monkeypatch):
    monkeypatch.setattr(runtime, "_search_result_candidates_from_text", blow_up("parser texte legacy"))
    monkeypatch.setattr(search, "search_envelope", lambda *a, **k: envelope([
        item("https://example.org/offre/(42)", title="Mission de déménagement",
             snippet="Mission de déménagement à Clichy, devis demandé."),
    ]))
    seen = []
    monkeypatch.setitem(runtime.TOOLS["browse"], "fn",
                        lambda args: seen.append(args["url"]) or {"texte": "preuve " + "x" * 200})
    from agents import deepseek
    actions = iter([{"tool": "search", "args": {"query": "mission déménagement"}}])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    result = runtime.run_agent("SOUT", "chercher", max_steps=3, allowed_tools={"search", "browse"},
                               search_browse_lockstep=True)

    assert seen == ["https://example.org/offre/(42)"]
    assert result["steps"][1]["lockstep_forced"] is True


def test_search_failure_does_not_produce_urls(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("moteur indisponible vers https://example.org/piège")

    monkeypatch.setitem(runtime.TOOLS["search"], "fn", fail)
    from agents import deepseek
    actions = iter([{"tool": "search", "args": {"query": "preuve"}}, {"final": "échec"}])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    result = runtime.run_agent("SOUT", "chercher", max_steps=3, allowed_tools={"search"})

    assert result["steps"][0]["result_urls"] == []
    assert "moteur indisponible" in result["steps"][0]["result"]


def test_structured_search_still_reaches_the_evidence_gate(monkeypatch):
    """Chaîne complète : SEARCH structuré -> BROWSE imposé -> gate #94 inchangé.

    L'URL contient des parenthèses : elle doit arriver intacte jusqu'à la provenance.
    """
    url = "https://example.org/marche/(42)"
    page_text = ("La communauté de communes de Clichy recherche un prestataire pour la gestion "
                 "des encombrants. Le marché est estimé à 48 000 euros par an. " + ("contexte " * 60))
    monkeypatch.setattr(search, "search_envelope", lambda *a, **k: envelope([
        item(url, title="Marché de gestion des encombrants", snippet="Appel d'offres."),
    ]))
    monkeypatch.setitem(runtime.TOOLS["browse"], "fn", lambda args: {
        "url": args["url"], "source": "browse",
        "page": {"final_url": args["url"], "requested_url": args["url"], "title": "Marché public",
                 "main_text": page_text, "text_chars": len(page_text), "blocked": False,
                 "extraction_method": "http:html_main", "rendered": False, "http_status": 200,
                 "fetched_at": "2026-09-24T10:00:00Z", "error": None},
    })
    from agents import deepseek
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "trouver un signal d'affaires"}]},
        {"tool": "search", "args": {"query": "marché public encombrants"}},
        {"final": "signal trouvé"},
        {"rapport": "un marché public a été identifié", "business_signals": [{
            "signal_type": "procurement", "buyer": "Communauté de communes de Clichy",
            "pain": "gestion des encombrants sans outil de suivi",
            "money_signal": "marché estimé à 48 000 euros par an",
            "evidence_url": url,
            "evidence_summary": "recherche un prestataire pour la gestion",
            "buyer_evidence": "La communauté de communes de Clichy recherche un prestataire",
            "pain_evidence": "pour la gestion des encombrants",
            "money_evidence": "Le marché est estimé à 48 000 euros par an",
            "summary_evidence": "recherche un prestataire pour la gestion",
            "test_channel": "réponse à l'appel d'offres", "test_offer": "audit gratuit",
            "next_test": "répondre à l'appel d'offres"}]},
        # Revue d'actionnabilité du signal qualifié : appel LLM indépendant, APRÈS le gate.
        {"classification": "actionable_now", "justification": "appel d'offres ouvert"},
    ])
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: next(actions))

    out = runtime.run_mission("signaux", max_steps_per_agent=3, business="octopus",
                              allowed_tools={"search", "browse"}, search_browse_lockstep=True,
                              search_browse_selector="evidence_relevance",
                              business_signal_focus=True, business_signal_target=1)

    steps = out["results"][0]["steps"]
    assert [step["tool"] for step in steps] == ["search", "browse"]
    assert steps[0]["result_urls"] == [url]
    assert out["business_signal_rejections"] == []
    assert out["business_signals"][0]["evidence_acquisition"] == {
        "final_url": url, "fetched_at": "2026-09-24T10:00:00Z",
    }
    assert out["business_signals"][0]["action_fields_nature"] == "inferred"


# --- 7. Compatibilité des consommateurs historiques --------------------------------------

def test_web_search_still_renders_the_legacy_text(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Retards de paiement", "https://example.org/retards", "Bing Web",
                     snippet="Délais constatés."),
    ])

    out = search.web_search("retards paiement PME")

    assert out.startswith("Requête effective : retards paiement PME")
    assert "https://example.org/retards" in out
    assert "Fournisseurs de recherche : bing_web" in out


def test_search_items_returns_items_and_errors(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Offre", "https://example.org/o", snippet="preuve"),
    ])
    keyless.setattr(search, "_bing_news_items", lambda q, n: (_ for _ in ()).throw(TimeoutError("timeout")))

    items, errors = search.search_items("devis PME")

    assert [entry["url"] for entry in items] == ["https://example.org/o"]
    assert errors and "TimeoutError" in errors[0]


def test_browser_tool_search_still_returns_text(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Offre", "https://example.org/o", snippet="preuve"),
    ])

    out = browser.BrowserTool(persistent=False).search("devis PME")

    assert "https://example.org/o" in out


def test_legacy_sout_research_still_renders_text(monkeypatch, keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Offre", "https://example.org/o", snippet="preuve"),
    ])
    from agents import deepseek
    monkeypatch.setattr(deepseek, "call", lambda *a, **k: "synthèse")

    out = legacy_agents.SOUT.research("devis PME", max_results=3)

    assert out["topic"] == "devis PME"
    assert "https://example.org/o" in out["results"]
    assert out["insight"] == "synthèse"


def test_veille_collect_still_consumes_search_items(monkeypatch):
    from businesses.veille import brief
    monkeypatch.setattr(search, "search_items", lambda q, n: (
        [{"title": "Offre", "url": "https://example.org/o", "source": "Bing Web",
          "date": "2026-09-22", "snippet": "preuve", "provider": "bing_web"}],
        ["Wikipedia : Timeout"],
    ))

    out = brief.collect("devis PME", 3)

    assert out["query"] == "devis PME"
    assert out["errors"] == ["Wikipedia : Timeout"]
    assert out["sources"][0]["n"] == 1
    assert out["sources"][0]["url"] == "https://example.org/o"


def test_handoff_artifact_stays_bounded_and_keeps_urls():
    structured = envelope([item(f"https://example.org/offre/{i}", snippet="extrait " * 200)
                           for i in range(6)])

    view = runtime._tool_result_view("search", structured, max_chars=1200)

    assert len(view) <= 1200
    assert "https://example.org/offre/0" in view
    assert len(structured["items"]) == 6  # la vue n'a pas mangé la structure


def test_render_envelope_is_a_view_not_a_source():
    structured = envelope([item()])
    snapshot = copy.deepcopy(structured)

    view = search.render_envelope(structured)

    assert structured == snapshot
    assert isinstance(view, str) and "https://example.org/offre/1" in view


def test_render_envelope_tolerates_a_non_envelope():
    assert search.render_envelope("texte brut") == ""
    assert search.render_envelope(None) == ""


def test_render_envelope_never_swallows_a_diagnostic():
    """Une erreur mal typée reste visible : elle n'est pas confondue avec un marché vide."""
    structured = envelope([], errors="Bing Web : SSLError")

    view = search.render_envelope(structured)

    assert "Sources en erreur : Bing Web : SSLError" in view
    assert "Aucun résultat exploitable." in view


def test_envelope_is_json_serialisable_for_the_trace(keyless):
    keyless.setattr(search, "_bing_web_items", lambda q, n: [
        search._item("bing_web", "Offre — été", "https://example.org/offre/(42)?a=1",
                     "Bing Web", "2026-09-22", "Extrait accentué."),
    ])

    import json
    result = search.search_envelope("devis PME")

    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_structured_search_results_are_seen_by_record_observation(monkeypatch):
    """`_seen_this_session` lit la structure, pas une regex sur une vue texte."""
    monkeypatch.setattr(search, "search_envelope",
                        lambda *a, **k: envelope([item("https://example.org/offre/(42)")]))
    monkeypatch.setattr(runtime, "_search_result_urls", blow_up("extraction par regex"))

    with runtime._search_cache():
        runtime._search({"query": "mission déménagement"})
        assert runtime._seen_this_session("https://example.org/offre/(42)") is True
        assert runtime._seen_this_session("https://invente.example.org/jamais") is False
