from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HumanAction:
    action_type: str
    service: str
    reason: str
    prepared_context: Dict[str, Any] = field(default_factory=dict)
    required_step: Optional[str] = None


class HumanInterface:
    def __init__(self):
        self.pending: list[HumanAction] = []

    def request(self, action_type: str, service: str, reason: str, **kwargs: Any) -> HumanAction:
        action = HumanAction(action_type=action_type, service=service, reason=reason, prepared_context=kwargs)
        self.pending.append(action)
        return action

    def clear(self) -> None:
        self.pending.clear()
