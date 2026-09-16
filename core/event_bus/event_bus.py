from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class Event:
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "system"


class EventBus:
    """Minimal async-friendly publisher/subscriber bus."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = {}

    def subscribe(self, event_name: str, callback: Callable[[Event], None]) -> None:
        self._subscribers.setdefault(event_name, []).append(callback)

    def publish(self, event_name: str, payload: Dict[str, Any] | None = None, source: str = "system") -> Event:
        event = Event(name=event_name, payload=payload or {}, source=source)
        for callback in list(self._subscribers.get(event_name, [])):
            callback(event)
        return event

    def emit(self, event_name: str, payload: Dict[str, Any] | None = None, source: str = "system") -> Event:
        return self.publish(event_name, payload=payload, source=source)
