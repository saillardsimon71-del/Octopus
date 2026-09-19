from __future__ import annotations

import pytest

from octopus.compute import ComputeOffer, ComputeOperation, ComputeRequest
from octopus.compute_broker import ComputeBroker
from octopus.compute_finance import (
    BudgetLimits,
    FinancialCircuitBreaker,
    FinancialCircuitOpen,
    GuardedComputeManager,
)


class Clock:
    def __init__(self, now=1_000_000.0):
        self.value = float(now)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def limits(**overrides):
    values = dict(
        per_unit_usd=0.01,
        batch_usd=0.25,
        business_daily_usd=1.0,
        global_daily_usd=2.0,
        max_runtime_s=3600,
        idle_timeout_s=300,
        reservation_ttl_s=120,
        shutdown_margin_usd_per_unit=0.001,
    )
    values.update(overrides)
    return BudgetLimits(**values)


def test_reservation_is_idempotent_and_reserves_worst_case_budget():
    clock = Clock()
    breaker = FinancialCircuitBreaker(limits(), now=clock)
    first = breaker.reserve(
        business="podalux", provider="salad", idempotency_key="video-1",
        estimated_cost_usd=0.006, price_per_hour=0.25, job_key="video-1",
    )
    same = breaker.reserve(
        business="podalux", provider="salad", idempotency_key="video-1",
        estimated_cost_usd=0.006, price_per_hour=0.25, job_key="video-1",
    )

    assert same["id"] == first["id"]
    assert first["estimated_cost_usd"] == pytest.approx(0.006)
    assert first["hard_cap_usd"] == pytest.approx(0.01)
    assert first["max_runtime_s"] == pytest.approx(144.0)


def test_global_daily_cap_counts_active_reservations_at_hard_cap():
    clock = Clock()
    breaker = FinancialCircuitBreaker(limits(global_daily_usd=0.015), now=clock)
    breaker.reserve(
        business="a", provider="salad", idempotency_key="a1",
        estimated_cost_usd=0.006, price_per_hour=0.25,
    )

    with pytest.raises(FinancialCircuitOpen, match="coût estimé|budget GPU"):
        breaker.reserve(
            business="b", provider="salad", idempotency_key="b1",
            estimated_cost_usd=0.006, price_per_hour=0.25,
        )


def test_batch_cap_blocks_parallel_batch_reservations():
    clock = Clock()
    breaker = FinancialCircuitBreaker(limits(batch_usd=0.02), now=clock)
    first = breaker.reserve(
        business="podalux", provider="salad", idempotency_key="batch-a",
        estimated_cost_usd=0.015, price_per_hour=0.25, units_planned=2, batch_key="batch-1",
    )
    assert first["hard_cap_usd"] == pytest.approx(0.02)

    with pytest.raises(FinancialCircuitOpen, match="budget GPU"):
        breaker.reserve(
            business="podalux", provider="salad", idempotency_key="batch-b",
            estimated_cost_usd=0.001, price_per_hour=0.25, units_planned=1, batch_key="batch-1",
        )


def test_idempotency_collision_is_fail_closed():
    breaker = FinancialCircuitBreaker(limits())
    breaker.reserve(
        business="podalux", provider="salad", idempotency_key="same",
        estimated_cost_usd=0.005, price_per_hour=0.25, job_key="one",
    )
    with pytest.raises(FinancialCircuitOpen, match="collision"):
        breaker.reserve(
            business="other", provider="salad", idempotency_key="same",
            estimated_cost_usd=0.005, price_per_hour=0.25, job_key="one",
        )


class FakeProvider:
    provider = "salad"

    def __init__(self, breaker=None, *, create_error=None):
        self.breaker = breaker
        self.create_error = create_error
        self.stops = []

    def quote(self, request):
        return ComputeOffer("salad", "gpu", "rtx_5090", 1, 32, "global", "batch", 0.25, 1, False, "community")

    def create(self, offer, request, *, idempotency_key):
        if self.breaker is not None:
            assert self.breaker.by_key(idempotency_key)["status"] == "reserved"
        if self.create_error:
            raise self.create_error
        return ComputeOperation("salad", "op-1", "running", "group-1")

    def stop(self, resource_id):
        self.stops.append(resource_id)
        return ComputeOperation("salad", "stop-1", "stopped", resource_id)


def test_guarded_manager_reserves_before_provider_create():
    clock = Clock()
    breaker = FinancialCircuitBreaker(limits(), now=clock)
    provider = FakeProvider(breaker)
    manager = GuardedComputeManager(ComputeBroker([provider]), {"salad": provider}, breaker)

    selection, reservation, operation = manager.provision(
        ComputeRequest(max_price_per_hour=0.30, auto_terminate_hours=1),
        business="podalux", idempotency_key="managed-1",
        runtime_seconds={"rtx_5090": 60},
    )

    assert selection.estimated_cost == pytest.approx(0.25 * 60 / 3600)
    assert reservation["status"] == "running"
    assert operation.resource_id == "group-1"


def test_guarded_manager_refuses_unbenchmarked_paid_compute():
    breaker = FinancialCircuitBreaker(limits())
    provider = FakeProvider()
    manager = GuardedComputeManager(ComputeBroker([provider]), {"salad": provider}, breaker)

    with pytest.raises(FinancialCircuitOpen, match="benchmark runtime obligatoire"):
        manager.provision(
            ComputeRequest(max_price_per_hour=0.30, auto_terminate_hours=1),
            business="podalux", idempotency_key="unknown-runtime",
            runtime_seconds={},
        )
    assert breaker.by_key("unknown-runtime") is None


def test_create_exception_becomes_ambiguous_and_budget_stays_reserved():
    breaker = FinancialCircuitBreaker(limits())
    provider = FakeProvider(create_error=TimeoutError("network"))
    manager = GuardedComputeManager(ComputeBroker([provider]), {"salad": provider}, breaker)

    with pytest.raises(FinancialCircuitOpen, match="ambiguë"):
        manager.provision(
            ComputeRequest(max_price_per_hour=0.30, auto_terminate_hours=1),
            business="podalux", idempotency_key="ambiguous-1",
            runtime_seconds={"rtx_5090": 60},
        )

    saved = breaker.by_key("ambiguous-1")
    assert saved["status"] == "ambiguous"
    assert saved["actual_cost_usd"] is None


def test_watchdog_trips_before_per_video_hard_cap():
    clock = Clock()
    breaker = FinancialCircuitBreaker(limits(idle_timeout_s=1000), now=clock)
    provider = FakeProvider()
    saved = breaker.reserve(
        business="podalux", provider="salad", idempotency_key="trip-cost",
        estimated_cost_usd=0.005, price_per_hour=0.36,
    )
    breaker.attach_operation(saved["id"], ComputeOperation("salad", "op", "running", "group-cost"))

    # 0.36 USD/h = 0.0001 USD/s. Avec marge de 0.001, arrêt avant 0.010 USD.
    clock.advance(91)
    actions = breaker.watchdog({"salad": provider})
    final = breaker.get(saved["id"])

    assert provider.stops == ["group-cost"]
    assert actions[0]["action"] == "stopped"
    assert final["status"] == "tripped"
    assert final["actual_cost_usd"] < 0.01


def test_new_process_watchdog_reconciles_persisted_reservation_after_crash():
    clock = Clock()
    first_process = FinancialCircuitBreaker(limits(idle_timeout_s=10), now=clock)
    provider = FakeProvider()
    saved = first_process.reserve(
        business="podalux", provider="salad", idempotency_key="restart",
        estimated_cost_usd=0.003, price_per_hour=0.10,
    )
    first_process.attach_operation(saved["id"], ComputeOperation("salad", "op", "running", "group-restart"))

    clock.advance(15)
    restarted_process = FinancialCircuitBreaker(limits(idle_timeout_s=10), now=clock)
    restarted_process.watchdog({"salad": provider})

    assert provider.stops == ["group-restart"]
    assert restarted_process.get(saved["id"])["status"] == "tripped"


def test_stale_pre_submission_reservation_is_released_without_cloud_call():
    clock = Clock()
    breaker = FinancialCircuitBreaker(limits(reservation_ttl_s=30), now=clock)
    saved = breaker.reserve(
        business="podalux", provider="salad", idempotency_key="stale",
        estimated_cost_usd=0.002, price_per_hour=0.10,
    )
    clock.advance(31)

    actions = breaker.watchdog({})
    final = breaker.get(saved["id"])

    assert actions == [{"reservation_id": saved["id"], "action": "released_stale_reservation"}]
    assert final["status"] == "cancelled"
    assert final["actual_cost_usd"] == 0


def test_snapshot_reports_actual_cost_per_completed_video():
    clock = Clock()
    breaker = FinancialCircuitBreaker(limits(), now=clock)
    saved = breaker.reserve(
        business="podalux", provider="salad", idempotency_key="metrics",
        estimated_cost_usd=0.008, price_per_hour=0.25, units_planned=2,
    )
    breaker.attach_operation(saved["id"], ComputeOperation("salad", "op", "running", "group"))
    breaker.finalize(saved["id"], actual_cost_usd=0.012, units_completed=2, nature="observed")

    snap = breaker.snapshot(business="podalux")
    assert snap["completed_units"] == 2
    assert snap["actual_cost_usd"] == pytest.approx(0.012)
    assert snap["actual_cost_per_unit_usd"] == pytest.approx(0.006)
