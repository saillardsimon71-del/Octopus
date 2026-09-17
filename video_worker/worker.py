"""Worker vidéo cloud : contrat d'exécution, pas de provider hard-codé.

Ce worker est volontairement séparé du runtime agents. Il reçoit un VideoJob,
exécute la chaîne lourde dans son propre environnement et doit publier les artefacts
vers un object storage avant de répondre COMPLETED.

Le pipeline réel FORGE/Remotion n'est pas copié ici tant que son exécution Linux,
les dépendances Chromium et le backend TTS n'ont pas été validés dans un conteneur.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from typing import Any, Mapping

from octopus.video.contract import VideoJob, VideoStatus


class WorkerError(RuntimeError):
    pass


def load_job(payload: Mapping[str, Any]) -> VideoJob:
    return VideoJob.from_dict(payload)


def process(payload: Mapping[str, Any]) -> dict[str, Any]:
    job = load_job(payload)
    # Fail closed : tant que le pipeline réel n'est pas installé dans l'image worker,
    # ne jamais annoncer COMPLETED. Cela évite une intégration factice.
    raise WorkerError(
        f"worker reçu job {job.job_id}, mais pipeline FORGE cloud non installé. "
        "Construire/valider l'image Remotion+Chromium+FFmpeg+TTS avant activation."
    )


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        result = process(payload)
    except (json.JSONDecodeError, WorkerError, ValueError) as exc:
        print(json.dumps({"status": VideoStatus.FAILED.value, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
