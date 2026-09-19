"""Part A — Safe commerce workflow (deterministic, no network).

Vertical slice: checkout → payment confirmation → budget reservation → human
approval → supplier order execution. Every step is idempotent and reconstructable
from the journal. Ordering cannot occur before payment AND approval.

No live Stripe, no live supplier: all adapters are deterministic fakes. No code
here claims any live integration.
"""
from __future__ import annotations

import pytest

from octopus import commerce, control, economy, journal, strategy, tasks

B = "commerce_test"


def _seed(business=B, budget=500.0):
    """Business with allowance + active supplier channel (deterministic executor)."""
    actions_mod = __import__("octopus.actions", fromlist=["actions"])

    def supplier_exec(channel, payload):
        return {"observation": f"supplier order {payload.get('order_id')}",
                "source_ref": f"supplier:sim:{channel['id']}",
                "metric": "supplier_order", "value": 1, "unit": "count"}

    actions_mod.register_executor("supplier", "place_order", supplier_exec)
    channel = economy.add_channel(business, "supplier", "FakeSupplier", created_by="human",
                                  capabilities=["order"], locator="sim://supplier")
    economy.update_channel(business, channel, actor="human", status="active", access="act")
    economy.grant_allowance(business, budget, "EUR", granted_by="human", rationale="enveloppe commerce")
    return channel


def test_cannot_order_before_payment():
    _seed()
    run = control.create_run(B, "web_launch", spec={"offer_id": "c1"},
                              budget_amount=50.0, budget_currency="EUR", created_by="human")
    run_id = run["id"]
    # Create order
    order = commerce.create_order(B, run_id, amount=10.0, currency="EUR",
                                  supplier_id="fake_supplier")
    oid = order["order_id"]
    # Try to place supplier order without payment
    with pytest.raises(commerce.CommerceError, match="paiement"):
        commerce.place_supplier_order(B, run_id, oid, actor="orbit")
    # Try reservation without payment
    with pytest.raises(commerce.CommerceError, match="paiement"):
        commerce.reserve_budget(B, run_id, oid)


def test_cannot_order_before_approval():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=50.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    order = commerce.create_order(B, run_id, amount=10.0, currency="EUR",
                                  supplier_id="fake_supplier")
    oid = order["order_id"]
    commerce.confirm_payment(B, run_id, oid)
    commerce.reserve_budget(B, run_id, oid)
    # No approval yet
    with pytest.raises(commerce.CommerceError, match="approbation"):
        commerce.place_supplier_order(B, run_id, oid, actor="orbit")


def test_full_flow_idempotent():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=50.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    order = commerce.create_order(B, run_id, amount=15.0, currency="EUR",
                                  supplier_id="fake_supplier")
    oid = order["order_id"]
    # Payment (idempotent)
    p1 = commerce.confirm_payment(B, run_id, oid)
    assert p1["status"] == "confirmed" and p1["payment_ref"]
    p2 = commerce.confirm_payment(B, run_id, oid)
    assert p2["duplicate"] is True and p2["payment_ref"] == p1["payment_ref"]
    # Reservation (idempotent)
    r1 = commerce.reserve_budget(B, run_id, oid)
    assert r1["status"] == "reserved"
    r2 = commerce.reserve_budget(B, run_id, oid)
    assert r2["duplicate"] is True
    # Approval
    commerce.approve_order(B, run_id, oid, actor="human")
    # Supplier order (idempotent)
    o1 = commerce.place_supplier_order(B, run_id, oid, actor="orbit")
    assert o1["status"] == "executed" and o1["supplier_ref"]
    o2 = commerce.place_supplier_order(B, run_id, oid, actor="orbit")
    assert o2["duplicate"] is True and o2["supplier_ref"] == o1["supplier_ref"]
    # Snapshot
    snap = commerce.commerce_snapshot(B, run_id)
    assert snap["orders"][0]["status"] == "executed"
    assert snap["payments"][0]["status"] == "confirmed"
    assert snap["events"]


def test_cancel_idempotent():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=50.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    order = commerce.create_order(B, run_id, amount=20.0, currency="EUR",
                                  supplier_id="fake_supplier")
    oid = order["order_id"]
    commerce.confirm_payment(B, run_id, oid)
    commerce.reserve_budget(B, run_id, oid)
    # Cancel before approval
    c1 = commerce.cancel_order(B, run_id, oid, actor="human", reason="test")
    assert c1["status"] == "cancelled"
    c2 = commerce.cancel_order(B, run_id, oid, actor="human", reason="test")
    assert c2["duplicate"] is True


def test_pause_blocks_commerce():
    _seed()
    run = control.create_run(B, "web_launch", spec={}, budget_amount=50.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    order = commerce.create_order(B, run_id, amount=10.0, currency="EUR",
                                  supplier_id="fake_supplier")
    oid = order["order_id"]
    control.pause_run(run_id, actor="human")
    with pytest.raises(commerce.CommerceError):
        commerce.confirm_payment(B, run_id, oid)
    control.resume_run(run_id, actor="human")
    p = commerce.confirm_payment(B, run_id, oid)
    assert p["status"] == "confirmed"


def test_business_isolation():
    _seed(B)
    _seed("other_biz")
    run = control.create_run(B, "web_launch", spec={}, budget_amount=10.0,
                              budget_currency="EUR", created_by="human")
    run_id = run["id"]
    order = commerce.create_order(B, run_id, amount=5.0, currency="EUR",
                                  supplier_id="s")
    oid = order["order_id"]
    with pytest.raises(commerce.CommerceError):
        commerce.confirm_payment("other_biz", run_id, oid)
