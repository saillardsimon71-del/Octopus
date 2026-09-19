"""Sélection multi-fournisseurs de calcul à coût minimal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .compute import ComputeOffer, ComputeRequest


class ComputeProvider(Protocol):
    provider: str

    def quote(self, request: ComputeRequest) -> ComputeOffer: ...


class ComputeBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComputeSelection:
    offer: ComputeOffer
    estimated_runtime_s: float | None
    estimated_cost: float | None
    provider_errors: dict[str, str]


class ComputeBroker:
    def __init__(self, providers: Sequence[ComputeProvider]):
        if not providers:
            raise ValueError("au moins un fournisseur compute est requis")
        self.providers = tuple(providers)

    def select(self, request: ComputeRequest, *, runtime_seconds: Mapping[str, float] | None = None) -> ComputeSelection:
        offers: list[tuple[ComputeOffer, float | None, float | None]] = []
        errors: dict[str, str] = {}
        for provider in self.providers:
            try:
                offer = provider.quote(request)
            except Exception as exc:
                errors[getattr(provider, "provider", type(provider).__name__)] = f"{type(exc).__name__}: {exc}"
                continue
            runtime = None
            estimated_cost = None
            if runtime_seconds is not None:
                runtime = runtime_seconds.get(f"{offer.provider}:{offer.accelerator}")
                if runtime is None:
                    runtime = runtime_seconds.get(offer.accelerator)
                if runtime is not None:
                    if runtime <= 0:
                        raise ValueError("les durées de benchmark doivent être positives")
                    estimated_cost = offer.price_per_hour * runtime / 3600.0
            offers.append((offer, runtime, estimated_cost))
        if not offers:
            detail = "; ".join(f"{provider}: {error}" for provider, error in sorted(errors.items()))
            raise ComputeBrokerError(f"aucune offre compute disponible ({detail})")

        if runtime_seconds is not None:
            benchmarked = [row for row in offers if row[2] is not None]
            if benchmarked:
                offers = benchmarked
                offers.sort(key=lambda row: (float(row[2]), row[0].price_per_hour, row[0].provider))
            else:
                offers.sort(key=lambda row: (row[0].price_per_hour, row[0].provider))
        else:
            offers.sort(key=lambda row: (row[0].price_per_hour, row[0].provider))
        offer, runtime, estimated_cost = offers[0]
        return ComputeSelection(offer, runtime, estimated_cost, errors)
