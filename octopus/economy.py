"""Boucle économique générique : canaux, grand livre, dépenses autorisées, résultats, réinvestissement.

Principes :
- l'argent réel n'est compté que s'il est observé (source + date) ; le reste est affiché à part ;
- chaque montant garde sa devise : aucune conversion n'est inventée ;
- aucune dépense sans autorisation : une enveloppe (`spend_allowances`) accordée par un humain ou par
  une politique de réinvestissement que l'humain a fixée, puis une demande (`spend_requests`) acceptée ;
- les canaux sont des entrées libres (site, marketplace, réseau, email, API, publicité...) découvertes
  et qualifiées au fil de l'eau, pas une liste figée.
Aucune fonction ici ne déplace d'argent : elles décident, mesurent et tracent.
"""
from __future__ import annotations

import json
import time

from . import journal, strategy, tasks
from .strategy import StrategyError

CASH_NATURES = ("observed", "unverified", "computed")
CHANNEL_ACCESS = ("none", "observe", "act")
CHANNEL_STATUSES = {"discovered": {"active", "abandoned"}, "active": {"suspended", "abandoned"},
                    "suspended": {"active", "abandoned"}}
CASH_METRIC = "cash_net"  # métrique d'expérience calculée depuis le grand livre : cash_net:EUR


def _amount(value, name: str = "amount") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise StrategyError(f"{name} doit être un nombre strictement positif")
    return float(value)


def _currency(value) -> str:
    code = str(value or "").strip().upper()
    if not code:
        raise StrategyError("devise requise")
    return code


def _actions():
    from . import actions  # import tardif : actions dépend de economy
    return actions


def _emit(conn, business: str, type_: str, data: dict) -> None:
    tasks._emit(conn, business, None, type_, data)


# --- canaux ----------------------------------------------------------------------------------------

def add_channel(business: str, kind: str, name: str, *, created_by: str, locator: str | None = None,
                capabilities: list[str] | None = None, access: str = "none", nature: str = "unverified",
                source_ref: str | None = None, notes: str | None = None) -> int:
    business = strategy._business(business)
    if access not in CHANNEL_ACCESS:
        raise StrategyError(f"access invalide : {access!r} (attendu : {CHANNEL_ACCESS})")
    if nature not in ("observed", "unverified", "hypothesis"):
        raise StrategyError("nature d'un canal : observed, unverified ou hypothesis")
    if nature == "observed" and not str(source_ref or "").strip():
        raise StrategyError("un canal observé exige source_ref")
    now = time.time()
    with tasks._tx() as conn:
        channel_id = int(conn.execute(
            "INSERT INTO economic_channels (business, kind, name, locator, capabilities, access, nature, source_ref, "
            "notes, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (business, strategy._text(kind, "kind").lower(), strategy._text(name, "name"), locator,
             json.dumps(sorted({str(c).strip().lower() for c in capabilities or [] if str(c).strip()})),
             access, nature, source_ref, notes, strategy._text(created_by, "created_by"), now, now)).lastrowid)
        _emit(conn, business, "economy.channel.created", {"id": channel_id, "kind": kind, "name": name})
    return channel_id


def update_channel(business: str, channel_id: int, *, actor: str, status: str | None = None,
                   access: str | None = None, nature: str | None = None, source_ref: str | None = None,
                   capabilities: list[str] | None = None, notes: str | None = None) -> None:
    business = strategy._business(business)
    with tasks._tx() as conn:
        row = conn.execute("SELECT * FROM economic_channels WHERE id=? AND business=?", (channel_id, business)).fetchone()
        if row is None:
            raise StrategyError(f"canal #{channel_id} introuvable pour {business!r}")
        changes: dict = {}
        if status is not None and status != row["status"]:
            if status not in CHANNEL_STATUSES.get(row["status"], set()):
                raise StrategyError(f"canal #{channel_id} : transition {row['status']} -> {status} interdite")
            changes["status"] = status
        if access is not None:
            if access not in CHANNEL_ACCESS:
                raise StrategyError(f"access invalide : {access!r}")
            if access == "act" and actor != "human":
                raise StrategyError("seul un humain accorde le droit d'agir sur un canal")
            changes["access"] = access
        if nature is not None:
            if nature == "observed" and not str(source_ref or row["source_ref"] or "").strip():
                raise StrategyError("un canal observé exige source_ref")
            changes["nature"] = nature
        if source_ref is not None:
            changes["source_ref"] = source_ref
        if capabilities is not None:
            changes["capabilities"] = json.dumps(sorted({str(c).strip().lower() for c in capabilities if str(c).strip()}))
        if notes is not None:
            changes["notes"] = notes
        if not changes:
            return
        changes["updated_at"] = time.time()
        conn.execute(f"UPDATE economic_channels SET {', '.join(f'{k}=?' for k in changes)} WHERE id=?",
                     [*changes.values(), channel_id])
        _emit(conn, business, "economy.channel.updated",
              {"id": channel_id, "actor": actor, "fields": sorted(k for k in changes if k != "updated_at")})


def channels(business: str, *, status: str | None = None, capability: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM economic_channels WHERE business=?", [strategy._business(business)]
    if status:
        sql += " AND status=?"
        params.append(status)
    rows = [{**dict(r), "capabilities": json.loads(r["capabilities"])} for r in journal.query(sql + " ORDER BY id", tuple(params))]
    return [r for r in rows if not capability or capability.lower() in r["capabilities"]]


# --- grand livre -----------------------------------------------------------------------------------

def record_cash(business: str, direction: str, amount: float, currency: str, category: str, *, nature: str,
                created_by: str, source_ref: str | None = None, occurred_at: float | None = None,
                experiment_id: int | None = None, channel_id: int | None = None, description: str | None = None,
                origin_task_id: int | None = None, spend_request_id: int | None = None,
                reverses_id: int | None = None) -> int:
    """Écriture immuable (correction = écriture inverse via `reverses_id`)."""
    business = strategy._business(business)
    if direction not in ("in", "out"):
        raise StrategyError("direction : in ou out")
    if nature not in ("observed", "unverified"):
        raise StrategyError("un mouvement d'argent est observed (source vérifiable) ou unverified")
    if nature == "observed" and not str(source_ref or "").strip():
        raise StrategyError("un mouvement observé exige source_ref (relevé, facture, export de plateforme...)")
    amount, currency = _amount(amount), _currency(currency)
    with tasks._tx() as conn:
        if experiment_id is not None:
            strategy._fetch(conn, "experiment", experiment_id, business)
        if channel_id is not None:
            strategy._check_external(conn, "channel", channel_id, business)
        if origin_task_id is not None:
            strategy._check_external(conn, "task", origin_task_id, business)
        if reverses_id is not None:
            original = conn.execute("SELECT * FROM ledger_entries WHERE id=? AND business=?",
                                    (reverses_id, business)).fetchone()
            if original is None or original["direction"] == direction or original["currency"] != currency:
                raise StrategyError("une contre-passation inverse une écriture du même business et de la même devise")
        entry_id = int(conn.execute(
            "INSERT INTO ledger_entries (business, experiment_id, channel_id, direction, amount, currency, category, "
            "description, nature, source_ref, occurred_at, reverses_id, spend_request_id, created_by, origin_task_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (business, experiment_id, channel_id, direction, amount, currency, strategy._text(category, "category"),
             description, nature, source_ref, occurred_at or time.time(), reverses_id, spend_request_id,
             strategy._text(created_by, "created_by"), origin_task_id, time.time())).lastrowid)
        if spend_request_id is not None:
            request = conn.execute("SELECT * FROM spend_requests WHERE id=? AND business=?",
                                   (spend_request_id, business)).fetchone()
            if request is None or request["status"] != "authorized" or direction != "out":
                raise StrategyError("seule une demande autorisée peut être exécutée par une sortie d'argent")
            if amount > request["amount"] + 1e-9 or currency != request["currency"]:
                raise StrategyError("la dépense réelle dépasse la demande autorisée ou change de devise")
            conn.execute("UPDATE spend_requests SET status='executed', updated_at=? WHERE id=?", (time.time(), spend_request_id))
        _emit(conn, business, "economy.cash.recorded",
              {"id": entry_id, "direction": direction, "amount": amount, "currency": currency, "nature": nature})
    return entry_id


def cash_summary(business: str, *, experiment_id: int | None = None, since: float | None = None,
                 until: float | None = None) -> dict:
    """Par devise : entrées/sorties observées et net calculé ; les montants non vérifiés restent à part."""
    sql = ("SELECT direction, currency, nature, SUM(amount) AS total FROM ledger_entries WHERE business=?")
    params: list = [strategy._business(business)]
    if experiment_id is not None:
        sql += " AND experiment_id=?"
        params.append(experiment_id)
    if since is not None:
        sql += " AND occurred_at>=?"
        params.append(since)
    if until is not None:
        sql += " AND occurred_at<?"
        params.append(until)
    out: dict[str, dict] = {}
    for row in journal.query(sql + " GROUP BY direction, currency, nature", tuple(params)):
        cur = out.setdefault(row["currency"], {"in_observed": 0.0, "out_observed": 0.0, "in_unverified": 0.0,
                                               "out_unverified": 0.0})
        cur[f"{row['direction']}_{row['nature']}"] = round(row["total"], 6)
    for cur in out.values():
        cur["net_observed"] = round(cur["in_observed"] - cur["out_observed"], 6)  # calculé
    return out


_CSV_COLUMNS = {
    "date": ("date", "created", "created_at", "occurred_at", "date operation", "date opération", "booking date",
             "transaction date", "available_on"),
    "amount": ("amount", "net", "montant", "value", "valeur", "total"),
    "currency": ("currency", "devise", "currency code"),
    "description": ("description", "libellé", "libelle", "label", "details", "memo", "type"),
    "id": ("id", "reference", "référence", "transaction id", "balance_transaction", "order id"),
}


def _parse_date(raw: str) -> float:
    from datetime import datetime
    text = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:19] if "T" in text or ":" in text else text, fmt).timestamp()
        except ValueError:
            continue
    if text.replace(".", "", 1).isdigit():
        return float(text)  # horodatage Unix
    raise StrategyError(f"date illisible : {raw!r}")


def _parse_amount(raw: str) -> float:
    text = raw.strip().replace(" ", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        raise StrategyError(f"montant illisible : {raw!r}") from None


def import_cash_csv(business: str, path: str, *, created_by: str, currency: str | None = None,
                    category: str = "import", experiment_id: int | None = None, channel_id: int | None = None) -> dict:
    """Export réel (banque, paiement, marketplace) -> écritures observées. Montant signé : + entrée, - sortie.

    Idempotent : chaque ligne a pour source `<fichier>#<id ou numéro de ligne>` et n'est importée qu'une fois.
    """
    import csv
    from pathlib import Path
    business = strategy._business(business)
    file = Path(path)
    text = file.read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    headers = {h.strip().lower(): h for h in reader.fieldnames or []}
    columns = {key: next((headers[name] for name in names if name in headers), None) for key, names in _CSV_COLUMNS.items()}
    if not columns["date"] or not columns["amount"]:
        raise StrategyError(f"colonnes date et montant introuvables (en-têtes : {list(headers.values())})")
    if not columns["currency"] and not currency:
        raise StrategyError("devise absente du fichier : préciser currency")
    imported, skipped = [], 0
    for index, row in enumerate(reader, start=2):
        raw_amount = (row.get(columns["amount"]) or "").strip()
        if not raw_amount:
            continue
        value = _parse_amount(raw_amount)
        if value == 0:
            continue
        ref = f"{file.name}#{(row.get(columns['id']) or '').strip() or f'ligne{index}'}"
        if journal.query("SELECT 1 FROM ledger_entries WHERE business=? AND source_ref=?", (business, ref)):
            skipped += 1
            continue
        imported.append(record_cash(
            business, "in" if value > 0 else "out", abs(value),
            (row.get(columns["currency"]) if columns["currency"] else None) or currency, category, nature="observed",
            created_by=created_by, source_ref=ref, occurred_at=_parse_date(row[columns["date"]]),
            description=(row.get(columns["description"]) or None) if columns["description"] else None,
            experiment_id=experiment_id, channel_id=channel_id))
    return {"imported": len(imported), "skipped_duplicates": skipped, "entry_ids": imported}


def llm_cost_usd(business: str, *, experiment_id: int | None = None, since: float | None = None) -> float:
    """Coût LLM calculé (tokens x prix du catalogue) ; par expérience via ses tâches liées."""
    business = strategy._business(business)
    if experiment_id is None:
        sql, params = "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE business=?", [business]
        if since is not None:
            sql += " AND ts>=?"
            params.append(since)
        return round(float(journal.query(sql, tuple(params))[0]["c"]), 6)
    run_ids = [r["run_id"] for r in journal.query(
        "SELECT t.run_id FROM strategy_links l JOIN tasks t ON t.id=l.to_id WHERE l.business=? AND l.from_type="
        "'experiment' AND l.from_id=? AND l.to_type='task' AND t.run_id IS NOT NULL", (business, experiment_id))]
    return round(sum(journal.subtree_cost(run_id) for run_id in set(run_ids)), 6)


# --- dépenses --------------------------------------------------------------------------------------

def grant_allowance(business: str, amount: float, currency: str, *, granted_by: str, rationale: str,
                    experiment_id: int | None = None, expires_at: float | None = None) -> int:
    business = strategy._business(business)
    granted_by = strategy._text(granted_by, "granted_by")
    if granted_by != "human" and not granted_by.startswith("policy:"):
        raise StrategyError("une enveloppe est accordée par un humain ou par une politique fixée par l'humain")
    amount, currency = _amount(amount), _currency(currency)
    with tasks._tx() as conn:
        if experiment_id is not None:
            strategy._fetch(conn, "experiment", experiment_id, business)
        allowance_id = int(conn.execute(
            "INSERT INTO spend_allowances (business, experiment_id, amount, currency, granted_by, rationale, expires_at, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (business, experiment_id, amount, currency, granted_by, strategy._text(rationale, "rationale"), expires_at,
             time.time())).lastrowid)
        _emit(conn, business, "economy.allowance.granted",
              {"id": allowance_id, "amount": amount, "currency": currency, "granted_by": granted_by})
    return allowance_id


def _committed(conn, column: str, value: int) -> float:
    return float(conn.execute(f"SELECT COALESCE(SUM(amount), 0) FROM spend_requests WHERE {column}=? AND status IN "
                              "('authorized', 'executed')", (value,)).fetchone()[0])


def authorize_spend(business: str, amount: float, currency: str, purpose: str, *, requested_by: str,
                    experiment_id: int | None = None) -> dict:
    """Décide (sans payer) : une enveloppe active couvre-t-elle cette dépense, dans la limite de l'expérience ?"""
    business = strategy._business(business)
    amount, currency = _amount(amount), _currency(currency)
    purpose, requested_by = strategy._text(purpose, "purpose"), strategy._text(requested_by, "requested_by")
    now = time.time()
    with tasks._tx() as conn:
        reason, allowance_id = None, None
        if experiment_id is not None:
            experiment = strategy._fetch(conn, "experiment", experiment_id, business)
            if experiment["status"] != "running":
                reason = f"expérience #{experiment_id} non active ({experiment['status']})"
            elif experiment["budget_limit"] is not None:
                if experiment["budget_currency"] != currency:
                    reason = f"limite de l'expérience en {experiment['budget_currency']}, demande en {currency}"
                elif _committed(conn, "experiment_id", experiment_id) + amount > experiment["budget_limit"] + 1e-9:
                    reason = "limite de budget de l'expérience atteinte"
        if reason is None:
            candidates = conn.execute(
                "SELECT * FROM spend_allowances WHERE business=? AND currency=? AND status='active' "
                "AND (expires_at IS NULL OR expires_at>?) AND (experiment_id IS NULL OR experiment_id IS ?) "
                "ORDER BY experiment_id IS NULL, id", (business, currency, now, experiment_id)).fetchall()
            for allowance in candidates:
                if allowance["amount"] - _committed(conn, "allowance_id", allowance["id"]) + 1e-9 >= amount:
                    allowance_id = allowance["id"]
                    break
            if allowance_id is None:
                reason = "aucune enveloppe active ne couvre ce montant" if candidates else "aucune enveloppe accordée"
        status = "authorized" if reason is None else "denied"
        request_id = int(conn.execute(
            "INSERT INTO spend_requests (business, experiment_id, allowance_id, amount, currency, purpose, status, reason, "
            "requested_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (business, experiment_id, allowance_id, amount, currency, purpose, status, reason, requested_by, now, now)
        ).lastrowid)
        _emit(conn, business, f"economy.spend.{status}",
              {"id": request_id, "amount": amount, "currency": currency, "reason": reason})
    return {"request_id": request_id, "status": status, "reason": reason, "allowance_id": allowance_id}


def gate_paid_call(business: str, purpose: str, *, estimate_env: str, requested_by: str,
                   experiment_id: int | None = None) -> dict:
    """Garde des appels payants hors LLM (GPU cloud, API facturée...) : estimation fixée par l'humain
    (variable `estimate_env`, ex. "0.40 USD") puis autorisation sur une enveloppe. Lève StrategyError sinon."""
    import os
    raw = os.environ.get(estimate_env, "").strip().split()
    if len(raw) != 2:
        raise StrategyError(f"{estimate_env} non configuré (ex. \"0.40 USD\") : coût inconnu, appel payant refusé")
    try:
        amount = float(raw[0].replace(",", "."))
    except ValueError:
        raise StrategyError(f"{estimate_env} illisible : {' '.join(raw)!r}") from None
    decision = authorize_spend(business, amount, raw[1], purpose, requested_by=requested_by, experiment_id=experiment_id)
    if decision["status"] != "authorized":
        raise StrategyError(f"dépense refusée ({amount:g} {raw[1].upper()}) : {decision['reason']}")
    return decision


def cancel_spend(business: str, request_id: int, *, actor: str) -> None:
    business = strategy._business(business)
    with tasks._tx() as conn:
        row = conn.execute("SELECT status FROM spend_requests WHERE id=? AND business=?", (request_id, business)).fetchone()
        if row is None or row["status"] != "authorized":
            raise StrategyError(f"demande #{request_id} non annulable")
        conn.execute("UPDATE spend_requests SET status='cancelled', updated_at=? WHERE id=?", (time.time(), request_id))
        _emit(conn, business, "economy.spend.cancelled", {"id": request_id, "actor": actor})


def allowances(business: str) -> list[dict]:
    rows = journal.query("SELECT * FROM spend_allowances WHERE business=? ORDER BY id", (strategy._business(business),))
    out = []
    conn = journal.connect()
    try:
        for row in rows:
            out.append({**dict(row), "committed": _committed(conn, "allowance_id", row["id"])})
    finally:
        conn.close()
    return out


# --- expériences : mesure, verdict, apprentissage --------------------------------------------------

def metric_value(business: str, experiment: dict) -> tuple[float | None, str]:
    """(valeur, statut) : cash_net:<DEVISE> calculé depuis le grand livre, sinon somme des valeurs observées."""
    metric = experiment["metric"] or ""
    if metric.startswith(CASH_METRIC + ":"):
        currency = _currency(metric.split(":", 1)[1])
        summary = cash_summary(business, experiment_id=experiment["id"]).get(currency)
        return (summary["net_observed"], "computed") if summary else (None, "computed")
    rows = journal.query("SELECT value FROM strategy_evidence WHERE business=? AND experiment_id=? AND metric=? "
                         "AND nature='observed' AND status='active' AND value IS NOT NULL",
                         (business, experiment["id"], metric))
    return (round(sum(r["value"] for r in rows), 6) if rows else None), "computed"


def evaluate_experiment(business: str, experiment_id: int, *, apply: bool = True, now: float | None = None) -> dict:
    """Verdict calculé en code. Avec `apply`, conclut une expérience tranchée et propose la décision suivante.

    supports : valeur >= cible ; refutes : à l'échéance valeur <= seuil d'arrêt ou aucune mesure, ou limite de
    budget consommée sans succès ; inconclusive : échéance passée entre les deux ; pending sinon.
    """
    business = strategy._business(business)
    now = now or time.time()
    experiment = strategy.get("experiment", experiment_id, business)
    if experiment is None:
        raise StrategyError(f"experiment #{experiment_id} introuvable pour {business!r}")
    value, _ = metric_value(business, experiment) if experiment["metric"] else (None, "computed")
    cash = cash_summary(business, experiment_id=experiment_id)
    llm = llm_cost_usd(business, experiment_id=experiment_id)
    spent = None
    if experiment["budget_limit"] is not None:
        spent = cash.get(experiment["budget_currency"], {}).get("out_observed", 0.0)
        if experiment["budget_currency"] == "USD":
            spent += llm
    expired = bool(experiment["deadline_at"] and experiment["deadline_at"] <= now)
    target, stop = experiment["target_value"], experiment["stop_value"]
    if value is not None and target is not None and value >= target:
        verdict, why = "supports", f"{experiment['metric']} = {value} >= cible {target}"
    elif value is not None and stop is not None and value <= stop and expired:
        verdict, why = "refutes", f"{experiment['metric']} = {value} <= seuil d'arrêt {stop}"
    elif spent is not None and spent >= experiment["budget_limit"]:
        verdict, why = "refutes", f"budget consommé ({spent} {experiment['budget_currency']}) sans atteindre la cible"
    elif expired and value is None:
        verdict, why = "refutes" if experiment["metric"] else "inconclusive", "échéance passée sans mesure observée"
    elif expired:
        verdict, why = "inconclusive", f"échéance passée : {experiment['metric']} = {value}"
    else:
        verdict, why = "pending", "en cours"
    result = {"experiment_id": experiment_id, "verdict": verdict, "reason": why, "metric": experiment["metric"],
              "value": value, "cash": cash, "llm_cost_usd": llm, "spent_in_budget_currency": spent, "nature": "computed"}
    if not apply or verdict == "pending" or experiment["status"] != "running":
        return result
    evidence_id = strategy.create(
        "evidence", business, f"Évaluation de l'expérience #{experiment_id} : {verdict}", created_by="policy:evaluate",
        nature="computed", source_type="economy.evaluate", source_ref=f"experiment#{experiment_id}",
        observation=json.dumps(result, ensure_ascii=False), experiment_id=experiment_id,
        metric=experiment["metric"], value=value)
    strategy.transition("experiment", experiment_id, business, "completed", actor="policy:evaluate", outcome=verdict,
                        actual_result=why, note=f"evidence#{evidence_id}")
    next_step = {"supports": "Étendre ou reproduire l'expérience dans une enveloppe de dépense autorisée",
                 "refutes": "Arrêter cette voie et réallouer l'effort vers une autre hypothèse",
                 "inconclusive": "Relancer avec une mesure plus directe ou abandonner"}[verdict]
    decision_id = strategy.create("decision", business, f"Suite de l'expérience #{experiment_id}", created_by="policy:evaluate",
                                  decision=next_step, rationale=why)
    strategy.link(business, "decision", decision_id, "evidence", evidence_id, "considers")
    strategy.link(business, "evidence", evidence_id, "experiment", experiment_id, "evaluates")
    return {**result, "evidence_id": evidence_id, "decision_id": decision_id}


# --- réinvestissement ------------------------------------------------------------------------------

def set_reinvest_policy(business: str, *, share: float, max_amount: float, currency: str, period_days: float,
                        set_by: str) -> None:
    """Règle fixée par l'humain : part du cash net observé réinjectée en enveloppe de dépense."""
    business = strategy._business(business)
    if set_by != "human":
        raise StrategyError("la politique de réinvestissement est fixée par un humain")
    if not 0 < share <= 1:
        raise StrategyError("share doit être dans ]0, 1]")
    max_amount, currency = _amount(max_amount, "max_amount"), _currency(currency)
    if period_days <= 0:
        raise StrategyError("period_days doit être positif")
    with tasks._tx() as conn:
        conn.execute("INSERT INTO reinvest_policies (business, share, max_amount, currency, period_days, set_by, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(business) DO UPDATE SET share=excluded.share, "
                     "max_amount=excluded.max_amount, currency=excluded.currency, period_days=excluded.period_days, "
                     "set_by=excluded.set_by, updated_at=excluded.updated_at",
                     (business, share, max_amount, currency, period_days, set_by, time.time()))
        _emit(conn, business, "economy.reinvest.policy", {"share": share, "max_amount": max_amount, "currency": currency})


def reinvest(business: str, *, now: float | None = None) -> dict:
    """Accorde au plus une enveloppe par période, proportionnelle au cash net observé de la période."""
    business = strategy._business(business)
    now = now or time.time()
    rows = journal.query("SELECT * FROM reinvest_policies WHERE business=?", (business,))
    if not rows:
        return {"status": "no_policy"}
    policy = dict(rows[0])
    since = now - policy["period_days"] * 86400
    already = journal.query("SELECT id FROM spend_allowances WHERE business=? AND granted_by='policy:reinvest' "
                            "AND created_at>=?", (business, since))
    if already:
        return {"status": "already_granted", "allowance_id": already[0]["id"]}
    net = cash_summary(business, since=since, until=now).get(policy["currency"], {}).get("net_observed", 0.0)
    if net <= 0:
        return {"status": "no_profit", "net_observed": net, "currency": policy["currency"]}
    amount = round(min(net * policy["share"], policy["max_amount"]), 2)
    allowance_id = grant_allowance(
        business, amount, policy["currency"], granted_by="policy:reinvest", expires_at=now + policy["period_days"] * 86400,
        rationale=f"{policy['share']:.0%} du cash net observé ({net} {policy['currency']}) sur {policy['period_days']:g} j")
    return {"status": "granted", "allowance_id": allowance_id, "amount": amount, "net_observed": net,
            "currency": policy["currency"]}


def status(business: str) -> dict:
    """Vue économique d'un business : tout chiffre porte son statut."""
    business = strategy._business(business)
    running = strategy.list_items("experiment", business, status="running", limit=50)
    return {
        "business": business,
        "cash": {"nature": "computed from observed ledger", "by_currency": cash_summary(business)},
        "llm_cost_usd": {"nature": "computed from token prices", "value": llm_cost_usd(business)},
        "allowances": [a for a in allowances(business) if a["status"] == "active"],
        "channels": channels(business),
        # Apprentissages : expériences conclues, pour ne pas reproposer ce qui a déjà échoué.
        "learnings": [{"experiment_id": e["id"], "summary": e["summary"], "action": e["action"], "metric": e["metric"],
                       "outcome": e["outcome"], "result": e["actual_result"],
                       "cash": cash_summary(business, experiment_id=e["id"]),
                       "llm_cost_usd": llm_cost_usd(business, experiment_id=e["id"])}
                      for e in strategy.list_items("experiment", business, status="completed", limit=15)],
        "action_executors": [f"{kind}:{action}" for kind, action in _actions().executors()],
        "recent_actions": [{k: a[k] for k in ("id", "channel_id", "action", "status", "reason")}
                           for a in _actions().list_actions(business, limit=10)],
        "pending_decisions": [{"id": d["id"], "summary": d["summary"], "decision": d["decision"],
                               "spend_amount": d["spend_amount"], "spend_currency": d["spend_currency"]}
                              for d in strategy.list_items("decision", business, status="proposed", limit=20)],
        "running_experiments": [{"id": e["id"], "summary": e["summary"], "metric": e["metric"],
                                 **{k: v for k, v in evaluate_experiment(business, e["id"], apply=False).items()
                                    if k in ("verdict", "value", "reason")}} for e in running],
    }


# Fonction objectif donnée à ORBIT : cash net observé. Aucun modèle économique, canal ou produit imposé.
DRIVE_GOAL = (
    "Objectif permanent : augmenter le cash net réellement encaissé (observé) par ce business, coûts déduits, "
    "par des moyens légaux. Commence par economy_status. Ne duplique pas les expériences en cours : collecte plutôt "
    "des observations sourcées utiles à leur mesure (record_observation). Traite les décisions en attente. Sinon, "
    "explore les moyens possibles d'encaisser (marchés, canaux, offres, compétences disponibles), enregistre les "
    "canaux découverts (register_channel), propose 1 à 3 expériences mesurables et peu coûteuses "
    "(propose_experiment, de préférence metric cash_net:DEVISE) et démarre la plus prometteuse (start_experiment). "
    "Toute dépense passe par request_spend. Ne présente jamais une estimation comme un fait."
)


def drive(business: str, *, now: float | None = None, budget_usd: float = 0.05, period_s: float = 86400) -> dict:
    """Relance ORBIT sur la fonction objectif si aucune mission n'est active (au plus une par période)."""
    business = strategy._business(business)
    now = now or time.time()
    active = journal.query(f"SELECT id FROM tasks WHERE business=? AND kind='orbit.mission' AND status IN "
                           f"({','.join('?' for _ in tasks.ACTIVE)})", (business, *tasks.ACTIVE))
    if active:
        return {"status": "mission_active", "task_id": active[0]["id"]}
    key = f"economy.drive:{business}:{int(now // period_s)}"
    existing = journal.query("SELECT id FROM tasks WHERE idempotency_key=?", (key,))
    if existing:
        return {"status": "already_driven", "task_id": existing[0]["id"]}
    task_id = tasks.enqueue(business, "orbit.mission", {"goal": DRIVE_GOAL, "max_steps": 8}, resource="llm",
                            budget_usd=budget_usd, idempotency_key=key)
    return {"status": "mission_queued", "task_id": task_id, "budget_usd": budget_usd}


def portfolio_cycle(*, now: float | None = None, drive_all: bool = False, budget_usd: float = 0.05) -> list[dict]:
    """Tous les businesses connus (objets stratégiques ou grand livre), y compris ceux ouverts par les agents.

    Évaluation et réinvestissement sans LLM pour tous ; relance ORBIT seulement avec `drive_all` (consentement
    donné par l'humain dans l'entrée de la planification).
    """
    ids = sorted({r["business"] for r in strategy.portfolio()} |
                 {r["business"] for r in journal.query("SELECT DISTINCT business FROM ledger_entries")})
    return [cycle(business, now=now, drive_orbit=drive_all, budget_usd=budget_usd) for business in ids]


def cycle(business: str, *, now: float | None = None, drive_orbit: bool = False, budget_usd: float = 0.05) -> dict:
    """Passe sans LLM : évalue les expériences en cours, réinvestit ; avec `drive_orbit`, relance l'exploration."""
    business = strategy._business(business)
    evaluations = [evaluate_experiment(business, e["id"], now=now)
                   for e in strategy.list_items("experiment", business, status="running", limit=500)]
    result = {"business": business,
              "evaluated": [{k: e[k] for k in ("experiment_id", "verdict", "reason")} for e in evaluations],
              "reinvest": reinvest(business, now=now)}
    if drive_orbit:
        result["drive"] = drive(business, now=now, budget_usd=budget_usd)
    return result
