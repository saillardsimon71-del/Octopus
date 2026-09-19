from __future__ import annotations

import io
import json
import urllib.error

import pytest

from octopus.compute import ComputeOffer, ComputeRequest
from octopus.salad import SaladClient, SaladConfig, SaladError


class Response:
    def __init__(self, payload=None, *, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self):
        if self.payload is None:
            return b""
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def http_404(request):
    return urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO(b'{"title":"Not Found"}'))


def config(**kwargs):
    return SaladConfig(api_key="secret", organization="octopus", project="video", **kwargs)


def test_quote_uses_live_prices_and_skips_unavailable_cheapest_gpu():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        if request.method == "GET" and request.full_url.endswith("/organizations/octopus/gpu-classes"):
            return Response({"items": [
                {"id": "3090", "name": "RTX 3090 (24 GB)", "gpu_class_type": "community",
                 "prices": [{"priority": "batch", "price": "0.090"}]},
                {"id": "5090l", "name": "RTX 5090 Laptop (24 GB)", "gpu_class_type": "community",
                 "prices": [{"priority": "batch", "price": "0.100"}]},
                {"id": "5090", "name": "RTX 5090 (32 GB)", "gpu_class_type": "community",
                 "prices": [{"priority": "batch", "price": "0.250"}]},
            ]})
        if request.method == "POST" and request.full_url.endswith("/availability/sce-gpu-availability"):
            payload = json.loads(request.data)
            if payload["gpu_classes"] == ["3090"]:
                return Response({"available_gpu_batch": 0})
            if payload["gpu_classes"] == ["5090l"]:
                return Response({"available_gpu_batch": 3})
        raise AssertionError((request.method, request.full_url, request.data))

    client = SaladClient(config(), opener=opener)
    offer = client.quote(ComputeRequest(min_vram_gb=24, max_price_per_hour=0.20, auto_terminate_hours=1))

    assert offer.offering_id == "5090l"
    assert offer.accelerator == "rtx_5090_laptop"
    assert offer.vram_gb == 24
    assert offer.price_per_hour == 0.10
    assert offer.tier == "batch"
    assert offer.available == 3
    assert all(req.get_header("Salad-api-key") == "secret" for req in seen)


def test_quote_can_pin_accelerator_and_country():
    def opener(request, timeout):
        if request.method == "GET":
            return Response({"items": [
                {"id": "5090", "name": "RTX 5090 (32 GB)", "gpu_class_type": "community",
                 "prices": [{"priority": "low", "price": "0.333"}]},
            ]})
        payload = json.loads(request.data)
        assert payload["country_codes"] == ["fr"]
        return Response({"available_gpu_low": 2})

    client = SaladClient(config(), opener=opener)
    offer = client.quote(ComputeRequest(accelerator="rtx_5090", min_vram_gb=32, region="FR", tier="low",
                                        max_price_per_hour=0.40, auto_terminate_hours=1))
    assert offer.accelerator == "rtx_5090"
    assert offer.region == "fr"


def test_create_is_deterministic_idempotent_and_verified():
    calls = []
    get_count = 0

    def opener(request, timeout):
        nonlocal get_count
        calls.append(request)
        if request.method == "GET":
            get_count += 1
            if get_count == 1:
                raise http_404(request)
            return Response({
                "id": "group-id", "name": SaladClient.group_name_for("task-42-attempt-1"),
                "priority": "batch", "current_state": {"status": "running"},
                "container": {"image": "ghcr.io/octopus/wan:1", "resources": {"gpu_classes": ["5090"]}},
            })
        if request.method == "POST" and request.full_url.endswith("/containers"):
            return Response({"id": "group-id"}, status=202)
        raise AssertionError((request.method, request.full_url))

    client = SaladClient(config(), opener=opener)
    request = ComputeRequest(image="ghcr.io/octopus/wan:1", env={"MODE": "wan"}, max_price_per_hour=0.30,
                             auto_terminate_hours=1)
    offer = ComputeOffer("salad", "5090", "rtx_5090", 1, 32, "global", "batch", 0.25, 4, False, "community")

    operation = client.create(offer, request, idempotency_key="task-42-attempt-1")

    assert operation.state == "running"
    assert operation.resource_id == SaladClient.group_name_for("task-42-attempt-1")
    assert operation.raw["requires_external_termination"] is True
    create = next(req for req in calls if req.method == "POST" and req.full_url.endswith("/containers"))
    sent = json.loads(create.data)
    assert sent["replicas"] == 1
    assert sent["restart_policy"] == "never"
    assert sent["container"]["priority"] == "batch"
    assert sent["container"]["resources"]["gpu_classes"] == ["5090"]
    assert sent["container"]["image"] == "ghcr.io/octopus/wan:1"
    assert sent["container"]["environment_variables"]["MODE"] == "wan"
    assert sent["container"]["environment_variables"]["OCTOPUS_AUTO_TERMINATE_HOURS"] == "1"


def test_create_reuses_existing_group_without_second_billable_submission():
    posts = []
    group_name = SaladClient.group_name_for("same")

    def opener(request, timeout):
        if request.method == "GET":
            return Response({
                "id": "group-id", "name": group_name, "priority": "batch",
                "current_state": {"status": "running"},
                "container": {"image": "wan:1", "resources": {"gpu_classes": ["5090"]}},
            })
        posts.append(request)
        raise AssertionError("aucune deuxième soumission")

    client = SaladClient(config(), opener=opener)
    offer = ComputeOffer("salad", "5090", "rtx_5090", 1, 32, "global", "batch", 0.25, 2, False, "community")
    operation = client.create(offer, ComputeRequest(image="wan:1", max_price_per_hour=0.3, auto_terminate_hours=1),
                              idempotency_key="same")
    assert operation.state == "running"
    assert posts == []


def test_create_returns_ambiguous_state_if_post_succeeds_but_verification_fails():
    gets = 0

    def opener(request, timeout):
        nonlocal gets
        if request.method == "GET":
            gets += 1
            raise http_404(request)
        if request.method == "POST":
            return Response({"id": "accepted"}, status=202)
        raise AssertionError

    client = SaladClient(config(), opener=opener)
    offer = ComputeOffer("salad", "5090", "rtx_5090", 1, 32, "global", "batch", 0.25, 2, False, "community")
    operation = client.create(offer, ComputeRequest(image="wan:1", max_price_per_hour=0.3, auto_terminate_hours=1),
                              idempotency_key="ambiguous")
    assert operation.state == "submitted_unverified"
    assert gets == 2


def test_stop_and_delete_use_explicit_cleanup_endpoints():
    methods = []

    def opener(request, timeout):
        methods.append((request.method, request.full_url))
        if request.method == "POST":
            return Response(None, status=202)
        if request.method == "GET":
            return Response({"id": "id", "name": "octopus-x", "current_state": {"status": "stopped"}})
        if request.method == "DELETE":
            return Response(None, status=202)
        raise AssertionError

    client = SaladClient(config(), opener=opener)
    assert client.stop("octopus-x").state == "stopped"
    assert client.delete("octopus-x").state == "deleting"
    assert any(method == "POST" and url.endswith("/octopus-x/stop") for method, url in methods)
    assert any(method == "DELETE" and url.endswith("/octopus-x") for method, url in methods)


def test_quote_requires_credentials_and_rejects_multi_gpu():
    unauthenticated = SaladClient(SaladConfig(None, "octopus", "video"), opener=lambda *_: pytest.fail("aucun HTTP"))
    with pytest.raises(SaladError, match="SALAD_API_KEY"):
        unauthenticated.quote(ComputeRequest(max_price_per_hour=1, auto_terminate_hours=1))

    client = SaladClient(config(), opener=lambda *_: pytest.fail("aucun HTTP"))
    with pytest.raises(SaladError, match="un seul GPU"):
        client.quote(ComputeRequest(gpu_count=2, max_price_per_hour=1, auto_terminate_hours=1))
