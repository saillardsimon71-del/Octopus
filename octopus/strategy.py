"""Boucle stratégique persistée : objectifs, hypothèses, expériences, décisions, revues.

Stockée dans le journal OCTOPUS (tables `strategy_*`, migration v5) ; toute écriture passe par une
transaction BEGIN IMMEDIATE et laisse un événement `strategy.*` dans `events`.

Règles :
- chaque objet appartient à un business ; une lecture exige ce business (isolation stricte) ;
- un parent ou un lien ne traverse jamais deux businesses ;
- les transitions de statut sont vérifiées ici ;
- une décision qui engage de l'argent n'est approuvée que par un humain (le reste peut l'être par une
  politique ou un agent) ; la dépense elle-même passe par `octopus.economy.authorize_spend` ;
- toute valeur chiffrée est une preuve avec un statut épistémique explicite (NATURES) ; `budget_limit`
  est une limite, pas une mesure.
"""
from __future__ import annotations

import time

from . import connectors, journal, tasks

GLOBAL_VIEW = "all"  # pseudo-business de la GUI (vue portefeuille) : jamais propriétaire d'un objet

KINDS: dict[str, dict] = {
    "objective": {
        "table": "strategy_objectives",
        "required": ("statement",),
        "fields": ("statement", "priority", "timeframe", "success_criteria"),
        "parent": None,
        "initial": "draft",
        "transitions": {"draft": {"active", "abandoned"}, "active": {"paused", "achieved", "abandoned"},
                        "paused": {"active", "abandoned"}},
        "closed": {"achieved", "abandoned"},
    },
    "hypothesis": {
        "table": "strategy_hypotheses",
        "required": ("statement",),
        "fields": ("statement", "expected_signal", "stop_criterion", "evidence_required"),
        "parent": ("objective_id", "objective"),
        "initial": "proposed",
        "transitions": {"proposed": {"testing", "abandoned"},
                        "testing": {"validated", "invalidated", "inconclusive", "abandoned"},
                        "inconclusive": {"testing", "abandoned"}},
        "closed": {"validated", "invalidated", "abandoned"},
    },
    "experiment": {
        "table": "strategy_experiments",
        "required": ("action",),
        "fields": ("action", "channel_id", "metric", "target_value", "stop_value", "budget_limit", "budget_currency",
                   "deadline_at", "expected_result", "actual_result"),
        "parent": ("hypothesis_id", "hypothesis"),
        "initial": "planned",
        "transitions": {"planned": {"running", "cancelled"}, "running": {"completed", "cancelled"}},
        "closed": {"completed", "cancelled"},
    },
    "decision": {
        "table": "strategy_decisions",
        "required": ("decision",),
        "fields": ("decision", "rationale", "alternatives", "resulting_action", "spend_amount", "spend_currency"),
        "parent": None,
        "initial": "proposed",
        "transitions": {"proposed": {"approved", "rejected"}},
        "closed": {"approved", "rejected"},
    },
    "review": {
        "table": "strategy_reviews",
        "required": (),
        "fields": ("period_start", "period_end", "due_at", "evidence_summary", "next_actions", "unresolved_risks"),
        "parent": None,
        "initial": "scheduled",
        "transitions": {"scheduled": {"done", "skipped"}},
        "closed": {"done", "skipped"},
    },
    # Preuve : immuable une fois enregistrée (seul le retrait est possible), nature toujours explicite.
    "evidence": {
        "table": "strategy_evidence",
        "required": ("nature", "source_type", "observation"),
        "fields": ("nature", "source_type", "source_ref", "captured_at", "observation", "confidence",
                   "experiment_id", "channel_id", "metric", "value", "unit"),
        "parent": None,
        "initial": "active",
        "transitions": {"active": {"retracted"}},
        "closed": {"retracted"},
        "immutable": True,
    },
}

OUTCOMES = ("supports", "refutes", "inconclusive")
# observed : constaté dans une source consultable (source + date) ; computed : calculé en code à partir
# d'observations (source = le calcul) ; inferred : déduit par un modèle ; hypothesis : supposé à tester ;
# unverified : déclaré sans source vérifiable (y compris par un humain).
NATURES = ("observed", "computed", "inferred", "hypothesis", "unverified")
NUMERIC = ("priority", "target_value", "stop_value", "budget_limit", "deadline_at", "captured_at", "value",
           "spend_amount", "period_start", "period_end", "due_at", "channel_id", "experiment_id")
CONFIDENCES = ("high", "medium", "low")
# Cibles de liens hors strategy_* : leur business est vérifié dans leur propre table.
EXTERNAL = {"task": "tasks", "channel": "economic_channels", "ledger": "ledger_entries"}


class StrategyError(ValueError):
    pass


def _spec(kind: str) -> dict:
    if kind not in KINDS:
        raise StrategyError(f"type stratégique inconnu : {kind!r}")
    return KINDS[kind]


def _business(business: str) -> str:
    value = (business or "").strip()
    if not value or value == GLOBAL_VIEW:
        raise StrategyError("business requis (la vue portefeuille 'all' ne possède aucun objet)")
    return value


def _text(value, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StrategyError(f"{name} requis")
    return text


def _fetch(conn, kind: str, item_id: int, business: str) -> dict:
    row = conn.execute(f"SELECT * FROM {_spec(kind)['table']} WHERE id=? AND business=?",
                       (item_id, business)).fetchone()
    if row is None:
        raise StrategyError(f"{kind} #{item_id} introuvable pour le business {business!r}")
    return dict(row)


def _check_fields(spec: dict, fields: dict) -> None:
    unknown = set(fields) - set(spec["fields"])
    if unknown:
        raise StrategyError(f"champs inconnus : {sorted(unknown)}")
    for name in NUMERIC:
        value = fields.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise StrategyError(f"{name} doit être un nombre")
    for amount, currency in (("budget_limit", "budget_currency"), ("spend_amount", "spend_currency")):
        if fields.get(amount) is not None:
            if fields[amount] < 0:
                raise StrategyError(f"{amount} doit être positif ou nul")
            if not str(fields.get(currency) or "").strip():
                raise StrategyError(f"{amount} exige {currency}")
            fields[currency] = str(fields[currency]).strip().upper()


def _check_evidence(fields: dict, created_by: str) -> None:
    nature = fields.get("nature")
    if nature not in NATURES:
        raise StrategyError(f"nature de preuve invalide : {nature!r} (attendu : {NATURES})")
    if fields.get("confidence") is not None and fields["confidence"] not in CONFIDENCES:
        raise StrategyError(f"confidence invalide : {fields['confidence']!r} (attendu : {CONFIDENCES})")
    if nature == "observed" and not (str(fields.get("source_ref") or "").strip() and fields.get("captured_at")):
        raise StrategyError("une observation exige source_ref et captured_at (sinon : unverified)")
    if nature == "computed" and not str(fields.get("source_ref") or "").strip():
        raise StrategyError("une valeur calculée exige source_ref (le calcul ou ses entrées)")
    if fields.get("value") is not None and not str(fields.get("metric") or "").strip():
        raise StrategyError("une valeur exige metric")


def _check_external(conn, kind: str, item_id: int, business: str) -> None:
    row = conn.execute(f"SELECT business FROM {EXTERNAL[kind]} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise StrategyError(f"{kind} #{item_id} introuvable")
    if row["business"] != business:
        raise StrategyError(f"{kind} #{item_id} appartient au business {row['business']!r}, pas {business!r}")


def _check_ref(conn, kind: str, item_id: int, business: str) -> None:
    if kind in EXTERNAL:
        _check_external(conn, kind, item_id, business)
    else:
        _fetch(conn, kind, item_id, business)


def create(kind: str, business: str, summary: str, *, created_by: str, parent_id: int | None = None,
           origin_task_id: int | None = None, origin_run_id: int | None = None, **fields) -> int:
    spec = _spec(kind)
    business = _business(business)
    _check_fields(spec, fields)
    for name in spec["required"]:
        fields[name] = _text(fields.get(name), name)
    if kind == "evidence":
        _check_evidence(fields, str(created_by or "").strip())
    row = {"business": business, "status": spec["initial"], "summary": _text(summary, "summary"),
           "created_by": _text(created_by, "created_by"), "origin_task_id": origin_task_id,
           "origin_run_id": origin_run_id, **fields}
    with tasks._tx() as conn:
        if spec["parent"]:
            column, parent_kind = spec["parent"]
            if parent_id is None:
                raise StrategyError(f"{kind} : {column} requis")
            parent = _fetch(conn, parent_kind, parent_id, business)
            if parent["status"] in KINDS[parent_kind]["closed"]:
                raise StrategyError(f"{parent_kind} #{parent_id} est clos ({parent['status']})")
            row[column] = parent_id
        elif parent_id is not None:
            raise StrategyError(f"{kind} n'a pas de parent")
        if origin_task_id is not None:
            _check_external(conn, "task", origin_task_id, business)
        if fields.get("experiment_id") is not None:
            _fetch(conn, "experiment", fields["experiment_id"], business)
        if fields.get("channel_id") is not None:
            _check_external(conn, "channel", fields["channel_id"], business)
        row["created_at"] = row["updated_at"] = time.time()
        cols = list(row)
        item_id = int(conn.execute(
            f"INSERT INTO {spec['table']} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols]).lastrowid)
        tasks._emit(conn, business, origin_task_id, f"strategy.{kind}.created",
                    {"id": item_id, "summary": row["summary"], "created_by": row["created_by"]})
    return item_id


def get(kind: str, item_id: int, business: str) -> dict | None:
    rows = journal.query(f"SELECT * FROM {_spec(kind)['table']} WHERE id=? AND business=?",
                         (item_id, _business(business)))
    return dict(rows[0]) if rows else None


def list_items(kind: str, business: str, *, status: str | None = None, parent_id: int | None = None,
               limit: int = 100) -> list[dict]:
    spec = _spec(kind)
    sql, params = f"SELECT * FROM {spec['table']} WHERE business=?", [_business(business)]
    if status:
        sql += " AND status=?"
        params.append(status)
    if parent_id is not None:
        if not spec["parent"]:
            raise StrategyError(f"{kind} n'a pas de parent")
        sql += f" AND {spec['parent'][0]}=?"
        params.append(parent_id)
    return [dict(r) for r in journal.query(sql + " ORDER BY id DESC LIMIT ?", tuple(params + [limit]))]


def update(kind: str, item_id: int, business: str, **fields) -> None:
    """Modifie les champs descriptifs d'un objet ouvert (le statut passe par `transition`)."""
    spec = _spec(kind)
    business = _business(business)
    if spec.get("immutable"):
        raise StrategyError(f"{kind} est immuable : retirer puis enregistrer une nouvelle preuve")
    summary = fields.pop("summary", None)
    _check_fields(spec, fields)
    for name in spec["required"]:
        if name in fields:
            fields[name] = _text(fields[name], name)
    if summary is not None:
        fields["summary"] = _text(summary, "summary")
    if not fields:
        return
    with tasks._tx() as conn:
        current = _fetch(conn, kind, item_id, business)
        if current["status"] in spec["closed"]:
            raise StrategyError(f"{kind} #{item_id} est clos ({current['status']})")
        changed = sorted(fields)
        fields["updated_at"] = time.time()
        conn.execute(f"UPDATE {spec['table']} SET {', '.join(f'{c}=?' for c in fields)} WHERE id=?",
                     [*fields.values(), item_id])
        tasks._emit(conn, business, None, f"strategy.{kind}.updated", {"id": item_id, "fields": changed})


def transition(kind: str, item_id: int, business: str, status: str, *, actor: str,
               outcome: str | None = None, actual_result: str | None = None, note: str | None = None) -> None:
    spec = _spec(kind)
    business = _business(business)
    actor = _text(actor, "actor")
    changes: dict = {"status": status}
    if kind == "experiment" and status == "completed":
        if outcome not in OUTCOMES:
            raise StrategyError(f"une expérience terminée exige un outcome parmi {OUTCOMES}")
        changes["outcome"] = outcome
        if actual_result is not None:
            changes["actual_result"] = _text(actual_result, "actual_result")
    elif outcome is not None or actual_result is not None:
        raise StrategyError("outcome et actual_result ne concernent que la fin d'une expérience")
    if kind == "decision" and status == "approved" and actor != "human":
        pending = get(kind, item_id, business)
        if pending and pending["spend_amount"]:
            raise StrategyError("une décision qui engage de l'argent n'est approuvée que par un humain")
    with tasks._tx() as conn:
        current = _fetch(conn, kind, item_id, business)
        if status not in spec["transitions"].get(current["status"], set()):
            raise StrategyError(f"{kind} #{item_id} : transition {current['status']} -> {status} interdite")
        now = time.time()
        if kind == "decision":
            changes.update(decided_by=actor, decided_at=now)
        changes["updated_at"] = now
        conn.execute(f"UPDATE {spec['table']} SET {', '.join(f'{c}=?' for c in changes)} WHERE id=?",
                     [*changes.values(), item_id])
        tasks._emit(conn, business, None, f"strategy.{kind}.{status}",
                    {"id": item_id, "from": current["status"], "actor": actor, "outcome": outcome,
                     "note": (note or "")[:500] or None})


def link(business: str, from_kind: str, from_id: int, to_kind: str, to_id: int, relation: str) -> int:
    """Relation N-N (revue -> objectif, décision -> expérience, expérience -> tâche...). Idempotent."""
    business = _business(business)
    relation = _text(relation, "relation")
    _spec(from_kind)
    if to_kind not in KINDS and to_kind not in EXTERNAL:
        raise StrategyError(f"cible de lien inconnue : {to_kind!r}")
    with tasks._tx() as conn:
        _check_ref(conn, from_kind, from_id, business)
        _check_ref(conn, to_kind, to_id, business)
        existing = conn.execute("SELECT id FROM strategy_links WHERE from_type=? AND from_id=? AND to_type=? "
                                "AND to_id=? AND relation=?", (from_kind, from_id, to_kind, to_id, relation)).fetchone()
        if existing:
            return int(existing["id"])
        link_id = int(conn.execute(
            "INSERT INTO strategy_links (business, from_type, from_id, to_type, to_id, relation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (business, from_kind, from_id, to_kind, to_id, relation, time.time())
        ).lastrowid)
        tasks._emit(conn, business, to_id if to_kind == "task" else None, "strategy.link.created",
                    {"from": f"{from_kind}#{from_id}", "to": f"{to_kind}#{to_id}", "relation": relation})
        return link_id


def links(business: str, kind: str, item_id: int) -> list[dict]:
    """Liens sortants et entrants d'un objet, limités à son business."""
    rows = journal.query(
        "SELECT * FROM strategy_links WHERE business=? AND ((from_type=? AND from_id=?) OR (to_type=? AND to_id=?)) "
        "ORDER BY id", (_business(business), kind, item_id, kind, item_id))
    return [dict(r) for r in rows]


# --- lecture pour les tableaux de bord -----------------------------------------------------------

def overview(business: str, limit: int = 5) -> dict:
    """État ouvert d'un business (lecture seule) : ce que la GUI ou la CLI affichent."""
    business = _business(business)
    experiments = (list_items("experiment", business, status="running", limit=limit)
                   + list_items("experiment", business, status="planned", limit=limit))[:limit]
    return {
        "business": business,
        "objectives": list_items("objective", business, status="active", limit=limit),
        "hypotheses": list_items("hypothesis", business, status="testing", limit=limit),
        "experiments": experiments,
        "evidence": list_items("evidence", business, status="active", limit=limit),
        "decisions": list_items("decision", business, status="proposed", limit=limit),
        "reviews": sorted(list_items("review", business, status="scheduled", limit=limit),
                          key=lambda r: r["due_at"] or float("inf")),
        "sources": connectors.summary_line(business),
    }


def portfolio() -> list[dict]:
    """Un résumé par business ayant au moins un objet stratégique."""
    rows = journal.query(
        "SELECT business, "
        "(SELECT COUNT(*) FROM strategy_objectives o WHERE o.business=b.business AND o.status='active') AS objectives, "
        "(SELECT COUNT(*) FROM strategy_experiments e WHERE e.business=b.business AND e.status IN ('planned','running')) "
        "AS experiments, "
        "(SELECT COUNT(*) FROM strategy_decisions d WHERE d.business=b.business AND d.status='proposed') AS decisions "
        "FROM (SELECT business FROM strategy_objectives UNION SELECT business FROM strategy_experiments "
        "UNION SELECT business FROM strategy_decisions UNION SELECT business FROM strategy_reviews "
        "UNION SELECT business FROM strategy_evidence) b ORDER BY business")
    return [dict(r) for r in rows]


# --- missions ORBIT ------------------------------------------------------------------------------

MISSION_RULES = ("Règles : ne présente aucune estimation comme un fait mesuré ; cite la source de chaque fait ; "
                 "signale explicitement les données absentes (CRM, finance, réseaux sociaux non connectés).")


def mission_context(business: str, *, objective_id: int | None = None, hypothesis_id: int | None = None,
                    experiment_id: int | None = None) -> dict:
    """Résout et valide la chaîne objectif > hypothèse > expérience d'une mission (code pur, sans LLM)."""
    business = _business(business)
    conn = journal.connect()
    try:
        experiment = hypothesis = None
        if experiment_id is not None:
            experiment = _fetch(conn, "experiment", experiment_id, business)
            if experiment["status"] in KINDS["experiment"]["closed"]:
                raise StrategyError(f"experiment #{experiment_id} est close ({experiment['status']})")
            if hypothesis_id is not None and hypothesis_id != experiment["hypothesis_id"]:
                raise StrategyError(f"experiment #{experiment_id} ne teste pas hypothesis #{hypothesis_id}")
            hypothesis_id = experiment["hypothesis_id"]
        if hypothesis_id is not None:
            hypothesis = _fetch(conn, "hypothesis", hypothesis_id, business)
            if objective_id is not None and objective_id != hypothesis["objective_id"]:
                raise StrategyError(f"hypothesis #{hypothesis_id} ne dépend pas de objective #{objective_id}")
            objective_id = hypothesis["objective_id"]
        if objective_id is None:
            raise StrategyError("mission stratégique : objective_id, hypothesis_id ou experiment_id requis")
        objective = _fetch(conn, "objective", objective_id, business)
    finally:
        conn.close()
    lines = [f"Contexte stratégique persistant (business {business}) :",
             f"- Objectif #{objective['id']} [{objective['status']}] : {objective['statement']}"
             + (f" ; critères de succès : {objective['success_criteria']}" if objective["success_criteria"] else "")]
    if hypothesis:
        lines.append(f"- Hypothèse #{hypothesis['id']} [{hypothesis['status']}] : {hypothesis['statement']}"
                     + (f" ; signal attendu : {hypothesis['expected_signal']}" if hypothesis["expected_signal"] else "")
                     + (f" ; critère d'arrêt : {hypothesis['stop_criterion']}" if hypothesis["stop_criterion"] else ""))
    if experiment:
        text = f"- Expérience #{experiment['id']} [{experiment['status']}] : {experiment['action']}"
        if experiment["budget_limit"] is not None:
            text += f" ; limite de budget : {experiment['budget_limit']:.2f} {experiment['budget_currency']}"
        if experiment["metric"]:
            text += (f" ; mesure : {experiment['metric']} (succès si >= {experiment['target_value']}, "
                     f"arrêt si <= {experiment['stop_value']})")
        if experiment["deadline_at"]:
            text += f" ; échéance : {time.strftime('%Y-%m-%d', time.localtime(experiment['deadline_at']))}"
        lines.append(text)
    lines.append(MISSION_RULES)
    return {"business": business, "objective_id": objective["id"],
            "hypothesis_id": hypothesis["id"] if hypothesis else None,
            "experiment_id": experiment["id"] if experiment else None, "brief": "\n".join(lines)}


# --- revues --------------------------------------------------------------------------------------

def review_snapshot(business: str, now: float | None = None) -> dict:
    """État persistant d'un business, calculé sans LLM. Ne contient aucune métrique externe."""
    business = _business(business)
    now = now or time.time()

    def counts(kind: str) -> dict[str, int]:
        return {r["status"]: r["n"] for r in journal.query(
            f"SELECT status, COUNT(*) AS n FROM {KINDS[kind]['table']} WHERE business=? GROUP BY status", (business,))}

    overdue = [r["id"] for r in journal.query(
        "SELECT id FROM strategy_experiments WHERE business=? AND status IN ('planned', 'running') "
        "AND deadline_at IS NOT NULL AND deadline_at < ? ORDER BY id", (business, now))]
    pending = [r["id"] for r in journal.query(
        "SELECT id FROM strategy_decisions WHERE business=? AND status='proposed' ORDER BY id", (business,))]
    active = [r["id"] for r in journal.query(
        "SELECT id FROM strategy_objectives WHERE business=? AND status='active' ORDER BY id", (business,))]
    snapshot = {"objectives": counts("objective"), "hypotheses": counts("hypothesis"),
                "experiments": counts("experiment"), "decisions": counts("decision"),
                "active_objectives": active, "overdue_experiments": overdue, "pending_decisions": pending}

    def fmt(values: dict[str, int]) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(values.items())) or "aucun"

    snapshot["text"] = "\n".join([
        f"État persistant calculé le {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))} (business {business}) :",
        f"- objectifs : {fmt(snapshot['objectives'])}",
        f"- hypothèses : {fmt(snapshot['hypotheses'])}",
        f"- expériences : {fmt(snapshot['experiments'])}"
        + (f" ; en retard : {', '.join(f'#{i}' for i in overdue)}" if overdue else ""),
        f"- décisions en attente d'un humain : {', '.join(f'#{i}' for i in pending) or 'aucune'}",
        f"- {connectors.summary_line(business)}",
    ])
    return snapshot


def schedule_review(business: str, *, due_in_s: float, created_by: str,
                    summary: str = "Revue stratégique") -> tuple[int, int]:
    """Revue ponctuelle : ligne `scheduled` + tâche `strategy.review` différée dans la file existante."""
    if due_in_s < 0:
        raise StrategyError("due_in_s doit être positif ou nul")
    business = _business(business)
    review_id = create("review", business, summary, created_by=created_by, due_at=time.time() + due_in_s)
    task_id = tasks.enqueue(business, "strategy.review", {"review_id": review_id}, delay_s=due_in_s,
                            max_attempts=2, idempotency_key=f"strategy.review#{review_id}")
    link(business, "review", review_id, "task", task_id, "executed_by")
    return review_id, task_id


def complete_review(business: str, review_id: int, *, actor: str, now: float | None = None) -> dict:
    """Clôt une revue avec l'état calculé et la rattache aux objectifs actifs. Rejouable."""
    review = get("review", review_id, business)
    if review is None:
        raise StrategyError(f"review #{review_id} introuvable pour le business {business!r}")
    if review["status"] != "scheduled":
        return {"review_id": review_id, "status": review["status"], "already_closed": True}
    snapshot = review_snapshot(business, now)
    update("review", review_id, business, evidence_summary=snapshot["text"], period_end=now or time.time())
    for objective_id in snapshot["active_objectives"]:
        link(business, "review", review_id, "objective", objective_id, "reviews")
    transition("review", review_id, business, "done", actor=actor)
    return {"review_id": review_id, "status": "done", **{k: v for k, v in snapshot.items() if k != "text"}}
