"""Continuation and cost accounting across bounded pursuit batches, without providers."""
import copy
import json
import time
import pytest
from agents import runtime, cancel
from agents.gui.workbench_v2_data import read_snapshot
from octopus import businesses, journal, llm, pricing, strategy, supervisor, tasks
NAME = "Produits digitaux & automatisation IA B2B"
DESCRIPTION = (
    "Créer et vendre des produits numériques, micro-services ou automatisations IA à des indépendants, TPE et PME. "
    "Privilégier les offres rapides à tester, peu coûteuses à produire et livrables numériquement, avec potentiel de revenu récurrent ou de volume. "
    "OCTOPUS choisit librement le problème, le segment, l’offre, le prix, le mode d’acquisition, la distribution et la livraison. "
    "Il peut considérer contenu, démonstrations ou créations proactives, génération de leads, affiliation, SEO, YouTube, TikTok, X, email, plateformes et autres canaux pertinents, "
    "sans supposer qu’un canal est actuellement autorisé ou disponible. Objectif : obtenir une preuve économique puis du cash réellement encaissé le plus vite possible."
)

@pytest.fixture(autouse=True)
def deny_real_network(monkeypatch):
    import requests, socket
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("real HTTP"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("real network"))

def page(url, text):
    from agents.browser import _page_record
    p = _page_record(requested_url=url, final_url=url, status=200, content_type="text/html",
        title="Source simulée", method="http:html_main", rendered=False, blocked=False, text=text, raw_chars=len(text))
    return {"url":url, "texte":text, "page":p.as_dict()}


GOALS = [
    "Comparer librement les possibilités observées",
    "Approfondir la piste encore intéressante",
    "Vérifier l'incertitude technique importante sur la piste atypique",
    "Définir une expérience depuis les nouvelles observations",
    "Identifier les limites de la prochaine action de cette expérience",
    "Préparer le message test sans l'envoyer",
]


class Trajectory:
    def __init__(self, monkeypatch, transport, *, intent="discovery", final_permission=True, cost=.001):
        self.stage = 0
        self.calls, self.states, self.acquired, self.action_count = [], [], [], {}
        self.intent, self.final_permission, self.cost = intent, final_permission, cost
        self.expire = False
        transport.handler = self.respond
        monkeypatch.setitem(runtime.TOOLS, "browse", {**runtime.TOOLS["browse"], "fn": self.browse})

    def browse(self, args):
        self.acquired.append(args["url"])
        if self.expire:
            self.expire = False
            cancel._deadline.set(time.monotonic() - 1)
        return page(args["url"], text=f"Observation simulée numéro {self.stage}. Ni preuve d'achat ni revenu.")

    def respond(self, provider, request):
        self.calls.append(copy.deepcopy(request))
        limit = request["max_tokens"]
        if limit == 700:
            self.stage += 1
            state = json.loads(request["messages"][1]["content"].rsplit("\n", 1)[-1])
            self.states.append(copy.deepcopy(state))
            out = {"tasks": [{"role": "SOUT", "task": f"Observation nouvelle {self.stage}"}]}
        elif limit == 500:
            count = self.action_count.get(self.stage, 0)
            self.action_count[self.stage] = count + 1
            out = {"tool": "browse", "args": {"url": f"https://simulated.example/step{self.stage}"}} if count == 0 else {"final": f"Observation {self.stage} conservée"}
        elif limit == 1600:
            permission = self.final_permission and self.stage == 6
            out = {"rapport": f"Observations {self.stage}, hypothèse uniquement.", "business_signals": [],
                "determination": {"action": "request_permission" if permission else "continue",
                    "reason": f"Nouvelle vérification utile après observation {self.stage}",
                    "next_goal": GOALS[min(self.stage,6)-1], "permission": "Autoriser explicitement l'envoi externe" if permission else "",
                    "intent": self.intent,
                    "strategies": [{"statement": "Offre atypique à explorer", "economic_rank": 1, "required_capabilities": ["email_send"]},
                        {"statement": "Alternative différente", "economic_rank": 2, "required_capabilities": ["search"]}]}}
        else:
            raise AssertionError(limit)
        text = json.dumps(out, ensure_ascii=False)
        usage = pricing.Usage(prompt_tokens=sum(len(m["content"]) for m in request["messages"])//4, completion_tokens=len(text)//4)
        return llm.TransportResult(text, usage, request["model"], resolved_provider="OfflineFake", provider_cost_usd=self.cost)


def batch(activity, oid=None):
    oid = supervisor.start_pursuit(business=activity.id, objective_id=oid)
    supervisor.run_pursuit(oid, business=activity.id)
    return oid


@pytest.mark.parametrize("intent", ["discovery", "validation"])
def test_continuity_of_model_continue_beyond_third(monkeypatch, transport, providers_up, intent):
    activity = businesses.create_activity(NAME, DESCRIPTION)
    script = Trajectory(monkeypatch, transport, intent=intent)
    oid = batch(activity)
    work = supervisor.work_tasks(activity.id, oid)
    assert len(work) == 3
    last = work[-1]["output"]
    assert last["determination"]["action"] == "continue"
    assert last["next_goal"] == GOALS[2]
    assert last["decision"] == "continue" and last["cycle_limit_reached"]
    assert "Limite de trois" not in last["reason"]
    assert len(journal.query("SELECT id FROM strategy_decisions WHERE business=?", (activity.id,))) == 3
    assert not tasks.pending_human_requests(activity.id)
    batch(activity, oid)
    assert script.states[3]["intention"] == intent
    assert script.states[3]["prochaine_recherche"] == GOALS[2]
    assert len(script.states) == 6
    assert all(t["output"]["decision"] == "continue" for t in supervisor.work_tasks(activity.id, oid)[:5])
    assert script.acquired == [f"https://simulated.example/step{i}" for i in range(1,7)]
    assert len(script.calls) == 24
    assert read_snapshot(activity.id)["token_cost_usd"] == pytest.approx(.024)
    assert len(journal.query("SELECT id FROM strategy_decisions WHERE business=?", (activity.id,))) == 6
    assert len(tasks.pending_human_requests(activity.id)) == 1
    assert len(supervisor.work_tasks(activity.id, oid)) == 6
    for item in supervisor.work_tasks(activity.id, oid):
        assessment = tasks.step_value(item["id"], "pursuit.strategy_assessment")
        assert assessment["retained"]["statement"] == "Offre atypique à explorer"


@pytest.mark.parametrize("rounds", [2,3,6])
def test_same_six_units_different_batch_boundaries(monkeypatch, transport, providers_up, rounds):
    monkeypatch.setattr(supervisor, "PURSUIT_ROUNDS", rounds)
    activity = businesses.create_activity(NAME, DESCRIPTION)
    script = Trajectory(monkeypatch, transport, intent="validation", final_permission=False)
    oid = None
    for _ in range(6//rounds):
        oid = batch(activity, oid)
    work = supervisor.work_tasks(activity.id, oid)
    snapshot = read_snapshot(activity.id)
    print("EQUAL_WORK", json.dumps({"rounds":rounds, "calls":len(script.calls), "cost":snapshot["token_cost_usd"],
        "decisions":[t["output"]["decision"] for t in work], "goals":[s["prochaine_recherche"] for s in script.states],
        "ranks":[tasks.step_value(t["id"],"pursuit.strategy_assessment")["retained"]["economic_rank"] for t in work],
        "chars":[sum(len(m["content"]) for m in r["messages"]) for r in script.calls],
        "sources":script.acquired,"objective":strategy.get("objective",oid,activity.id)["status"],
        "human":len(tasks.pending_human_requests(activity.id))},ensure_ascii=False))
    assert len(work) == 6 and len(script.calls) == 24 and len(set(script.acquired)) == 6
    assert [t["output"]["decision"] for t in work] == ["continue"] * 6
    assert [s["prochaine_recherche"] for s in script.states][1:] == GOALS[:5]
    assert all(tasks.step_value(t["id"], "pursuit.strategy_assessment")["retained"]["economic_rank"] == 1 for t in work)
    assert snapshot["token_cost_usd"] == pytest.approx(.024)
    assert len(journal.query("SELECT id FROM strategy_decisions WHERE business=?", (activity.id,))) == 6


def test_repeated_resume_accumulates_cost_above_old_run_and_daily_caps(monkeypatch, transport, providers_up):
    activity = businesses.create_activity(NAME, DESCRIPTION)
    from octopus import catalog
    providers_up.update(set(catalog.load().raw["providers"]) - {"deepseek"})
    script = Trajectory(monkeypatch, transport, final_permission=False, cost=.25)
    oid = None
    for _ in range(3):
        oid = batch(activity, oid)
    work = supervisor.work_tasks(activity.id, oid)
    assert all(t["budget_usd"] is None and "llm_cap_usd" not in t["input"] for t in work)
    assert len(work) == 9 and len(script.calls) == 36
    assert read_snapshot(activity.id)["token_cost_usd"] == pytest.approx(9.)
    assert [t["budget_usd"] for t in work if t["input"]["round"] == 1] == [None,None,None]
    assert not tasks.pending_human_requests(activity.id)
    assert read_snapshot(activity.id)["pursuit_llm"] == {"spent_usd":9.}
    assert all(r["budget_usd"] is None for r in journal.query("SELECT budget_usd FROM runs"))
    assert {r["provider"] for r in journal.query("SELECT provider FROM llm_calls WHERE status='ok'")} == {"deepseek"}


def test_timeout_after_observation_keeps_checkpoint_and_reuses_source(monkeypatch, transport, providers_up):
    monkeypatch.setattr(supervisor, "PURSUIT_ROUNDS", 1)
    activity = businesses.create_activity(NAME, DESCRIPTION)
    script = Trajectory(monkeypatch, transport, intent="validation", final_permission=False)
    script.expire = True
    oid = batch(activity)
    first = supervisor.work_tasks(activity.id, oid)[0]
    assert first["status"] == "done_degraded" and first["output"]["execution_status"] == "timeout"
    progress = tasks.step_value(first["id"], "pursuit.progress")
    assert len(progress["agent_steps"]) == 1
    assert not tasks.pending_human_requests(activity.id)
    batch(activity, oid)
    assert script.acquired == ["https://simulated.example/step1"]
    assert script.stage == 1
    assert len(script.calls) == 4
    assert read_snapshot(activity.id)["token_cost_usd"] == pytest.approx(.004)
    assert not tasks.pending_human_requests(activity.id)


def test_non_pursuit_run_and_daily_llm_limits_remain_enforced(monkeypatch):
    from octopus import catalog
    cat = catalog.load()
    with journal.run("octopus", "other-work", budget_usd=.05) as ctx:
        assert not ctx.llm_cost_observation_only
        assert "budget du run" in llm._budget_block(ctx, cat, "octopus", .06)
    with journal.run("octopus", "other-work") as ctx:
        monkeypatch.setattr(journal, "spent_today", lambda *_: 2.)
        assert "journalier" in llm._budget_block(ctx, cat, "octopus", .01)


def test_same_model_json_fallback_still_records_both_calls_without_cost_stop(monkeypatch, transport, providers_up):
    from octopus import catalog
    providers_up.update(set(catalog.load().raw["providers"]) - {"deepseek"})
    activity = businesses.create_activity(NAME, DESCRIPTION)
    script = Trajectory(monkeypatch, transport, final_permission=False, cost=.25)
    original, invalidated = script.respond, False
    def syntax_error_once(provider, request):
        nonlocal invalidated
        result = original(provider, request)
        if request["max_tokens"] == 1600 and not invalidated:
            invalidated = True
            assert request["response_format"] == {"type":"json_object"}
            return llm.TransportResult('{"rapport":"x" "determination":{}}', result.usage,
                result.requested_model, provider_cost_usd=.25)
        return result
    transport.handler = syntax_error_once
    batch(activity)
    rows = [dict(r) for r in journal.query("SELECT status, cost_usd, justification FROM llm_calls ORDER BY id")]
    assert len(rows) == 13 and len(script.acquired) == 3
    invalid = next(i for i,r in enumerate(rows) if r["status"] == "invalid")
    assert rows[invalid+1]["status"] == "ok"
    assert json.loads(rows[invalid]["justification"])["structured_method"] == "json_object"
    assert json.loads(rows[invalid+1]["justification"])["structured_method"] == "text"
    assert read_snapshot(activity.id)["token_cost_usd"] == pytest.approx(3.25)
    assert not tasks.pending_human_requests(activity.id)


def test_third_cycle_missing_capability_keeps_adjusted_continuation(monkeypatch, transport, providers_up):
    activity = businesses.create_activity(NAME, DESCRIPTION)
    script = Trajectory(monkeypatch, transport, intent="validation", final_permission=False)
    original = script.respond
    def missing_tool(provider, request):
        result = original(provider, request)
        if request["max_tokens"] == 1600 and script.stage == 3:
            data = json.loads(result.text)
            data["determination"].update(action="request_permission", permission="L'outil email_send est absent.")
            return llm.TransportResult(json.dumps(data,ensure_ascii=False), result.usage, result.requested_model, provider_cost_usd=.001)
        return result
    transport.handler = missing_tool
    oid = batch(activity)
    assert supervisor.work_tasks(activity.id, oid)[-1]["output"]["decision"] == "continue"
    assert not tasks.pending_human_requests(activity.id)
    batch(activity, oid)
    assert script.states[3]["prochaine_recherche"] == GOALS[2]
    assert script.states[3]["intention"] == "validation"


def test_historical_pursuit_budget_request_is_cancelled_on_explicit_resume(monkeypatch, transport, providers_up):
    activity = businesses.create_activity(NAME, DESCRIPTION)
    oid = supervisor.start_pursuit(business=activity.id)
    first = supervisor.work_tasks(activity.id, oid)[0]
    tasks.claim("offline", task_id=first["id"])
    tasks.save_step(first["id"], "determination", {"execution_status":"budget_exceeded",
        "synthesis_status":"degraded", "synthesis_error":"BudgetExceeded: old cap", "results":[]})
    suffix = " Une réponse seule n'accorde aucun droit. Adaptez l'objectif ou configurez une autorisation explicite."
    rid = tasks.request_human(first["id"], "offline", "pursuit.permission",
        "Plafond LLM explicitement atteint ; décision de l'opérateur nécessaire." + suffix)
    assert read_snapshot(activity.id)["requests"][0]["technical_obsolete"]
    script = Trajectory(monkeypatch, transport, final_permission=False)
    batch(activity, oid)
    assert len(script.states) == 3
    assert not tasks.pending_human_requests(activity.id)
    assert journal.query("SELECT status FROM human_requests WHERE id=?", (rid,))[0]["status"] == "cancelled"
    for table in ("spend_requests", "channel_actions", "ledger_entries"):
        assert not journal.query(f"SELECT id FROM {table}")
