"""Deterministic simulated acquisition funnel integrated with the business control plane.

Vertical slice : content brief → video artifact request → publish (approval) →
TikTok comment (GO) → consent-aware lead → fake DM guide → follow-up task → conversion →
snapshot with metrics, evidence, approvals, audit.

Idempotency: pause / resume / retry never duplicates publishing, leads, DMs, follow-ups or conversions.
No network, no LLM — all adapters are deterministic fakes.
"""
from __future__ import annotations

import pytest

from octopus import actions, control, economy, funnel, journal, strategy, tasks

B = "funnel_test"


def _seed(business=B, budget=200.0):
    """Business prêt : canal actif + enveloppe + exécuteurs déterministes."""
    def make(action):
        def run(channel, payload):
            return {"observation": f"{action} simulée", "source_ref": f"sim:{action}:{channel['id']}",
                    "metric": f"{action}_ok", "value": 1, "unit": "count"}
        return run
    for kind in ("prepare_brief", "request_video", "publish_video"):
        actions.register_executor("web", kind, make(kind))
    channel = economy.add_channel(business, "web", "Site", created_by="human",
                                   capabilities=["publish", "observe"], locator="https://exemple.fr")
    economy.update_channel(business, channel, actor="human", status="active", access="act")
    economy.grant_allowance(business, budget, "EUR", granted_by="human", rationale="enveloppe de test")
    return channel


def test_playbook_loads():
    pb = control.load_playbook("tiktok_funnel")
    assert pb["id"] == "tiktok_funnel"
    kinds = [a["kind"] for a in pb["actions"]]
    assert kinds == ["prepare_brief", "request_video", "publish_video"]
    assert pb["actions"][2]["requires_approval"] is True


def test_funnel_end_to_end():
    channel = _seed()
    run = control.create_run(B, "tiktok_funnel", spec={"offer_id": "guide_01"},
                              budget_amount=50.0, budget_currency="EUR", created_by="human")
    run_id = run["id"]
    [brief, video, publish] = control.materialize_actions(run_id)

    # --- content brief + video artifact request (free, no approval) ---
    r1 = control.execute_action(run_id, brief, actor="orbit")
    assert r1["status"] == "executed" and r1["evidence_id"]
    r2 = control.execute_action(run_id, video, actor="orbit")
    assert r2["status"] == "executed" and r2["evidence_id"]

    # --- simulated publish behind approval boundary ---
    r3 = control.execute_action(run_id, publish, actor="orbit")
    assert r3["status"] == "awaiting_approval"
    control.approve_action(run_id, publish, actor="human")
    r4 = control.execute_action(run_id, publish, actor="orbit")
    assert r4["status"] == "executed" and r4["evidence_id"]

    # --- funnel setup ---
    f = funnel.setup_funnel(B, run_id, "guide_01")
    assert f["run_id"] == run_id
    f2 = funnel.setup_funnel(B, run_id, "guide_01")  # idempotent
    assert f2["duplicate"] is True

    # --- idempotent TikTok comment with keyword GO ---
    lead = funnel.handle_comment(B, run_id, "@user1", "GO", keyword="GO")
    assert lead is not None and lead["handle"] == "@user1" and lead["consent"] is True
    lead_id = lead["id"]

    # same comment → same lead, no duplicate
    lead_dup = funnel.handle_comment(B, run_id, "@user1", "GO", keyword="GO")
    assert lead_dup["id"] == lead_id and lead_dup["duplicate"] is True

    # comment without keyword → no lead
    assert funnel.handle_comment(B, run_id, "@user2", "nice video", keyword="GO") is None

    # different user with GO → new lead
    lead2 = funnel.handle_comment(B, run_id, "@user2", "GO!", keyword="GO")
    assert lead2 is not None and lead2["id"] != lead_id

    # same user, different comment text → same lead (no duplicate)
    lead2b = funnel.handle_comment(B, run_id, "@user2", "GO please", keyword="GO")
    assert lead2b["id"] == lead2["id"] and lead2b["duplicate"] is True

    # --- deterministic fake DM adapter delivers a guide ---
    dm = funnel.deliver_guide(B, run_id, lead_id, guide_text="Voici votre guide")
    assert dm["status"] == "delivered" and dm["delivery_ref"]
    dm_dup = funnel.deliver_guide(B, run_id, lead_id, guide_text="Voici votre guide")
    assert dm_dup["id"] == dm["id"] and dm_dup["duplicate"] is True

    # --- follow-up task (idempotent) ---
    fu = funnel.create_follow_up(B, run_id, lead_id)
    assert fu["task_id"] is not None
    fu_dup = funnel.create_follow_up(B, run_id, lead_id)
    assert fu_dup["task_id"] == fu["task_id"] and fu_dup["duplicate"] is True

    # --- conversion event (idempotent) ---
    conv = funnel.record_conversion(B, run_id, lead_id, amount=9.0, currency="EUR")
    assert conv["kind"] == "conversion" and conv["amount"] == 9.0
    conv_dup = funnel.record_conversion(B, run_id, lead_id, amount=9.0, currency="EUR")
    assert conv_dup["id"] == conv["id"] and conv_dup["duplicate"] is True

    # --- snapshot exposes metrics, evidence, approvals, audit ---
    snap = funnel.funnel_snapshot(B, run_id)
    assert snap["funnel"]["offer_id"] == "guide_01"
    assert snap["funnel"]["keyword"] == "GO"
    m = snap["metrics"]
    assert m["comments"] >= 3   # @user1 GO, @user2 nice video, @user2 GO!, @user2 GO please
    assert m["leads"] == 2
    assert m["dms"] == 1
    assert m["follow_ups"] == 1
    assert m["conversions"] == 1
    assert len(snap["leads"]) == 2
    assert snap["approvals"]  # publish action was approved
    assert snap["evidence"]   # at least the publish evidence
    assert snap["events"]     # audit trail


def test_pause_resume_never_duplicates():
    _seed()
    run = control.create_run(B, "tiktok_funnel", spec={"offer_id": "g2"},
                              budget_amount=50.0, budget_currency="EUR", created_by="human")
    run_id = run["id"]
    [brief, video, publish] = control.materialize_actions(run_id)
    control.execute_action(run_id, brief, actor="orbit")
    control.execute_action(run_id, video, actor="orbit")
    control.approve_action(run_id, publish, actor="human")
    control.execute_action(run_id, publish, actor="orbit")

    funnel.setup_funnel(B, run_id, "g2")

    # capture a lead
    lead = funnel.handle_comment(B, run_id, "@pause1", "GO")
    lead_id = lead["id"]
    funnel.deliver_guide(B, run_id, lead_id, guide_text="guide")
    funnel.create_follow_up(B, run_id, lead_id)
    funnel.record_conversion(B, run_id, lead_id, amount=19.0, currency="EUR")

    # pause
    control.pause_run(run_id, actor="human")

    # funnel operations are blocked while paused
    with pytest.raises(funnel.FunnelError):
        funnel.handle_comment(B, run_id, "@pause2", "GO")
    with pytest.raises(funnel.FunnelError):
        funnel.deliver_guide(B, run_id, lead_id, guide_text="guide")
    with pytest.raises(funnel.FunnelError):
        funnel.create_follow_up(B, run_id, lead_id)
    with pytest.raises(funnel.FunnelError):
        funnel.record_conversion(B, run_id, lead_id, amount=19.0, currency="EUR")

    # resume
    control.resume_run(run_id, actor="human")

    # retry after resume: everything is idempotent, no duplicates
    lead_dup = funnel.handle_comment(B, run_id, "@pause1", "GO")
    assert lead_dup["id"] == lead_id and lead_dup["duplicate"] is True
    dm_dup = funnel.deliver_guide(B, run_id, lead_id, guide_text="guide")
    assert dm_dup["duplicate"] is True
    fu_dup = funnel.create_follow_up(B, run_id, lead_id)
    assert fu_dup["duplicate"] is True
    conv_dup = funnel.record_conversion(B, run_id, lead_id, amount=19.0, currency="EUR")
    assert conv_dup["duplicate"] is True

    snap = funnel.funnel_snapshot(B, run_id)
    assert snap["metrics"]["leads"] == 1
    assert snap["metrics"]["dms"] == 1
    assert snap["metrics"]["follow_ups"] == 1
    assert snap["metrics"]["conversions"] == 1


def test_business_isolation():
    _seed(B)
    _seed("other_business")
    run = control.create_run(B, "tiktok_funnel", spec={}, budget_amount=10.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    [b, v, p] = control.materialize_actions(run_id)
    funnel.setup_funnel(B, run_id, "iso")
    funnel.handle_comment(B, run_id, "@iso", "GO")

    # other business cannot see or touch this funnel
    with pytest.raises(funnel.FunnelError):
        funnel.handle_comment("other_business", run_id, "@x", "GO")
    with pytest.raises(funnel.FunnelError):
        funnel.funnel_snapshot("other_business", run_id)


def test_events_and_evidence_are_journaled():
    _seed()
    run = control.create_run(B, "tiktok_funnel", spec={}, budget_amount=10.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    [b, v, p] = control.materialize_actions(run_id)
    control.execute_action(run_id, b, actor="orbit")
    control.execute_action(run_id, v, actor="orbit")
    control.approve_action(run_id, p, actor="human")
    control.execute_action(run_id, p, actor="orbit")
    funnel.setup_funnel(B, run_id, "ev")
    lead = funnel.handle_comment(B, run_id, "@ev", "GO")
    funnel.deliver_guide(B, run_id, lead["id"], guide_text="g")
    funnel.create_follow_up(B, run_id, lead["id"])
    funnel.record_conversion(B, run_id, lead["id"], amount=9.0, currency="EUR")

    # audit trail via events
    event_types = {e["type"] for e in tasks.events(task_id=run_id, limit=200)}
    assert "funnel.setup" in event_types
    assert "funnel.comment.received" in event_types
    assert "funnel.lead.captured" in event_types
    assert "funnel.dm.delivered" in event_types
    assert "funnel.follow_up.created" in event_types
    assert "funnel.conversion" in event_types
    assert "action.approved" in event_types

    # evidence from strategy
    evidence_items = strategy.list_items("evidence", B, status="active", limit=100)
    funnel_evidence = [e for e in evidence_items if "funnel" in (e.get("summary") or "").lower()
                       or "dm" in (e.get("summary") or "").lower()
                       or "conversion" in (e.get("summary") or "").lower()]
    assert len(funnel_evidence) >= 2  # DM + conversion at minimum


def test_conversion_records_ledger_entry():
    _seed()
    run = control.create_run(B, "tiktok_funnel", spec={}, budget_amount=50.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    [b, v, p] = control.materialize_actions(run_id)
    control.execute_action(run_id, b, actor="orbit")
    control.execute_action(run_id, v, actor="orbit")
    control.approve_action(run_id, p, actor="human")
    control.execute_action(run_id, p, actor="orbit")
    funnel.setup_funnel(B, run_id, "cash")
    lead = funnel.handle_comment(B, run_id, "@cash", "GO")
    funnel.deliver_guide(B, run_id, lead["id"], guide_text="g")
    conv = funnel.record_conversion(B, run_id, lead["id"], amount=27.0, currency="EUR")
    assert conv["amount"] == 27.0 and conv["currency"] == "EUR"

    # the conversion is reflected in the cash summary
    summary = economy.cash_summary(B)
    assert summary.get("EUR", {}).get("in_observed", 0) >= 27.0
