"""Human terrain, common engine; no live transport, navigation or economic effects."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import re
from types import SimpleNamespace
import queue

import pytest

from agents import runtime
from agents.gui.workbench_v2_data import read_snapshot
from agents.gui.workspaces import WorkspaceRegistry
from octopus import businesses, economy, journal, resources, strategy, supervisor, tasks
from octopus import capability_acquisition as acquisition
from test_pursuit_real_discovery import Script, InterruptedBeforeSynthesis, no_effects


def activity(name="Vidéo B2B proactive", description="Créer et vendre du contenu vidéo à des professionnels"):
    return businesses.create_activity(name, description)


def test_a_creation_persists_in_canonical_registry_and_survives_restart(isolated):
    item = activity()
    assert item.path == isolated / "businesses" / item.id / "business.toml"
    assert item.handlers == [] and item.budget_daily_usd is None
    businesses._cache.clear()
    found = businesses.discover()[item.id]
    assert found.name == item.name and found.description == item.description
    registry = WorkspaceRegistry(isolated / "gui.json", readonly=True)
    assert registry.get(item.id).name == item.name
    assert not (isolated / "gui.json").exists()
    assert item.id in read_snapshot()["businesses"]


@pytest.mark.parametrize("name", ["Deux espaces", "Été à Rouen", "../../outside", "a/b\\c", "..", "💶 !?", "all", "octopus"])
def test_b_generated_id_is_safe_stable_and_not_reserved(isolated, name):
    item = activity(name)
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", item.id)
    assert item.id not in {"all", "octopus"}
    assert item.path.resolve().is_relative_to((isolated / "businesses").resolve())
    businesses._cache.clear()
    assert businesses.get(item.id).id == item.id


def test_collision_and_concurrent_creations_never_overwrite():
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(lambda _: activity("Même nom"), range(8)))
    assert len({item.id for item in items}) == 8
    assert all(businesses.get(item.id).name == "Même nom" for item in items)


def test_forced_id_collision_preserves_existing_file(monkeypatch):
    monkeypatch.setattr(businesses.uuid, "uuid4", lambda: SimpleNamespace(hex="1"*32))
    first = activity("Identique")
    original = first.path.read_bytes()
    values = iter(["1"*32, "2"*32])
    monkeypatch.setattr(businesses.uuid, "uuid4", lambda: SimpleNamespace(hex=next(values)))
    second = activity("Identique")
    assert first.id != second.id and first.path.read_bytes() == original


def test_registry_symlink_cannot_escape_dataroot(isolated, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (isolated / "businesses").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="DataRoot"):
        activity()
    assert list(outside.iterdir()) == []


def test_atomic_failure_leaves_no_discoverable_half_declaration(monkeypatch):
    monkeypatch.setattr(businesses.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("interrupted write")))
    with pytest.raises(OSError, match="interrupted write"):
        activity()
    assert businesses.discover() == {}


def test_c_create_does_not_run_or_grant_anything(transport):
    activity()
    assert transport.calls == []
    for table in ("tasks", "runs", "llm_calls", "strategy_objectives", "economic_channels", "spend_allowances", "ledger_entries"):
        assert journal.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0
    no_effects()


def test_d_start_is_scoped_reusable_and_budget_unchanged():
    a, b = activity("A"), activity("B")
    aid = supervisor.start_pursuit(business=a.id)
    bid = supervisor.start_pursuit(business=b.id)
    assert aid != bid
    assert supervisor.start_pursuit(business=b.id) == bid
    objective = strategy.get("objective", bid, b.id)
    assert b.name in objective["statement"] and b.description in objective["statement"]
    work = supervisor.work_tasks(b.id, bid)
    assert len(work) == 1 and work[0]["business"] == b.id
    assert work[0]["budget_usd"] == .20 and work[0]["input"]["browser_public_only"] is True
    with pytest.raises(supervisor.SupervisorError):
        supervisor.start_pursuit(objective_id=aid, business=b.id)
    no_effects()


def test_e_f_g_l_new_activity_never_inherits_legacy_or_other_activity(monkeypatch, transport, providers_up):
    a, b = activity("API monitoring", "API-cost monitoring"), activity()
    def prior(*_, **__):
        return {"rapport": "API-cost https://api-cost.example", "results": [], "execution_status": "completed",
                "determination": {"action": "pause", "reason": "API-cost épuisée", "strategies": [{
                    "statement": "API-cost retained", "economic_rank": 1, "economic_criteria": ["cash_received"],
                    "required_capabilities": ["email_send"]}]}}
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "run_mission", prior)
        aid = supervisor.start_pursuit(business=a.id)
        supervisor.run_pursuit(aid, business=a.id)
        legacy = supervisor.start_pursuit()
        supervisor.run_pursuit(legacy)
    before = copy.deepcopy(supervisor.work_tasks(a.id, aid))
    script = Script(monkeypatch, transport)
    bid = supervisor.start_pursuit(business=b.id)
    supervisor.run_pursuit(bid, business=b.id)
    assert "API-cost" not in json.dumps(script.calls, ensure_ascii=False)
    assert script.states[0]["activité_déclarée"]["description"] == b.description
    output = supervisor.work_tasks(b.id, bid)[0]["output"]
    assert output["strategy_assessment"]["retained"]["economic_rank"] == 1
    assert output["strategy_assessment"]["retained"]["executability"] == "missing_capability"
    assert output["strategy_execution"]["authorized"] is False
    assert supervisor.work_tasks(a.id, aid) == before
    assert read_snapshot(b.id)["objectives"][0]["business"] == b.id
    assert read_snapshot("octopus")["objectives"]
    assert all(row["business"] == b.id for row in read_snapshot(b.id)["evidence"])
    no_effects()


def test_h_ledger_and_llm_costs_are_separate_per_currency_category_and_activity():
    a, b = activity("A"), activity("B")
    for item, value in ((a, 20), (b, 35)):
        economy.record_cash(item.id, "in", value, "EUR", "customer_receipt", nature="observed",
                            created_by="human", source_ref="simulated receipt", occurred_at=1)
    economy.record_cash(a.id, "in", 100, "EUR", "owner_funding", nature="observed",
                        created_by="human", source_ref="simulated funding", occurred_at=1)
    economy.record_cash(b.id, "out", 5, "EUR", "expense", nature="observed",
                        created_by="human", source_ref="simulated expense", occurred_at=1)
    for item, cost in ((a, .01), (b, .02)):
        journal.record_llm_call({"ts": 1, "business": item.id, "task": "fake", "model": "fake",
                                 "profile": "economical", "provider": "fake", "cost_class": "paid",
                                 "status": "ok", "cost_usd": cost})
    rows = {item["id"]: item for item in read_snapshot()["activities"]}
    assert rows[a.id]["cash"]["EUR"]["customer_receipts"] == 20
    assert rows[b.id]["cash"]["EUR"]["customer_receipts"] == 35
    assert rows[b.id]["cash"]["EUR"]["out_observed"] == 5
    assert rows[a.id]["llm_cost_usd"] == .01 and rows[b.id]["llm_cost_usd"] == .02
    assert read_snapshot(a.id)["token_cost_usd"] == .01
    assert all(row["business"] == a.id for row in read_snapshot(a.id)["ledger"])
    assert sum(item["cash"].get("EUR", {}).get("customer_receipts", 0) for item in rows.values()) == 55


def test_i_recovery_resumes_only_this_activity_without_recollection(monkeypatch, transport, providers_up):
    a, b = activity("A"), activity("B")
    other = supervisor.start_pursuit(business=a.id)
    untouched = copy.deepcopy(supervisor.work_tasks(a.id, other))
    script = Script(monkeypatch, transport, crash=True)
    oid = supervisor.start_pursuit(business=b.id)
    first = supervisor.work_tasks(b.id, oid)[0]
    with pytest.raises(InterruptedBeforeSynthesis):
        supervisor.run_pursuit(oid, business=b.id)
    saved = tasks.step_value(first["id"], "pursuit.progress")
    sources = list(script.acquisitions)
    tasks.reap(now=first["created_at"] + 100000)
    supervisor.start_pursuit(objective_id=oid, business=b.id)
    supervisor.run_pursuit(oid, business=b.id)
    assert script.acquisitions == sources
    assert supervisor.work_tasks(b.id, oid)[-1]["output"]["results"] == saved["results"]
    assert supervisor.work_tasks(a.id, other) == untouched
    assert all(row["business"] == b.id for row in journal.query("SELECT business FROM llm_calls"))
    no_effects()


def test_k_readonly_blocks_creation_start_pause_and_execution(monkeypatch):
    b = activity()
    oid = supervisor.start_pursuit(business=b.id)
    before = copy.deepcopy(supervisor.work_tasks(b.id, oid))
    monkeypatch.setenv("OCTOPUS_WORKBENCH_READONLY", "1")
    assert b.id in read_snapshot()["businesses"]
    for action in (lambda: activity("interdit"), lambda: supervisor.start_pursuit(business=b.id),
                   lambda: supervisor.pause_pursuit(oid, business=b.id), lambda: supervisor.run_pursuit(oid, business=b.id)):
        with pytest.raises(PermissionError):
            action()
    assert supervisor.work_tasks(b.id, oid) == before


def test_o_description_is_only_data_and_cannot_inject_configuration():
    text = '"\nhandlers = ["evil.module"]\nbudget_daily_usd = 999\npermission="act"\npath="../../outside"\nexecutor="email_send"'
    b = activity("Hostile description", text)
    assert businesses.get(b.id).description == text
    assert b.handlers == [] and b.budget_daily_usd is None
    assert set(b.raw) == {"id", "name", "description", "handlers"}
    assert "evil.module" not in businesses.handler_modules()
    oid = supervisor.start_pursuit(business=b.id)
    task = supervisor.work_tasks(b.id, oid)[0]
    assert task["budget_usd"] == .20 and set(task["input"]["allowed_tools"]) == supervisor.PURSUIT_TOOLS
    no_effects()


def test_description_control_characters_and_unicode_round_trip():
    item = activity("Texte 💶", 'Description\x7f\x00\n"quoted" \\ fin 💶')
    assert businesses.get(item.id).description == item.description


def test_other_activity_cannot_supply_previous_observations(transport):
    a, b = activity("A"), activity("B")
    aid = supervisor.start_pursuit(business=a.id)
    bid = supervisor.start_pursuit(business=b.id)
    previous = supervisor.work_tasks(a.id, aid)[0]
    current = supervisor.work_tasks(b.id, bid)[0]
    payload = {**current["input"], "previous_id": previous["id"]}
    with journal.connect() as connection:
        connection.execute("UPDATE tasks SET input=? WHERE id=?", (json.dumps(payload), current["id"]))
    supervisor.run_pursuit(bid, business=b.id)
    assert tasks.get(current["id"])["status"] == "failed"
    assert "hors de l'activité" in tasks.get(current["id"])["error"]
    assert tasks.get(previous["id"]) == previous and transport.calls == []


def test_global_resources_remain_shared_but_local_resources_never_leak():
    a, b = activity("A"), activity("B")
    for key, scope in (("global_email", None), ("local_a_email", a.id), ("local_b_email", b.id)):
        resources.declare(key, "account", key, created_by="human", business=scope, capabilities=["email_send"])
    facts = acquisition.snapshot(["email_send"], business=b.id)["capabilities"][0]["resolution"]["resources"]
    assert {row["key"] for row in facts} == {"global_email", "local_b_email"}
    with journal.run(b.id, "offline"):
        view = runtime._resources_status({})
    assert {row["key"] for row in view["resources"]} == {"global_email", "local_b_email"}
    assert view["overview"]["total"] == 2


def test_start_callback_keeps_the_clicked_activity_if_selection_changes(monkeypatch):
    from agents import procs
    from agents.gui.workbench_v2 import WorkbenchV2
    from agents.gui import workbench_v2 as ui
    a, b = activity("A"), activity("B")
    deferred, launches = [], []
    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            deferred.append(self.target)
    monkeypatch.setattr(ui.threading, "Thread", Thread)
    monkeypatch.setattr(procs, "spawn", lambda args, *_a, **_k:
                        launches.append(args) or (SimpleNamespace(poll=lambda: None), "fake.log"))
    app = SimpleNamespace(_readonly=False, _creating=False, selected_business_id=a.id,
                          _pursuit_processes=[], _background_results=queue.SimpleQueue())
    WorkbenchV2._start_pursuit(app)
    app.selected_business_id = b.id
    deferred[0]()
    assert launches[0][2] == a.id
    assert strategy.list_items("objective", a.id) and not strategy.list_items("objective", b.id)
    assert app._pursuit_processes[0][0] == a.id
    no_effects()


@pytest.mark.parametrize("page", ["Vue d'ensemble", "Missions", "Livrables", "Navigateur", "Activité", "Paramètres"])
def test_j_switch_clears_previous_data_before_rendering(page):
    from agents.gui.workbench_v2 import WorkbenchV2
    rendered = []
    app = SimpleNamespace(selected_business_id="a", _snapshot={"secret": "a"}, _snapshot_error=None,
                          _snapshot_at=10, _sync_business_menu=lambda: None, _load_snapshot=lambda: None)
    app._show_page = lambda name: rendered.append((name, app._snapshot))
    WorkbenchV2._select_business_then(app, page, "b")
    assert rendered == [(page, None)] and app._snapshot_at == 0


def test_j_async_old_snapshot_and_error_are_ignored():
    from agents.gui.workbench_v2 import WorkbenchV2
    results, loads = queue.SimpleQueue(), []
    results.put(("v2_snapshot", "a", {"secret": "a"}))
    results.put(("v2_error", "error in a", "a"))
    app = SimpleNamespace(selected_business_id="b", _snapshot=None, _snapshot_error=None,
                          _snapshot_at=0., _snapshot_busy=True, _reload_after_create=False,
                          current_page="Livrables", _background_results=results,
                          _worker_label=lambda: "idle", side_worker=SimpleNamespace(configure=lambda **_: None),
                          _load_snapshot=lambda: loads.append("b"), after=lambda *_: None)
    app._refresh = lambda: WorkbenchV2._refresh(app)
    WorkbenchV2._refresh(app)
    assert app._snapshot is None and app._snapshot_error is None and loads == ["b"]
