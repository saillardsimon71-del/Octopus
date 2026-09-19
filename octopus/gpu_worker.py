"""Remote media/GPU seam : contrat adaptateur fin, réutilisable et résilient.

Permet à OCTOPUS de pointer plus tard vers un worker Wan2GP distant sans
modifier le moteur. Fournit un adaptateur fake déterministe (aucun réseau,
aucune installation, aucun appel live).

Fonctionnalités :
- capability discovery
- soumission idempotente (par job_id)
- progression de statut (submitted → running → completed/failed/cancelled/timeout)
- artefacts
- retry après échec
- timeout
- annulation coopérative
- reprise après redémarrage (état persisté sur disque)
- snapshot opérateur avec historique

Réutilise : octopus.tasks pour les événements d'audit, octopus.journal pour
l'infrastructure SQLite existante (pas de nouvelle table : l'état GPU est
confiné à un dossier JSON par job, comme `octopus.video.state`).
"""
from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import tasks


class GpuWorkerError(RuntimeError):
    """Erreur du seam GPU worker."""


# --- Capability / adapter contract -----------------------------------------------------------

class GpuWorkerAdapter(ABC):
    """Contrat minimal pour un worker GPU distant (ex. Wan2GP)."""

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Renvoie les capacités du worker : name, capabilities list, max_duration_s, max_resolution."""
        raise NotImplementedError

    @abstractmethod
    def submit(self, job_id: str, prompt: str, task_type: str, **params) -> str:
        """Soumet un job. Renvoie un remote_id. Doit être idempotent côté worker."""
        raise NotImplementedError

    @abstractmethod
    def status(self, job_id: str, remote_id: str) -> dict[str, Any]:
        """Renvoie {status, progress, artifacts, error}."""
        raise NotImplementedError

    @abstractmethod
    def cancel(self, job_id: str, remote_id: str) -> None:
        """Annule un job. Idempotent."""
        raise NotImplementedError


# --- Deterministic fake adapter ----------------------------------------------------------------

@dataclass
class _FakeJob:
    job_id: str
    remote_id: str
    prompt: str
    task_type: str
    status: str = "submitted"  # submitted, running, completed, failed, cancelled, timeout
    progress: int = 0
    total_steps: int = 1
    artifacts: list[dict] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    timeout_s: float | None = None
    cancel_requested: bool = False
    fail_first_n: int = 0
    attempts: int = 0


class FakeGpuWorker(GpuWorkerAdapter):
    """Adaptateur fake déterministe. Aucun réseau, aucun GPU réel.

    Simulation : chaque appel à `status` fait avancer le job d'un step.
    `fail_first_n` : les N premières tentatives échouent (pour tester retry).
    """

    def __init__(self, name: str, capabilities: list[str], max_duration_s: float,
                 max_resolution: str, steps: int = 3, fail_first_n: int = 0):
        self._name = name
        self._capabilities = capabilities
        self._max_duration_s = max_duration_s
        self._max_resolution = max_resolution
        self._steps = steps
        self._fail_first_n = fail_first_n
        self._jobs: dict[str, _FakeJob] = {}

    def capabilities(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "capabilities": list(self._capabilities),
            "max_duration_s": self._max_duration_s,
            "max_resolution": self._max_resolution,
        }

    def submit(self, job_id: str, prompt: str, task_type: str, **params) -> str:
        if task_type not in self._capabilities:
            raise GpuWorkerError(f"capability {task_type!r} non supportée (disponibles : {self._capabilities})")
        remote_id = f"remote-{hashlib.sha256(f'{job_id}:{prompt}'.encode()).hexdigest()[:12]}"
        if job_id in self._jobs:
            existing = self._jobs[job_id]
            if existing.status == "failed":
                # Retry: reset job for a new attempt
                existing.status = "submitted"
                existing.progress = 0
                existing.error = None
                existing.artifacts = []
                existing.timeout_s = params.get("timeout_s")
            return existing.remote_id  # idempotent
        self._jobs[job_id] = _FakeJob(
            job_id=job_id, remote_id=remote_id, prompt=prompt, task_type=task_type,
            total_steps=self._steps, fail_first_n=self._fail_first_n,
            timeout_s=params.get("timeout_s"),
        )
        return remote_id

    def status(self, job_id: str, remote_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise GpuWorkerError(f"job {job_id} inconnu")
        if job.status == "submitted":
            job.status = "running"
        if job.status == "running" and not job.cancel_requested:
            job.progress += 1
            if job.fail_first_n > 0 and job.attempts < job.fail_first_n and job.progress >= job.total_steps:
                job.status = "failed"
                job.error = "simulated transient failure"
                job.attempts += 1
            elif job.progress >= job.total_steps:
                job.status = "completed"
                job.artifacts = [
                    {"name": "output.mp4", "url": f"sim://gpu/{job_id}/output.mp4",
                     "kind": "video", "sha256": hashlib.sha256(f'{job_id}:final'.encode()).hexdigest()},
                ]
        elif job.status == "running" and job.cancel_requested:
            job.status = "cancelled"
        # Timeout check
        if job.status == "running" and job.timeout_s is not None:
            if time.time() - job.created_at > job.timeout_s:
                job.status = "timeout"
                job.error = "timeout expired"
        return {"status": job.status, "progress": job.progress, "total_steps": job.total_steps,
                "artifacts": list(job.artifacts), "error": job.error}

    def cancel(self, job_id: str, remote_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            raise GpuWorkerError(f"job {job_id} inconnu")
        if job.status in ("completed", "cancelled", "timeout"):
            return  # idempotent
        job.cancel_requested = True
        if job.status == "submitted":
            job.status = "cancelled"


# --- Persistent job store (resume after restart) ------------------------------------------------

class GpuJobStore:
    """État persistant par job sur disque (JSON). Survit à un redémarrage."""

    def __init__(self, root: Path | None = None):
        self.root = root or Path.cwd() / "gpu_jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in job_id)
        if not safe:
            raise GpuWorkerError("job_id vide")
        return self.root / f"{safe}.json"

    def load(self, job_id: str) -> dict | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, job_id: str, data: dict) -> None:
        path = self._path(job_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def append_history(self, job_id: str, entry: dict) -> None:
        data = self.load(job_id) or {"job_id": job_id, "history": []}
        data.setdefault("history", []).append(entry)
        self.save(job_id, data)

    def delete(self, job_id: str) -> None:
        self._path(job_id).unlink(missing_ok=True)


# --- High-level operations -----------------------------------------------------------------------

def submit_job(store: GpuJobStore, adapter: GpuWorkerAdapter, business: str,
               *, job_id: str, prompt: str, task_type: str, timeout_s: float | None = None,
               retry: bool = False) -> dict:
    """Soumet un job GPU. Idempotent par job_id. Peut réessayer après échec si retry=True."""
    # Idempotency check from persisted state
    existing = store.load(job_id)
    if existing and existing.get("remote_id"):
        current_status = existing.get("status", "unknown")
        if retry and current_status in ("failed",):
            pass  # allow retry
        else:
            return {"job_id": job_id, "remote_id": existing["remote_id"],
                    "status": existing["status"], "duplicate": True}

    try:
        remote_id = adapter.submit(job_id, prompt, task_type, timeout_s=timeout_s)
    except GpuWorkerError as exc:
        if "capability" in str(exc):
            raise
        raise GpuWorkerError(f"soumission impossible : {exc}") from exc

    now = time.time()
    store.save(job_id, {
        "job_id": job_id,
        "business": business,
        "remote_id": remote_id,
        "prompt": prompt,
        "task_type": task_type,
        "status": "submitted",
        "created_at": existing["created_at"] if existing and "created_at" in existing else now,
        "timeout_s": timeout_s,
        "history": (existing["history"] if existing else []) + [
            {"ts": now, "event": "submitted", "remote_id": remote_id, "retry": retry},
        ],
    })
    tasks.emit(business, None, "gpu_job.submitted", {"job_id": job_id, "remote_id": remote_id, "retry": retry})
    return {"job_id": job_id, "remote_id": remote_id, "status": "submitted", "duplicate": False}


def poll_status(store: GpuJobStore, adapter: GpuWorkerAdapter, business: str,
                *, job_id: str) -> dict:
    """Interroge le statut du job et met à jour l'état persistant."""
    data = store.load(job_id)
    if data is None:
        raise GpuWorkerError(f"job {job_id} inconnu dans le store")
    remote_id = data["remote_id"]
    try:
        result = adapter.status(job_id, remote_id)
    except GpuWorkerError:
        # After restart the adapter may not have the job in memory (ephemeral worker).
        # Report last persisted state — resume semantics.
        last = data.get("status", "unknown")
        return {"job_id": job_id, "status": last, "progress": data.get("progress", 0),
                "total_steps": data.get("total_steps", 1), "artifacts": data.get("artifacts", []),
                "error": data.get("error")}
    status = result["status"]

    # Persist update
    data["status"] = status
    data["progress"] = result.get("progress", 0)
    data["total_steps"] = result.get("total_steps", 1)
    data["artifacts"] = result.get("artifacts", [])
    data["error"] = result.get("error")
    data["updated_at"] = time.time()
    store.save(job_id, data)

    # Emit event for state transitions
    prev = data.get("prev_status") or "submitted"
    if status != prev:
        data["prev_status"] = status
        store.save(job_id, data)
        store.append_history(job_id, {"ts": time.time(), "event": status,
                                      "progress": result.get("progress", 0)})
        tasks.emit(business, None, f"gpu_job.{status}", {"job_id": job_id, "progress": result.get("progress")})

    return {"job_id": job_id, "status": status, "progress": result.get("progress", 0),
            "total_steps": result.get("total_steps", 1), "artifacts": result.get("artifacts", []),
            "error": result.get("error")}


def cancel_job(store: GpuJobStore, adapter: GpuWorkerAdapter, business: str,
               *, job_id: str) -> dict:
    """Annule un job. Idempotent."""
    data = store.load(job_id)
    if data is None:
        raise GpuWorkerError(f"job {job_id} inconnu dans le store")
    if data.get("status") in ("completed", "cancelled", "timeout"):
        return {"job_id": job_id, "status": data["status"], "duplicate": True}
    adapter.cancel(job_id, data["remote_id"])
    data["status"] = "cancelled"
    data["updated_at"] = time.time()
    store.save(job_id, data)
    store.append_history(job_id, {"ts": time.time(), "event": "cancelled"})
    tasks.emit(business, None, "gpu_job.cancelled", {"job_id": job_id})
    return {"job_id": job_id, "status": "cancelled", "duplicate": False}


def job_snapshot(store: GpuJobStore, business: str, *, job_id: str) -> dict:
    """État visible par l'opérateur : job, statut, artefacts, historique."""
    data = store.load(job_id)
    if data is None:
        raise GpuWorkerError(f"job {job_id} inconnu dans le store")
    return {
        "job_id": job_id,
        "business": data.get("business", business),
        "status": data.get("status", "unknown"),
        "progress": data.get("progress", 0),
        "total_steps": data.get("total_steps", 1),
        "artifacts": data.get("artifacts", []),
        "error": data.get("error"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "remote_id": data.get("remote_id"),
        "history": data.get("history", []),
    }