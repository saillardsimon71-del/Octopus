"""État local minimal d'un rendu cloud.

Ce fichier ne remplace pas la file SQLite d'OCTOPUS : il conserve uniquement l'identité
du job fournisseur afin qu'un redémarrage puisse reprendre un job déjà soumis sans
créer automatiquement une seconde exécution payante.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RenderStateError(RuntimeError):
    pass


class AmbiguousSubmissionError(RenderStateError):
    """Une soumission pourrait avoir été acceptée mais son identifiant n'a pas été reçu."""


@dataclass(frozen=True)
class RenderState:
    job_id: str
    provider: str
    state: str
    remote_id: str | None = None
    attempt: int = 1
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "job_id": self.job_id,
            "provider": self.provider,
            "state": self.state,
            "remote_id": self.remote_id,
            "attempt": self.attempt,
            "updated_at": self.updated_at,
        }


class RenderStateStore:
    def __init__(self, root: Path | None = None):
        env_root = os.environ.get("PODALUX_VIDEO_STATE_DIR", "").strip()
        self.root = Path(env_root) if env_root else (root or Path.cwd() / "out" / ".cloud_video_state")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in job_id)
        if not safe:
            raise RenderStateError("job_id vide")
        return self.root / f"{safe}.json"

    def load(self, job_id: str) -> RenderState | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RenderStateError(f"état de rendu illisible: {path}") from exc
        if not isinstance(data, dict) or str(data.get("job_id")) != job_id:
            raise RenderStateError(f"état de rendu incohérent: {path}")
        attempt = int(data.get("attempt") or 1)
        if attempt < 1:
            raise RenderStateError(f"tentative de rendu invalide: {path}")
        return RenderState(
            job_id=job_id,
            provider=str(data.get("provider") or "unknown"),
            state=str(data.get("state") or "UNKNOWN"),
            remote_id=str(data["remote_id"]) if data.get("remote_id") else None,
            attempt=attempt,
            updated_at=float(data.get("updated_at") or 0),
        )

    def save(self, state: RenderState) -> None:
        path = self._path(state.job_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def mark_submitting(self, job_id: str, provider: str, *, attempt: int = 1) -> RenderState:
        state = RenderState(job_id, provider, "SUBMITTING", None, max(1, attempt), time.time())
        self.save(state)
        return state

    def mark_submitted(self, job_id: str, provider: str, remote_id: str, status: str, *, attempt: int = 1) -> RenderState:
        state = RenderState(job_id, provider, status, remote_id, max(1, attempt), time.time())
        self.save(state)
        return state

    def mark_status(self, state: RenderState, status: str) -> RenderState:
        updated = RenderState(state.job_id, state.provider, status, state.remote_id, state.attempt, time.time())
        self.save(updated)
        return updated

    def require_resume_safe(self, state: RenderState, *, ambiguous_after_s: float = 300) -> None:
        if state.state != "SUBMITTING":
            return
        age = max(0.0, time.time() - state.updated_at)
        suffix = " (ancien état)" if age >= ambiguous_after_s else ""
        raise AmbiguousSubmissionError(
            f"soumission cloud ambiguë pour {state.job_id}{suffix} (sans remote_id depuis {age:.0f}s); "
            "ne pas resoumettre automatiquement, vérifier le fournisseur puis supprimer/"
            "réconcilier l'état local"
        )

    def clear(self, job_id: str) -> None:
        self._path(job_id).unlink(missing_ok=True)
