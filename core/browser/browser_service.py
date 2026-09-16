from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BrowserContext:
    name: str
    business_id: str
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BrowserService:
    """Shared browser resource. One service, multiple business contexts."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.contexts: Dict[str, BrowserContext] = {}

    def open_context(self, business_id: str, name: str = "default") -> BrowserContext:
        key = f"{business_id}:{name}"
        context = BrowserContext(name=name, business_id=business_id, session_id=key)
        self.contexts[key] = context
        return context

    def close_context(self, business_id: str, name: str = "default") -> None:
        key = f"{business_id}:{name}"
        self.contexts.pop(key, None)

    def list_contexts(self) -> List[BrowserContext]:
        return list(self.contexts.values())
