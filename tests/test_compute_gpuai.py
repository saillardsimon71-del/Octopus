from __future__ import annotations

import json

import pytest

from octopus import economy
from octopus.compute import ComputeRequest
from octopus.compute_finance import BudgetLimits, FinancialCircuitBreaker, FinancialCircuitOpen
from octopus.gpuai import GPUAIClient, GPUAIConfig, GPUAIError




def reservation_id(key: str, price_per_hour: float) -> int:
    economy.grant_allowance("compute-test", 1.0, "USD", granted_by="human", rationale="provider unit test")
    breaker = FinancialCircuitBreaker(BudgetLimits(require_allowance=True))
    saved = breaker.reserve(
        business="compute-test", provider="gpuai", idempotency_key=key,
        estimated_cost_usd=0.005, price_per_hour=price_per_hour,
    )
    return int(saved["id"])


class Response:
    def __init__(self, payload, *, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_quote_selects_available_offer_with_observed_vram_and_price_cap():
    def opener(request, timeout):
        if request.full_url.endswith("/gpu-types?limit=100"):
            return Response({"data": [
                {"gpu_type": "rtx_4090", "vram_gb": 24},
                {"gpu_type": "rtx_5090", "vram_gb": 32},
            ]})
        if request.full_url.endswith("/pricing?include_unavailable=true&limit=100"):
            return Response({"data": [
                {"offering_id": "slow", "gpu_type": "rtx_5090", "gpu_count": 1, "region": "eu-west",
                 "tier": "on_demand", "price_per_hour": 0.49, "available": 0, "instant_boot": True,
                 "capacity_class": "secure"},
                {"offering_id": "chosen", "gpu_type": "rtx_5090", "gpu_count": 1, "region": "eu-east",
                 "tier": "on_demand", "price_per_hour": 0.54, "available": 2, "instant_boot": True,
                 "capacity_class": "community"},
                {"offering_id": "too-small", "gpu_type": "rtx_4090", "gpu_count": 1, "region": "eu-east",
                 "tier": "on_demand", "price_per_hour": 0.30, "available": 2, "instant_boot": True,
                 "capacity_class": "community"},
            ]})
        raise AssertionError(request.full_url)

    client = GPUAIClient(GPUAIConfig(api_key=None), opener=opener)
    offer = client.quote(ComputeRequest(min_vram_gb=30, max_price_per_hour=0.60, auto_terminate_hours=1))

    assert offer.offering_id == "chosen"
    assert offer.accelerator == "rtx_5090"
    assert offer.vram_gb == 32
    assert offer.price_per_hour == 0.54
    assert offer.observed_at > 0


def test_create_is_bounded_idempotent_and_uses_exact_quote():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return Response(
            {"operation_id": "op-1", "kind": "instance.create", "state": "pending"},
            status=202,
            headers={"Operation-Id": "op-1"},
        )

    client = GPUAIClient(GPUAIConfig(api_key="secret"), opener=opener)
    request = ComputeRequest(
        accelerator="rtx_5090", gpu_count=1, max_price_per_hour=0.60,
        auto_terminate_hours=1, ssh_key_ids=("key-1",),
    )
    offer = client.offer_from_dict({
        "offering_id": "offer-1", "gpu_type": "rtx_5090", "gpu_count": 1, "region": "eu-east",
        "tier": "on_demand", "price_per_hour": 0.54, "available": 2, "instant_boot": True,
        "capacity_class": "community", "vram_gb": 32,
    })

    operation = client.create(
        offer, request, idempotency_key="task-42-attempt-1",
        reservation_id=reservation_id("task-42-attempt-1", offer.price_per_hour),
    )

    sent = json.loads(seen[0].data)
    assert operation.operation_id == "op-1"
    assert seen[0].get_header("Idempotency-key") == "task-42-attempt-1"
    assert sent == {
        "gpu_type": "rtx_5090", "gpu_count": 1, "tier": "on_demand", "region": "eu-east",
        "offering_id": "offer-1", "max_price_per_hour": 0.60, "viewed_price_per_hour": 0.54,
        "auto_terminate_hours": 1, "ssh_key_ids": ["key-1"],
    }


@pytest.mark.parametrize("field,value", [
    ("max_price_per_hour", None),
    ("auto_terminate_hours", None),
    ("auto_terminate_hours", 0),
])
def test_paid_compute_request_requires_hard_cost_and_runtime_bounds(field, value):
    kwargs = {"max_price_per_hour": 1.0, "auto_terminate_hours": 1}
    kwargs[field] = value
    with pytest.raises(ValueError):
        ComputeRequest(**kwargs)


def test_mutating_gpuai_calls_require_credentials():
    client = GPUAIClient(GPUAIConfig(api_key=None), opener=lambda *args: pytest.fail("aucun appel"))
    request = ComputeRequest(max_price_per_hour=1, auto_terminate_hours=1)
    offer = client.offer_from_dict({
        "offering_id": "offer-1", "gpu_type": "rtx_5090", "gpu_count": 1, "region": "eu-east",
        "tier": "on_demand", "price_per_hour": 0.54, "available": 1, "instant_boot": True,
        "capacity_class": "community", "vram_gb": 32,
    })
    with pytest.raises(GPUAIError, match="GPUAI_API_KEY"):
        client.create(
            offer, request, idempotency_key="one",
            reservation_id=reservation_id("one", offer.price_per_hour),
        )


def test_create_rejects_direct_billable_call_without_reservation():
    client = GPUAIClient(GPUAIConfig(api_key="secret"), opener=lambda *_: pytest.fail("aucun HTTP"))
    request = ComputeRequest(max_price_per_hour=1, auto_terminate_hours=1)
    offer = client.offer_from_dict({
        "offering_id": "offer-1", "gpu_type": "rtx_5090", "gpu_count": 1, "region": "eu-east",
        "tier": "on_demand", "price_per_hour": 0.54, "available": 1, "instant_boot": True,
        "capacity_class": "community", "vram_gb": 32,
    })
    with pytest.raises(FinancialCircuitOpen, match="réservation financière active"):
        client.create(offer, request, idempotency_key="bypass")
