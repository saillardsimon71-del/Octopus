"""Safe commerce workflow : checkout, paiement simulé, réservation budget, approbation
humaine, exécution commande fournisseur.

Déterministe, hors-ligne : aucun appel Stripe, aucun appel fournisseur réel.
Toutes les étapes sont idempotentes et reconstruisibles depuis le journal.

Réutilise : octopus.control (run, approbation), octopus.economy (enveloppe, grand livre),
octopus.actions (exécution sur canal avec preuve), octopus.tasks (événements),
octopus.journal (persistance), octopus.strategy (preuves).
"""
from __future__ import annotations

import hashlib
import json
import time

from . import actions, economy, journal, strategy, tasks
from .strategy import StrategyError


class CommerceError(StrategyError):
    pass


def _business(business: str) -> str:
    return strategy._business(business)


def _text(value, name: str) -> str:
    return strategy._text(value, name)


def _hash(*parts) -> str:
    blob = "|".join(str(p) for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _emit(conn, business: str, run_id: int, type_: str, data: dict | None = None) -> None:
    tasks._emit(conn, business, run_id, type_, data)


def _check_running(conn, run_id: int, business: str) -> None:
    row = conn.execute("SELECT status FROM business_runs WHERE id=? AND business=?", (run_id, business)).fetchone()
    if row is None:
        raise CommerceError(f"run #{run_id} introuvable pour {business!r}")
    if row["status"] != "running":
        raise CommerceError(f"run #{run_id} {row['status']} : opération commerce impossible")


def _get_order(conn, business: str, run_id: int, order_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM commerce_orders WHERE order_id=? AND business=? AND run_id=?",
        (order_id, business, run_id),
    ).fetchone()
    if row is None:
        raise CommerceError(f"commande {order_id!r} introuvable pour le run #{run_id} de {business!r}")
    return dict(row)


def _set_order(conn, order_id: str, business: str, status: str, **fields) -> None:
    fields.update(status=status, updated_at=time.time())
    conn.execute(
        f"UPDATE commerce_orders SET {', '.join(f'{k}=?' for k in fields)} WHERE order_id=? AND business=?",
        [*fields.values(), order_id, business],
    )


def create_order(business: str, run_id: int, amount: float, currency: str, supplier_id: str,
                 *, idempotency_key: str | None = None) -> dict:
    """Crée une commande commerce. Idempotente par idempotency_key."""
    business = _business(business)
    supplier_id = _text(supplier_id, "supplier_id")
    currency = currency.strip().upper()
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        raise CommerceError("amount doit être strictement positif")
    now = time.time()
    key = idempotency_key or f"order:{run_id}:{supplier_id}:{_hash(amount, currency)}"
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        existing = conn.execute("SELECT * FROM commerce_orders WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return {"order_id": existing["order_id"], "status": existing["status"],
                    "amount": existing["amount"], "currency": existing["currency"],
                    "supplier_id": existing["supplier_id"], "duplicate": True}
        order_id = f"ord-{_hash(run_id, supplier_id, amount, currency, str(now))}"
        conn.execute(
            "INSERT INTO commerce_orders (business, run_id, order_id, supplier_id, amount, currency, "
            "status, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)",
            (business, run_id, order_id, supplier_id, float(amount), currency, key, now, now),
        )
        _emit(conn, business, run_id, "commerce.order.created", {"order_id": order_id, "amount": amount})
    return {"order_id": order_id, "status": "created", "amount": float(amount),
            "currency": currency, "supplier_id": supplier_id, "duplicate": False}


def confirm_payment(business: str, run_id: int, order_id: str) -> dict:
    """Confirme un paiement simulé (déterministe, aucun Stripe). Idempotent par order_id."""
    business = _business(business)
    now = time.time()
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        order = _get_order(conn, business, run_id, order_id)
        if order["status"] == "cancelled":
            raise CommerceError(f"commande {order_id} annulée : paiement impossible")
        existing = conn.execute("SELECT * FROM commerce_payments WHERE order_id=? AND business=?",
                                (order_id, business)).fetchone()
        if existing:
            _emit(conn, business, run_id, "commerce.payment.confirmed", {"order_id": order_id, "duplicate": True})
            return {"order_id": order_id, "status": "confirmed", "payment_ref": existing["payment_ref"],
                    "duplicate": True}
        payment_ref = f"pay:sim:{_hash(order_id, business)[:16]}"
        conn.execute(
            "INSERT INTO commerce_payments (business, order_id, amount, currency, status, payment_ref, "
            "idempotency_key, created_at) VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?)",
            (business, order_id, order["amount"], order["currency"], payment_ref,
             f"payment:{order_id}", now),
        )
        _set_order(conn, order_id, business, "paid", payment_ref=payment_ref)
        _emit(conn, business, run_id, "commerce.payment.confirmed", {"order_id": order_id, "payment_ref": payment_ref})
    return {"order_id": order_id, "status": "confirmed", "payment_ref": payment_ref, "duplicate": False}


def reserve_budget(business: str, run_id: int, order_id: str) -> dict:
    """Réserve le budget (enveloppe). Exige que le paiement soit confirmé. Idempotent."""
    business = _business(business)
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        order = _get_order(conn, business, run_id, order_id)
        if order["status"] in ("reserved", "approved", "executed"):
            _emit(conn, business, run_id, "commerce.budget.reserved", {"order_id": order_id, "duplicate": True})
            return {"order_id": order_id, "status": "reserved", "spend_request_id": order["spend_request_id"],
                    "duplicate": True}
        if order["payment_ref"] is None:
            raise CommerceError(f"commande {order_id} : paiement non confirmé avant réservation budget")
        if order["status"] == "cancelled":
            raise CommerceError(f"commande {order_id} annulée : réservation impossible")
    # Enveloppe via economy (hors transaction car economy gère sa propre tx)
    decision = economy.authorize_spend(
        business, order["amount"], order["currency"],
        f"commerce order {order_id}", requested_by="commerce.reserve")
    if decision["status"] != "authorized":
        with tasks._tx() as conn:
            _set_order(conn, order_id, business, "blocked", reason=decision["reason"])
            _emit(conn, business, run_id, "commerce.budget.blocked",
                  {"order_id": order_id, "reason": decision["reason"]})
        return {"order_id": order_id, "status": "blocked", "reason": decision["reason"], "duplicate": False}
    spend_request_id = decision["request_id"]
    with tasks._tx() as conn:
        _set_order(conn, order_id, business, "reserved", spend_request_id=spend_request_id)
        _emit(conn, business, run_id, "commerce.budget.reserved",
              {"order_id": order_id, "spend_request_id": spend_request_id})
    return {"order_id": order_id, "status": "reserved", "spend_request_id": spend_request_id, "duplicate": False}


def approve_order(business: str, run_id: int, order_id: str, actor: str) -> dict:
    """Approbation humaine explicite avant exécution commande fournisseur."""
    business = _business(business)
    actor = _text(actor, "actor")
    if actor != "human":
        raise CommerceError("seul un humain approuve une commande fournisseur")
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        order = _get_order(conn, business, run_id, order_id)
        if order["status"] == "approved":
            _emit(conn, business, run_id, "commerce.order.approved", {"order_id": order_id, "duplicate": True})
            return {"order_id": order_id, "status": "approved", "duplicate": True}
        if order["status"] != "reserved":
            raise CommerceError(f"commande {order_id} en statut {order['status']} : approbation impossible (reserved requis)")
        _set_order(conn, order_id, business, "approved")
        _emit(conn, business, run_id, "commerce.order.approved", {"order_id": order_id, "actor": actor})
    return {"order_id": order_id, "status": "approved", "duplicate": False}


def place_supplier_order(business: str, run_id: int, order_id: str, actor: str) -> dict:
    """Exécute la commande fournisseur. Exige paiement + réservation + approbation. Idempotent."""
    business = _business(business)
    actor = _text(actor, "actor")
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        order = _get_order(conn, business, run_id, order_id)
        if order["status"] == "executed":
            _emit(conn, business, run_id, "commerce.order.executed", {"order_id": order_id, "duplicate": True})
            return {"order_id": order_id, "status": "executed", "supplier_ref": order["supplier_ref"],
                    "evidence_id": order.get("evidence_id"), "duplicate": True}
        if order["status"] == "cancelled":
            raise CommerceError(f"commande {order_id} annulée : exécution impossible")
        if order["payment_ref"] is None:
            raise CommerceError(f"commande {order_id} : paiement non confirmé avant exécution")
        if order["status"] != "approved":
            raise CommerceError(f"commande {order_id} : approbation humaine requise avant exécution (statut {order['status']})")
    # Exécution via actions sur le canal supplier
    channel = economy.channels(business, status="active", capability="order")
    channel = next((c for c in channel if c["kind"] == "supplier"), None)
    if channel is None:
        with tasks._tx() as conn:
            _set_order(conn, order_id, business, "blocked", reason="aucun canal supplier actif")
            _emit(conn, business, run_id, "commerce.order.blocked", {"order_id": order_id})
        return {"order_id": order_id, "status": "blocked", "reason": "aucun canal supplier actif", "duplicate": False}
    result = actions.propose(
        business, channel["id"], "place_order",
        {"order_id": order_id, "supplier_id": order["supplier_id"], "amount": order["amount"],
         "currency": order["currency"]},
        requested_by=actor,
        idempotency_key=f"supplier:{order_id}",
    )
    with tasks._tx() as conn:
        if result["status"] == "executed":
            _set_order(conn, order_id, business, "executed",
                       supplier_ref=f"supplier:{channel['id']}:{order_id}",
                       evidence_id=result.get("evidence_id"))
            _emit(conn, business, run_id, "commerce.order.executed", {"order_id": order_id})
        else:
            _set_order(conn, order_id, business, "failed", reason=result.get("reason"))
            _emit(conn, business, run_id, "commerce.order.failed", {"order_id": order_id, "reason": result.get("reason")})
    return {"order_id": order_id, "status": result["status"], "supplier_ref": f"supplier:{channel['id']}:{order_id}",
            "evidence_id": result.get("evidence_id"), "duplicate": False}


def cancel_order(business: str, run_id: int, order_id: str, actor: str, reason: str = "annulée") -> dict:
    """Annule une commande. Idempotent."""
    business = _business(business)
    actor = _text(actor, "actor")
    with tasks._tx() as conn:
        order = _get_order(conn, business, run_id, order_id)
        if order["status"] == "cancelled":
            _emit(conn, business, run_id, "commerce.order.cancelled", {"order_id": order_id, "duplicate": True})
            return {"order_id": order_id, "status": "cancelled", "duplicate": True}
        if order["status"] == "executed":
            raise CommerceError(f"commande {order_id} déjà exécutée : annulation impossible")
        _set_order(conn, order_id, business, "cancelled", reason=reason)
        _emit(conn, business, run_id, "commerce.order.cancelled", {"order_id": order_id, "actor": actor, "reason": reason})
    return {"order_id": order_id, "status": "cancelled", "duplicate": False}


def commerce_snapshot(business: str, run_id: int) -> dict:
    """État visible par l'opérateur : commandes, paiements, approbations, événements."""
    business = _business(business)
    conn = journal.connect()
    try:
        row = conn.execute("SELECT status FROM business_runs WHERE id=? AND business=?", (run_id, business)).fetchone()
        if row is None:
            raise CommerceError(f"run #{run_id} introuvable pour {business!r}")
        orders = [dict(r) for r in conn.execute(
            "SELECT * FROM commerce_orders WHERE business=? AND run_id=? ORDER BY id", (business, run_id)).fetchall()]
        payments = [dict(r) for r in conn.execute(
            "SELECT * FROM commerce_payments WHERE business=? ORDER BY id", (business,)).fetchall()]
    finally:
        conn.close()
    events = tasks.events(task_id=run_id, limit=200)
    return {
        "orders": orders,
        "payments": payments,
        "events": events,
    }