"""Business Control Plane : crée un run d'activité depuis un playbook réutilisable,
matérialise des actions ordonnées, exige une approbation humaine pour les actions coûteuses
ou irréversibles, exécute idempotemment, comptabilise le budget et expose l'état.

Réutilise les briques existantes :
- journal/economy pour l'enveloppe de dépense et le grand livre ;
- actions pour l'exécution sur un canal avec preuve ;
- strategy pour la preuve observée ;
- tasks pour les événements d'audit.

Toute mutation passe par une transaction BEGIN IMMEDIATE et laisse un événement `run.*` ou
`action.*` dans `events`. L'état est reconstruisible depuis les tables persistées.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import actions, economy, journal, strategy, tasks
from .strategy import StrategyError

PLAYBOOKS = {
    "web_launch": {
        "id": "web_launch",
        "description": "Préparer un brief de contenu puis publier la page d'offre sur le site.",
        "actions": [
            {"kind": "prepare_brief", "label": "Préparer le brief", "cost_amount": 0, "requires_approval": False},
            {"kind": "publish_page", "label": "Publier la page", "cost_amount": 5.0,
             "cost_currency": "EUR", "requires_approval": True, "capability": "publish"},
        ],
    },
    "tiktok_funnel": {
        "id": "tiktok_funnel",
        "description": "Brief contenu, vidéo, publication sur TikTok, puis capture de leads via mot-clé.",
        "actions": [
            {"kind": "prepare_brief", "label": "Préparer le brief", "cost_amount": 0, "requires_approval": False},
            {"kind": "request_video", "label": "Demander la vidéo", "cost_amount": 0, "requires_approval": False},
            {"kind": "publish_video", "label": "Publier la vidéo", "cost_amount": 5.0,
             "cost_currency": "EUR", "requires_approval": True, "capability": "publish"},
        ],
    }
}

RUN_TRANSITIONS = {"running": {"paused", "done", "abandoned"}, "paused": {"running", "abandoned"},
                   "done": set(), "abandoned": set()}
ACTION_TRANSITIONS = {"proposed": {"awaiting_approval", "executed", "blocked", "failed"},
                      "awaiting_approval": {"approved", "blocked", "failed"},
                      "approved": {"executed", "failed"},
                      "executed": set(), "blocked": {"proposed"}, "failed": {"proposed"}}


class ControlError(StrategyError):
    pass


def load_playbook(playbook: str) -> dict:
    if playbook not in PLAYBOOKS:
        raise ControlError(f"playbook inconnu : {playbook!r} (disponibles : {sorted(PLAYBOOKS)})")
    return PLAYBOOKS[playbook]


def _emit(conn, business: str, task_id: int | None, type_: str, data: dict | None = None) -> None:
    tasks._emit(conn, business, task_id, type_, data)


def _amount(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ControlError(f"{name} doit être un nombre positif ou nul")
    return float(value)


def _currency(value) -> str:
    code = str(value or "").strip().upper()
    if not code:
        raise ControlError("devise requise")
    return code


def _run(conn, run_id: int, business: str | None = None) -> dict:
    sql, params = "SELECT * FROM business_runs WHERE id=?", [run_id]
    if business:
        sql += " AND business=?"
        params.append(business)
    row = conn.execute(sql, tuple(params)).fetchone()
    if row is None:
        raise ControlError(f"run #{run_id} introuvable{f' pour {business!r}' if business else ''}")
    return dict(row)


def _action(conn, action_id: int, run: dict) -> dict:
    row = conn.execute("SELECT * FROM business_actions WHERE id=? AND run_id=? AND business=?",
                       (action_id, run["id"], run["business"])).fetchone()
    if row is None:
        raise ControlError(f"action #{action_id} introuvable pour le run #{run['id']}")
    return dict(row)


def _set_action(conn, action_id: int, run: dict, status: str, **fields) -> None:
    fields.update(status=status, updated_at=time.time())
    conn.execute(f"UPDATE business_actions SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                 [*fields.values(), action_id])
    _emit(conn, run["business"], run["id"], f"action.{status}",
          {"action_id": action_id, "reason": fields.get("reason")})


def _spent(conn, run_id: int) -> float:
    rows = conn.execute("SELECT cost_amount, cost_currency, status FROM business_actions WHERE run_id=?", (run_id,)).fetchall()
    return round(sum(r["cost_amount"] for r in rows if r["status"] == "executed" and r["cost_currency"] is not None), 6)


def _touch_run(conn, run_id: int) -> None:
    conn.execute("UPDATE business_runs SET updated_at=? WHERE id=?", (time.time(), run_id))


# --- création ---------------------------------------------------------------------------------------

def create_run(business: str, playbook: str, spec: dict | None = None, *, budget_amount: float,
               budget_currency: str = "EUR", created_by: str, origin_task_id: int | None = None) -> dict:
    playbook = load_playbook(playbook)
    business = strategy._business(business)
    budget_amount = _amount(budget_amount, "budget_amount")
    budget_currency = _currency(budget_currency)
    created_by = strategy._text(created_by, "created_by")
    now = time.time()
    with tasks._tx() as conn:
        # Le budget déclaré ne peut pas dépasser l'enveloppe totale déjà accordée par l'humain.
        committed = float(conn.execute(
            "SELECT COALESCE(SUM(a.cost_amount),0) FROM business_actions a JOIN business_runs r ON a.run_id=r.id "
            "WHERE r.business=? AND a.cost_currency=? AND a.status IN ('executed')", (business, budget_currency)).fetchone()[0])
        allowance = float(conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM spend_allowances WHERE business=? AND currency=? AND status='active'",
            (business, budget_currency)).fetchone()[0])
        if committed + budget_amount > allowance + 1e-9:
            raise ControlError(f"budget {budget_amount:g} {budget_currency} supérieur à l'enveloppe disponible "
                               f"({allowance - committed:g} restants)")
        run_id = int(conn.execute(
            "INSERT INTO business_runs (business, playbook, spec, budget_amount, budget_currency, created_by, "
            "origin_task_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (business, playbook["id"], json.dumps(spec or {}, ensure_ascii=False), budget_amount, budget_currency,
             created_by, origin_task_id, now, now)).lastrowid)
        _emit(conn, business, origin_task_id, "run.created", {"id": run_id, "playbook": playbook["id"]})
    return get_run(run_id)


def get_run(run_id: int, business: str | None = None) -> dict:
    conn = journal.connect()
    try:
        row = _run(conn, run_id, business)
    finally:
        conn.close()
    row["spec"] = json.loads(row["spec"])
    row["spent"] = spent_for_run(run_id)
    return row


def list_runs(business: str, *, status: str | None = None, limit: int = 100) -> list[dict]:
    sql, params = "SELECT * FROM business_runs WHERE business=?", [strategy._business(business)]
    if status:
        sql += " AND status=?"
        params.append(status)
    rows = journal.query(sql + " ORDER BY id DESC LIMIT ?", tuple(params + [limit]))
    out = []
    for r in rows:
        row = dict(r)
        row["spec"] = json.loads(row["spec"])
        out.append(row)
    return out


def _transition_run(run_id: int, business: str, status: str, actor: str) -> dict:
    with tasks._tx() as conn:
        run = _run(conn, run_id, business)
        if status not in RUN_TRANSITIONS.get(run["status"], set()):
            raise ControlError(f"run #{run_id} : transition {run['status']} -> {status} interdite")
        conn.execute("UPDATE business_runs SET status=?, updated_at=? WHERE id=?", (status, time.time(), run_id))
        _emit(conn, run["business"], run_id, f"run.{status}", {"id": run_id, "actor": actor})
    return get_run(run_id)


def pause_run(run_id: int, actor: str) -> dict:
    return _transition_run(run_id, None, "paused", strategy._text(actor, "actor"))


def resume_run(run_id: int, actor: str) -> dict:
    return _transition_run(run_id, None, "running", strategy._text(actor, "actor"))


# --- actions ----------------------------------------------------------------------------------------

def _action_idempotency(run_id: int, seq: int, kind: str, channel_id: int | None, payload: dict) -> str:
    blob = json.dumps({"seq": seq, "kind": kind, "channel": channel_id, "payload": payload}, sort_keys=True)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
    return f"run:{run_id}:seq:{seq}:{digest}"


def materialize_actions(run_id: int) -> list[int]:
    """Crée les actions du playbook dans l'ordre. Idempotent : une action déjà créée est conservée."""
    with tasks._tx() as conn:
        run = _run(conn, run_id)
        playbook = load_playbook(run["playbook"])
        spec = json.loads(run["spec"])
        created = []
        for index, action in enumerate(playbook["actions"], start=1):
            key = _action_idempotency(run_id, index, action["kind"], action.get("channel_id"),
                                      {"spec": spec, "label": action.get("label")})
            existing = conn.execute("SELECT id FROM business_actions WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                created.append(int(existing["id"]))
                continue
            action_id = int(conn.execute(
                "INSERT INTO business_actions (run_id, business, seq, kind, label, channel_id, payload, "
                "requires_approval, cost_amount, cost_currency, idempotency_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, run["business"], index, action["kind"], action.get("label"), action.get("channel_id"),
                 json.dumps({"spec": spec, "label": action.get("label")}, ensure_ascii=False),
                 int(action.get("requires_approval", False)), _amount(action.get("cost_amount", 0), "cost"),
                 _currency(action["cost_currency"]) if action.get("cost_currency") else None, key, time.time(),
                 time.time())).lastrowid)
            _emit(conn, run["business"], run_id, "action.proposed", {"action_id": action_id, "seq": index, "kind": action["kind"]})
            created.append(action_id)
        _touch_run(conn, run_id)
    return created


def get_action(run_id: int, action_id: int) -> dict:
    conn = journal.connect()
    try:
        run = _run(conn, run_id)
        row = _action(conn, action_id, run)
    finally:
        conn.close()
    row["payload"] = json.loads(row["payload"])
    row["requires_approval"] = bool(row["requires_approval"])
    return row


def list_actions(run_id: int) -> list[dict]:
    conn = journal.connect()
    try:
        run = _run(conn, run_id)
        rows = conn.execute("SELECT * FROM business_actions WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        item["requires_approval"] = bool(item["requires_approval"])
        out.append(item)
    return out


def approve_action(run_id: int, action_id: int, actor: str) -> dict:
    actor = strategy._text(actor, "actor")
    if actor != "human":
        raise ControlError("seul un humain approuve une action")
    with tasks._tx() as conn:
        run = _run(conn, run_id)
        action = _action(conn, action_id, run)
        if not action["requires_approval"]:
            raise ControlError(f"action #{action_id} n'exige pas d'approbation")
        if action["status"] != "awaiting_approval" and action["status"] != "proposed":
            raise ControlError(f"action #{action_id} non approvable (statut {action['status']})")
        _set_action(conn, action_id, run, "approved")
        _touch_run(conn, run_id)
    return get_action(run_id, action_id)


def _resolve_channel(conn, run: dict, action: dict) -> dict | None:
    channel_id = action.get("channel_id")
    if channel_id is None:
        spec = load_playbook(run["playbook"])
        capability = next((a.get("capability") for a in spec["actions"] if a["kind"] == action["kind"]), None)
        rows = conn.execute(
            "SELECT * FROM economic_channels WHERE business=? AND status='active' AND access='act'", (run["business"],)
        ).fetchall()
        for row in rows:
            caps = json.loads(row["capabilities"] or "[]")
            if capability and capability in caps:
                return dict(row)
            if not capability:
                return dict(row)
        return None
    row = conn.execute("SELECT * FROM economic_channels WHERE id=? AND business=?", (channel_id, run["business"])).fetchone()
    if row is None:
        raise ControlError(f"canal #{channel_id} introuvable pour {run['business']!r}")
    return dict(row)


def execute_action(run_id: int, action_id: int, actor: str) -> dict:
    """Exécute une action si le run est actif, l'approbation est acquise et le canal le permet.

    L'exécution est idempotente : une action déjà exécutée renvoie son état sans nouvelle preuve.
    Le coût passe par `economy.authorize_spend` (enveloppe), puis par l'action de canal avec preuve.
    """
    actor = strategy._text(actor, "actor")
    with tasks._tx() as conn:
        run = _run(conn, run_id)
        if run["status"] != "running":
            raise ControlError(f"run #{run_id} {run['status']} : exécution impossible")
        action = _action(conn, action_id, run)
        if action["status"] == "executed":
            return {"action_id": action_id, "status": "executed", "duplicate": True,
                    "evidence_id": action["evidence_id"]}
        if action["requires_approval"] and action["status"] not in ("approved",):
            if action["status"] != "awaiting_approval":
                _set_action(conn, action_id, run, "awaiting_approval")
            return {"action_id": action_id, "status": "awaiting_approval"}
    # Hors transaction : l'action de canal gère sa propre enveloppe et sa preuve.
    channel = _resolve_channel(journal.connect(), run, action)
    if channel is None:
        with tasks._tx() as conn:
            run = _run(conn, run_id)
            _set_action(conn, action_id, run, "blocked", reason="aucun canal actif avec accès 'act' pour cette capacité")
            _touch_run(conn, run_id)
        return {"action_id": action_id, "status": "blocked", "reason": "aucun canal actif disponible"}
    payload = json.loads(action["payload"])
    spec = payload.get("spec") or {}
    result = actions.propose(
        run["business"], channel["id"], action["kind"], {"spec": spec, "label": action.get("label")},
        requested_by=actor, spend_amount=(action["cost_amount"] or None), spend_currency=action["cost_currency"],
        idempotency_key=action["idempotency_key"])
    with tasks._tx() as conn:
        run = _run(conn, run_id)
        if result["status"] == "executed":
            _set_action(conn, action_id, run, "executed", evidence_id=result.get("evidence_id"),
                        spend_request_id=result.get("spend_request_id"))
        elif result["status"] == "blocked":
            _set_action(conn, action_id, run, "blocked", reason=result.get("reason"))
        else:
            _set_action(conn, action_id, run, "failed", reason=result.get("reason"))
        _touch_run(conn, run_id)
    return {**result, "action_id": action_id}


def spent_for_run(run_id: int) -> float:
    conn = journal.connect()
    try:
        _run(conn, run_id)
        return _spent(conn, run_id)
    finally:
        conn.close()


def snapshot(run_id: int) -> dict:
    conn = journal.connect()
    try:
        run = _run(conn, run_id)
        action_rows = conn.execute("SELECT * FROM business_actions WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
    finally:
        conn.close()
    business = run["business"]
    actions_out = []
    for row in action_rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        item["requires_approval"] = bool(item["requires_approval"])
        actions_out.append(item)
    evidence = [e for e in strategy.list_items("evidence", business, status="active", limit=100)
                if any("action" in str(e.get("summary") or "") for _ in [0])]
    approvals = [a for a in actions_out if a["status"] in ("awaiting_approval", "approved")]
    spent = round(sum(a["cost_amount"] for a in actions_out if a["status"] == "executed"), 6)
    return {
        "run": get_run(run_id),
        "actions": actions_out,
        "budget": {"amount": run["budget_amount"], "currency": run["budget_currency"], "spent": spent},
        "evidence": evidence,
        "approvals": approvals,
        "events": tasks.events(task_id=run_id, limit=100),
    }
