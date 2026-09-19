"""Adaptateur SaladCloud Container Engine pour calcul GPU éphémère et borné."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .compute import ComputeOffer, ComputeOperation, ComputeRequest


class SaladError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SaladConfig:
    api_key: str | None
    organization: str | None
    project: str | None
    api_base_url: str = "https://api.salad.com/api/public"
    timeout_s: float = 60.0
    default_image: str | None = None
    cpu: int = 8
    memory_mb: int = 16384
    storage_bytes: int = 50 * 1024**3
    shm_size_mb: int = 4096

    @classmethod
    def from_env(cls) -> "SaladConfig":
        return cls(
            api_key=os.environ.get("SALAD_API_KEY", "").strip() or None,
            organization=os.environ.get("SALAD_ORGANIZATION", "").strip() or None,
            project=os.environ.get("SALAD_PROJECT", "").strip() or None,
            api_base_url=os.environ.get("SALAD_API_BASE_URL", "https://api.salad.com/api/public").rstrip("/"),
            timeout_s=float(os.environ.get("SALAD_REQUEST_TIMEOUT_S", "60")),
            default_image=os.environ.get("SALAD_CONTAINER_IMAGE", "").strip() or None,
            cpu=int(os.environ.get("SALAD_CPU", "8")),
            memory_mb=int(os.environ.get("SALAD_MEMORY_MB", "16384")),
            storage_bytes=int(os.environ.get("SALAD_STORAGE_BYTES", str(50 * 1024**3))),
            shm_size_mb=int(os.environ.get("SALAD_SHM_SIZE_MB", "4096")),
        )


class SaladClient:
    provider = "salad"

    _TIER_ALIASES = {
        "on_demand": "batch",
        "cheapest": "batch",
        "lowest": "batch",
        "batch": "batch",
        "low": "low",
        "medium": "medium",
        "high": "high",
    }

    def __init__(self, config: SaladConfig, *, opener: Callable | None = None):
        self.config = config
        self._opener = opener or urllib.request.urlopen

    def _require_scope(self, *, project: bool = False) -> None:
        if not self.config.api_key:
            raise SaladError("SALAD_API_KEY absente")
        if not self.config.organization:
            raise SaladError("SALAD_ORGANIZATION absente")
        if project and not self.config.project:
            raise SaladError("SALAD_PROJECT absent")

    def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None,
                 authenticated: bool = True) -> tuple[dict[str, Any], Any, int]:
        if authenticated:
            self._require_scope()
        url = self.config.api_base_url + path
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Salad-Api-Key"] = str(self.config.api_key)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.config.timeout_s) as response:
                raw = response.read().decode("utf-8")
                status = int(getattr(response, "status", 200))
                response_headers = getattr(response, "headers", {})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise SaladError(f"SaladCloud HTTP {exc.code} sur {method} {path}: {detail}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SaladError(f"SaladCloud réseau sur {method} {path}: {exc}") from exc
        if not raw.strip():
            return {}, response_headers, status
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SaladError(f"SaladCloud réponse non JSON sur {method} {path}") from exc
        if not isinstance(data, dict):
            raise SaladError(f"SaladCloud réponse inattendue sur {method} {path}")
        return data, response_headers, status

    @staticmethod
    def _slug_gpu_name(name: str) -> str:
        base = re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "").strip().lower())
        return re.sub(r"[^a-z0-9]+", "_", base).strip("_")

    @staticmethod
    def _vram_gb(name: str) -> int:
        match = re.search(r"\((\d+(?:\.\d+)?)\s*GB\)", str(name or ""), flags=re.I)
        return int(float(match.group(1))) if match else 0

    @classmethod
    def _priority(cls, tier: str) -> str:
        key = str(tier or "").strip().lower()
        try:
            return cls._TIER_ALIASES[key]
        except KeyError as exc:
            raise SaladError(f"tier SaladCloud non supporté: {tier}") from exc

    @staticmethod
    def _price_for(item: dict[str, Any], priority: str) -> float | None:
        for row in item.get("prices") or []:
            if not isinstance(row, dict):
                continue
            row_priority = str(row.get("priority") or "").lower()
            if row_priority == "lowest":
                row_priority = "batch"
            if row_priority != priority:
                continue
            try:
                price = float(row.get("price"))
            except (TypeError, ValueError):
                return None
            return price if price > 0 else None
        return None

    def _country_codes(self, request: ComputeRequest) -> list[str]:
        if not request.region:
            return []
        region = request.region.strip().lower()
        if not re.fullmatch(r"[a-z]{2}", region):
            raise SaladError("SaladCloud attend un code pays ISO alpha-2 dans ComputeRequest.region")
        return [region]

    def _availability(self, gpu_class_id: str, request: ComputeRequest, priority: str) -> int:
        payload: dict[str, Any] = {
            "gpu_classes": [gpu_class_id],
            "cpu": self.config.cpu,
            "memory": self.config.memory_mb,
            "storage_amount": self.config.storage_bytes,
        }
        countries = self._country_codes(request)
        if countries:
            payload["country_codes"] = countries
        data, _, _ = self._request(
            "POST",
            f"/organizations/{urllib.parse.quote(str(self.config.organization), safe='')}/availability/sce-gpu-availability",
            payload=payload,
        )
        return max(0, int(data.get(f"available_gpu_{priority}") or 0))

    def quote(self, request: ComputeRequest) -> ComputeOffer:
        self._require_scope()
        if request.gpu_count != 1:
            raise SaladError("SaladCloud provider supporte actuellement un seul GPU par replica")
        priority = self._priority(request.tier)
        data, _, _ = self._request(
            "GET", f"/organizations/{urllib.parse.quote(str(self.config.organization), safe='')}/gpu-classes"
        )
        items = data.get("items") or []
        if not isinstance(items, list):
            raise SaladError("liste des GPU SaladCloud invalide")
        requested_accelerator = self._slug_gpu_name(request.accelerator) if request.accelerator else None
        candidates: list[tuple[float, dict[str, Any], str, int]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            gpu_id = str(item.get("id") or "")
            name = str(item.get("name") or "")
            accelerator = self._slug_gpu_name(name)
            vram_gb = self._vram_gb(name)
            price = self._price_for(item, priority)
            if not gpu_id or not accelerator or price is None:
                continue
            if requested_accelerator and accelerator != requested_accelerator:
                continue
            if vram_gb < request.min_vram_gb or price > request.max_price_per_hour:
                continue
            candidates.append((price, item, accelerator, vram_gb))
        candidates.sort(key=lambda row: (row[0], bool(row[1].get("is_high_demand")), row[2]))
        for price, item, accelerator, vram_gb in candidates:
            available = self._availability(str(item["id"]), request, priority)
            if available <= 0:
                continue
            return ComputeOffer(
                provider=self.provider,
                offering_id=str(item["id"]),
                accelerator=accelerator,
                gpu_count=1,
                vram_gb=vram_gb,
                region=(request.region or "global").lower(),
                tier=priority,
                price_per_hour=price,
                available=available,
                instant_boot=False,
                capacity_class=str(item.get("gpu_class_type") or "community"),
                raw=dict(item),
            )
        raise SaladError("aucune offre SaladCloud disponible dans les limites demandées")

    @staticmethod
    def group_name_for(idempotency_key: str) -> str:
        if not idempotency_key or len(idempotency_key) > 500:
            raise SaladError("idempotency_key SaladCloud invalide")
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
        return f"octopus-{digest}"

    def _group_path(self, group_name: str) -> str:
        self._require_scope(project=True)
        org = urllib.parse.quote(str(self.config.organization), safe="")
        project = urllib.parse.quote(str(self.config.project), safe="")
        group = urllib.parse.quote(group_name, safe="")
        return f"/organizations/{org}/projects/{project}/containers/{group}"

    def get_group(self, group_name: str) -> dict[str, Any]:
        data, _, _ = self._request("GET", self._group_path(group_name))
        return data

    def _get_group_or_none(self, group_name: str) -> dict[str, Any] | None:
        try:
            return self.get_group(group_name)
        except SaladError as exc:
            if exc.status == 404:
                return None
            raise

    def _operation_from_group(self, group: dict[str, Any], *, fallback_name: str) -> ComputeOperation:
        current_state = group.get("current_state") if isinstance(group.get("current_state"), dict) else {}
        name = str(group.get("name") or fallback_name)
        return ComputeOperation(
            provider=self.provider,
            operation_id=str(group.get("id") or name),
            state=str(current_state.get("status") or "pending"),
            resource_id=name,
            raw=group,
        )

    def create(self, offer: ComputeOffer, request: ComputeRequest, *, idempotency_key: str) -> ComputeOperation:
        self._require_scope(project=True)
        if offer.provider != self.provider:
            raise SaladError(f"offre d'un autre fournisseur: {offer.provider}")
        if request.gpu_count != 1 or offer.gpu_count != 1:
            raise SaladError("SaladCloud provider supporte actuellement un seul GPU par replica")
        if offer.price_per_hour > request.max_price_per_hour:
            raise SaladError("le prix de l'offre dépasse la limite demandée")
        if self._priority(request.tier) != offer.tier:
            raise SaladError("le tier de l'offre ne correspond pas à la requête")
        if request.ssh_key_ids or request.ports or request.environment:
            raise SaladError("ssh_key_ids, ports et environment ne sont pas encore mappés vers SaladCloud")
        image = (request.image or self.config.default_image or "").strip()
        if not image:
            raise SaladError("une image OCI SaladCloud est obligatoire via ComputeRequest.image ou SALAD_CONTAINER_IMAGE")

        group_name = self.group_name_for(idempotency_key)
        existing = self._get_group_or_none(group_name)
        if existing is not None:
            resources = ((existing.get("container") or {}).get("resources") or {}) if isinstance(existing.get("container"), dict) else {}
            gpu_classes = resources.get("gpu_classes") or []
            existing_image = str((existing.get("container") or {}).get("image") or "") if isinstance(existing.get("container"), dict) else ""
            existing_priority = str(existing.get("priority") or (existing.get("container") or {}).get("priority") or "") if isinstance(existing.get("container"), dict) else str(existing.get("priority") or "")
            if offer.offering_id not in gpu_classes or existing_image != image or existing_priority != offer.tier:
                raise SaladError("collision d'idempotence SaladCloud avec une configuration différente")
            return self._operation_from_group(existing, fallback_name=group_name)

        container_env = dict(request.env)
        container_env.setdefault("OCTOPUS_COMPUTE_IDEMPOTENCY_KEY", idempotency_key)
        container_env.setdefault("OCTOPUS_AUTO_TERMINATE_HOURS", str(request.auto_terminate_hours))
        payload: dict[str, Any] = {
            "name": group_name,
            "display_name": group_name,
            "replicas": 1,
            "autostart_policy": True,
            "restart_policy": "never",
            "container": {
                "image": image,
                "image_caching": True,
                "priority": offer.tier,
                "environment_variables": container_env,
                "resources": {
                    "cpu": self.config.cpu,
                    "memory": self.config.memory_mb,
                    "gpu_classes": [offer.offering_id],
                    "storage_amount": self.config.storage_bytes,
                    "shm_size": self.config.shm_size_mb,
                },
            },
        }
        countries = self._country_codes(request)
        if countries:
            payload["country_codes"] = countries
        org = urllib.parse.quote(str(self.config.organization), safe="")
        project = urllib.parse.quote(str(self.config.project), safe="")
        submit_error = None
        data: dict[str, Any] = {}
        try:
            data, _, _ = self._request("POST", f"/organizations/{org}/projects/{project}/containers", payload=payload)
        except SaladError as exc:
            # Un timeout/erreur réseau après POST peut avoir créé le groupe. Le nom est déterministe :
            # lire avant toute décision, et ne jamais resoumettre aveuglément.
            submit_error = exc

        # Salad recommande de vérifier toute écriture par une lecture. On ne resoumet jamais
        # la création si cette lecture échoue : une requête facturable ambiguë reste ambiguë.
        try:
            verified = self.get_group(group_name)
        except SaladError as exc:
            detail = submit_error or exc
            return ComputeOperation(
                provider=self.provider,
                operation_id=str(data.get("id") or group_name),
                state="submitted_unverified",
                resource_id=group_name,
                error=str(detail),
                raw={"create_response": data, "requires_external_termination": True, "submit_error": str(submit_error) if submit_error else None},
            )
        operation = self._operation_from_group(verified, fallback_name=group_name)
        return ComputeOperation(
            provider=operation.provider,
            operation_id=operation.operation_id,
            state=operation.state,
            resource_id=operation.resource_id,
            error=operation.error,
            raw={**operation.raw, "requires_external_termination": True},
        )

    def stop(self, group_name: str) -> ComputeOperation:
        path = self._group_path(group_name)
        self._request("POST", path + "/stop")
        try:
            group = self.get_group(group_name)
        except SaladError as exc:
            return ComputeOperation(self.provider, group_name, "stop_submitted_unverified", group_name, str(exc))
        return self._operation_from_group(group, fallback_name=group_name)

    def delete(self, group_name: str) -> ComputeOperation:
        path = self._group_path(group_name)
        data, _, _ = self._request("DELETE", path)
        return ComputeOperation(self.provider, str(data.get("id") or group_name), "deleting", group_name, raw=data)
