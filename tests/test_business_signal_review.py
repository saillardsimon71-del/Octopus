"""Revue d'actionnabilité : couche de MESURE posée APRÈS le gate #94, jamais devant.

Ces tests prouvent que :
- le reviewer est un appel LLM indépendant de la synthèse qui a produit le signal
  (contexte borné au signal + sa preuve source, sortie structurée validée) ;
- actionable_business_signal_count ne compte que les actionable_now ;
- une panne du reviewer est fail-open : mission et résultats structurels #94 intacts,
  revue marquée degraded, aucune classification inventée ;
- la revue n'ajoute aucun SEARCH/BROWSE ;
- le gate #94 et ses sorties restent inchangés par la couche de revue.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy

import pytest

from agents import deepseek, runtime, search
from octopus import llm


URL = "https://example.org/requests?id=42"
TEXT = ("Cabinet comptable Acme. Relances de factures effectuées manuellement chaque semaine. "
        "Nous recrutons un prestataire pour automatiser les relances. "
        "Merci de répondre à cette demande de prestation.")


def evidence_step(requested=URL, final=URL, text=TEXT,
                  fetched_at="2026-09-24T12:00:00+00:00"):
    """Une acquisition browse vérifiée #94 (même forme que test_business_signal_evidence)."""
    page = {
        "requested_url": requested, "final_url": final, "main_text": text,
        "text_chars": len(text), "blocked": False, "error": None,
        "http_status": 200, "extraction_method": "http:html_main",
        "rendered": False, "fetched_at": fetched_at,
    }
    data = {"url": final, "page": page}
    return {"tool": "browse", "args": {"url": requested}, "result_data": data,
            "browse_meta": runtime._browse_result_meta(data)}


def signal_for(url=URL, buyer="Cabinet comptable Acme", pain="relances manuelles",
               pain_quote="Relances de factures effectuées manuellement chaque semaine",
               money_quote="Nous recrutons un prestataire pour automatiser les relances"):
    return {
        "signal_type": "explicit_request",
        "buyer": buyer,
        "pain": pain,
        "money_signal": "recherche de prestataire",
        "evidence_url": url,
        "evidence_summary": "demande d'automatisation des relances",
        "buyer_evidence": buyer,
        "pain_evidence": pain_quote,
        "money_evidence": money_quote,
        "summary_evidence": money_quote,
        "test_channel": "réponse à la demande",
        "test_offer": "prototype supervisé",
        "next_test": "faire relire une proposition par un humain",
    }


RAPPORT_TOKEN = "RAPPORT_DE_SYNTHESE_JAMAIS_TRANSFERE"
GOAL = "OBJECTIF_UNIQUE_DE_MISSION invisible pour le reviewer"


def _run_focused_mission(monkeypatch, raw_signals, steps, verdicts, *, apply_validate=False):
    """Mission focus minimale : plan -> sous-agent aux acquisitions figées -> synthèse -> revues.

    `verdicts` est une file : chaque élément est soit le dict renvoyé par le reviewer,
    soit une exception levée par l'appel LLM. Tous les appels sont enregistrés dans `calls`.
    """
    verdicts = iter(verdicts)
    calls = []

    def call_json(agent, task, model, messages, **kwargs):
        calls.append({"agent": agent, "task": task, "model": model,
                      "messages": deepcopy(messages), "kwargs": kwargs})
        if task == "planification":
            return {"tasks": [{"role": "SOUT", "task": "collecter des preuves"}]}
        if task == "synthese":
            return {"rapport": RAPPORT_TOKEN, "business_signals": deepcopy(raw_signals)}
        if task == "revue_signal":
            outcome = next(verdicts)
            if isinstance(outcome, BaseException):
                raise outcome
            if apply_validate and kwargs.get("validate") is not None:
                # Émule le chemin réel : la passerelle applique le validateur puis
                # convertit un rejet en InvalidOutput (une GatewayError).
                try:
                    return kwargs["validate"](outcome)
                except ValueError as exc:
                    raise llm.InvalidOutput(str(exc))
            return outcome
        raise AssertionError(f"appel LLM inattendu : {agent}/{task}")

    def run_agent(role, goal, max_steps=10, **kwargs):
        return {"role": role, "task": goal, "final": "collecte terminée",
                "steps": deepcopy(steps)}

    monkeypatch.setattr(runtime.deepseek, "call_json", call_json)
    monkeypatch.setattr(runtime, "run_agent", run_agent)
    out = runtime.run_mission(GOAL, max_steps_per_agent=3, business="octopus",
                              allowed_tools={"search", "browse"},
                              business_signal_focus=True, business_signal_target=1)
    return out, calls


def _review_calls(calls):
    return [call for call in calls if call["task"] == "revue_signal"]


# --- Sortie structurée du reviewer ------------------------------------------------------

@pytest.mark.parametrize("classification", list(runtime._BUSINESS_SIGNAL_REVIEW_CLASSES))
def test_review_output_accepts_exactly_the_five_classes(classification):
    verdict = runtime._validate_business_signal_review({
        "classification": classification,
        "justification": "preuve lue dans la page",
        # Le reviewer ne doit pas annexer de champs à la mesure.
        "signal_corrige": {"buyer": "inventé"},
    })
    assert verdict == {"classification": classification,
                       "justification": "preuve lue dans la page"}


@pytest.mark.parametrize("data", [
    {"classification": "definitely_actionable", "justification": "hors vocabulaire"},
    {"classification": "Actionable_Now", "justification": "casse différente"},
    {"classification": "", "justification": "vide"},
    {"justification": "pas de classification"},
    {"classification": "uncertain"},
    {"classification": "uncertain", "justification": "   "},
    {"classification": None, "justification": "nul"},
    "actionable_now",
    ["actionable_now", "justification"],
    None,
])
def test_review_output_rejects_anything_outside_the_contract(data):
    with pytest.raises(ValueError):
        runtime._validate_business_signal_review(data)


# --- Reviewer indépendant de la synthèse ---------------------------------------------------

def test_reviewer_call_is_independent_from_the_synthesis(monkeypatch):
    raw = signal_for()
    out, calls = _run_focused_mission(
        monkeypatch, [raw], [evidence_step()],
        [{"classification": "actionable_now", "justification": "demande publiée non échue"}])

    assert len(out["business_signals"]) == 1
    order = [call["task"] for call in calls]
    assert order.index("revue_signal") > order.index("synthese") > order.index("planification")
    assert len(_review_calls(calls)) == 1

    review = _review_calls(calls)[0]
    assert review["agent"] == "REVUE"  # appel distinct de la synthèse ("ORBIT"/"synthese")
    assert review["kwargs"].get("validate") is runtime._validate_business_signal_review
    system, user = review["messages"][0]["content"], review["messages"][1]["content"]

    # Instruction conceptuelle minimale + contrat de sortie structuré, rien de plus.
    assert "Voici un signal économique proposé et sa preuve source" in system
    assert "Évalue indépendamment" in system
    assert "Ne complète aucune information manquante et ne corrige pas le signal" in system
    for classification in runtime._BUSINESS_SIGNAL_REVIEW_CLASSES:
        assert classification in system
    assert "justification courte" in system
    assert "MODE BUSINESS SIGNAL" not in system  # pas le contrat de la synthèse

    # Le reviewer reçoit le signal proposé et le texte de l'acquisition correspondante.
    payload = json.loads(user)
    assert payload["signal_propose"]["buyer"] == "Cabinet comptable Acme"
    assert payload["signal_propose"]["money_evidence"] in TEXT
    assert "nous recrutons un prestataire" in payload["preuve_source"]

    # Ni l'objectif de mission, ni le rapport de synthèse ne fuient vers le reviewer.
    for content in (system, user):
        assert RAPPORT_TOKEN not in content
        assert "OBJECTIF_UNIQUE_DE_MISSION" not in content


def test_reviewer_reads_the_acquisition_of_its_own_signal(monkeypatch):
    other_url = "https://example.org/autre-page"
    other_text = "Entreprise Zeta. Publie une offre pour automatiser ses devis manuels. " * 3
    raw_b = signal_for(url=other_url, buyer="Entreprise Zeta",
                       pain="devis manuels", pain_quote="devis manuels",
                       money_quote="Publie une offre pour automatiser ses devis manuels")
    steps = [evidence_step(), evidence_step(other_url, other_url, other_text)]
    verdicts = [{"classification": "uncertain", "justification": "a"},
                {"classification": "market_evidence", "justification": "b"}]

    out, calls = _run_focused_mission(monkeypatch, [signal_for(), raw_b], steps, verdicts)
    assert len(out["business_signals"]) == 2
    reviews = _review_calls(calls)
    assert len(reviews) == 2
    first = json.loads(reviews[0]["messages"][1]["content"])
    second = json.loads(reviews[1]["messages"][1]["content"])
    assert first["signal_propose"]["evidence_url"] == URL
    assert second["signal_propose"]["evidence_url"] == other_url
    assert "entreprise zeta" in second["preuve_source"]
    assert "entreprise zeta" not in first["preuve_source"]
    assert "cabinet comptable acme" not in second["preuve_source"]


# --- Compteur actionable_now ---------------------------------------------------------------

def test_actionable_count_only_counts_actionable_now(monkeypatch):
    text = (
        "Cabinet Alpha : relances manuelles chaque semaine. Nous recrutons un prestataire H. "
        "Cabinet Beta : saisie des devis a la main. Budget prevu pour un outil I. "
        "Cabinet Gamma : appel d'offres facturation publie. Depot deja cloture J. "
    ) * 2
    raw = [
        signal_for(buyer="Cabinet Alpha", pain="relances manuelles",
                   pain_quote="relances manuelles chaque semaine",
                   money_quote="Nous recrutons un prestataire H"),
        signal_for(buyer="Cabinet Beta", pain="saisie des devis",
                   pain_quote="saisie des devis a la main",
                   money_quote="Budget prevu pour un outil I"),
        signal_for(buyer="Cabinet Gamma", pain="reponse a un appel d'offres",
                   pain_quote="appel d'offres facturation publie",
                   money_quote="Depot deja cloture J"),
    ]
    decisions = []
    monkeypatch.setattr(runtime.db, "decide",
                        lambda agent, decision, payload=None: decisions.append(payload))
    out, calls = _run_focused_mission(
        monkeypatch, raw, [evidence_step(text=text)],
        [{"classification": "actionable_now", "justification": "recrutement en cours"},
         {"classification": "market_evidence", "justification": "budget sans demande ouverte"},
         {"classification": "historical_or_closed", "justification": "dépôt déjà clôturé"}])

    assert len(_review_calls(calls)) == 3
    assert len(out["business_signals"]) == 3
    assert [r["classification"] for r in out["business_signal_reviews"]] == [
        "actionable_now", "market_evidence", "historical_or_closed"]
    assert out["actionable_business_signal_count"] == 1
    assert out["business_signal_review_status"] == "reviewed"

    done = next(p for p in decisions if p and p.get("synthesis_status") == "validated")
    assert done["business_signal_count"] == 3  # comptage structurel #94 inchangé
    assert done["actionable_business_signal_count"] == 1
    assert done["business_signal_review_status"] == "reviewed"


# --- Panne reviewer : fail-open sur les résultats structurels -------------------------------

def test_reviewer_failure_is_fail_open_on_structural_results(monkeypatch):
    raw = signal_for()
    steps = [evidence_step()]
    out, _calls = _run_focused_mission(
        monkeypatch, [raw], steps, [llm.GatewayError("passerelle indisponible")])

    # La mission n'échoue pas et les résultats structurels #94 restent disponibles.
    assert out["synthesis_status"] == "validated"
    expected_accepted, expected_rejected = runtime._qualify_business_signals(
        [deepcopy(raw)], [{"role": "SOUT", "steps": deepcopy(steps)}])
    assert out["business_signals"] == expected_accepted
    assert out["business_signal_rejections"] == expected_rejected

    # La revue est marquée degraded et aucune classification n'est inventée.
    assert out["business_signal_reviews"] == [{
        "signal_index": 0,
        "evidence_url": URL,
        "status": "degraded",
        "classification": None,
        "justification": None,
        "error": "GatewayError: passerelle indisponible",
    }]
    assert out["actionable_business_signal_count"] == 0
    assert out["business_signal_review_status"] == "degraded"


def test_reviewer_output_outside_contract_degrades_without_inventing(monkeypatch):
    # Le validateur rejette la classification hors vocabulaire du LLM : revue degraded,
    # classification nulle, et les résultats structurels restent intacts.
    raw = signal_for()
    out, _calls = _run_focused_mission(
        monkeypatch, [raw], [evidence_step()],
        [{"classification": "buy_this_now", "justification": "classe inventée par le LLM"}],
        apply_validate=True)
    assert out["synthesis_status"] == "validated"
    assert len(out["business_signals"]) == 1
    review = out["business_signal_reviews"][0]
    assert review["status"] == "degraded"
    assert review["classification"] is None
    assert "InvalidOutput" in review["error"]
    assert out["actionable_business_signal_count"] == 0
    assert out["business_signal_review_status"] == "degraded"


def test_partial_reviewer_failure_degrades_only_the_failed_review(monkeypatch):
    other_url = "https://example.org/autre-page"
    other_text = "Entreprise Zeta. Publie une offre pour automatiser ses devis manuels. " * 3
    raw = [signal_for(),
           signal_for(url=other_url, buyer="Entreprise Zeta",
                      pain="devis manuels", pain_quote="devis manuels",
                      money_quote="Publie une offre pour automatiser ses devis manuels")]
    steps = [evidence_step(), evidence_step(other_url, other_url, other_text)]
    out, _calls = _run_focused_mission(
        monkeypatch, raw, steps,
        [llm.GatewayError("quota épuisé"),
         {"classification": "actionable_now", "justification": "offre publiée ce mois"}])

    assert len(out["business_signals"]) == 2  # le gate n'est pas affecté
    statuses = [(r["status"], r["classification"]) for r in out["business_signal_reviews"]]
    assert statuses == [("degraded", None), ("reviewed", "actionable_now")]
    assert out["actionable_business_signal_count"] == 1
    assert out["business_signal_review_status"] == "degraded"


def test_no_review_call_without_qualified_signals(monkeypatch):
    rejected_signal = signal_for(money_quote="Citation absente de la page acquise")
    out, calls = _run_focused_mission(monkeypatch, [rejected_signal], [evidence_step()], [])
    assert _review_calls(calls) == []  # aucun appel reviewer sans signal valide
    assert out["business_signals"] == []
    assert out["business_signal_reviews"] == []
    assert out["actionable_business_signal_count"] == 0
    assert out["business_signal_review_status"] == "no_signals"


# --- Aucun SEARCH/BROWSE supplémentaire ------------------------------------------------------

def _envelope(items=(), **fields):
    base = {"query": "prestataire relances", "effective_query": "prestataire relances",
            "purpose": search.SEARCH_PURPOSE_BUSINESS, "items": list(items), "errors": []}
    base.update(fields)
    return base


def test_review_adds_no_search_or_browse_call(monkeypatch):
    """Boucle réelle (run_agent non mocké) : la revue réutilise l'acquisition stockée."""
    counts = {"search": 0, "browse": 0}

    def search_envelope(*args, **kwargs):
        counts["search"] += 1
        return _envelope([{"title": "Demande de prestataire", "url": URL,
                           "source": "Source", "date": "2026-09-24",
                           "snippet": "Nous recrutons un prestataire.",
                           "provider": "bing_web"}])

    def browse_fn(args):
        counts["browse"] += 1
        return deepcopy(evidence_step(args["url"], args["url"]))["result_data"]

    monkeypatch.setattr(search, "search_envelope", search_envelope)
    monkeypatch.setitem(runtime.TOOLS["browse"], "fn", browse_fn)

    actions = iter([
        {"tasks": [{"role": "SOUT", "task": "trouver une demande achetable"}]},
        {"tool": "search", "args": {"query": "prestataire relances factures"}},
        {"tool": "browse", "args": {"url": URL}},
        {"final": "demande lue"},
        {"rapport": "un signal", "business_signals": [signal_for()]},
        {"classification": "actionable_now", "justification": "prestataire recherché"},
    ])
    tasks_called = []

    def call_json(agent, task, model, messages, **kwargs):
        tasks_called.append(task)
        return next(actions)

    monkeypatch.setattr(runtime.deepseek, "call_json", call_json)

    out = runtime.run_mission("trouver une opportunité", max_steps_per_agent=4,
                              business="octopus", allowed_tools={"search", "browse"},
                              business_signal_focus=True, business_signal_target=1)

    assert "revue_signal" in tasks_called  # la revue a bien eu lieu…
    assert counts == {"search": 1, "browse": 1}  # …sans aucune acquisition supplémentaire
    assert len(out["business_signals"]) == 1
    assert out["actionable_business_signal_count"] == 1


# --- Gate #94 inchangé ------------------------------------------------------------------------

def test_review_layer_leaves_gate_output_identical(monkeypatch):
    raw_ok = signal_for()
    raw_ko = signal_for(buyer="Cabinet rejeté car citation absente",
                        money_quote="Citation absente de la page")
    steps = [evidence_step()]
    out, _calls = _run_focused_mission(
        monkeypatch, [raw_ok, raw_ko], steps,
        [{"classification": "market_evidence", "justification": "marché démontré"}])

    expected_accepted, expected_rejected = runtime._qualify_business_signals(
        [deepcopy(raw_ok), deepcopy(raw_ko)], [{"role": "SOUT", "steps": deepcopy(steps)}])
    assert out["business_signals"] == expected_accepted
    assert out["business_signal_rejections"] == expected_rejected
    # La revue est une liste parallèle : les signaux eux-mêmes ne portent aucune trace
    # de la mesure (ni classification ni justification stockées dedans).
    for signal in out["business_signals"]:
        assert set(signal) == set(expected_accepted[0])
        assert "classification" not in signal
        assert "review" not in signal


def test_qualify_business_signals_source_is_pinned_byte_for_byte():
    # Cette PR exige que _qualify_business_signals reste strictement inchangé :
    # toute modification future du gate #94 devra mettre à jour cette empreinte en
    # connaissance de cause. Le comportement reste couvert par test_business_signal_evidence.
    digest = hashlib.sha256(
        inspect.getsource(runtime._qualify_business_signals).encode("utf-8")).hexdigest()
    assert digest == "c512f17f87d8e685eb5852fadb5c0833bddf7cb74c021157a7a831f89f4b9ec9"


# --- Exposition côté handler (tâche orbit.mission) ---------------------------------------------

def _run_orbit_mission_task(monkeypatch, mission_result):
    from agents import task_handlers  # noqa: F401
    from octopus import worker

    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: deepcopy(mission_result))
    worker.enqueue("atelier_revue", "orbit.mission", {
        "goal": "mesurer l'actionnabilité",
        "business_signal_focus": True,
        "business_signal_target": 1,
    })
    return worker.run_one("w", kinds=["orbit.mission"], log=lambda s: None)


def _structural_mission_result(**overrides):
    result = {
        "plan": [{"role": "SOUT", "task": "collecter"}],
        "results": [],
        "rapport": "un signal structurel",
        "synthesis_status": "validated",
        "business_signals": [signal_for()],
        "business_signal_rejections": [],
        "business_signal_reviews": [{
            "signal_index": 0,
            "evidence_url": URL,
            "status": "reviewed",
            "classification": "actionable_now",
            "justification": "demande non échue",
        }],
        "actionable_business_signal_count": 1,
        "business_signal_review_status": "reviewed",
    }
    result.update(overrides)
    return result


def test_mission_handler_exposes_review_metrics_separately(monkeypatch):
    done = _run_orbit_mission_task(monkeypatch, _structural_mission_result())

    # Le bloc structurel #94 conserve exactement sa métrique historique.
    assert done["output"]["business_signal_result"]["metric"] == "qualified_business_signal_count"
    assert done["output"]["business_signal_result"]["observed"] == 1
    assert done["output"]["business_signal_result"]["evaluation_status"] == "evaluated"
    # La mesure d'actionnabilité est exposée séparément, sans modifier les signaux.
    assert done["output"]["business_signals"] == [signal_for()]
    assert done["output"]["business_signal_reviews"] == _structural_mission_result()[
        "business_signal_reviews"]
    assert done["output"]["actionable_business_signal_count"] == 1
    assert done["output"]["business_signal_review_status"] == "reviewed"


def test_mission_handler_reports_a_degraded_review_without_inventing(monkeypatch):
    degraded = _structural_mission_result(
        business_signal_reviews=[{
            "signal_index": 0,
            "evidence_url": URL,
            "status": "degraded",
            "classification": None,
            "justification": None,
            "error": "GatewayError: panne",
        }],
        actionable_business_signal_count=0,
        business_signal_review_status="degraded",
    )
    done = _run_orbit_mission_task(monkeypatch, degraded)

    assert done["output"]["business_signal_result"]["observed"] == 1  # gate intact
    assert done["output"]["business_signal_review_status"] == "degraded"
    assert done["output"]["actionable_business_signal_count"] == 0
    assert done["output"]["business_signal_reviews"][0]["classification"] is None


def test_mission_handler_marks_review_unavailable_when_synthesis_degraded(monkeypatch):
    unavailable = _structural_mission_result(
        rapport="Synthèse LLM indisponible.",
        synthesis_status="degraded",
        synthesis_error="NoEligibleModel: test",
    )
    # Chemin runtime dégradé : aucune clé business signal n'est produite du tout.
    for key in ("business_signals", "business_signal_rejections", "business_signal_reviews",
                "actionable_business_signal_count", "business_signal_review_status"):
        del unavailable[key]
    done = _run_orbit_mission_task(monkeypatch, unavailable)

    assert done["output"]["synthesis_status"] == "degraded"
    assert done["output"]["business_signal_result"]["evaluation_status"] == "unavailable"
    assert done["output"]["business_signal_reviews"] == []
    assert done["output"]["actionable_business_signal_count"] == 0
    assert done["output"]["business_signal_review_status"] == "unavailable"
