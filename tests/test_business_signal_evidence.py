"""Offline adversarial regressions: acquired text is not semantic entailment."""
from copy import deepcopy

import pytest

from agents import runtime


URL = "https://example.org/requests?id=42"
TEXT = ("Cabinet comptable Acme. Relances de factures effectuées manuellement chaque semaine. "
        "Nous recrutons un prestataire pour automatiser les relances. "
        "Merci de répondre à cette demande de prestation.")


def evidence_case(requested=URL, final=URL, text=TEXT):
    page = {
        "requested_url": requested, "final_url": final, "main_text": text,
        "text_chars": len(text), "blocked": False, "error": None,
        "http_status": 200, "extraction_method": "http:html_main",
        "rendered": False, "fetched_at": "2026-09-24T12:00:00+00:00",
    }
    data = {"url": final, "page": page}
    step = {"tool": "browse", "args": {"url": requested}, "result_data": data,
            "browse_meta": runtime._browse_result_meta(data)}
    signal = {
        "signal_type": "explicit_request", "buyer": "Cabinet comptable Acme",
        "pain": "relances manuelles", "money_signal": "recherche de prestataire",
        "evidence_url": final, "evidence_summary": "demande d'automatisation des relances",
        "buyer_evidence": "Cabinet comptable Acme",
        "pain_evidence": "Relances de factures effectuées manuellement chaque semaine",
        "money_evidence": "Nous recrutons un prestataire pour automatiser les relances",
        "summary_evidence": "Nous recrutons un prestataire pour automatiser les relances",
        "test_channel": "réponse à la demande", "test_offer": "prototype supervisé",
        "next_test": "faire relire une proposition par un humain",
    }
    return signal, [{"role": "SOUT", "steps": [step]}]


@pytest.mark.parametrize("damage", [
    "no_meta", "no_data", "no_page", "blocked", "error", "short", "empty",
    "fake_length", "refused", "no_date", "no_method", "wrong_final", "wrong_requested",
    "http_error", "meta_error", "meta_blocked",
])
def test_acquisition_fails_closed(damage):
    signal, results = evidence_case()
    step = results[0]["steps"][0]
    page = step["result_data"]["page"]
    if damage == "no_meta":
        step.pop("browse_meta")
    elif damage == "no_data":
        step.pop("result_data")
    elif damage == "no_page":
        step["result_data"] = {"texte": TEXT, "url": URL}
    elif damage == "blocked":
        page["blocked"] = True
    elif damage == "error":
        page["error"] = "timeout"
    elif damage in {"short", "empty", "fake_length"}:
        page["main_text"] = "court" if damage != "empty" else ""
    elif damage == "refused":
        step["result_data"]["refused"] = True
    elif damage == "no_date":
        page.pop("fetched_at")
    elif damage == "no_method":
        page.pop("extraction_method")
    elif damage == "wrong_final":
        page["final_url"] = "https://example.org/other"
    elif damage == "wrong_requested":
        page["requested_url"] = "https://example.org/other"
    elif damage == "http_error":
        page["http_status"] = 500
    elif damage == "meta_error":
        step["browse_meta"]["error"] = "timeout"
    elif damage == "meta_blocked":
        step["browse_meta"]["blocked"] = True
    assert runtime._verified_browse_urls(results) == set()
    assert runtime._qualify_business_signals([signal], results)[0] == []


def test_real_loop_browse_exception_is_not_opened(monkeypatch):
    monkeypatch.setattr(runtime.deepseek, "call_json", lambda *a, **k: {
        "tool": "browse", "args": {"url": URL}})
    def fail(args):
        raise TimeoutError("audit timeout")
    monkeypatch.setitem(runtime.TOOLS["browse"], "fn", fail)
    result = runtime.run_agent("SOUT", "read", max_steps=1, allowed_tools={"browse"})
    assert "audit timeout" in result["steps"][0]["result"]
    assert runtime._verified_browse_urls([result]) == set()


def test_forbidden_browse_is_not_opened(monkeypatch):
    monkeypatch.setattr(runtime.deepseek, "call_json", lambda *a, **k: {
        "tool": "browse", "args": {"url": URL}})
    result = runtime.run_agent("SOUT", "read", max_steps=1, allowed_tools={"search"})
    assert runtime._verified_browse_urls([result]) == set()


@pytest.mark.parametrize("field", ["buyer", "pain", "money_signal", "evidence_summary"])
def test_generic_page_does_not_support_invented_facts(field):
    signal, results = evidence_case(text="Statistiques macroéconomiques générales. " * 10)
    signal[field] = "Information inventée 5000 EUR"
    assert runtime._qualify_business_signals([signal], results)[0] == []


@pytest.mark.parametrize("field", ["buyer_evidence", "pain_evidence", "money_evidence", "summary_evidence"])
@pytest.mark.parametrize("value", [None, [], {"quote": "inventé"}, "", "Extrait absent de la page"])
def test_missing_or_fabricated_quote_rejected(field, value):
    signal, results = evidence_case()
    signal[field] = value
    assert runtime._qualify_business_signals([signal], results)[0] == []


@pytest.mark.parametrize("kind,money", [
    ("job_demand", "Nous recrutons un salarié pour cette tâche"),
    ("procurement", "Appel d'offres pour une prestation de relance"),
    ("paid_alternative", "Nous payons déjà un abonnement à ce logiciel"),
])
def test_positive_without_numeric_budget(kind, money):
    signal, results = evidence_case(text=TEXT + " " + money)
    signal.update(signal_type=kind, money_evidence=money)
    accepted, rejected = runtime._qualify_business_signals([signal], results)
    assert not rejected
    assert len(accepted) == 1
    assert accepted[0]["action_fields_nature"] == "inferred"
    assert runtime._canonical_evidence_url(URL) in runtime._verified_browse_urls(results)


def test_redirect_and_tracking_alias_share_acquired_content():
    requested = "https://www.example.org/go/?utm_source=x"
    final = "https://example.org/requests?id=42"
    signal, results = evidence_case(requested, final)
    for url in (requested, final + "&utm_campaign=y#section"):
        signal["evidence_url"] = url
        assert len(runtime._qualify_business_signals([signal], results)[0]) == 1
    signal["evidence_url"] = "https://example.org/requests?id=43"
    assert runtime._qualify_business_signals([signal], results)[0] == []


def test_quotes_cannot_be_borrowed_from_another_url_or_capture():
    signal, first = evidence_case(text=TEXT.replace("Cabinet comptable Acme", "Autre entreprise"))
    _, second = evidence_case(text="Cabinet comptable Acme. " + "Texte générique sans demande. " * 10)
    assert runtime._qualify_business_signals([signal], first + second)[0] == []
    _, other = evidence_case(requested="https://example.org/other", final="https://example.org/other")
    assert runtime._qualify_business_signals([signal], first + other)[0] == []


def test_quote_normalization_and_deduplication():
    signal, results = evidence_case()
    signal["buyer_evidence"] = "CABINET\u00a0COMPTABLE   ACME"
    accepted, rejected = runtime._qualify_business_signals([signal, deepcopy(signal)], results)
    assert len(accepted) == 1
    assert rejected[0]["reasons"] == ["duplicate_signal"]


def test_empty_candidates_are_not_evidence_of_no_market():
    assert runtime._qualify_business_signals([], []) == ([], [])


def test_business_selector_counts_distinct_tokens():
    result = "- Reporting\n  https://example.org/a\n  Reporting simple"
    assert runtime._select_search_browse_candidate(
        result, "freelance data automatisation reporting", "business_signal_relevance") is None
    result += " automatisation"
    assert runtime._select_search_browse_candidate(
        result, "freelance data automatisation reporting", "business_signal_relevance") is not None


def test_business_selector_chooses_valid_runner_up():
    result = ("- Reporting\n  https://www.service-public.fr/a\n  Informations générales\n"
              "- Automatisation factures\n  https://example.org/jobs/1\n  Cherchons prestataire")
    choice = runtime._select_search_browse_candidate(
        result, "reporting automatisation factures", "business_signal_relevance")
    assert choice is not None and choice["url"] == "https://example.org/jobs/1"


def test_business_selector_keeps_legitimate_film_work():
    result = ("- Société de production cherche monteur freelance pour film publicitaire\n"
              "  https://example.org/jobs/film\n  Mission rémunérée de montage vidéo")
    assert runtime._select_search_browse_candidate(
        result, "monteur freelance film publicitaire", "business_signal_relevance") is not None


def test_redirect_aliases_do_not_duplicate_signal():
    signal, results = evidence_case(requested="https://example.org/go", final=URL)
    alias = {**signal, "evidence_url": "https://example.org/go"}
    accepted, rejected = runtime._qualify_business_signals([signal, alias], results)
    assert len(accepted) == 1
    assert rejected[0]["reasons"] == ["duplicate_signal"]
    assert accepted[0]["evidence_acquisition"] == {
        "final_url": URL, "fetched_at": "2026-09-24T12:00:00+00:00"}


@pytest.mark.parametrize("raw", [None, {}, "[]"])
def test_malformed_signal_list_is_explicitly_rejected(raw):
    assert runtime._qualify_business_signals(raw, [])[1] == [
        {"signal": raw, "reasons": ["signals_not_list"]}]


def test_literal_presence_is_not_semantic_entailment():
    # Limite intentionnelle : une citation réelle peut être mal interprétée.
    # Ne pas prétendre que le gate déterministe remplace la revue humaine.
    signal, results = evidence_case()
    signal["money_signal"] = "Interprétation non vérifiée de la citation"
    accepted, _ = runtime._qualify_business_signals([signal], results)
    assert len(accepted) == 1
    assert accepted[0]["money_evidence"] in TEXT


@pytest.mark.parametrize("field", ["buyer", "pain", "money_signal", "evidence_summary"])
def test_fact_fields_must_be_strings(field):
    signal, results = evidence_case()
    signal[field] = {"fake": "not a string"}
    assert runtime._qualify_business_signals([signal], results)[0] == []


def test_business_selector_site_constraint_and_ties():
    result = ("- Automatisation factures\n  https://other.org/1\n  Automatisation factures\n"
              "- Automatisation factures\n  https://example.org/2\n  Prestataire\n"
              "- Automatisation factures\n  https://example.org/3\n  Prestataire")
    choice = runtime._select_search_browse_candidate(
        result, "automatisation factures site:example.org", "business_signal_relevance")
    assert choice["url"] == "https://example.org/2"
    assert choice["distinct_overlap"] == 2


def test_business_selector_honours_explicit_site_even_for_a_dictionary():
    """Une contrainte `site:` explicite du LLM est respectée, jamais vetée par une liste d'hôtes.

    L'ancienne règle `_LOW_EVIDENCE_HOSTS` pouvait refuser d'ouvrir un domaine que l'agent
    avait explicitement demandé. La pertinence d'une page est jugée par le LLM ; le sélecteur
    ne garde que la contrainte de domaine, qui est un contrat d'outil.
    """
    result = ("- Automatisation factures\n  https://www.larousse.fr/dictionnaires/1\n"
              "  Automatisation factures")
    choice = runtime._select_search_browse_candidate(
        result, "automatisation factures site:larousse.fr", "business_signal_relevance")
    assert choice is not None
    assert choice["url"] == "https://www.larousse.fr/dictionnaires/1"
    assert choice["site_match"] is True


def test_business_selector_refuses_wrapper_hosts():
    """Un hôte de redirection ne sert jamais la page de l'éditeur : invariants de browse."""
    result = ("- Actualité automatisation factures\n  https://news.google.com/rss/articles/x\n"
              "  Les PME automatisent leurs factures")
    assert runtime._select_search_browse_candidate(
        result, "automatisation factures PME", "business_signal_relevance") is None


def test_gate_reads_full_acquisition_not_truncated_prompt():
    signal, results = evidence_case(text="Introduction sans preuve. " * 400 + TEXT)
    step = results[0]["steps"][0]
    step["result"] = runtime._tool_result_view("browse", step["result_data"])
    assert "Cabinet comptable Acme" not in step["result"]
    assert len(runtime._qualify_business_signals([signal], results)[0]) == 1


def test_native_http_acquisition_flows_to_gate(monkeypatch):
    from agents import browser
    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        encoding = "utf-8"
        def iter_content(self, chunk_size):
            yield f"<main>{TEXT}</main>".encode()
        def close(self):
            pass
    monkeypatch.setattr(browser.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(runtime.web_guard, "check", lambda *a, **k: runtime.web_guard.PUBLIC)
    monkeypatch.setattr(runtime.deepseek, "call_json", lambda *a, **k: {
        "tool": "browse", "args": {"url": URL}})
    result = runtime.run_agent("SOUT", "read", max_steps=1, allowed_tools={"browse"})
    signal, _ = evidence_case()
    accepted, rejected = runtime._qualify_business_signals([signal], [result])
    assert not rejected and len(accepted) == 1


def _as_tavily_acquisition(results, *, text=TEXT, requested=URL, final=URL, error=None):
    step = results[0]["steps"][0]
    page = step["result_data"]["page"]
    page.update({
        "requested_url": requested,
        "final_url": final,
        "main_text": text,
        "text_chars": len(text),
        "raw_chars": len(text),
        "content_type": "application/pdf",
        "extraction_method": "tavily:extract_basic",
        "blocked": False,
        "error": error,
        "http_status": 200,
    })
    step["args"] = {"url": requested}
    step["result_data"]["url"] = final
    step["browse_meta"] = runtime._browse_result_meta(step["result_data"])
    return step


def test_gate_94_accepts_literal_evidence_from_tavily_extract():
    signal, results = evidence_case()
    _as_tavily_acquisition(results)

    accepted, rejected = runtime._qualify_business_signals([signal], results)

    assert not rejected
    assert len(accepted) == 1
    assert accepted[0]["evidence_acquisition"] == {
        "final_url": URL, "fetched_at": "2026-09-24T12:00:00+00:00"}


def test_gate_94_rejects_tavily_quote_absent_from_acquisition_even_if_search_snippet_has_it():
    signal, results = evidence_case(text="Texte extrait sans les citations demandées. " * 10)
    _as_tavily_acquisition(results, text="Texte extrait sans les citations demandées. " * 10)
    search_step = {
        "tool": "search",
        "result": "Cabinet comptable Acme. Relances de factures effectuées manuellement chaque semaine. "
                  "Nous recrutons un prestataire pour automatiser les relances.",
        "result_urls": [URL],
    }
    results[0]["steps"].insert(0, search_step)

    accepted, rejected = runtime._qualify_business_signals([signal], results)

    assert accepted == []
    assert any("buyer_evidence_not_in_source" in item["reasons"] for item in rejected)


def test_gate_94_rejects_tavily_url_mismatch_and_failed_extraction():
    signal, mismatch_results = evidence_case()
    _as_tavily_acquisition(
        mismatch_results,
        text="Document PDF public détecté. Extraction textuelle indisponible.",
        error="document_pdf_text_unavailable: Tavily Extract failed: url_mismatch",
    )

    failed_signal, failed_results = evidence_case()
    _as_tavily_acquisition(
        failed_results,
        text="Document PDF public détecté. Extraction textuelle indisponible.",
        error="document_pdf_text_unavailable: Tavily Extract failed: failed_results",
    )

    assert runtime._qualify_business_signals([signal], mismatch_results)[0] == []
    assert runtime._qualify_business_signals([failed_signal], failed_results)[0] == []


# --- Contrat de sérialisation de la synthèse : une acquisition, citations continues ---

def _business_synthesis_system_prompt(monkeypatch):
    calls = []
    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "trouver une demande achetable"}]},
        {"final": "rien lu"},
        {"rapport": "aucun signal", "business_signals": []},
    ])

    def call_json(agent, task, model, messages, **kwargs):
        calls.append((task, messages))
        return next(actions)

    monkeypatch.setattr(runtime.deepseek, "call_json", call_json)
    runtime.run_mission("identifier une opportunité vendable", max_steps_per_agent=1,
                        business="octopus", allowed_tools={"browse"},
                        business_signal_focus=True, business_signal_target=1)
    return next(messages for task, messages in calls if task == "synthese")[0]["content"]


def test_synthesis_contract_binds_signal_to_one_acquisition(monkeypatch):
    prompt = _business_synthesis_system_prompt(monkeypatch)
    # La règle #94 existante est conservée, puis clarifiée.
    assert "citations exactes de 8 à 600 caractères du texte de cette même acquisition" in prompt
    assert "choisis exactement UNE acquisition réellement ouverte" in prompt
    assert "evidence_url identifie cette acquisition" in prompt
    assert "les quatre champs *_evidence proviennent tous de son texte" in prompt


def test_synthesis_contract_requires_one_continuous_literal_quote(monkeypatch):
    prompt = _business_synthesis_system_prompt(monkeypatch)
    assert "UNE SEULE sous-chaîne continue, copiée mot pour mot" in prompt
    assert "ne paraphrase pas" in prompt


def test_synthesis_contract_forbids_ellipsis_and_fragment_assembly(monkeypatch):
    prompt = _business_synthesis_system_prompt(monkeypatch)
    assert "ne concatène jamais plusieurs fragments" in prompt
    assert "n'insère jamais « ... » ni « … » pour les relier" in prompt


def test_synthesis_contract_uses_one_representation_for_html_and_pdf(monkeypatch):
    prompt = _business_synthesis_system_prompt(monkeypatch)
    assert "page HTML et PDF), n'en utilise qu'une seule, sans les mélanger" in prompt
    assert ("si le PDF contient les quatre preuves, evidence_url est l'URL exacte du PDF acquis "
            "et les quatre citations viennent du PDF") in prompt


# --- Gate #94 inchangé : ces cas restent rejetés, quelle que soit la consigne ---

def test_gate_94_still_rejects_composite_quote_joined_by_ellipsis():
    signal, results = evidence_case()
    for joiner in (" ... ", " … ", " "):
        signal["summary_evidence"] = ("Cabinet comptable Acme." + joiner
                                      + "Merci de répondre à cette demande de prestation.")
        accepted, rejected = runtime._qualify_business_signals([signal], results)
        assert accepted == []
        assert "summary_evidence_not_in_source" in rejected[0]["reasons"]


def test_gate_94_still_rejects_quotes_split_between_html_and_pdf_acquisitions():
    html_url, pdf_url = "https://example.org/avis/42", "https://example.org/avis/42.pdf"
    signal, html = evidence_case(html_url, html_url,
                                 text="Cabinet comptable Acme. Avis de marché publié. " * 3)
    pdf_text = TEXT.replace("Cabinet comptable Acme. ", "")
    _, pdf = evidence_case()
    _as_tavily_acquisition(pdf, text=pdf_text, requested=pdf_url, final=pdf_url)
    for url in (html_url, pdf_url):
        signal["evidence_url"] = url
        accepted, rejected = runtime._qualify_business_signals([signal], html + pdf)
        assert accepted == []
        assert "quotes_not_in_same_acquisition" in rejected[0]["reasons"]
    # Positif de contrôle : les quatre citations dans le seul PDF, evidence_url = PDF.
    _, pdf_full = evidence_case()
    _as_tavily_acquisition(pdf_full, requested=pdf_url, final=pdf_url)
    signal["evidence_url"] = pdf_url
    assert len(runtime._qualify_business_signals([signal], html + pdf_full)[0]) == 1


def test_gate_94_still_rejects_absent_quote():
    signal, results = evidence_case()
    signal["money_evidence"] = "Budget de 150 000 € HT par an"
    accepted, rejected = runtime._qualify_business_signals([signal], results)
    assert accepted == []
    assert rejected
