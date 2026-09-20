from __future__ import annotations

from octopus.compute import ComputeOffer, ComputeRequest
from octopus.compute_broker import ComputeBroker


class Provider:
    def __init__(self, provider, offer=None, error=None):
        self.provider = provider
        self.offer = offer
        self.error = error

    def quote(self, request):
        if self.error:
            raise RuntimeError(self.error)
        return self.offer


def offer(provider, accelerator, price):
    return ComputeOffer(provider, provider + "-offer", accelerator, 1, 24, "global", "batch", price, 1, False, "community")


def test_broker_selects_lowest_hourly_price_without_benchmarks():
    broker = ComputeBroker([Provider("gpuai", offer("gpuai", "rtx_5090", 0.51)),
                            Provider("salad", offer("salad", "rtx_3090", 0.09))])
    selection = broker.select(ComputeRequest(max_price_per_hour=1, auto_terminate_hours=1))
    assert selection.offer.provider == "salad"
    assert selection.estimated_cost is None


def test_broker_uses_cost_per_job_when_benchmarks_exist():
    broker = ComputeBroker([Provider("salad-3090", offer("salad-3090", "rtx_3090", 0.09)),
                            Provider("salad-5090", offer("salad-5090", "rtx_5090", 0.25))])
    selection = broker.select(
        ComputeRequest(max_price_per_hour=1, auto_terminate_hours=1),
        runtime_seconds={"rtx_3090": 120, "rtx_5090": 20},
    )
    assert selection.offer.accelerator == "rtx_5090"
    assert selection.estimated_cost == 0.25 * 20 / 3600


def test_broker_keeps_provider_errors_while_using_a_working_provider():
    broker = ComputeBroker([Provider("broken", error="offline"), Provider("salad", offer("salad", "rtx_3090", 0.09))])
    selection = broker.select(ComputeRequest(max_price_per_hour=1, auto_terminate_hours=1))
    assert selection.offer.provider == "salad"
    assert "broken" in selection.provider_errors
