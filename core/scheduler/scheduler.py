from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ScheduledTask:
    name: str
    callback: Callable[[], Any]
    when: Optional[datetime] = None
    interval_seconds: Optional[int] = None
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class Scheduler:
    """Tiny scheduler for immediate, delayed and periodic work."""

    def __init__(self):
        self.tasks: List[ScheduledTask] = []

    def schedule(self, name: str, callback: Callable[[], Any], when: Optional[datetime] = None, interval_seconds: Optional[int] = None, **metadata: Any) -> ScheduledTask:
        task = ScheduledTask(name=name, callback=callback, when=when, interval_seconds=interval_seconds, metadata=metadata)
        self.tasks.append(task)
        return task

    def run_due(self) -> List[str]:
        executed: List[str] = []
        for task in self.tasks:
            if task.enabled and task.when is None:
                task.callback()
                executed.append(task.name)
        return executed
