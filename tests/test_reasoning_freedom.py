"""Liberties and deterministic boundaries; no claim about model intelligence."""
import copy
import json
from types import SimpleNamespace
import pytest
from agents import runtime, browser, web_guard, deepseek
from octopus import supervisor, strategy, tasks, journal
from octopus import strategy_separation as separation
from test_business_signal_evidence import evidence_case, TEXT, URL
from test_pursuit_real_discovery import Script, no_effects


def candidate(sources=None):
    return {"statement": "Hypothèse d'arbitrage de distribution encore incomplète", "sources": sources or []}


def test_a_unknown_type_and_incomplete_opportunity_are_representable():
    raw, results = evidence_case()
    raw = {"statement": "Anomalie inhabituelle", "signal_type": "unanticipated_arbitrage",
           "sources": [{"url": URL}]}
    accepted, rejected = runtime._qualify_business_signals([raw], results)
    assert len(accepted) == 1 and not rejected
    assert accepted[0]["nature"] == "inferred"


def test_b_one_hypothesis_can_combine_three_acquisitions():
    results, sources = [], []
    for i in range(3):
        url = f"https://unanticipated.example/source/{i}"
        _, acquired = evidence_case(requested=url, final=url)
        results.extend(acquired)
        sources.append({"url": url, "quote": "Cabinet comptable Acme"})
    accepted, rejected = runtime._qualify_business_signals([candidate(sources)], results)
    assert not rejected and len(accepted[0]["sources"]) == 3
    assert all(s["acquisition"]["fetched_at"] for s in accepted[0]["sources"])


def test_c_quote_is_optional_and_unquoted_analysis_stays_inferred():
    _, results = evidence_case()
    accepted, rejected = runtime._qualify_business_signals([candidate([{"url": URL}])], results)
    assert not rejected and accepted[0]["nature"] == "inferred"
    assert "quote" not in accepted[0]["sources"][0]


@pytest.mark.parametrize("source", [{"url": "https://never-acquired.example/"},
                                      {"url": URL, "quote": "invented cash 5000 EUR"}])
def test_i_false_provenance_or_quote_never_becomes_observed(source):
    _, results = evidence_case()
    accepted, rejected = runtime._qualify_business_signals([candidate([source])], results)
    assert not accepted and rejected


def test_i_candidate_without_sources_is_hypothesis_even_if_model_claims_observed():
    accepted, rejected = runtime._qualify_business_signals([{**candidate(), "nature": "observed"}], [])
    assert not rejected and accepted[0]["nature"] == "hypothesis"
    assert accepted[0]["sources"] == []


def test_f_g_partial_strategy_is_not_a_capability_or_permission_claim():
    inventory = supervisor.pursuit_capability_inventory()
    proposal = {"statement": "Arbitrage inhabituel, buyer et prix encore inconnus"}
    assessment = separation.assess([proposal], inventory)
    retained = assessment["retained"]
    assert retained and retained["executability"] == "not_established"
    assert not separation.authorize_execution(assessment)["authorized"]
    assert separation.assess([{**proposal, "required_capabilities": ["email_send"]}], inventory)["retained"]
    no_effects()


def test_model_order_is_preserved_for_observation_not_replaced_by_criteria():
    proposals = [{"statement": "Hypothèse inhabituelle", "economic_rank": 1},
                 {"statement": "Hypothèse classique", "economic_rank": 2, "economic_criteria": ["cash_received"]}]
    result = separation.assess(proposals, supervisor.pursuit_capability_inventory(), observation_only=True)
    assert result["retained"]["statement"] == proposals[0]["statement"]


def test_e_model_chooses_sources_and_action_order_without_automatic_replan_or_review(monkeypatch, transport, providers_up):
    script = Script(monkeypatch, transport, single_plan=True)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert sum(r["max_tokens"] == 700 for r in script.calls) == 1
    assert not any(r["max_tokens"] == 1000 for r in script.calls)
    assert script.acquisitions == ["service"]
    no_effects()


def test_validation_pause_is_respected_without_forced_discovery(monkeypatch):
    def paused(*args, **kwargs):
        return {"execution_status": "completed", "synthesis_status": "validated", "rapport": "Pause choisie",
                "pursuit_intent": "validation", "results": [],
                "determination": {"action": "pause", "reason": "Exploration suffisante pour cette session", "next_goal": "", "permission": ""}}
    monkeypatch.setattr(runtime, "run_mission", paused)
    oid = supervisor.start_pursuit()
    task = supervisor.work_tasks("octopus", oid)[0]
    with journal.connect() as conn:
        conn.execute("UPDATE tasks SET input=? WHERE id=?", (json.dumps({**task["input"], "pursuit_intent": "validation"}), task["id"]))
    supervisor.run_pursuit(oid)
    assert len(supervisor.work_tasks("octopus", oid)) == 1
    assert tasks.pending_human_requests("octopus") == []


def test_d_structured_public_block_does_not_create_human_boundary(monkeypatch):
    _, acquired = evidence_case()
    page = acquired[0]["steps"][0]["result_data"]["page"]
    page.update(blocked=True, error="source denied by remote service", http_status=403)
    result = {"rapport": "Source indisponible", "execution_status": "completed", "results": acquired,
              "determination": {"action": "request_permission", "reason": "Impossible de lire la source",
                "permission": "L'opérateur doit fournir un accès manuel à cette page", "next_goal": ""}}
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: copy.deepcopy(result))
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert tasks.pending_human_requests("octopus") == []
    assert len(supervisor.work_tasks("octopus", oid)) == 3
    assert supervisor.work_tasks("octopus", oid)[0]["output"]["decision"] == "continue"
    no_effects()


def test_h_technical_failure_never_overrides_real_execution_refusal(monkeypatch):
    _, acquired = evidence_case()
    acquired[0]["steps"][0]["result_data"]["page"].update(blocked=True, error="remote block", http_status=403)
    acquired[0]["steps"].append({"tool": "send_email", "result_data": {"refused": True, "reason": "permission absente"}})
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: {
        "rapport": "Refus réel", "execution_status": "completed", "results": acquired,
        "determination": {"action": "request_permission", "reason": "Refus", "permission": "Autoriser l'envoi", "next_goal": ""}})
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    assert tasks.pending_human_requests("octopus")
    assert not journal.query("SELECT id FROM channel_actions")


def test_runtime_marks_public_block_and_reports_next_source_to_agent(monkeypatch):
    import ipaddress
    _, acquired = evidence_case()
    page = acquired[0]["steps"][0]["result_data"]["page"]
    page.update(blocked=True, error="remote protection", http_status=403)
    monkeypatch.setattr(web_guard, "_resolved_ips", lambda *_: [ipaddress.ip_address("8.8.8.8")])
    monkeypatch.setattr(runtime, "_current_task_id", lambda **_: None)
    monkeypatch.setattr(browser, "acquire_public_page", lambda *a, **k: SimpleNamespace(as_dict=lambda: page))
    result = runtime._browse({"url": URL})
    assert result["refused"] and result["failure_class"] == "technical"
    assert "source Web inaccessible" in result["reason"]


def test_j_budgets_tools_and_finance_are_unchanged():
    assert supervisor.PURSUIT_ROUNDS == 3
    assert supervisor.PURSUIT_TOOLS == {"search", "browse", "resources_status", "economy_status", "browser_navigate", "browser_snapshot", "browser_scroll", "browser_back",
        "account_task", "request_account", "create_artifact", "register_channel", "act_on_channel",
        "browser_click", "browser_type", "browser_select", "browser_check", "browser_press", "browser_verify", "browser_upload"}
    no_effects()


def test_guard_refusal_is_not_a_public_source_failure():
    _, acquired = evidence_case()
    data = acquired[0]["steps"][0]["result_data"]
    for error in ("navigation refusée par le garde-fou", "navigation finale refusée par le garde-fou"):
        data["page"].update(blocked=True, error=error, http_status=None)
        assert runtime._public_source_failure(data) is None
    data["page"].update(error="remote timeout", http_status=403)
    data.update(refused=True, reason="outil browse interdit par la politique de cette mission")
    assert runtime._public_source_failure(data) is None


def test_short_acquired_information_is_not_rejected_for_a_length_heuristic():
    _, acquired = evidence_case(text="Prix : 3 EUR")
    raw = candidate([{"url": URL, "quote": "3 EUR"}])
    accepted, rejected = runtime._qualify_business_signals([raw], acquired)
    assert accepted and not rejected and accepted[0]["nature"] == "inferred"


def test_browser_guard_abort_retains_policy_origin_after_navigation_error(monkeypatch):
    http = browser._page_record(
        requested_url=URL, final_url=URL, status=403, content_type="text/html",
        title="", method="http", rendered=False, blocked=True, text="", raw_chars=0,
        error="source distante bloquée",
    )
    def goto(url):
        raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
    fake = SimpleNamespace(blocked=[URL], goto=goto, stop=lambda: None)
    monkeypatch.setattr(browser, "fetch_public_http", lambda *a, **k: http)
    monkeypatch.setattr(browser, "new_browser", lambda **k: fake)
    page = browser.acquire_public_page(URL, guard=lambda url: False).as_dict()
    assert page["error"] == "navigation refusée par le garde-fou" and page["blocked"]
    assert runtime._public_source_failure({"page": page}) is None


def test_existing_source_boundary_reconciles_same_memo_without_recollection(monkeypatch):
    from test_pursuit_recovery import old_waiting_request
    from octopus import worker
    permission = "L'opérateur doit fournir un accès manuel à cette page"
    oid, tid, rid, memo = old_waiting_request(permission=permission, reason=permission)
    _, acquired = evidence_case()
    acquired[0]["steps"][0]["result_data"]["page"].update(blocked=True, error="remote block", http_status=403)
    memo["results"] = acquired
    tasks.save_step(tid, "determination", memo)
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: pytest.fail("recollection"))
    assert supervisor.start_pursuit(objective_id=oid) == oid
    assert not tasks.pending_human_requests("octopus")
    assert journal.query("SELECT status FROM human_requests WHERE id=?", (rid,))[0]["status"] == "cancelled"
    worker.run_one(task_id=tid)
    assert tasks.get(tid)["status"] == "done"
    assert tasks.get(tid)["output"]["decision"] == "continue"
    assert tasks.step_value(tid, "determination") == memo
    assert not journal.query("SELECT id FROM channel_actions")


def test_refuted_idea_can_be_investigated_without_revalidating_or_executing_it(monkeypatch):
    from test_strategy_separation import _refuted
    statement = "Idée réfutée, nouvelle information à chercher"
    hid = _refuted(statement)
    monkeypatch.setattr(runtime, "run_mission", lambda *a, **k: {
        "rapport": "Aucun nouveau fait encore", "execution_status": "completed", "results": [],
        "determination": {"action": "continue", "reason": "Rechercher une information nouvelle",
            "next_goal": statement, "permission": "", "strategies": [{"statement": statement, "required_capabilities": ["search"]}]}})
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    work = supervisor.work_tasks("octopus", oid)
    assert len(work) == 3 and work[0]["output"]["decision"] == "continue"
    assert strategy.get("hypothesis", hid, "octopus")["status"] == "invalidated"
    assert work[0]["output"]["strategy_assessment"]["retained"] is None
    assert not work[0]["output"]["strategy_execution"]["triggered"]


def test_free_agent_can_browse_before_search_and_ignore_a_blocked_source(monkeypatch):
    sequence = iter([{"tool": "browse", "args": {"url": "https://unanticipated.example/report"}},
                     {"tool": "search", "args": {"query": "anomalie choisie librement"}},
                     {"final": "Hypothèse libre, sans encaissement constaté"}])
    calls = []
    def model(*a, **k):
        calls.append(copy.deepcopy(a[3]))
        return next(sequence)
    monkeypatch.setattr(deepseek, "call_json", model)
    _, acquired = evidence_case()
    data = acquired[0]["steps"][0]["result_data"]
    data["page"].update(blocked=True, error="remote protection", http_status=403)
    monkeypatch.setitem(runtime.TOOLS, "browse", {**runtime.TOOLS["browse"], "fn": lambda _: data})
    monkeypatch.setitem(runtime.TOOLS, "search", {**runtime.TOOLS["search"], "fn": lambda _: "recherche simulée"})
    with journal.run("octopus", "offline"):
        result = runtime.run_agent("SOUT", "Libre", allowed_tools={"search", "browse"}, max_steps=3)
    assert [s["tool"] for s in result["steps"]] == ["browse", "search"]
    assert result["steps"][0]["failure_class"] == "technical"
    assert any("aucune permission humaine" in m["content"] for m in calls[1])
    assert not tasks.pending_human_requests("octopus")
