import sqlite3
from pathlib import Path
from typing import Any, Dict, List


class SQLiteStore:
    def __init__(self, database_path: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT,
                category TEXT DEFAULT 'general'
            )
            """
        )
        self.connection.commit()

    def set(self, key: str, value: Any, category: str = "general") -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO memory (key, value, category) VALUES (?, ?, ?)",
            (key, str(value), category),
        )
        self.connection.commit()

    def get(self, key: str) -> Any:
        row = self.connection.execute(
            "SELECT value FROM memory WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else row["value"]

    def all(self) -> List[Dict[str, Any]]:
        rows = self.connection.execute("SELECT key, value, category FROM memory").fetchall()
        return [dict(r) for r in rows]
