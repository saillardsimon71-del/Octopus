"""Real supervisor/runtime/gateway; provider and public pages simulated, no intelligence score."""
import copy
import ipaddress
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agents import browser, deepseek, runtime, search, web_guard
from agents.gui.workbench_v2_data import read_snapshot
from octopus import actions, economy, journal, llm, strategy, supervisor, tasks
from octopus import strategy_separation as separation
from octopus.pricing import Usage

API = "API-cost monitoring"
EXHAUSTED = "API-cost : offres gratuites observées ; recherches répétitives, information marginale faible ; achat inconnu, pas réfutée."
SOURCES = {
    "service": ("Studio professionnel", "demande de contenus personnalisés", "rémunère cette prestation à 80 EUR"),
    "audience": ("Programme marchand", "lecteurs comparant des équipements", "commission de 0.20 EUR proposée par vente attribuée"),
    "commerce": ("Acheteurs spécialisés", "recherchent des pièces à commander", "prix affiché de 24 EUR par pièce"),
}


def source_payload(lane):
    actor, activity, money = SOURCES[lane]
    url = f"https://fixture.example/{lane}"
    text = f"{actor}. {activity}. {money}. Source simulée : ni commande, ni encaissement, ni rentabilité démontrés."
    page = {"requested_url": url, "final_url": url, "main_text": text, "text_chars": len(text),
            "blocked": False, "error": None, "http_status": 200, "extraction_method": "http:html_main",
            "rendered": False, "fetched_at": "2026-10-02T10:00:00+00:00"}
    return {"url": url, "texte": text, "page": page}


def signal(lane):
    actor, activity, money = SOURCES[lane]
    return {"signal_type": "monetization" if lane == "audience" else "explicit_request",
            "buyer": actor, "pain": activity, "money_signal": money,
            "evidence_url": source_payload(lane)["url"], "evidence_summary": "Source de monétisation à étudier",
            "buyer_evidence": actor, "pain_evidence": activity, "money_evidence": money,
            "summary_evidence": money, "test_channel": "canal conceptuel, disponibilité à vérifier",
            "test_offer": "hypothèse d'offre minimale", "next_test": "observer coûts et demande indépendante"}


def proposals():
    return [
        {"statement": "Démonstration personnalisée avant contact professionnel", "economic_rank": 1,
         "economic_criteria": ["cash_received", "margin"], "required_capabilities": ["email_send"],
         "economic_justification": "Faisable en principe ; coûts et conversion inconnus"},
        {"statement": "Actifs éditoriaux automatisés à faible revenu unitaire", "economic_rank": 2,
         "economic_criteria": ["cash_received", "margin"], "required_capabilities": ["publish_content"],
         "economic_justification": "Volume × automatisation ; coûts marginaux IA/distribution inconnus, profit non démontré"},
    ]


def determination(action="pause", goal="", reason="Aucune option encore suffisamment convaincante"):
    return {"action": action, "next_goal": goal, "reason": reason, "permission": ""}


class Script:
    """Fakes transport only; plans, collection, validators, reviews and costs remain real code."""
    def __init__(self, monkeypatch, transport, *, single_plan=False, cycle=False, empty=False, crash=False):
        self.calls, self.acquisitions, self.searches, self.states = [], [], [], []
        self.agent_steps = {}
        self.mission = 0
        self.intent = None
        self.single_plan, self.cycle, self.empty, self.crash = single_plan, cycle, empty, crash
        self.interrupted = False
        if crash:
            call = deepseek.call_json
            def interrupt(agent, stage, model, messages, **kwargs):
                if stage == "determination" and not self.interrupted:
                    self.interrupted = True
                    raise InterruptedBeforeSynthesis()  # Avant toute soumission provider.
                return call(agent, stage, model, messages, **kwargs)
            monkeypatch.setattr(deepseek, "call_json", interrupt)
        monkeypatch.setitem(runtime.TOOLS, "browse", {**runtime.TOOLS["browse"], "fn": self.browse})
        monkeypatch.setitem(runtime.TOOLS, "search", {**runtime.TOOLS["search"], "fn": self.lookup})
        monkeypatch.setattr(supervisor, "pursuit_strategy_effect", lambda *_: pytest.fail("Strategy execution in discovery"))
        transport.handler = self.respond

    def browse(self, args):
        lane = args["url"].rsplit("/", 1)[-1]
        self.acquisitions.append(lane)
        return source_payload(lane)

    def lookup(self, args):
        self.searches.append((args["query"], runtime._SEARCH_PURPOSE.get()))
        return "Source simulée à acquérir ; aucun revenu observé"

    def respond(self, provider, request):
        limit, messages = request["max_tokens"], request["messages"]
        self.calls.append(copy.deepcopy(request))
        if limit == 700:
            feedback = len(messages) > 2
            if not feedback:
                self.mission += 1
                state = json.loads(messages[1]["content"].rsplit("\n", 1)[-1])
                self.intent = state.get("intention", "discovery")
                self.states.append(state)
            lanes = ["service", "audience"] if self.intent == "discovery" else ["service"]
            if self.single_plan and not feedback:
                lanes = lanes[:1]
            answer = {"tasks": [{"role": "SOUT", "task": f"{self.mission}:{lane} — observer une voie économique indépendante"}
                                for lane in lanes]}
        elif limit == 500:
            tag = messages[1]["content"].splitlines()[0].split("Objectif : ")[-1].split(" — ")[0]
            lane = tag.split(":")[-1]
            index = self.agent_steps.get(tag, 0)
            self.agent_steps[tag] = index + 1
            answer = ({"tool": "search", "args": {"query": f"monétisation {lane}"}} if index == 0 else
                      {"tool": "browse", "args": {"url": source_payload(lane)["url"]}} if index == 1 else
                      {"final": "Source acquise, interprétation et profit restent incertains"})
        elif limit == 4000:
            answer = {"rapport": "Sources simulées distinctes ; aucun cash observé",
                      "determination": determination()}
            if self.intent == "discovery":
                answer["business_signals"] = [] if self.empty else [signal("service"), signal("audience")]
                if not self.empty:
                    answer["determination"]["strategies"] = proposals()
            if self.cycle and self.mission == 1:
                answer["determination"] = {**determination("continue", "Observer une preuve d'achat sur la piste sélectionnée"),
                                           "strategies": proposals(), "intent": "validation"}
            if self.cycle and self.mission == 2:
                answer["determination"] = determination(reason="Fenêtre locale épuisée ; information marginale faible")
        elif limit == 1000:
            answer = {"classification": "market_evidence", "justification": "Interprétation à vérifier ; aucun achat constaté"}
        else:
            raise AssertionError(f"Unexpected output limit {limit}")
        text = json.dumps(answer, ensure_ascii=False)
        usage = Usage(prompt_tokens=max(1, sum(len(m["content"]) for m in messages)//4),
                      completion_tokens=max(1, len(text)//4))
        assert usage.completion_tokens <= limit
        return llm.TransportResult(text, usage, request["model"], resolved_provider="OfflineFake", provider_cost_usd=.001)


class InterruptedBeforeSynthesis(BaseException):
    pass


def no_effects():
    for table in ("channel_actions", "spend_requests", "ledger_entries"):
        assert not journal.query(f"SELECT id FROM {table}")
    assert tasks.pending_human_requests("octopus") == []
    assert not journal.query("SELECT id FROM tasks WHERE kind='capability.acquire'")


def seed_anchor(monkeypatch):
    raw = {"rapport": (API + " : outils gratuits/open-source, achat inconnu. ")*35,
           "plan": [], "results": [{"steps": [{"tool": "browser_navigate", "result":
                     (API + " : alternatives gratuites sans demande d'achat. ")*25} for _ in range(12)]}],
           "execution_status": "completed", "synthesis_status": "validated",
           "determination": {**determination(reason=EXHAUSTED), "strategies": [
               {"statement": API, "economic_rank": 1, "economic_criteria": ["cash_received"],
                "economic_justification": EXHAUSTED, "required_capabilities": ["phone_call"]}]}}
    with monkeypatch.context() as scoped:
        scoped.setattr(runtime, "run_mission", lambda *a, **k: copy.deepcopy(raw))
        oid = supervisor.start_pursuit()
        supervisor.run_pursuit(oid)
    return oid, supervisor.work_tasks("octopus", oid)[0]


def test_1_cold_discovery_executes_the_model_plan_without_forced_replan(monkeypatch, transport, providers_up):
    script = Script(monkeypatch, transport)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    output = supervisor.work_tasks("octopus", oid)[0]["output"]
    assert script.acquisitions == ["service", "audience"]
    assert sum(r["max_tokens"] == 700 for r in script.calls) == 1
    assert not any(r["max_tokens"] == 1000 for r in script.calls)
    # Only acquired references are accepted; the fixture proposes two freely.
    assert len(output["business_signals"]) == 2
    assert len(output["strategy_assessment"]["considered"]) == 2
    assert all(purpose == search.SEARCH_PURPOSE_BUSINESS for _, purpose in script.searches)
    assert read_snapshot()["token_cost_usd"] == pytest.approx(.008)
    assert all(work["status"] == "done" for work in supervisor.work_tasks("octopus", oid))
    no_effects()


def test_2_niche_history_projection_is_compact_and_attributed(tmp_path):
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(project/"tests/pursuit_discovery_capture.py"), str(project), str(tmp_path/"capture")],
                            check=True, capture_output=True, text=True)
    metrics = json.loads(result.stdout)
    assert metrics["goal_chars"] < 3500 and metrics["niche_mentions"] <= 10
    assert metrics["niche_field_chars"] < 800  # Absolute context bound, not a prompt-padding ratio.
    assert metrics["business_signal_focus"] is True and metrics["business_signal_target"] is None
    captured = json.loads((tmp_path/"capture/capture.json").read_text())
    assert "conclusion_modèle_non_preuve" in captured["goal"]
    assert metrics["provider_calls"] == 0 and metrics["quality_benchmark"] is False


def test_3_resume_same_objective_keeps_a_and_acquires_independent_signals(monkeypatch, transport, providers_up):
    oid, old = seed_anchor(monkeypatch)
    historical_output = copy.deepcopy(old["output"])
    historical_proof = [dict(row) for row in journal.query("SELECT * FROM strategy_evidence")]
    aid = old["output"]["strategy_assessment"]["hypothesis_id"]
    script = Script(monkeypatch, transport)
    assert supervisor.start_pursuit(objective_id=oid) == oid
    supervisor.run_pursuit(oid)
    assert script.states[0]["intention"] == "discovery"
    assert EXHAUSTED == script.states[0]["travail_précédent"]["conclusion_modèle_non_preuve"]
    assert script.states[0]["écarts_de_capacités"] == []
    assert script.acquisitions == ["service", "audience"]
    assert tasks.get(old["id"])["output"] == historical_output
    assert [dict(row) for row in journal.query("SELECT * FROM strategy_evidence ORDER BY id")][:len(historical_proof)] == historical_proof
    assert strategy.get("hypothesis", aid, "octopus")["status"] == "proposed"
    no_effects()


def test_4_5_6_proactive_service_and_indirect_mass_monetization_remain_options(monkeypatch, transport, providers_up):
    Script(monkeypatch, transport)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    output = supervisor.work_tasks("octopus", oid)[0]["output"]
    assert output["business_signals"][1]["buyer"] == SOURCES["audience"][0]
    assert output["business_signals"][1]["signal_type"] == "monetization"
    retained = output["strategy_assessment"]["retained"]
    assert retained["economic_rank"] == 1 and retained["executability"] == "missing_capability"
    assert "email_send" in retained["missing_capabilities"]
    assert "Volume × automatisation" in output["strategy_assessment"]["considered"][1]["economic_justification"]
    assert output["economic_result"] is None and economy.cash_summary("octopus") == {}
    no_effects()


def test_7_commerce_signal_and_conceptual_missing_executors():
    payload = source_payload("commerce")
    steps = [{"steps": [{"tool": "browse", "args": {"url": payload["url"]}, "result_data": payload,
                          "browse_meta": runtime._browse_result_meta(payload)}]}]
    accepted, rejected = runtime._qualify_business_signals([signal("commerce")], steps)
    assert len(accepted) == 1 and not rejected
    assessed = separation.assess([{"statement": "Offre commerce à tester", "economic_criteria": ["cash_received"],
                                  "economic_justification": "Capital, livraison, publicité et coûts inconnus",
                                  "required_capabilities": ["advertise", "supplier_order", "payment", "commerce_platform"]}],
                                 supervisor.pursuit_capability_inventory())
    assert assessed["retained"]["executability"] == "missing_capability"
    assert not separation.authorize_execution(assessed)["authorized"]


def test_8_useful_a_continues_after_round_bound_without_forced_discovery(monkeypatch):
    calls = []
    def useful(goal, **kwargs):
        calls.append((json.loads(goal.rsplit("\n", 1)[-1]), kwargs))
        return {"rapport": "Observation ciblée informative", "execution_status": "completed", "results": [],
                "determination": {**determination("continue", "Observer la nouvelle demande acheteur sur A", "Une observation utile reste disponible"), "intent": "validation"}}
    monkeypatch.setattr(runtime, "run_mission", useful)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert [state["intention"] for state, _ in calls] == ["discovery", "validation", "validation"]
    assert strategy.get("objective", oid, "octopus")["status"] == "paused"
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert calls[3][0]["intention"] == "validation"
    assert calls[3][0]["prochaine_recherche"] == "Observer la nouvelle demande acheteur sur A"
    no_effects()


def test_9_local_validation_pause_does_not_force_a_new_discovery_cycle(monkeypatch, transport, providers_up):
    script = Script(monkeypatch, transport, cycle=True)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    work = supervisor.work_tasks("octopus", oid)
    assert [state["intention"] for state in script.states] == ["discovery", "validation"]
    assert len(work[1]["output"]["results"]) == 1
    assert work[1]["output"]["decision"] == "pause" and len(work) == 2
    assert all("llm_cap_usd" not in w["input"] and w["budget_usd"] is None for w in work)
    assert read_snapshot()["token_cost_usd"] < .20
    no_effects()


def test_10_no_execution_substitution_even_when_b_available():
    assessed = separation.assess([
        {"statement": "A meilleure", "economic_criteria": ["cash_received"], "economic_rank": 1, "required_capabilities": ["phone_call"]},
        {"statement": "B moins bonne", "economic_criteria": ["cash_received"], "economic_rank": 2, "required_capabilities": ["search"]}],
        supervisor.pursuit_capability_inventory())
    assert assessed["retained"]["statement"] == "A meilleure"
    assert assessed["considered"][1]["executability"] == "executable"
    result = separation.dispatch_if_authorized(assessed, lambda _: pytest.fail("Substituted execution"))
    assert result["authorized"] is False and result["substituted"] is False


def test_11_new_proof_b_keeps_normal_economic_ranking():
    # Le test intégré de preuve canonique et changement de rang est conservé dans #120.
    from test_economic_discovery import test_f_new_observed_evidence_and_comparison_can_retain_b
    with pytest.MonkeyPatch.context() as patch:
        test_f_new_observed_evidence_and_comparison_can_retain_b(patch)


def test_12_empty_discovery_can_pause_and_retry_later_without_forced_choice(monkeypatch, transport, providers_up):
    script = Script(monkeypatch, transport, empty=True)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    first = supervisor.work_tasks("octopus", oid)[0]["output"]
    assert first["business_signals"] == [] and "strategy_assessment" not in first
    assert first["decision"] == "pause"
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert [s["intention"] for s in script.states] == ["discovery", "discovery"]
    no_effects()


def test_discovery_recovery_after_all_sources_does_not_recollect(monkeypatch, transport, providers_up):
    script = Script(monkeypatch, transport, crash=True)
    oid = supervisor.start_pursuit()
    first = supervisor.work_tasks("octopus", oid)[0]
    with pytest.raises(InterruptedBeforeSynthesis):
        supervisor.run_pursuit(oid)
    checkpoint = tasks.step_value(first["id"], "pursuit.progress")
    assert checkpoint["collect_complete"] and checkpoint["pursuit_intent"] == "discovery"
    sources, requests = list(script.acquisitions), len(script.calls)
    tasks.reap(now=first["created_at"] + 100000)
    supervisor.start_pursuit(objective_id=oid)
    supervisor.run_pursuit(oid)
    assert script.acquisitions == sources == ["service", "audience"]
    assert [r["max_tokens"] for r in script.calls[requests:]] == [4000]
    latest = supervisor.work_tasks("octopus", oid)[-1]
    assert latest["output"]["results"] == checkpoint["results"]
    assert latest["output"]["resumed_collection"] is True
    assert latest["status"] == "done"
    no_effects()


def test_discovery_can_exceed_old_llm_budget_without_human_permission(monkeypatch, transport, providers_up):
    from octopus import pricing
    Script(monkeypatch, transport)
    original = transport.handler
    def expensive(provider, request):
        result = original(provider, request)
        return llm.TransportResult(result.text, result.usage, request["model"], provider_cost_usd=.199)
    monkeypatch.setattr(pricing, "estimate_max_cost", lambda *_: .002)
    transport.handler = expensive
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert len(transport.calls) > 1
    assert supervisor.work_tasks("octopus", oid)[0]["status"] == "done"
    assert read_snapshot()["token_cost_usd"] == pytest.approx(.199 * len(transport.calls))
    assert not tasks.pending_human_requests("octopus")
    assert not journal.query("SELECT id FROM spend_requests")


def test_public_browse_never_uses_saved_account_even_with_cookies(monkeypatch):
    monkeypatch.setattr(runtime, "_current_task_id", lambda **_: 17)
    monkeypatch.setattr(tasks, "get", lambda *_: {"input": {"browser_public_only": True}})
    monkeypatch.setattr(web_guard, "_resolved_ips", lambda host:
                        [ipaddress.ip_address("127.0.0.1" if host == "127.0.0.1" else "8.8.8.8")])
    monkeypatch.setattr(runtime.config, "ACCOUNT_DOMAINS", ("fixture.example",))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda *_: pytest.fail("Saved profile consulted"))
    monkeypatch.setattr(browser, "new_browser", lambda **_: pytest.fail("Account browser created"))
    def acquire(url, guard):
        assert guard(url)
        assert not guard("http://127.0.0.1/")
        return type("Page", (), {"as_dict": lambda _: source_payload("service")["page"]})()
    monkeypatch.setattr(browser, "acquire_public_page", acquire)
    with web_guard.session() as state:
        output = runtime._browse({"url": source_payload("service")["url"]})
        assert output["page"]["main_text"] and state.account_read is False
        state.account_read = True
        with pytest.raises(web_guard.BrowseRefused):
            runtime._browse({"url": source_payload("service")["url"]})


def test_public_browse_fails_closed_if_task_authority_cannot_be_read(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("journal inaccessible")
    monkeypatch.setattr(runtime, "_current_task_id", fail)
    monkeypatch.setattr(web_guard, "check", lambda *_: web_guard.ACCOUNT)
    monkeypatch.setattr(browser, "new_browser", lambda **_: pytest.fail("Account opened without authority"))
    monkeypatch.setattr(browser, "acquire_public_page", lambda *_a, **_k: pytest.fail("Acquisition without authority"))
    with pytest.raises(RuntimeError, match="journal inaccessible"):
        runtime._browse({"url": "https://fixture.example/service"})


def test_real_worker_public_browse_reads_the_task_boundary(monkeypatch, transport, providers_up):
    Script(monkeypatch, transport)
    monkeypatch.setitem(runtime.TOOLS, "browse", {**runtime.TOOLS["browse"], "fn": runtime._browse})
    monkeypatch.setattr(web_guard, "_resolved_ips", lambda *_: [ipaddress.ip_address("8.8.8.8")])
    monkeypatch.setattr(runtime.config, "ACCOUNT_DOMAINS", ("fixture.example",))
    monkeypatch.setattr(browser, "profile_has_cookies", lambda *_: pytest.fail("Saved profile consulted"))
    monkeypatch.setattr(browser, "new_browser", lambda **_: pytest.fail("Connected browser created"))
    def acquire(url, guard):
        assert guard(url)
        task = tasks.get(runtime._current_task_id(strict=True))
        assert task["input"]["browser_public_only"] is True
        data = source_payload(url.rsplit("/", 1)[-1])
        return type("Page", (), {"as_dict": lambda _: data["page"]})()
    monkeypatch.setattr(browser, "acquire_public_page", acquire)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    first = supervisor.work_tasks("octopus", oid)[0]
    assert first["status"] == "done" and len(first["output"]["business_signals"]) == 2
    no_effects()
