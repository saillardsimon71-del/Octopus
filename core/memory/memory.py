from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class MemoryRecord:
    key: str
    value: Any
    category: str = "general"


class BusinessMemory:
    def __init__(self, business_name: str):
        self.business_name = business_name
        self.records: Dict[str, MemoryRecord] = {}

    def set(self, key: str, value: Any, category: str = "general") -> MemoryRecord:
        record = MemoryRecord(key=key, value=value, category=category)
        self.records[key] = record
        return record

    def get(self, key: str) -> Any:
        record = self.records.get(key)
        return None if record is None else record.value


class AgentMemory:
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.records: Dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        self.records[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.records.get(key, default)
