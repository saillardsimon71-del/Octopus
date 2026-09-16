from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class AppSettings:
    """Shared runtime settings for all businesses."""

    business_name: str = "short_video"
    llm_default_model: str = "deepseek-chat"
    llm_fallback_model: str = "deepseek-reasoner"
    browser_auto_start: bool = False
    browser_headless: bool = True
    max_agents_per_business: int = 8
    memory_backend: str = "sqlite"
    use_scheduler: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    def merge(self, **kwargs: Any) -> "AppSettings":
        for key, value in kwargs.items():
            setattr(self, key, value)
        return self
