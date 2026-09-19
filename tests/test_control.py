"""Business control plane : crée un run depuis un playbook, matérialise des actions ordonnées,
gère les approbations (humain), l'exécution idempotente, le budget, et la reprise sans duplication.
"""
from __future__ import annotations

import json
import pytest

from octopus import control, economy, journal, strategy, tasks
from octopus.control import ControlError, load_playbook

B = "atelier_run"


def _register_executors(business=B):
    from octopus import actions
    # Exécuteurs déterministes : aucun réseau, preuve observée factice mais tracée.
    def make(action):
        def run(channel, payload):
            return {"observation": f"{action} simulée", "source_ref": f"sim:{action}:{channel['id']}",
                    "metric": action + "_ok", "value": 1, "unit": "count"}
        return run
    actions.register_executor("web", "prepare_brief", make("prepare_brief"))
    actions.register_executor("web", "publish_page", make("publish_page"))


def _seed(business=B, budget=100.0, currency="EUR"):
    """Business prêt : canal actif + enveloppe pour couvrir les actions payantes."""
    _register_executors(business)
    channel = economy.add_channel(business, "web", "Site", created_by="human",
                                  capabilities=["publish", "observe"], locator="https://exemple.fr")
    economy.update_channel(business, channel, actor="human", status="active", access="act")
    economy.grant_allowance(business, budget, currency, granted_by="human",
                            rationale="enveloppe de test")
    return channel


def test_playbook_loads_and_validates():
    pb = load_playbook("web_launch")
    assert pb["id"] == "web_launch"
    assert [a["kind"] for a in pb["actions"]] == ["prepare_brief", "publish_page"]
    assert pb["actions"][1]["requires_approval"] is True
    with pytest.raises(ControlError):
        load_playbook("inconnu")


def test_create_run_from_playbook_is_persisted():
    _seed()
    run = control.create_run(B, "web_launch", spec={"idea": "landing page"},
                             budget_amount=50.0, budget_currency="EUR", created_by="human")
    assert run["status"] == "running" and run["playbook"] == "web_launch"
    assert run["budget_amount"] == 50.0 and run["budget_currency"] == "EUR"
    row = journal.query("SELECT * FROM business_runs WHERE id=?", (run["id"],))[0]
    assert row["spec"] and json.loads(row["spec"])["idea"] == "landing page"
    with pytest.raises(ControlError):
        control.create_run("other", "web_launch", spec={}, budget_amount=1, budget_currency="EUR",
                           created_by="human")  # business isolé
    with pytest.raises(ControlError):
        control.create_run(B, "web_launch", spec={}, budget_amount=999.0, budget_currency="EUR",
                           created_by="human")  # dépasse l'enveloppe


def test_materialize_actions_in_order_and_idempotent():
    _seed()
    run = control.create_run(B, "web_launch", spec={"idea": "x"}, budget_amount=20.0,
                             budget_currency="EUR", created_by="human")
    first = control.materialize_actions(run["id"])
    second = control.materialize_actions(run["id"])  # rejouable : mêmes ids, pas de duplication
    assert len(first) == 2 and second == first
    brief, page = first
    assert [a["status"] for a in control.list_actions(run["id"])] == ["proposed", "proposed"]
    assert control.get_action(run["id"], brief)["seq"] == 1
    assert control.get_action(run["id"], page)["requires_approval"] is True


def test_execution_requires_human_approval_then_executes_once():
    channel = _seed()
    run = control.create_run(B, "web_launch", spec={"idea": "y"}, budget_amount=30.0,
                             budget_currency="EUR", created_by="human")
    [brief, page] = control.materialize_actions(run["id"])
    r1 = control.execute_action(run["id"], brief, actor="orbit")
    assert r1["status"] == "executed" and r1["evidence_id"]
    # L'action payante exige une approbation humaine
    r2 = control.execute_action(run["id"], page, actor="orbit")
    assert r2["status"] == "awaiting_approval"
    r3 = control.approve_action(run["id"], page, actor="human")
    assert r3["status"] == "approved"
    r4 = control.execute_action(run["id"], page, actor="orbit")
    assert r4["status"] == "executed" and r4["evidence_id"]
    # Rejouer : idempotent, pas de nouvelle preuve
    r5 = control.execute_action(run["id"], page, actor="orbit")
    assert r5["status"] == "executed" and r5["duplicate"] is True
    assert r5["evidence_id"] == r4["evidence_id"]
    assert len([e for e in strategy.list_items("evidence", B) if "action" in e["summary"]]) == 2


def test_pause_resume_retry_are_deterministic():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=20.0, budget_currency="EUR",
                             created_by="human")
    [brief, _page] = control.materialize_actions(run["id"])
    control.execute_action(run["id"], brief, actor="orbit")
    control.pause_run(run["id"], actor="human")
    with pytest.raises(ControlError):
        control.execute_action(run["id"], brief, actor="orbit")
    control.resume_run(run["id"], actor="human")
    assert control.get_run(run["id"])["status"] == "running"


def test_budget_accounting_uses_ledger_and_allowance():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=10.0, budget_currency="EUR",
                             created_by="human")
    [brief, page] = control.materialize_actions(run["id"])
    control.execute_action(run["id"], brief, actor="orbit")  # gratuit
    control.approve_action(run["id"], page, actor="human")
    control.execute_action(run["id"], page, actor="orbit")  # payant 5 EUR
    spent = control.spent_for_run(run["id"])
    assert spent == pytest.approx(5.0)
    assert spent <= run["budget_amount"]


def test_snapshot_exposes_state_budget_evidence_and_events():
    _seed()
    run = control.create_run(B, "web_launch", spec={"k": 1}, budget_amount=10.0,
                             budget_currency="EUR", created_by="human")
    [brief, page] = control.materialize_actions(run["id"])
    control.execute_action(run["id"], brief, actor="orbit")
    control.approve_action(run["id"], page, actor="human")
    snap = control.snapshot(run["id"])
    assert snap["run"]["status"] == "running"
    assert len(snap["actions"]) == 2
    assert snap["budget"]["amount"] == 10.0 and snap["budget"]["currency"] == "EUR"
    assert snap["budget"]["spent"] >= 0
    assert snap["evidence"] and snap["events"]
    assert snap["approvals"][0]["id"] == page


def test_events_are_journaled_for_audit():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=10.0,
                             budget_currency="EUR", created_by="human")
    [brief, page] = control.materialize_actions(run["id"])
    control.execute_action(run["id"], brief, actor="orbit")
    control.approve_action(run["id"], page, actor="human")
    # Les événements d'action sont rattachés au run ; run.created est émis avec task_id=origin_task_id
    # (None ici), donc il est lu par business dans le journal.
    action_types = [e["type"] for e in tasks.events(task_id=run["id"], limit=100)]
    assert "action.proposed" in action_types and "action.approved" in action_types
    conn = journal.connect()
    try:
        rows = conn.execute("SELECT type FROM events WHERE business=?", (B,)).fetchall()
    finally:
        conn.close()
    all_types = [r["type"] for r in rows]
    assert "run.created" in all_types


def test_rejects_business_outside_isolation():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=10.0,
                             budget_currency="EUR", created_by="human")
    with pytest.raises(ControlError):
        control.approve_action(run["id"], 9999, actor="human")  # action inconnue
    with pytest.raises(ControlError):
        control.get_run(run["id"], business="other")
