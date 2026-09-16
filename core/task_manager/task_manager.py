from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


@dataclass
class Task:
    task_id: str
    name: str
    status: str = "pending"
    payload: Dict[str, Any] = field(default_factory=dict)
    scheduled_for: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    results: List[Any] = field(default_factory=list)


class TaskManager:
    """Simple task manager with minimal lifecycle support."""

    def __init__(self):
        self._tasks: Dict[str, Task] = {}

    def create(self, task_id: str, name: str, payload: Optional[Dict[str, Any]] = None, scheduled_for: Optional[datetime] = None) -> Task:
        task = Task(task_id=task_id, name=name, payload=payload or {}, scheduled_for=scheduled_for)
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def start(self, task_id: str) -> Task:
        task = self._tasks[task_id]
        task.status = "running"
        return task

    def complete(self, task_id: str, result: Any = None) -> Task:
        task = self._tasks[task_id]
        task.status = "completed"
        if result is not None:
            task.results.append(result)
        return task

    def fail(self, task_id: str, error: str) -> Task:
        task = self._tasks[task_id]
        task.status = "failed"
        task.payload["error"] = error
        return task

    def list(self) -> List[Task]:
        return list(self._tasks.values())

    @staticmethod
    def in_seconds(seconds: int) -> timedelta:
        return timedelta(seconds=seconds)
