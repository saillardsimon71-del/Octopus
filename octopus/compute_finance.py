"""Disjoncteur financier fail-closed pour le calcul GPU payant.

Une ressource cloud ne doit jamais être créée directement en production : le caller réserve d'abord
un plafond dans cette couche. Les réservations actives comptent à leur pire coût autorisé afin que
deux processus concurrents ne puissent pas consommer le même budget.

Le watchdog est volontairement indépendant du worker vidéo. Après un crash/restart, il relit SQLite,
réconcilie les opérations fournisseur quand c'est possible et coupe les ressources dépassant coût,
TTL ou délai d'inactivité.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping

from . import journal, tasks
from .compute import ComputeOperation, ComputeRequest
from .compute_broker import ComputeBroker, ComputeSelection


ACTIVE = ("reserved", "provisioning", "running", "ambiguous", "stopping")
BILLABLE = ("provisioning", "running", "ambiguous", "stopping")
TERMINAL = ("closed", "cancelled", "tripped")


class FinancialCircuitOpen(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetLimits:
    per_unit_usd: float = 0.01
    batch_usd: float = 0.25
    business_daily_usd: float = 1.00
    global_daily_usd: float = 2.00
    max_runtime_s: float = 3600.0
    idle_timeout_s: float = 300.0
    reservation_ttl_s: float = 120.0
    shutdown_margin_usd_per_unit: float = 0.001

    def __post_init__(self) -> None:
        values = {
            "per_unit_usd": self.per_unit_usd,
            "batch_usd": self.batch_usd,
            "business_daily_usd": self.business_daily_usd,
            "global_daily_usd": self.global_daily_usd,
            "max_runtime_s": self.max_runtime_s,
            "idle_timeout_s": self.idle_timeout_s,
            "reservation_ttl_s": self.reservation_ttl_s,
        }
        if any(value <= 0 for value in values.values()):
            raise ValueError("tous les plafonds financiers/temps doivent être strictement positifs")
        if self.shutdown_margin_usd_per_unit < 0 or self.shutdown_margin_usd_per_unit >= self.per_unit_usd:
            raise ValueError("shutdown_margin_usd_per_unit doit être >= 0 et < per_unit_usd")

    @classmethod
    def from_env(cls) -> "BudgetLimits":
        def number(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            try:
                return default if not raw else float(raw)
            except ValueError as exc:
                raise FinancialCircuitOpen(f"{name} illisible: {raw!r}") from exc
        return cls(
            per_unit_usd=number("OCTOPUS_GPU_MAX_COST_PER_VIDEO_USD", 0.01),
            batch_usd=number("OCTOPUS_GPU_MAX_BATCH_USD", 0.25),
            business_daily_usd=number("OCTOPUS_GPU_MAX_BUSINESS_DAILY_USD", 1.00),
            global_daily_usd=number("OCTOPUS_GPU_MAX_GLOBAL_DAILY_USD", 2.00),
            max_runtime_s=number("OCTOPUS_GPU_MAX_RUNTIME_S", 3600.0),
            idle_timeout_s=number("OCTOPUS_GPU_IDLE_TIMEOUT_S", 300.0),
            reservation_ttl_s=number("OCTOPUS_GPU_RESERVATION_TTL_S", 120.0),
            shutdown_margin_usd_per_unit=number("OCTOPUS_GPU_SHUTDOWN_MARGIN_PER_VIDEO_USD", 0.001),
        )


def _clean(value: str | None, name: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text and not optional:
        raise ValueError(f"{name} requis")
    return text or None


def _event(conn, reservation_id: int, kind: str, *, amount_usd: float | None = None,
           data: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO compute_cost_events (reservation_id, ts, kind, amount_usd, data) VALUES (?, ?, ?, ?, ?)",
        (reservation_id, time.time(), kind, amount_usd, json.dumps(data or {}, ensure_ascii=False)),
    )


def _row(row) -> dict | None:
    return dict(row) if row is not None else None


class FinancialCircuitBreaker:
    def __init__(self, limits: BudgetLimits | None = None, *, now=time.time):
        self.limits = limits or BudgetLimits.from_env()
        self._now = now

    def get(self, reservation_id: int) -> dict | None:
        rows = journal.query("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,))
        return _row(rows[0]) if rows else None

    def by_key(self, idempotency_key: str) -> dict | None:
        rows = journal.query("SELECT * FROM compute_reservations WHERE idempotency_key=?", (idempotency_key,))
        return _row(rows[0]) if rows else None

    @staticmethod
    def _committed_sql(where: str, params: list[Any]) -> tuple[str, tuple[Any, ...]]:
        active = ",".join("?" for _ in ACTIVE)
        sql = (
            "SELECT COALESCE(SUM(CASE WHEN status IN (" + active + ") THEN hard_cap_usd "
            "ELSE COALESCE(actual_cost_usd, 0) END), 0) AS total FROM compute_reservations WHERE " + where
        )
        return sql, tuple([*ACTIVE, *params])

    def _committed(self, conn, where: str, params: list[Any]) -> float:
        sql, values = self._committed_sql(where, params)
        return float(conn.execute(sql, values).fetchone()["total"] or 0.0)

    def reserve(self, *, business: str, provider: str, idempotency_key: str, estimated_cost_usd: float,
                price_per_hour: float, units_planned: int = 1, batch_key: str | None = None,
                job_key: str | None = None, task_id: int | None = None,
                max_runtime_s: float | None = None, idle_timeout_s: float | None = None) -> dict:
        business = _clean(business, "business")
        provider = _clean(provider, "provider")
        key = _clean(idempotency_key, "idempotency_key")
        batch_key = _clean(batch_key, "batch_key", optional=True)
        job_key = _clean(job_key, "job_key", optional=True)
        if isinstance(units_planned, bool) or units_planned < 1:
            raise ValueError("units_planned doit être >= 1")
        if estimated_cost_usd <= 0 or price_per_hour <= 0:
            raise ValueError("estimated_cost_usd et price_per_hour doivent être > 0")

        now = float(self._now())
        day_start = now - (now % 86400)
        requested_runtime = min(float(max_runtime_s or self.limits.max_runtime_s), self.limits.max_runtime_s)
        requested_idle = min(float(idle_timeout_s or self.limits.idle_timeout_s), requested_runtime)
        if requested_runtime <= 0 or requested_idle <= 0:
            raise ValueError("runtime et idle timeout doivent être > 0")

        with tasks._tx() as conn:
            existing = conn.execute("SELECT * FROM compute_reservations WHERE idempotency_key=?", (key,)).fetchone()
            if existing is not None:
                old = dict(existing)
                expected = (business, provider, batch_key, job_key, int(units_planned))
                actual = (old["business"], old["provider"], old["batch_key"], old["job_key"], old["units_planned"])
                if expected != actual:
                    raise FinancialCircuitOpen("collision de clé d'idempotence avec une réservation différente")
                return old

            per_unit_cap = self.limits.per_unit_usd * units_planned
            batch_used = self._committed(conn, "batch_key=?", [batch_key]) if batch_key else 0.0
            business_used = self._committed(
                conn, "(business=? AND created_at>=?) OR (business=? AND status IN (" +
                ",".join("?" for _ in ACTIVE) + "))",
                [business, day_start, business, *ACTIVE],
            )
            global_used = self._committed(
                conn, "created_at>=? OR status IN (" + ",".join("?" for _ in ACTIVE) + ")",
                [day_start, *ACTIVE],
            )

            batch_remaining = self.limits.batch_usd - batch_used if batch_key else self.limits.batch_usd
            business_remaining = self.limits.business_daily_usd - business_used
            global_remaining = self.limits.global_daily_usd - global_used
            hard_cap = min(per_unit_cap, batch_remaining, business_remaining, global_remaining)
            if hard_cap <= 1e-9:
                raise FinancialCircuitOpen("budget GPU épuisé avant réservation")
            if estimated_cost_usd > hard_cap + 1e-9:
                raise FinancialCircuitOpen(
                    f"coût estimé {estimated_cost_usd:.6f} USD > plafond disponible {hard_cap:.6f} USD"
                )

            cost_runtime = hard_cap / price_per_hour * 3600.0
            effective_runtime = min(requested_runtime, cost_runtime)
            cur = conn.execute(
                "INSERT INTO compute_reservations "
                "(idempotency_key, business, provider, job_key, batch_key, task_id, status, units_planned, "
                "estimated_cost_usd, hard_cap_usd, price_per_hour, max_runtime_s, idle_timeout_s, "
                "created_at, last_activity_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (key, business, provider, job_key, batch_key, task_id, units_planned,
                 float(estimated_cost_usd), float(hard_cap), float(price_per_hour), effective_runtime,
                 requested_idle, now, now, now),
            )
            reservation_id = int(cur.lastrowid)
            _event(conn, reservation_id, "reserved", amount_usd=hard_cap, data={
                "estimated_cost_usd": estimated_cost_usd,
                "limits": {
                    "per_unit_usd": self.limits.per_unit_usd,
                    "batch_remaining_usd": batch_remaining,
                    "business_daily_remaining_usd": business_remaining,
                    "global_daily_remaining_usd": global_remaining,
                },
            })
            tasks._emit(conn, business, task_id, "compute.finance.reserved", {
                "reservation_id": reservation_id, "provider": provider, "hard_cap_usd": hard_cap,
                "estimated_cost_usd": estimated_cost_usd,
            })
            return dict(conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone())

    def attach_operation(self, reservation_id: int, operation: ComputeOperation) -> dict:
        now = float(self._now())
        with tasks._tx() as conn:
            row = conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                raise FinancialCircuitOpen(f"réservation #{reservation_id} introuvable")
            if row["status"] in TERMINAL:
                raise FinancialCircuitOpen(f"réservation #{reservation_id} déjà close")
            ambiguous = "unverified" in operation.state or "ambiguous" in operation.state
            status = "ambiguous" if ambiguous else ("running" if operation.resource_id else "provisioning")
            conn.execute(
                "UPDATE compute_reservations SET operation_id=?, resource_id=COALESCE(?, resource_id), status=?, "
                "started_at=COALESCE(started_at, ?), last_activity_at=?, reason=?, updated_at=? WHERE id=?",
                (operation.operation_id, operation.resource_id, status, now, now, operation.error, now, reservation_id),
            )
            _event(conn, reservation_id, "operation_attached", data={
                "operation_id": operation.operation_id, "resource_id": operation.resource_id,
                "state": operation.state, "ambiguous": ambiguous,
            })
            return dict(conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone())

    def mark_ambiguous(self, reservation_id: int, reason: str) -> dict:
        now = float(self._now())
        with tasks._tx() as conn:
            row = conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None or row["status"] in TERMINAL:
                raise FinancialCircuitOpen(f"réservation #{reservation_id} non ambiguïsable")
            conn.execute(
                "UPDATE compute_reservations SET status='ambiguous', started_at=COALESCE(started_at, ?), "
                "reason=?, updated_at=? WHERE id=?", (now, str(reason)[:1000], now, reservation_id),
            )
            _event(conn, reservation_id, "ambiguous", data={"reason": str(reason)[:1000]})
            return dict(conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone())

    def touch(self, reservation_id: int, *, units_completed: int | None = None) -> dict:
        now = float(self._now())
        with tasks._tx() as conn:
            row = conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None or row["status"] not in ACTIVE:
                raise FinancialCircuitOpen(f"réservation #{reservation_id} inactive")
            completed = row["units_completed"] if units_completed is None else int(units_completed)
            if completed < row["units_completed"] or completed > row["units_planned"]:
                raise ValueError("units_completed doit progresser sans dépasser units_planned")
            conn.execute(
                "UPDATE compute_reservations SET last_activity_at=?, units_completed=?, "
                "status=CASE WHEN status='provisioning' AND resource_id IS NOT NULL THEN 'running' ELSE status END, "
                "updated_at=? WHERE id=?", (now, completed, now, reservation_id),
            )
            return dict(conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone())

    def cancel(self, reservation_id: int, *, reason: str) -> dict:
        now = float(self._now())
        with tasks._tx() as conn:
            row = conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                raise FinancialCircuitOpen(f"réservation #{reservation_id} introuvable")
            if row["status"] not in ("reserved",):
                raise FinancialCircuitOpen("seule une réservation non soumise peut être libérée sans arrêt fournisseur")
            conn.execute(
                "UPDATE compute_reservations SET status='cancelled', actual_cost_usd=0, cost_nature='computed', "
                "reason=?, closed_at=?, updated_at=? WHERE id=?", (reason[:1000], now, now, reservation_id),
            )
            _event(conn, reservation_id, "cancelled", amount_usd=0, data={"reason": reason[:1000]})
            return dict(conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone())

    def finalize(self, reservation_id: int, *, actual_cost_usd: float | None = None,
                 units_completed: int | None = None, tripped: bool = False,
                 reason: str | None = None, nature: str = "computed") -> dict:
        now = float(self._now())
        with tasks._tx() as conn:
            row = conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                raise FinancialCircuitOpen(f"réservation #{reservation_id} introuvable")
            if row["status"] in ("closed", "tripped"):
                return dict(row)
            started = row["started_at"] or row["created_at"]
            computed = max(0.0, now - started) * row["price_per_hour"] / 3600.0
            actual = computed if actual_cost_usd is None else float(actual_cost_usd)
            if actual < 0:
                raise ValueError("actual_cost_usd ne peut pas être négatif")
            completed = row["units_completed"] if units_completed is None else int(units_completed)
            if completed < 0 or completed > row["units_planned"]:
                raise ValueError("units_completed invalide")
            status = "tripped" if tripped else "closed"
            conn.execute(
                "UPDATE compute_reservations SET status=?, units_completed=?, actual_cost_usd=?, cost_nature=?, "
                "reason=COALESCE(?, reason), closed_at=?, updated_at=? WHERE id=?",
                (status, completed, actual, nature, reason, now, now, reservation_id),
            )
            _event(conn, reservation_id, status, amount_usd=actual, data={
                "units_completed": completed,
                "cost_per_unit_usd": (actual / completed) if completed else None,
                "reason": reason,
            })
            tasks._emit(conn, row["business"], row["task_id"], f"compute.finance.{status}", {
                "reservation_id": reservation_id, "actual_cost_usd": actual, "units_completed": completed,
            })
            return dict(conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone())

    def _update_resource_from_operation(self, reservation_id: int, operation: ComputeOperation) -> dict:
        now = float(self._now())
        with tasks._tx() as conn:
            row = conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone()
            if row is None:
                raise FinancialCircuitOpen("réservation introuvable")
            if operation.state in ("failed", "cancelled"):
                conn.execute(
                    "UPDATE compute_reservations SET status='cancelled', actual_cost_usd=0, cost_nature='computed', "
                    "reason=?, closed_at=?, updated_at=? WHERE id=?",
                    (operation.error or operation.state, now, now, reservation_id),
                )
                _event(conn, reservation_id, "provider_operation_failed", amount_usd=0,
                       data={"state": operation.state, "error": operation.error})
            else:
                status = "running" if operation.resource_id else row["status"]
                conn.execute(
                    "UPDATE compute_reservations SET resource_id=COALESCE(?, resource_id), status=?, "
                    "last_activity_at=?, updated_at=? WHERE id=?",
                    (operation.resource_id, status, now, now, reservation_id),
                )
            return dict(conn.execute("SELECT * FROM compute_reservations WHERE id=?", (reservation_id,)).fetchone())

    def _begin_stop(self, row: dict, reason: str) -> None:
        now = float(self._now())
        with tasks._tx() as conn:
            conn.execute(
                "UPDATE compute_reservations SET status='stopping', reason=?, stop_requested_at=COALESCE(stop_requested_at, ?), "
                "updated_at=? WHERE id=?", (reason[:1000], now, now, row["id"]),
            )
            _event(conn, row["id"], "trip_requested", data={"reason": reason[:1000]})

    def watchdog(self, providers: Mapping[str, Any], *, now: float | None = None) -> list[dict]:
        now = float(self._now() if now is None else now)
        rows = [dict(row) for row in journal.query(
            "SELECT * FROM compute_reservations WHERE status IN (" + ",".join("?" for _ in ACTIVE) + ") ORDER BY id",
            ACTIVE,
        )]
        actions: list[dict] = []
        for row in rows:
            provider = providers.get(row["provider"])
            if row["status"] == "reserved" and now - row["created_at"] >= self.limits.reservation_ttl_s:
                self.cancel(row["id"], reason="réservation expirée avant soumission")
                actions.append({"reservation_id": row["id"], "action": "released_stale_reservation"})
                continue

            # Une création GPU.ai peut n'avoir que son operation_id. Au restart, on la résout avant toute décision.
            if not row["resource_id"] and row["operation_id"] and provider is not None and hasattr(provider, "get_operation"):
                try:
                    operation = provider.get_operation(row["operation_id"])
                    row = self._update_resource_from_operation(row["id"], operation)
                except Exception as exc:
                    actions.append({"reservation_id": row["id"], "action": "reconcile_failed",
                                    "error": f"{type(exc).__name__}: {exc}"})

            if row["status"] in TERMINAL:
                continue
            started = row["started_at"] or row["created_at"]
            elapsed = max(0.0, now - started)
            accrued = elapsed * row["price_per_hour"] / 3600.0
            trip_at = max(
                0.0,
                row["hard_cap_usd"] - self.limits.shutdown_margin_usd_per_unit * row["units_planned"],
            )
            reason = None
            if accrued >= trip_at - 1e-12:
                reason = f"plafond coût approché ({accrued:.6f}/{row['hard_cap_usd']:.6f} USD)"
            elif elapsed >= row["max_runtime_s"]:
                reason = f"TTL GPU dépassé ({elapsed:.1f}/{row['max_runtime_s']:.1f}s)"
            elif now - row["last_activity_at"] >= row["idle_timeout_s"]:
                reason = f"GPU inactif ({now - row['last_activity_at']:.1f}s)"

            if row["status"] == "stopping" and row["stop_operation_id"] and provider is not None and hasattr(provider, "get_operation"):
                try:
                    stop_op = provider.get_operation(row["stop_operation_id"])
                    if stop_op.state == "succeeded":
                        final = self.finalize(row["id"], tripped=True, reason=row["reason"])
                        actions.append({"reservation_id": row["id"], "action": "stopped", "actual_cost_usd": final["actual_cost_usd"]})
                        continue
                except Exception as exc:
                    actions.append({"reservation_id": row["id"], "action": "stop_reconcile_failed",
                                    "error": f"{type(exc).__name__}: {exc}"})

            if reason is None and row["status"] != "stopping":
                continue
            if provider is None:
                actions.append({"reservation_id": row["id"], "action": "provider_missing", "reason": reason or row["reason"]})
                continue
            if not row["resource_id"]:
                actions.append({"reservation_id": row["id"], "action": "awaiting_resource_id", "reason": reason or row["reason"]})
                continue

            if row["status"] != "stopping":
                self._begin_stop(row, reason or "arrêt financier")
            try:
                if hasattr(provider, "stop"):
                    operation = provider.stop(row["resource_id"])
                elif hasattr(provider, "delete"):
                    operation = provider.delete(row["resource_id"])
                else:
                    raise FinancialCircuitOpen(f"provider {row['provider']} sans stop/delete")
            except Exception as exc:
                actions.append({"reservation_id": row["id"], "action": "stop_failed",
                                "error": f"{type(exc).__name__}: {exc}"})
                continue

            state = str(getattr(operation, "state", "") or "").lower()
            if state in ("stopped", "terminated", "deleted", "succeeded"):
                final = self.finalize(row["id"], tripped=True, reason=reason or row["reason"])
                actions.append({"reservation_id": row["id"], "action": "stopped", "actual_cost_usd": final["actual_cost_usd"]})
            else:
                with tasks._tx() as conn:
                    conn.execute("UPDATE compute_reservations SET stop_operation_id=?, updated_at=? WHERE id=?",
                                 (getattr(operation, "operation_id", None), now, row["id"]))
                    _event(conn, row["id"], "stop_submitted", data={
                        "operation_id": getattr(operation, "operation_id", None), "state": state,
                    })
                actions.append({"reservation_id": row["id"], "action": "stop_submitted", "state": state})
        return actions

    def snapshot(self, *, business: str | None = None, now: float | None = None) -> dict:
        now = float(self._now() if now is None else now)
        day_start = now - (now % 86400)
        params: list[Any] = []
        where = "1=1"
        if business:
            where = "business=?"
            params.append(business)
        rows = [dict(row) for row in journal.query(
            f"SELECT * FROM compute_reservations WHERE {where} ORDER BY id DESC", tuple(params)
        )]
        active = [r for r in rows if r["status"] in ACTIVE]
        today = [r for r in rows if r["created_at"] >= day_start or r["status"] in ACTIVE]
        committed = sum(r["hard_cap_usd"] if r["status"] in ACTIVE else float(r["actual_cost_usd"] or 0) for r in today)
        actual = sum(float(r["actual_cost_usd"] or 0) for r in rows if r["status"] in TERMINAL)
        units = sum(int(r["units_completed"] or 0) for r in rows if r["status"] in TERMINAL)
        return {
            "business": business,
            "active_reservations": len(active),
            "today_committed_usd": round(committed, 6),
            "actual_cost_usd": round(actual, 6),
            "completed_units": units,
            "actual_cost_per_unit_usd": round(actual / units, 6) if units else None,
            "limits": self.limits.__dict__,
        }


class GuardedComputeManager:
    """Unique chemin autorisé pour provisionner du compute payant dans OCTOPUS."""

    def __init__(self, broker: ComputeBroker, providers: Mapping[str, Any],
                 breaker: FinancialCircuitBreaker | None = None):
        self.broker = broker
        self.providers = dict(providers)
        self.breaker = breaker or FinancialCircuitBreaker()

    def provision(self, request: ComputeRequest, *, business: str, idempotency_key: str,
                  runtime_seconds: Mapping[str, float], units_planned: int = 1,
                  batch_key: str | None = None, job_key: str | None = None,
                  task_id: int | None = None) -> tuple[ComputeSelection, dict, ComputeOperation]:
        selection = self.broker.select(request, runtime_seconds=runtime_seconds)
        if selection.estimated_cost is None or selection.estimated_runtime_s is None:
            raise FinancialCircuitOpen("benchmark runtime obligatoire avant tout provisionnement payant")
        provider = self.providers.get(selection.offer.provider)
        if provider is None:
            raise FinancialCircuitOpen(f"provider {selection.offer.provider} non configuré")

        reservation = self.breaker.reserve(
            business=business,
            provider=selection.offer.provider,
            idempotency_key=idempotency_key,
            estimated_cost_usd=selection.estimated_cost,
            price_per_hour=selection.offer.price_per_hour,
            units_planned=units_planned,
            batch_key=batch_key,
            job_key=job_key,
            task_id=task_id,
            max_runtime_s=min(request.auto_terminate_hours * 3600.0, self.breaker.limits.max_runtime_s),
        )
        if reservation["status"] != "reserved":
            existing = reservation
            if existing["operation_id"]:
                return selection, existing, ComputeOperation(
                    existing["provider"], existing["operation_id"], existing["status"], existing["resource_id"],
                    existing["reason"],
                )
            raise FinancialCircuitOpen("réservation idempotente existante sans opération fournisseur")

        try:
            operation = provider.create(selection.offer, request, idempotency_key=idempotency_key)
        except Exception as exc:
            # Après l'entrée dans create(), une erreur réseau peut être post-soumission. Fail closed :
            # on conserve le budget et exige une réconciliation, jamais une seconde création automatique.
            ambiguous = self.breaker.mark_ambiguous(reservation["id"], f"{type(exc).__name__}: {exc}")
            raise FinancialCircuitOpen(
                f"création fournisseur ambiguë, réservation #{reservation['id']} conservée pour réconciliation"
            ) from exc
        attached = self.breaker.attach_operation(reservation["id"], operation)
        return selection, attached, operation

    def watchdog(self) -> list[dict]:
        return self.breaker.watchdog(self.providers)
