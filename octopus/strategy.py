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

import json
import math
import re
import time
from contextlib import nullcontext

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
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value)):
            raise StrategyError(f"{name} doit être un nombre fini")
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
    metric, value = fields.get("metric") or "", fields.get("value")
    # Conventions de mesure du pilote, dans les preuves existantes (aucun nouvel état).
    if metric in ("delivery", "customer_acceptance", "customer_use") or metric.startswith("human_minutes:"):
        if fields.get("experiment_id") is None:
            raise StrategyError("une mesure de résultat exige experiment_id")
        if nature == "computed" and value is None:
            return  # L'évaluateur conserve explicitement l'absence de mesure, pas un faux zéro.
        if metric.startswith("human_minutes:"):
            if not metric.partition(":")[2].strip() or value is None or value < 0:
                raise StrategyError("human_minutes exige une phase et une durée positive ou nulle")
        elif value not in (0, 1):
            raise StrategyError(f"{metric} exige value=0 ou value=1 ; absence de preuve = inconnu")


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
           origin_task_id: int | None = None, origin_run_id: int | None = None, _conn=None, **fields) -> int:
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
    with (nullcontext(_conn) if _conn is not None else tasks._tx()) as conn:
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


def get(kind: str, item_id: int, business: str, *, _conn=None) -> dict | None:
    query = journal.query if _conn is None else lambda sql, params: _conn.execute(sql, params).fetchall()
    rows = query(f"SELECT * FROM {_spec(kind)['table']} WHERE id=? AND business=?",
                         (item_id, _business(business)))
    return dict(rows[0]) if rows else None


def list_items(kind: str, business: str, *, status: str | None = None, parent_id: int | None = None,
               limit: int = 100, _conn=None) -> list[dict]:
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
    query = journal.query if _conn is None else lambda sql, params: _conn.execute(sql, params).fetchall()
    return [dict(r) for r in query(sql + " ORDER BY id DESC LIMIT ?", tuple(params + [limit]))]


def update(kind: str, item_id: int, business: str, *, _conn=None, **fields) -> None:
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
    with (nullcontext(_conn) if _conn is not None else tasks._tx()) as conn:
        current = _fetch(conn, kind, item_id, business)
        if current["status"] in spec["closed"]:
            raise StrategyError(f"{kind} #{item_id} est clos ({current['status']})")
        changed = sorted(fields)
        fields["updated_at"] = time.time()
        conn.execute(f"UPDATE {spec['table']} SET {', '.join(f'{c}=?' for c in fields)} WHERE id=?",
                     [*fields.values(), item_id])
        tasks._emit(conn, business, None, f"strategy.{kind}.updated", {"id": item_id, "fields": changed})


def transition(kind: str, item_id: int, business: str, status: str, *, actor: str,
               outcome: str | None = None, actual_result: str | None = None, note: str | None = None, _conn=None) -> None:
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
        pending = get(kind, item_id, business, _conn=_conn)
        if pending and pending["spend_amount"]:
            raise StrategyError("une décision qui engage de l'argent n'est approuvée que par un humain")
    with (nullcontext(_conn) if _conn is not None else tasks._tx()) as conn:
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


def link(business: str, from_kind: str, from_id: int, to_kind: str, to_id: int, relation: str, *, _conn=None) -> int:
    """Relation N-N (revue -> objectif, décision -> expérience, expérience -> tâche...). Idempotent."""
    business = _business(business)
    relation = _text(relation, "relation")
    _spec(from_kind)
    if to_kind not in KINDS and to_kind not in EXTERNAL:
        raise StrategyError(f"cible de lien inconnue : {to_kind!r}")
    with (nullcontext(_conn) if _conn is not None else tasks._tx()) as conn:
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


def _hypothesis_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", str(value or "").casefold(), flags=re.UNICODE)).strip()


_LEARNING_STOP_WORDS = frozenset({"avec", "dans", "pour", "sans", "sous", "cette", "cela", "leur", "leurs",
                                  "plus", "tous", "tout", "mais", "donc", "elle", "elles", "nous", "vous",
                                  "they", "them", "this", "that", "from", "with", "have", "will", "were"})


def _learning_terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[^\W_]{4,}", str(text or "").casefold())
            if word not in _LEARNING_STOP_WORDS}


def learning_context(business: str, *, limit: int = 8, topic: str | None = None) -> dict:
    """Leçons d'expériences évaluées, lues depuis les objets stratégiques persistés.

    Seules les évaluations calculées par `economy.evaluate_experiment`, encore actives et
    liées à une expérience terminée sont candidates. Une review rédigée librement ou une
    sortie de modèle ne peut donc pas devenir une leçon probante à elle seule. Les anciennes
    évaluations sans review sont exposées comme historique calculé, sans mutation du journal.
    `topic` sert uniquement à classer lexicalement la fenêtre bornée des leçons; l'absence
    de correspondance laisse les résultats les plus récents en premier.
    """
    business = _business(business)
    if type(limit) is not int or limit < 1 or limit > 50:
        raise StrategyError("limit doit être un entier entre 1 et 50")
    topic_terms = _learning_terms(topic or "")
    rows = journal.query(
        "SELECT x.*, ev.id AS evaluation_evidence_id, ev.observation AS evaluation_observation, "
        "ev.status AS evaluation_status FROM strategy_experiments x JOIN strategy_evidence ev "
        "ON ev.business=x.business AND ev.experiment_id=x.id AND ev.nature='computed' "
        "AND ev.source_type='economy.evaluate' AND ev.source_ref=('experiment#' || x.id) "
        "AND ev.created_by='policy:evaluate' WHERE x.business=? AND x.status='completed' "
        "AND ev.status='active' ORDER BY x.updated_at DESC, ev.id", (business,))
    lessons: list[dict] = []
    seen_experiments: set[int] = set()
    all_evidence_ids: set[int] = set()
    for row in rows:
        experiment_id = int(row["id"])
        if experiment_id in seen_experiments:
            continue
        seen_experiments.add(experiment_id)
        try:
            evaluated = json.loads(row["evaluation_observation"])
        except (TypeError, ValueError):
            continue
        if (not isinstance(evaluated, dict) or evaluated.get("experiment_id") != experiment_id
                or evaluated.get("verdict") not in OUTCOMES
                or evaluated.get("verdict_scope") != "configured_metric_only"
                or evaluated.get("nature") != "computed"):
            continue
        if row["outcome"] != evaluated["verdict"]:
            continue
        hypothesis = get("hypothesis", int(row["hypothesis_id"]), business)
        objective = get("objective", hypothesis["objective_id"], business) if hypothesis else None
        if not hypothesis or not objective:
            continue

        # Une conclusion calculée dépend des preuves actives qui ont servi à l'évaluation.
        source_ids = evaluated.get("outcomes", {}).get("evidence_ids", [])
        if not isinstance(source_ids, list) or any(type(item) is not int or item <= 0 for item in source_ids):
            continue
        source_rows = []
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            source_rows = [dict(item) for item in journal.query(
                f"SELECT id, business, status, nature, source_type, source_ref, captured_at, observation, metric, value, "
                f"experiment_id, created_by FROM strategy_evidence WHERE business=? AND id IN ({placeholders}) ORDER BY id",
                (business, *source_ids))]
            # Une preuve retracted ou déplacée invalide la conclusion historique. Les éléments
            # unverified/inferred restent distincts et ne sont jamais admis comme preuve.
            if (len(source_rows) != len(set(source_ids))
                    or any(item["status"] != "active" or item["experiment_id"] != experiment_id
                           for item in source_rows)):
                continue
        sources = [item for item in source_rows
                   if (item["nature"] == "observed" and item["source_ref"] and item["captured_at"])
                   or (item["nature"] == "computed" and item["source_ref"]
                       and item["source_type"] == "economy.evaluate" and item["created_by"] == "policy:evaluate")]

        # Reviews sont le support persistant des leçons récentes; les évaluations v5 antérieures
        # restent récupérables sans fabriquer ni modifier de ligne à la lecture.
        review_rows = journal.query(
            "SELECT r.* FROM strategy_reviews r JOIN strategy_links l ON l.business=r.business "
            "AND l.from_type='review' AND l.from_id=r.id AND l.to_type='experiment' AND l.to_id=? "
            "AND l.relation='reviews' WHERE r.business=? AND r.status='done' "
            "AND r.created_by='policy:evaluate' ORDER BY r.id DESC",
            (experiment_id, business))
        review = None
        for candidate in review_rows:
            try:
                content = json.loads(candidate["evidence_summary"] or "{}")
            except (TypeError, ValueError):
                continue
            links_to_evidence = journal.query(
                "SELECT 1 FROM strategy_links WHERE business=? AND from_type='review' AND from_id=? "
                "AND to_type='evidence' AND to_id=? AND relation='based_on' LIMIT 1",
                (business, candidate["id"], row["evaluation_evidence_id"]))
            if (links_to_evidence and isinstance(content, dict)
                    and content.get("schema") == "octopus.experiment_learning.v1"
                    and content.get("experiment_id") == experiment_id
                    and content.get("evaluation_evidence_id") == row["evaluation_evidence_id"]):
                review = dict(candidate)
                break
        # Le payload calculé est la source canonique. Le champ lesson de la review n'est
        # accepté que comme texte d'interprétation associé à ces références vérifiées.
        lesson_payload = json.loads(review["evidence_summary"]) if review else None
        lesson_ids = {int(row["evaluation_evidence_id"]), *(int(item["id"]) for item in sources)}
        all_evidence_ids.update(lesson_ids)
        decision_rows = journal.query(
            "SELECT d.* FROM strategy_decisions d JOIN strategy_links l ON l.business=d.business "
            "AND l.from_type='decision' AND l.from_id=d.id AND l.to_type='evidence' AND l.to_id=? "
            "AND l.relation='considers' WHERE d.business=? AND d.created_by='policy:evaluate' "
            "ORDER BY d.id LIMIT 1",
            (row["evaluation_evidence_id"], business))
        if not decision_rows:
            continue
        decision = dict(decision_rows[0])
        # Vérifier la review contre le même résultat calculé et les liens réels, plutôt que
        # croire son texte libre, même si le champ created_by prétend être une politique.
        from . import economy
        expected_lesson = economy._lesson_content(
            dict(row), evaluated, int(row["evaluation_evidence_id"]), int(decision["id"]))
        trusted_review = bool(review and lesson_payload == expected_lesson)
        if not trusted_review:
            # Une review libre homonyme ne doit pas masquer une review policy réellement dérivée.
            for candidate in review_rows:
                try:
                    content = json.loads(candidate["evidence_summary"] or "{}")
                except (TypeError, ValueError):
                    continue
                based_on = journal.query(
                    "SELECT 1 FROM strategy_links WHERE business=? AND from_type='review' AND from_id=? "
                    "AND to_type='evidence' AND to_id=? AND relation='based_on' LIMIT 1",
                    (business, candidate["id"], row["evaluation_evidence_id"]))
                if based_on and content == expected_lesson:
                    review, lesson_payload, trusted_review = dict(candidate), content, True
                    break
        review_id = int(review["id"]) if trusted_review else None
        lessons.append({
            "review_id": review_id,
            "lesson_status": "persisted" if trusted_review else "legacy_evaluation",
            "objective": {"id": int(objective["id"]), "statement": objective["statement"]},
            "hypothesis": {"id": int(hypothesis["id"]), "status": hypothesis["status"],
                           "statement": hypothesis["statement"], "expected_signal": hypothesis["expected_signal"],
                           "stop_criterion": hypothesis["stop_criterion"]},
            "experiment": {"id": experiment_id, "action": row["action"], "metric": row["metric"],
                           "status": row["status"], "outcome": row["outcome"]},
            "result": {"verdict": evaluated["verdict"], "scope": "configured_metric_only",
                       "metric": evaluated.get("metric"), "value": evaluated.get("value"),
                       "reason": evaluated.get("reason"),
                       "technical_completion": evaluated.get("outcomes", {}).get("technical_completion"),
                       "delivery": evaluated.get("outcomes", {}).get("delivery"),
                       "customer_acceptance": evaluated.get("outcomes", {}).get("customer_acceptance"),
                       "customer_use": evaluated.get("outcomes", {}).get("customer_use"),
                       "cash_by_currency": evaluated.get("outcomes", {}).get("cash_by_currency"),
                       "contribution_by_currency": evaluated.get("outcomes", {}).get("contribution_by_currency")},
            "costs": {"llm_usd_at_evaluation": evaluated.get("llm_cost_usd"),
                      "historical_cash_by_currency": evaluated.get("cash"),
                      "sunk_costs_are_not_a_decision_input": True},
            "evidence_ids": sorted(lesson_ids),
            "evidence": expected_lesson["supporting_evidence"],
            "unverified_claims_not_used_as_proof": expected_lesson["unverified_claims_not_used_as_proof"],
            "evaluation_evidence_id": int(row["evaluation_evidence_id"]),
            "decision": {"id": int(decision["id"]), "decision": decision["decision"],
                         "rationale": decision["rationale"], "resulting_action": decision["resulting_action"]},
            "lesson": expected_lesson["lesson"],
            "next_action": expected_lesson["next_action"],
        })

    def relevance(lesson: dict) -> int:
        if not topic_terms:
            return 0
        text = " ".join((lesson["objective"]["statement"], lesson["hypothesis"]["statement"],
                         lesson["experiment"]["action"], str(lesson["experiment"].get("metric") or "")))
        return len(topic_terms & _learning_terms(text))

    # La fenêtre envoyée au modèle est bornée; les expériences plus anciennes restent
    # candidates si leurs objectifs/hypothèses/actions recoupent le sujet courant.
    lessons.sort(key=relevance, reverse=True)
    selected_lessons = lessons[:limit]

    # N'injecter que les preuves exploitables (observed/computed) rattachées à une expérience,
    # ainsi que les preuves de leçons valides. Aucune sortie de modèle libre ne devient preuve.
    evidence_rows = journal.query(
        "SELECT ev.id, ev.source_ref, ev.observation, x.action, x.summary AS experiment_summary, "
        "h.statement AS hypothesis_statement, o.statement AS objective_statement "
        "FROM strategy_evidence ev JOIN strategy_experiments x ON x.id=ev.experiment_id AND x.business=ev.business "
        "JOIN strategy_hypotheses h ON h.id=x.hypothesis_id AND h.business=x.business "
        "JOIN strategy_objectives o ON o.id=h.objective_id AND o.business=h.business "
        "WHERE ev.business=? AND ev.status='active' AND ev.experiment_id IS NOT NULL AND "
        "((ev.nature='observed' AND ev.source_ref IS NOT NULL AND ev.captured_at IS NOT NULL) OR "
        "(ev.nature='computed' AND ev.source_type='economy.evaluate' AND ev.created_by='policy:evaluate' "
        "AND ev.source_ref IS NOT NULL)) ORDER BY ev.created_at DESC, ev.id DESC LIMIT 100", (business,))
    all_evidence_ids.update(int(item["id"]) for item in evidence_rows)
    reconsideration_rows = journal.query(
        "SELECT ev.id, ev.source_ref, ev.observation, h.statement AS hypothesis_statement, "
        "o.statement AS objective_statement FROM strategy_evidence ev JOIN strategy_links l "
        "ON l.business=ev.business AND l.from_type='evidence' AND l.from_id=ev.id "
        "AND l.to_type='hypothesis' AND l.relation='reconsiders' "
        "JOIN strategy_hypotheses h ON h.id=l.to_id AND h.business=l.business "
        "JOIN strategy_objectives o ON o.id=h.objective_id AND o.business=h.business "
        "WHERE ev.business=? AND ev.status='active' AND ev.nature='observed' "
        "AND ev.source_ref IS NOT NULL AND ev.captured_at IS NOT NULL ORDER BY ev.created_at DESC, ev.id DESC LIMIT 100",
        (business,))
    all_evidence_ids.update(int(item["id"]) for item in reconsideration_rows)
    invalidated = [
        {"hypothesis_id": item["hypothesis"]["id"], "hypothesis": item["hypothesis"]["statement"],
         "experiment_id": item["experiment"]["id"], "action": item["experiment"]["action"],
         "evidence_ids": item["evidence_ids"], "verdict": item["result"]["verdict"], "lesson": item["lesson"]}
        for item in lessons if item["hypothesis"]["status"] == "invalidated"
        and item["result"]["verdict"] == "refutes"]
    if topic_terms:
        invalidated.sort(key=lambda item: len(topic_terms & _learning_terms(
            f"{item['hypothesis']} {item['action']}")), reverse=True)
    return {"business": business, "lessons": selected_lessons, "invalidated_hypotheses": invalidated[:30],
            "available_evidence_ids": sorted(all_evidence_ids)}


def repeated_invalidated_strategy(business: str, proposed_goal: str) -> dict | None:
    """Détecte une répétition textuelle directe parmi toutes les hypothèses invalidées.

    L'anti-répétition ne dépend pas de la fenêtre de leçons injectée au modèle (qui reste bornée).
    Pas de rapprochement sémantique LLM : seule une reprise textuelle explicite est bloquée ici.
    """
    business = _business(business)
    goal = _hypothesis_key(proposed_goal)
    if not goal:
        return None
    rows = journal.query(
        "SELECT h.id AS hypothesis_id, h.statement AS hypothesis, x.id AS experiment_id, x.action, "
        "ev.observation FROM strategy_hypotheses h JOIN strategy_experiments x "
        "ON x.business=h.business AND x.hypothesis_id=h.id JOIN strategy_evidence ev "
        "ON ev.business=x.business AND ev.experiment_id=x.id AND ev.status='active' "
        "AND ev.nature='computed' AND ev.source_type='economy.evaluate' "
        "AND ev.source_ref=('experiment#' || x.id) AND ev.created_by='policy:evaluate' "
        "WHERE h.business=? AND h.status='invalidated' AND x.status='completed' AND x.outcome='refutes' "
        "ORDER BY h.id DESC, ev.id", (business,))
    checked: set[tuple[int, int]] = set()
    for row in rows:
        key = (int(row["hypothesis_id"]), int(row["experiment_id"]))
        if key in checked:
            continue
        checked.add(key)
        try:
            evaluated = json.loads(row["observation"])
        except (TypeError, ValueError):
            continue
        if (not isinstance(evaluated, dict) or evaluated.get("experiment_id") != key[1]
                or evaluated.get("verdict") != "refutes"
                or evaluated.get("verdict_scope") != "configured_metric_only"):
            continue
        if any((candidate := _hypothesis_key(value)) and candidate == goal
               for value in (row["hypothesis"], row["action"])):
            return {"hypothesis_id": key[0], "hypothesis": row["hypothesis"],
                    "experiment_id": key[1], "action": row["action"], "verdict": "refutes"}
    return None


def propose_pursuit_hypothesis(business: str, objective_id: int, task_id: int, proposal: dict,
                               *, available_evidence_ids: list[int]) -> dict:
    """Persiste une hypothèse LLM comme proposition, jamais comme fait.

    Les références doivent déjà être visibles parmi les preuves actives rattachées aux expériences.
    Une hypothèse invalidée n'est répétable que si OCTOPUS nomme l'hypothèse, explique la
    reconsidération et cite une nouvelle preuve observée explicitement reliée par `reconsiders`.
    """
    with tasks._tx() as conn:
        query = lambda sql, params: conn.execute(sql, params).fetchall()
        business = _business(business)
        if not isinstance(proposal, dict):
            return {"status": "ignored", "reason": "hypothesis doit être un objet"}
        raw_statement = proposal.get("statement")
        statement = raw_statement.strip() if isinstance(raw_statement, str) else ""
        if not statement or len(statement) > 1000:
            return {"status": "ignored", "reason": "énoncé d'hypothèse absent ou trop long"}
        key = _hypothesis_key(statement)
        invalidated = [dict(item) for item in query(
            "SELECT id, statement FROM strategy_hypotheses WHERE business=? AND status='invalidated' ORDER BY id DESC",
            (business,)) if _hypothesis_key(item["statement"]) == key]
        requested_reconsideration = proposal.get("reconsiders_hypothesis_id")
        if invalidated and not (type(requested_reconsideration) is int
                                and any(item["id"] == requested_reconsideration for item in invalidated)):
            return {"status": "blocked_repetition", "hypothesis_id": int(invalidated[0]["id"]),
                    "reason": "hypothèse déjà invalidée, sans preuve observée nouvelle"}
        expected_value, stop_value = proposal.get("expected_signal"), proposal.get("stop_criterion")
        if not isinstance(expected_value, str) or not isinstance(stop_value, str):
            return {"status": "ignored", "reason": "signal attendu et critère d'arrêt doivent être textuels"}
        expected_signal, stop_criterion = expected_value.strip(), stop_value.strip()
        if not expected_signal or not stop_criterion:
            return {"status": "ignored", "reason": "une hypothèse persistée exige signal attendu et critère d'arrêt"}
        ids = proposal.get("evidence_ids", [])
        if (not isinstance(ids, list) or not ids or len(ids) > 20
                or any(type(item) is not int or item <= 0 for item in ids)):
            return {"status": "ignored", "reason": "références de preuve absentes ou invalides"}
        evidence_ids = sorted(set(ids))
        allowed = {int(item) for item in available_evidence_ids if type(item) is int and item > 0}
        if not set(evidence_ids) <= allowed:
            return {"status": "ignored", "reason": "preuve citée absente du contexte persistant de ce cycle"}
        placeholders = ",".join("?" for _ in evidence_ids)
        evidence = [dict(row) for row in query(
            f"SELECT * FROM strategy_evidence WHERE business=? AND status='active' AND nature IN ('observed','computed') "
            f"AND id IN ({placeholders}) ORDER BY id", (business, *evidence_ids))]
        if len(evidence) != len(evidence_ids):
            return {"status": "ignored", "reason": "preuve rétractée, non vérifiée ou d'un autre business"}

        reconsiders = proposal.get("reconsiders_hypothesis_id")
        if reconsiders is not None and (type(reconsiders) is not int or reconsiders <= 0):
            return {"status": "ignored", "reason": "reconsiders_hypothesis_id invalide"}
        raw_reconsideration_reason = proposal.get("reconsideration_reason", "")
        if not isinstance(raw_reconsideration_reason, str):
            return {"status": "ignored", "reason": "reconsideration_reason doit être textuel"}
        reconsideration_reason = raw_reconsideration_reason.strip()
        old = get("hypothesis", reconsiders, business, _conn=conn) if reconsiders is not None else None
        if reconsiders is not None and (not old or old["status"] != "invalidated" or not reconsideration_reason):
            return {"status": "ignored", "reason": "une reconsidération exige une hypothèse invalidée et une raison explicite"}
        new_reconsideration_evidence = []
        if old:
            for item in evidence:
                explicit = query(
                    "SELECT 1 FROM strategy_links WHERE business=? AND from_type='evidence' AND from_id=? "
                    "AND to_type='hypothesis' AND to_id=? AND relation='reconsiders' LIMIT 1",
                    (business, item["id"], old["id"]))
                if (explicit and item["nature"] == "observed" and item["source_ref"]
                        and item["captured_at"] and old["updated_at"] < item["captured_at"] <= time.time()
                        and item["created_at"] > old["updated_at"]):
                    new_reconsideration_evidence.append(item)
            if not new_reconsideration_evidence:
                return {"status": "ignored", "reason": "aucune preuve observée, nouvelle et explicitement reliée"}

        objective = get("objective", objective_id, business, _conn=conn)
        if not objective or objective["status"] != "active":
            return {"status": "ignored", "reason": "objectif pursuit absent ou inactif"}
        if invalidated and not (old and any(item["id"] == old["id"] for item in invalidated)
                                 and new_reconsideration_evidence):
            return {"status": "blocked_repetition", "hypothesis_id": int(invalidated[0]["id"]),
                    "reason": "hypothèse déjà invalidée, sans preuve observée nouvelle"}

        # Réutiliser une proposition ouverte identique sous le même objectif; ne pas créer de doublons.
        existing = [item for item in list_items("hypothesis", business, parent_id=objective_id, limit=500, _conn=conn)
                    if item["status"] in {"proposed", "testing", "inconclusive"}
                    and _hypothesis_key(item["statement"]) == key]
        if existing:
            hypothesis_id = int(existing[0]["id"])
            status = "reused"
        else:
            summary = "Hypothèse proposée par pursuit; interprétation à tester"
            if old:
                summary += f"; reconsidère #{old['id']}: {reconsideration_reason}"
            # Retry/crash du même travail : réutiliser l'objet déjà créé pour cette tâche.
            prior = query("SELECT id FROM strategy_hypotheses WHERE business=? AND origin_task_id=? "
                                  "AND statement=? ORDER BY id LIMIT 1", (business, task_id, statement))
            hypothesis_id = int(prior[0]["id"]) if prior else create(
                "hypothesis", business, summary, _conn=conn, created_by="octopus:pursuit", parent_id=objective_id,
                origin_task_id=task_id, statement=statement,
                expected_signal=expected_signal[:500], stop_criterion=stop_criterion[:500],
                evidence_required="Preuve observée ou résultat économique vérifiable; la sortie LLM seule n'est pas une preuve.")
            status = "created"

        for item in evidence:
            link(business, "hypothesis", hypothesis_id, "evidence", item["id"], "considers", _conn=conn)
            if item.get("experiment_id") is not None:
                link(business, "hypothesis", hypothesis_id, "experiment", int(item["experiment_id"]), "learns_from", _conn=conn)
        if old:
            link(business, "hypothesis", hypothesis_id, "hypothesis", int(old["id"]), "reconsiders", _conn=conn)
        return {"status": status, "hypothesis_id": hypothesis_id,
                "reconsiders_hypothesis_id": int(old["id"]) if old else None,
                "evidence_ids": evidence_ids}


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
