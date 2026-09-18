"""Contrats fournisseurs pour louer un calcul accéléré borné."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ComputeRequest:
    accelerator: str | None = None
    min_vram_gb: int = 0
    gpu_count: int = 1
    region: str | None = None
    tier: str = "on_demand"
    max_price_per_hour: float | None = None
    auto_terminate_hours: int | None = None
    ssh_key_ids: tuple[str, ...] = ()
    environment: str | None = None
    image: str | None = None
    ports: tuple[dict[str, Any], ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.gpu_count < 1:
            raise ValueError("gpu_count doit être positif")
        if self.min_vram_gb < 0:
            raise ValueError("min_vram_gb ne peut pas être négatif")
        if self.max_price_per_hour is None or self.max_price_per_hour <= 0:
            raise ValueError("max_price_per_hour est obligatoire et doit être positif")
        if self.auto_terminate_hours is None or not 1 <= self.auto_terminate_hours <= 720:
            raise ValueError("auto_terminate_hours est obligatoire et doit être dans [1, 720]")


@dataclass(frozen=True)
class ComputeOffer:
    provider: str
    offering_id: str
    accelerator: str
    gpu_count: int
    vram_gb: int
    region: str
    tier: str
    price_per_hour: float
    available: int
    instant_boot: bool
    capacity_class: str
    observed_at: float = field(default_factory=time.time)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComputeOperation:
    provider: str
    operation_id: str
    state: str
    resource_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComputeInstance:
    provider: str
    instance_id: str
    state: str
    accelerator: str
    gpu_count: int
    price_per_hour: float
    connection: dict[str, Any] = field(default_factory=dict)
    ready_at: str | None = None
    auto_terminate_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

