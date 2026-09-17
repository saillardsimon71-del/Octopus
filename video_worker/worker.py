"""Worker vidéo cloud : point d'entrée fournisseur-agnostique.

Le worker reçoit un VideoJob, exécute le FORGE existant dans un workspace isolé et
publie le résultat vers le backend de stockage configuré. Une exception remonte au
provider afin qu'un échec soit réellement marqué comme échec, jamais comme succès.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Mapping

from octopus.video.contract import VideoJob
from octopus.video.storage import ArtifactStoreError, store_from_env

from .executor import ExecutorError, ForgeExecutor


class WorkerError(RuntimeError):
    pass


def load_job(payload: Mapping[str, Any]) -> VideoJob:
    return VideoJob.from_dict(payload)


def process(payload: Mapping[str, Any]) -> dict[str, Any]:
    job = load_job(payload)
    try:
        store = store_from_env()
        result = ForgeExecutor(store).render(job)
    except (ArtifactStoreError, ExecutorError, OSError, ValueError) as exc:
        raise WorkerError(str(exc)) from exc
    return result.to_dict()


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, Mapping):
            raise WorkerError("payload racine doit être un objet JSON")
        result = process(payload)
    except (json.JSONDecodeError, WorkerError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
