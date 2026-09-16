from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class TaskType(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    VISION = "vision"


@dataclass
class LLMRoute:
    task_type: TaskType
    model: str
    reason: str


class LLMRouter:
    """Selects a model without hard-coding a provider."""

    def __init__(self, default_model: str = "deepseek-chat", fallback_model: str = "deepseek-reasoner"):
        self.default_model = default_model
        self.fallback_model = fallback_model

    def choose(self, task_type: TaskType, **context: Any) -> LLMRoute:
        if task_type == TaskType.SIMPLE:
            return LLMRoute(task_type, context.get("cheap_model", self.default_model), "low-cost path")
        if task_type == TaskType.VISION:
            return LLMRoute(task_type, context.get("vision_model", self.default_model), "image-capable model")
        if task_type == TaskType.COMPLEX:
            return LLMRoute(task_type, context.get("powerful_model", self.fallback_model), "high reasoning path")
        return LLMRoute(task_type, context.get("medium_model", self.default_model), "balanced path")

    def generate(self, prompt: str, task_type: TaskType, **context: Any) -> Dict[str, Any]:
        route = self.choose(task_type, **context)
        return {
            "model": route.model,
            "prompt": prompt,
            "task_type": route.task_type.value,
            "reason": route.reason,
        }
