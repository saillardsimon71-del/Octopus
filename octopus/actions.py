"""Actions réelles sur des canaux économiques (publier, vendre, écrire, annoncer...), de façon générique.

Une action est proposée par un agent ; elle s'exécute seulement si :
1. le canal appartient au business, est actif et l'humain lui a accordé l'accès `act` ;
2. un exécuteur est enregistré pour (type de canal, action) — aucun n'est fourni par défaut ;
3. son coût éventuel est couvert par une enveloppe (`economy.authorize_spend`).
Sinon elle reste tracée avec la raison du blocage. Le résultat d'une exécution devient une preuve
observée (l'exécuteur doit renvoyer une source vérifiable), rattachée à l'expérience.
"""
from __future__ import annotations

import json
import time
from typing import Callable

from . import economy, journal, strategy, tasks
from .strategy import StrategyError

# executor(channel: dict, payload: dict) -> {"observation": str, "source_ref": str, "metric"?, "value"?, "unit"?}
_EXECUTORS: dict[tuple[str, str], tuple[Callable[[dict, dict], dict], str]] = {}


def register_executor(channel_kind: str, action: str, fn: Callable[[dict, dict], dict], *, cost_class: str) -> None:
    if cost_class not in {"local", "free_quota", "paid"}:
        raise ValueError("cost_class doit être local, free_quota ou paid")
    _EXECUTORS[(channel_kind.strip().lower(), action.strip().lower())] = (fn, cost_class)


def executors() -> list[tuple[str, str]]:
    return sorted(_EXECUTORS)


def _set(conn, action_id: int, business: str, status: str, **fields) -> None:
    fields.update(status=status, updated_at=time.time())
    conn.execute(f"UPDATE channel_actions SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                 [*fields.values(), action_id])
    tasks._emit(conn, business, None, f"action.{status}", {"id": action_id, "reason": fields.get("reason")})


def propose(business: str, channel_id: int, action: str, payload: dict | None = None, *, requested_by: str,
            experiment_id: int | None = None, spend_amount: float | None = None, spend_currency: str | None = None,
            idempotency_key: str | None = None) -> dict:
    """Enregistre puis tente l'action. Renvoie son état final (executed, blocked, failed)."""
    business = strategy._business(business)
    action = strategy._text(action, "action").lower()
    now = time.time()
    with tasks._tx() as conn:
        if idempotency_key:
            existing = conn.execute("SELECT id, status FROM channel_actions WHERE idempotency_key=?",
                                    (idempotency_key,)).fetchone()
            if existing:
                return {"action_id": existing["id"], "status": existing["status"], "duplicate": True}
        channel = conn.execute("SELECT * FROM economic_channels WHERE id=? AND business=?",
                               (channel_id, business)).fetchone()
        if channel is None:
            raise StrategyError(f"canal #{channel_id} introuvable pour {business!r}")
        if experiment_id is not None:
            strategy._fetch(conn, "experiment", experiment_id, business)
        action_id = int(conn.execute(
            "INSERT INTO channel_actions (business, channel_id, experiment_id, action, payload, status, requested_by, "
            "idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)",
            (business, channel_id, experiment_id, action, json.dumps(payload or {}, ensure_ascii=False),
             strategy._text(requested_by, "requested_by"), idempotency_key, now, now)).lastrowid)
        channel = dict(channel)
        reason = None
        if channel["status"] != "active":
            reason = f"canal {channel['status']} (activation requise)"
        elif channel["access"] != "act":
            reason = "accès 'act' non accordé par l'humain sur ce canal"
        elif (channel["kind"], action) not in _EXECUTORS:
            reason = f"aucun exécuteur pour {channel['kind']}:{action} (intégration à construire)"
        elif _EXECUTORS[(channel["kind"], action)][1] == "paid" and not spend_amount:
            reason = "exécuteur de cost class paid sans coût déclaré"
        if reason:
            _set(conn, action_id, business, "blocked", reason=reason, decided_by="policy:actions")
            return {"action_id": action_id, "status": "blocked", "reason": reason}
    spend_request_id = None
    if spend_amount:
        decision = economy.authorize_spend(business, spend_amount, spend_currency, f"action #{action_id} {action}",
                                           requested_by=requested_by, experiment_id=experiment_id)
        if decision["status"] != "authorized":
            with tasks._tx() as conn:
                _set(conn, action_id, business, "blocked", reason=f"dépense : {decision['reason']}",
                     decided_by="policy:actions")
            return {"action_id": action_id, "status": "blocked", "reason": f"dépense : {decision['reason']}"}
        spend_request_id = decision["request_id"]
    try:
        result = _EXECUTORS[(channel["kind"], action)][0](channel, payload or {})
        source = str(result.get("source_ref") or "").strip()
        if not source:
            raise StrategyError("l'exécuteur n'a pas renvoyé de source vérifiable")
    except Exception as exc:  # l'échec d'une intégration externe est un résultat, pas un plantage du moteur
        if spend_request_id:
            economy.cancel_spend(business, spend_request_id, actor="policy:actions")
        with tasks._tx() as conn:
            _set(conn, action_id, business, "failed", reason=f"{type(exc).__name__}: {exc}"[:500],
                 spend_request_id=spend_request_id, decided_by="policy:actions")
        return {"action_id": action_id, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500]}
    fields = {k: result[k] for k in ("metric", "unit") if result.get(k)}
    if result.get("value") is not None:
        fields["value"] = float(result["value"])
    evidence_id = strategy.create(
        "evidence", business, f"Résultat de l'action #{action_id} {action}", created_by=f"executor:{channel['kind']}",
        nature="observed", source_type="channel_action", source_ref=source, captured_at=time.time(),
        observation=str(result.get("observation") or ""), experiment_id=experiment_id, channel_id=channel_id, **fields)
    with tasks._tx() as conn:
        _set(conn, action_id, business, "executed", result=json.dumps(result, ensure_ascii=False, default=str),
             evidence_id=evidence_id, spend_request_id=spend_request_id, decided_by="policy:actions")
    return {"action_id": action_id, "status": "executed", "evidence_id": evidence_id, "spend_request_id": spend_request_id}


def list_actions(business: str, *, status: str | None = None, limit: int = 50) -> list[dict]:
    sql, params = "SELECT * FROM channel_actions WHERE business=?", [strategy._business(business)]
    if status:
        sql += " AND status=?"
        params.append(status)
    return [dict(r) for r in journal.query(sql + " ORDER BY id DESC LIMIT ?", tuple(params + [limit]))]
