from dataclasses import dataclass, field
from typing import Dict, List

from core.agent_runtime import AgentConfig, AgentRuntime
from core.browser import BrowserService
from core.database import SQLiteStore
from core.event_bus import EventBus
from core.llm_router import LLMRouter, TaskType
from core.memory import BusinessMemory
from core.scheduler import Scheduler
from core.task_manager import TaskManager


@dataclass
class ShortVideoBusiness:
    name: str = "short_video"
    goals: List[str] = field(default_factory=lambda: [
        "find opportunities",
        "produce short-form ads",
        "publish and measure",
    ])
    skills: List[str] = field(default_factory=lambda: [
        "research",
        "copywriting",
        "video planning",
        "voice selection",
        "qc",
    ])
    config: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tasks = TaskManager()
        self.events = EventBus()
        self.scheduler = Scheduler()
        self.memory = BusinessMemory(self.name)
        self.db = SQLiteStore("businesses/short_video/data/business.db")
        self.browser = BrowserService(headless=True)
        self.llm = LLMRouter()

    def create_agent(self, name: str, mission: str, skills: List[str] | None = None) -> AgentRuntime:
        config = AgentConfig(
            name=name,
            mission=mission,
            objectives=["deliver business value"],
            skills=skills or [],
            allowed_tools=["browser", "llm", "memory", "scheduler"],
        )
        return AgentRuntime(config)

    def route_task(self, prompt: str, task_type: TaskType = TaskType.MEDIUM) -> Dict[str, object]:
        return self.llm.generate(prompt, task_type, business=self.name)
