"""Façade de production utilisée par FORGE.

Le mode local délègue au pipeline FORGE historique. Le mode cloud convertit le job
vers VideoJob et passe par le renderer fournisseur. Cette couche permet de migrer
sans dupliquer le moteur Remotion.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Mapping

from .contract import VideoJob, VideoResult
from .renderers import CloudVideoRenderer, LocalVideoRenderer, VideoRenderer


class VideoServiceError(RuntimeError):
    pass


def stable_job_id(job: Mapping[str, Any]) -> str:
    """ID déterministe : même script + paramètres => même identité de rendu."""
    canonical = json.dumps(job, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"video-{digest}"


class VideoService:
    def __init__(self, *, cloud_renderer: VideoRenderer | None = None,
                 local_renderer: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
                 mode: str = "local"):
        self.mode = mode.strip().lower()
        self.cloud_renderer = cloud_renderer
        self.local_renderer = local_renderer

    def render(self, offer_id: str, job: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.mode == "local":
            if not self.local_renderer:
                raise VideoServiceError("pipeline local non injecté")
            return self.local_renderer(offer_id, job)
        if self.mode != "cloud":
            raise VideoServiceError(f"mode vidéo inconnu: {self.mode!r}")
        if self.cloud_renderer is None:
            raise VideoServiceError("renderer cloud non configuré")
        video_job = VideoJob.from_legacy_job(job, job_id=stable_job_id(job))
        result = self.cloud_renderer.render(video_job)
        if result.qc:
            return dict(result.qc)
        return result.to_dict()
