"""Adaptateur REST GPU.ai conforme à l'OpenAPI v1."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .compute import ComputeInstance, ComputeOffer, ComputeOperation, ComputeRequest


class GPUAIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GPUAIConfig:
    api_key: str | None
    api_base_url: str = "https://api.gpu.ai/v1"
    timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> "GPUAIConfig":
        return cls(
            api_key=os.environ.get("GPUAI_API_KEY", "").strip() or None,
            api_base_url=os.environ.get("GPUAI_API_BASE_URL", "https://api.gpu.ai/v1").rstrip("/"),
            timeout_s=float(os.environ.get("GPUAI_REQUEST_TIMEOUT_S", "60")),
        )


class GPUAIClient:
    provider = "gpuai"

    def __init__(self, config: GPUAIConfig, *, opener: Callable | None = None):
        self.config = config
        self._opener = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                 payload: dict[str, Any] | None = None, authenticated: bool = False,
                 idempotency_key: str | None = None) -> tuple[dict, Any, int]:
        if authenticated and not self.config.api_key:
            raise GPUAIError("GPUAI_API_KEY absente")
        url = self.config.api_base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.config.timeout_s) as response:
                raw = response.read().decode("utf-8")
                status = int(getattr(response, "status", 200))
                response_headers = getattr(response, "headers", {})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise GPUAIError(f"GPU.ai HTTP {exc.code} sur {method} {path}: {detail}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GPUAIError(f"GPU.ai réseau sur {method} {path}: {exc}") from exc
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise GPUAIError(f"GPU.ai réponse non JSON sur {method} {path}") from exc
        if not isinstance(data, dict):
            raise GPUAIError(f"GPU.ai réponse inattendue sur {method} {path}")
        return data, response_headers, status

    def _list_all(self, path: str, params: dict[str, Any], *, authenticated: bool = False) -> list[dict]:
        found = []
        cursor = None
        while True:
            query = dict(params)
            if cursor:
                query["cursor"] = cursor
            data, _, _ = self._request("GET", path, params=query, authenticated=authenticated)
            page = data.get("data") or []
            if not isinstance(page, list):
                raise GPUAIError(f"GPU.ai pagination invalide sur {path}")
            found.extend(item for item in page if isinstance(item, dict))
            cursor = data.get("next_cursor")
            if not cursor:
                return found

    def offer_from_dict(self, item: dict, *, observed_at: float | None = None) -> ComputeOffer:
        import time
        return ComputeOffer(
            provider=self.provider,
            offering_id=str(item.get("offering_id") or ""),
            accelerator=str(item.get("gpu_type") or ""),
            gpu_count=int(item.get("gpu_count") or 0),
            vram_gb=int(item.get("vram_gb") or 0),
            region=str(item.get("region") or ""),
            tier=str(item.get("tier") or ""),
            price_per_hour=float(item.get("price_per_hour") or 0),
            available=int(item.get("available") or 0),
            instant_boot=bool(item.get("instant_boot")),
            capacity_class=str(item.get("capacity_class") or ""),
            observed_at=observed_at or time.time(),
            raw=dict(item),
        )

    def quote(self, request: ComputeRequest) -> ComputeOffer:
        types = self._list_all("/gpu-types", {"limit": 100})
        vram = {str(item.get("gpu_type")): int(item.get("vram_gb") or 0) for item in types}
        rows = self._list_all("/pricing", {"include_unavailable": "true", "limit": 100})
        offers = []
        for row in rows:
            row = {**row, "vram_gb": vram.get(str(row.get("gpu_type")), 0)}
            offer = self.offer_from_dict(row)
            if not offer.offering_id or offer.available <= 0 or offer.gpu_count != request.gpu_count:
                continue
            if request.accelerator and offer.accelerator != request.accelerator:
                continue
            if offer.vram_gb < request.min_vram_gb or offer.price_per_hour > request.max_price_per_hour:
                continue
            if request.region and offer.region != request.region:
                continue
            if offer.tier != request.tier:
                continue
            offers.append(offer)
        if not offers:
            raise GPUAIError("aucune offre GPU.ai disponible dans les limites demandées")
        return min(offers, key=lambda offer: (offer.price_per_hour, not offer.instant_boot,
                                               offer.capacity_class != "secure", offer.offering_id))

    def create(
        self, offer: ComputeOffer, request: ComputeRequest, *,
        idempotency_key: str, reservation_id: int | None = None,
    ) -> ComputeOperation:
        if offer.provider != self.provider:
            raise GPUAIError(f"offre d'un autre fournisseur: {offer.provider}")
        if offer.price_per_hour > request.max_price_per_hour:
            raise GPUAIError("le prix de l'offre dépasse la limite demandée")
        from .compute_finance import require_active_provider_reservation
        require_active_provider_reservation(
            reservation_id,
            provider=self.provider,
            idempotency_key=idempotency_key,
            price_per_hour=offer.price_per_hour,
        )
        payload: dict[str, Any] = {
            "gpu_type": offer.accelerator,
            "gpu_count": offer.gpu_count,
            "tier": offer.tier,
            "region": offer.region,
            "offering_id": offer.offering_id,
            "max_price_per_hour": request.max_price_per_hour,
            "viewed_price_per_hour": offer.price_per_hour,
            "auto_terminate_hours": request.auto_terminate_hours,
            "ssh_key_ids": list(request.ssh_key_ids),
        }
        for key, value in (("environment", request.environment), ("image", request.image)):
            if value:
                payload[key] = value
        if request.ports:
            payload["ports"] = list(request.ports)
        if request.env:
            payload["env"] = dict(request.env)
        data, headers, _ = self._request("POST", "/instances", payload=payload, authenticated=True,
                                         idempotency_key=idempotency_key)
        operation_id = data.get("operation_id") or headers.get("Operation-Id") or headers.get("operation-id")
        if not operation_id:
            raise GPUAIError("GPU.ai création acceptée sans operation_id")
        return ComputeOperation(self.provider, str(operation_id), str(data.get("state") or "pending"),
                                str(data["resource_id"]) if data.get("resource_id") else None,
                                raw=data)



    def get_operation(self, operation_id: str) -> ComputeOperation:
        operation_id = str(operation_id or "").strip()
        if not operation_id:
            raise GPUAIError("operation_id GPU.ai requis")
        data, _, _ = self._request("GET", f"/operations/{urllib.parse.quote(operation_id, safe='')}",
                                    authenticated=True)
        error = data.get("error")
        if isinstance(error, dict):
            error = error.get("detail") or error.get("message") or json.dumps(error, ensure_ascii=False)
        return ComputeOperation(
            self.provider,
            str(data.get("operation_id") or operation_id),
            str(data.get("state") or "pending"),
            str(data["resource_id"]) if data.get("resource_id") else None,
            str(error) if error else None,
            raw=data,
        )

    def get_instance(self, instance_id: str) -> ComputeInstance:
        instance_id = str(instance_id or "").strip()
        if not instance_id:
            raise GPUAIError("instance_id GPU.ai requis")
        data, _, _ = self._request("GET", f"/instances/{urllib.parse.quote(instance_id, safe='')}",
                                    authenticated=True)
        return ComputeInstance(
            provider=self.provider,
            instance_id=str(data.get("instance_id") or data.get("id") or instance_id),
            state=str(data.get("status") or data.get("state") or "unknown"),
            accelerator=str(data.get("gpu_type") or data.get("accelerator") or ""),
            gpu_count=int(data.get("gpu_count") or 1),
            price_per_hour=float(data.get("price_per_hour") or 0),
            connection=dict(data.get("connection") or {}),
            ready_at=str(data["ready_at"]) if data.get("ready_at") else None,
            auto_terminate_at=str(data["auto_terminate_at"]) if data.get("auto_terminate_at") else None,
            raw=data,
        )

    def stop(self, instance_id: str) -> ComputeOperation:
        instance_id = str(instance_id or "").strip()
        if not instance_id:
            raise GPUAIError("instance_id GPU.ai requis")
        path = f"/instances/{urllib.parse.quote(instance_id, safe='')}"
        try:
            data, headers, status = self._request("DELETE", path, authenticated=True)
        except GPUAIError as exc:
            if exc.status == 404:
                return ComputeOperation(self.provider, f"gone:{instance_id}", "succeeded", instance_id, raw={"gone": True})
            raise
        operation_id = data.get("operation_id") or headers.get("Operation-Id") or headers.get("operation-id")
        if operation_id:
            return ComputeOperation(self.provider, str(operation_id), str(data.get("state") or "pending"),
                                    instance_id, raw=data)
        if status in (200, 204):
            return ComputeOperation(self.provider, f"delete:{instance_id}", "succeeded", instance_id, raw=data)
        raise GPUAIError("GPU.ai terminaison acceptée sans operation_id")
