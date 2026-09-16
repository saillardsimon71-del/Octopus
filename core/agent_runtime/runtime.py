from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentConfig:
    name: str
    mission: str
    objectives: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    memory_scope: str = "business"
    budget_limit: Optional[float] = None


@dataclass
class AgentState:
    status: str = "idle"
    last_task_id: Optional[str] = None
    history: List[str] = field(default_factory=list)
    notes: Dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """Generic runtime for agent instances shared by every company."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.state = AgentState()
        self.tasks: List[str] = []

    def assign_task(self, task_id: str) -> None:
        self.state.last_task_id = task_id
        self.tasks.append(task_id)
        self.state.status = "busy"

    def mark_idle(self) -> None:
        self.state.status = "idle"

    def log(self, message: str) -> None:
        self.state.history.append(message)
