from dataclasses import dataclass, field
from typing import Dict


@dataclass
class CostEntry:
    provider: str
    model: str
    tokens: int = 0
    cost_usd: float = 0.0
    metadata: Dict[str, str] = field(default_factory=dict)


class CostTracker:
    """Tracks API and compute spend across all business workloads."""

    def __init__(self):
        self.entries: list[CostEntry] = []

    def add(self, provider: str, model: str, tokens: int, cost_usd: float, **metadata: str) -> CostEntry:
        entry = CostEntry(provider=provider, model=model, tokens=tokens, cost_usd=cost_usd, metadata=metadata)
        self.entries.append(entry)
        return entry

    def total_tokens(self) -> int:
        return sum(entry.tokens for entry in self.entries)

    def total_cost_usd(self) -> float:
        return sum(entry.cost_usd for entry in self.entries)
