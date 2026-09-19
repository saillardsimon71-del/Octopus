"""Simulated acquisition funnel integrated with the business control plane.

Deterministic, no network : un commentaire TikTok avec mot-clé capture un lead consentant,
un faux adaptateur DM livre un guide, un follow-up task est créé, et la conversion est
enregistrée au grand livre. Tout est idempotent et audité.

Réutilise : octopus.control (playbook, actions, approbation), journal (schema v8),
economy (grand livre), strategy (preuves), tasks (file, événements).
"""
from __future__ import annotations

import hashlib
import time

from . import economy, journal, strategy, tasks
from .strategy import StrategyError


class FunnelError(StrategyError):
    pass


def _business(business: str) -> str:
    return strategy._business(business)


def _text(value, name: str) -> str:
    return strategy._text(value, name)


def _run_status(conn, run_id: int, business: str) -> str:
    row = conn.execute("SELECT status FROM business_runs WHERE id=? AND business=?", (run_id, business)).fetchone()
    if row is None:
        raise FunnelError(f"run #{run_id} introuvable pour {business!r}")
    return row["status"]


def _check_running(conn, run_id: int, business: str) -> None:
    status = _run_status(conn, run_id, business)
    if status != "running":
        raise FunnelError(f"run #{run_id} {status} : opération funnel impossible")


def _emit(conn, business: str, run_id: int, type_: str, data: dict | None = None) -> None:
    tasks._emit(conn, business, run_id, type_, data)


def _hash(*parts) -> str:
    blob = "|".join(str(p) for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def setup_funnel(business: str, run_id: int, offer_id: str, keyword: str = "GO") -> dict:
    """Liaison idempotente entre un business_run et le funnel (offer + mot-clé)."""
    business = _business(business)
    offer_id = _text(offer_id, "offer_id")
    keyword = _text(keyword, "keyword").upper()
    now = time.time()
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        existing = conn.execute("SELECT * FROM funnel_runs WHERE run_id=? AND business=?", (run_id, business)).fetchone()
        if existing:
            return {"run_id": run_id, "offer_id": existing["offer_id"], "keyword": existing["keyword"],
                    "duplicate": True}
        conn.execute("INSERT INTO funnel_runs (run_id, business, offer_id, keyword, created_at) VALUES (?, ?, ?, ?, ?)",
                     (run_id, business, offer_id, keyword, now))
        _emit(conn, business, run_id, "funnel.setup", {"run_id": run_id, "offer_id": offer_id, "keyword": keyword})
    return {"run_id": run_id, "offer_id": offer_id, "keyword": keyword, "duplicate": False}


def handle_comment(business: str, run_id: int, handle: str, text: str, keyword: str | None = None) -> dict | None:
    """Enregistre un commentaire TikTok simulé. Si le mot-clé est présent, capture un lead
    consentant (idempotent par user). Renvoie le lead ou None si pas de mot-clé.
    """
    business = _business(business)
    handle = _text(handle, "handle")
    text = _text(text, "text")
    now = time.time()
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        # Resolve keyword from funnel_runs if not specified
        if keyword is None:
            fr = conn.execute("SELECT keyword FROM funnel_runs WHERE run_id=? AND business=?", (run_id, business)).fetchone()
            if fr is None:
                raise FunnelError(f"funnel non configuré pour le run #{run_id}")
            keyword = fr["keyword"]
        else:
            keyword = keyword.upper()

        # Record comment event (idempotent)
        comment_key = f"comment:{run_id}:{handle}:{_hash(text)}"
        existing_comment = conn.execute("SELECT id FROM funnel_events WHERE idempotency_key=?", (comment_key,)).fetchone()
        if not existing_comment:
            conn.execute(
                "INSERT INTO funnel_events (business, run_id, kind, idempotency_key, source_ref, created_at) "
                "VALUES (?, ?, 'comment', ?, ?, ?)",
                (business, run_id, comment_key, f"tiktok:comment:{handle}:{_hash(text)[:12]}", now))
            _emit(conn, business, run_id, "funnel.comment.received", {"handle": handle, "keyword_match": keyword.upper() in text.upper()})

        # If keyword not in text, no lead
        if keyword.upper() not in text.upper():
            return None

        # Get or create lead (idempotent by business + handle)
        existing_lead = conn.execute("SELECT id, consent FROM funnel_leads WHERE business=? AND handle=?",
                                      (business, handle)).fetchone()
        if existing_lead:
            lead_id = int(existing_lead["id"])
            _emit(conn, business, run_id, "funnel.comment.received",
                  {"handle": handle, "duplicate": True})
            return {"id": lead_id, "handle": handle, "consent": bool(existing_lead["consent"]), "duplicate": True}

        lead_id = int(conn.execute(
            "INSERT INTO funnel_leads (business, run_id, handle, consent, source_ref, idempotency_key, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?)",
            (business, run_id, handle, f"tiktok:comment:{handle}:{_hash(text)[:12]}",
             f"lead:{run_id}:{handle}", now)).lastrowid)
        _emit(conn, business, run_id, "funnel.lead.captured", {"handle": handle, "lead_id": lead_id})
        return {"id": lead_id, "handle": handle, "consent": True, "duplicate": False}


def deliver_guide(business: str, run_id: int, lead_id: int, guide_text: str = "Guide") -> dict:
    """Livre un guide via un faux adaptateur DM déterministe. Idempotent par (run, lead)."""
    business = _business(business)
    guide_text = _text(guide_text, "guide_text")
    now = time.time()
    dm_key = f"dm:{run_id}:{lead_id}"
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        # Verify lead exists
        lead = conn.execute("SELECT * FROM funnel_leads WHERE id=? AND business=? AND run_id=?",
                            (lead_id, business, run_id)).fetchone()
        if lead is None:
            raise FunnelError(f"lead #{lead_id} introuvable pour le run #{run_id}")
        # Idempotent check
        existing = conn.execute("SELECT * FROM funnel_events WHERE idempotency_key=? AND kind='dm'", (dm_key,)).fetchone()
        if existing:
            delivery_ref = existing["source_ref"]
            _emit(conn, business, run_id, "funnel.dm.delivered", {"lead_id": lead_id, "duplicate": True})
            return {"id": int(existing["id"]), "lead_id": lead_id, "status": "delivered",
                    "delivery_ref": delivery_ref, "duplicate": True}
        delivery_ref = f"dm:{run_id}:{lead_id}:{_hash(guide_text)[:12]}"
        event_id = int(conn.execute(
            "INSERT INTO funnel_events (business, run_id, kind, lead_id, idempotency_key, source_ref, created_at) "
            "VALUES (?, ?, 'dm', ?, ?, ?, ?)",
            (business, run_id, lead_id, dm_key, delivery_ref, now)).lastrowid)
        _emit(conn, business, run_id, "funnel.dm.delivered", {"lead_id": lead_id, "event_id": event_id})
    # Record evidence (outside transaction for strategy.create)
    evidence_id = strategy.create(
        "evidence", business, f"DM guide livré au lead #{lead_id} (run #{run_id})",
        created_by="funnel:dm_adapter", nature="computed", source_type="channel_action",
        source_ref=delivery_ref, captured_at=now, observation=guide_text[:500],
        channel_id=None, metric="dm_delivered", value=1.0, unit="count")
    # Link event to evidence
    with tasks._tx() as conn:
        conn.execute("UPDATE funnel_events SET evidence_id=? WHERE id=?", (evidence_id, event_id))
    return {"id": event_id, "lead_id": lead_id, "status": "delivered",
            "delivery_ref": delivery_ref, "duplicate": False}


def create_follow_up(business: str, run_id: int, lead_id: int) -> dict:
    """Crée un follow-up task idempotent (idempotency_key par run+lead)."""
    business = _business(business)
    now = time.time()
    fu_key = f"follow_up:{run_id}:{lead_id}"
    # Check existence and validate (first transaction)
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        lead = conn.execute("SELECT * FROM funnel_leads WHERE id=? AND business=? AND run_id=?",
                            (lead_id, business, run_id)).fetchone()
        if lead is None:
            raise FunnelError(f"lead #{lead_id} introuvable pour le run #{run_id}")
        existing = conn.execute("SELECT * FROM funnel_events WHERE idempotency_key=? AND kind='follow_up'", (fu_key,)).fetchone()
        if existing:
            _ref = existing["source_ref"] or ""
            task_id = int(_ref[5:]) if _ref.startswith("task#") else 0
            _emit(conn, business, run_id, "funnel.follow_up.created", {"lead_id": lead_id, "duplicate": True})
            return {"task_id": task_id, "lead_id": lead_id, "duplicate": True}
    # Enqueue the task outside the transaction (idempotent via idempotency_key)
    task_id = tasks.enqueue(business, "funnel.follow_up",
                            {"run_id": run_id, "lead_id": lead_id, "handle": lead["handle"]},
                            idempotency_key=fu_key)
    # Record the event (second transaction)
    with tasks._tx() as conn:
        event_id = int(conn.execute(
            "INSERT INTO funnel_events (business, run_id, kind, lead_id, idempotency_key, source_ref, created_at) "
            "VALUES (?, ?, 'follow_up', ?, ?, ?, ?)",
            (business, run_id, lead_id, fu_key, f"task#{task_id}", now)).lastrowid)
        _emit(conn, business, run_id, "funnel.follow_up.created", {"lead_id": lead_id, "task_id": task_id})
    return {"task_id": task_id, "lead_id": lead_id, "event_id": event_id, "duplicate": False}


def record_conversion(business: str, run_id: int, lead_id: int, amount: float, currency: str = "EUR") -> dict:
    """Enregistre un événement de conversion (revenue) idempotent, avec écriture au grand livre."""
    business = _business(business)
    currency = strategy._currency(currency) if hasattr(strategy, "_currency") else currency.upper()
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        raise FunnelError("amount doit être strictement positif")
    now = time.time()
    conv_key = f"conversion:{run_id}:{lead_id}"
    with tasks._tx() as conn:
        _check_running(conn, run_id, business)
        lead = conn.execute("SELECT * FROM funnel_leads WHERE id=? AND business=? AND run_id=?",
                            (lead_id, business, run_id)).fetchone()
        if lead is None:
            raise FunnelError(f"lead #{lead_id} introuvable pour le run #{run_id}")
        existing = conn.execute("SELECT * FROM funnel_events WHERE idempotency_key=? AND kind='conversion'", (conv_key,)).fetchone()
        if existing:
            _emit(conn, business, run_id, "funnel.conversion", {"lead_id": lead_id, "duplicate": True})
            return {"id": int(existing["id"]), "lead_id": lead_id, "kind": "conversion",
                    "amount": existing["amount"], "currency": existing["currency"],
                    "evidence_id": existing["evidence_id"], "duplicate": True}
        event_id = int(conn.execute(
            "INSERT INTO funnel_events (business, run_id, kind, lead_id, idempotency_key, source_ref, amount, currency, created_at) "
            "VALUES (?, ?, 'conversion', ?, ?, ?, ?, ?, ?)",
            (business, run_id, lead_id, conv_key, f"stripe:simulated:{_hash(run_id, lead_id)[:12]}",
             float(amount), currency, now)).lastrowid)
        _emit(conn, business, run_id, "funnel.conversion", {"lead_id": lead_id, "amount": amount, "currency": currency})
    # Record ledger entry (observed cash in)
    entry_id = economy.record_cash(
        business, "in", amount, currency, "funnel_conversion",
        nature="observed", created_by="funnel:conversion", source_ref=f"stripe:simulated:{_hash(run_id, lead_id)[:12]}",
        occurred_at=now, channel_id=None)
    # Record evidence
    evidence_id = strategy.create(
        "evidence", business, f"Conversion lead #{lead_id} : {amount:g} {currency} (run #{run_id})",
        created_by="funnel:conversion", nature="observed", source_type="channel_action",
        source_ref=f"stripe:simulated:{_hash(run_id, lead_id)[:12]}", captured_at=now,
        observation=f"Vente guide {amount:g} {currency}", metric="cash_net", value=float(amount), unit=currency,
        channel_id=None)
    with tasks._tx() as conn:
        conn.execute("UPDATE funnel_events SET evidence_id=? WHERE id=?", (evidence_id, event_id))
    return {"id": event_id, "lead_id": lead_id, "kind": "conversion",
            "amount": float(amount), "currency": currency, "evidence_id": evidence_id, "duplicate": False}


def funnel_snapshot(business: str, run_id: int) -> dict:
    """État complet du funnel pour un run : métriques, leads, approbations, preuves, événements."""
    business = _business(business)
    conn = journal.connect()
    try:
        row = conn.execute("SELECT * FROM funnel_runs WHERE run_id=? AND business=?", (run_id, business)).fetchone()
        if row is None:
            raise FunnelError(f"funnel non configuré pour le run #{run_id}")
        funnel_info = dict(row)
        leads = [dict(r) for r in conn.execute(
            "SELECT * FROM funnel_leads WHERE run_id=? AND business=? ORDER BY id", (run_id, business)).fetchall()]
        for lead in leads:
            lead["consent"] = bool(lead["consent"])
        events = [dict(r) for r in conn.execute(
            "SELECT * FROM funnel_events WHERE run_id=? AND business=? ORDER BY id", (run_id, business)).fetchall()]
        metrics = {
            "comments": sum(1 for e in events if e["kind"] == "comment"),
            "leads": len(leads),
            "dms": sum(1 for e in events if e["kind"] == "dm"),
            "follow_ups": sum(1 for e in events if e["kind"] == "follow_up"),
            "conversions": sum(1 for e in events if e["kind"] == "conversion"),
        }
        # Revenue from conversions
        metrics["revenue"] = round(sum(e["amount"] or 0 for e in events if e["kind"] == "conversion"), 6)
    finally:
        conn.close()

    # Evidence from strategy (funnel-related)
    all_evidence = strategy.list_items("evidence", business, status="active", limit=200)
    funnel_evidence = [e for e in all_evidence if "funnel" in (e.get("summary") or "").lower()
                       or "dm" in (e.get("summary") or "").lower()
                       or "conversion" in (e.get("summary") or "").lower()
                       or "lead" in (e.get("summary") or "").lower()]
    # Also include action evidence from the control plane (publish, brief, video)
    from . import control as _control
    snap_control = _control.snapshot(run_id)
    action_evidence = snap_control["evidence"]
    # Approvals : toutes les actions qui ont traversé la frontière d'approbation humaine
    approvals = [a for a in snap_control["actions"] if a["requires_approval"]]

    return {
        "funnel": {"run_id": run_id, "offer_id": funnel_info["offer_id"], "keyword": funnel_info["keyword"]},
        "metrics": metrics,
        "leads": leads,
        "events": tasks.events(task_id=run_id, limit=200),
        "evidence": action_evidence + funnel_evidence,
        "approvals": approvals,
    }
