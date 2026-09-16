from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class ToolSpec:
    name: str
    description: str
    function: Callable[..., Any]
    tags: List[str] = field(default_factory=list)


class ToolRegistry:
    """Registry for reusable business-independent tools."""

    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def list(self) -> List[str]:
        return sorted(self._tools.keys())
