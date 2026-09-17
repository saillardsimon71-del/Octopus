"""Façade de production utilisée par FORGE/cycle.

Le mode local délègue au pipeline historique. Le mode cloud convertit le job vers
VideoJob, attend le résultat distant puis rematérialise uniquement les petits frames
nécessaires au QC vision local.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from .contract import VideoJob, VideoResult
from .renderers import VideoRenderer


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
        metrics = dict(result.qc)
        metrics["cloud_video_url"] = result.video_url
        metrics["cloud_job_id"] = result.job_id
        self._materialize_frames(offer_id, metrics, result)
        self._write_control_manifest(offer_id, result)
        return metrics

    @staticmethod
    def _materialize_frames(offer_id: str, metrics: dict[str, Any], result: VideoResult) -> None:
        frames = metrics.get("frames")
        if not isinstance(frames, list) or not frames:
            return
        by_name = {artifact.name: artifact.url for artifact in result.artifacts if artifact.kind == "image"}
        allowed_urls = {artifact.url for artifact in result.artifacts if artifact.kind == "image"}
        root = Path(os.environ.get("PODALUX_ROOT", Path.cwd()))
        target_dir = root / "out" / offer_id / "frames"
        target_dir.mkdir(parents=True, exist_ok=True)
        materialized: list[str] = []
        for item in frames:
            raw = str(item)
            url = by_name.get(Path(raw).name)
            if raw.startswith(("https://", "http://")):
                if raw not in allowed_urls:
                    raise VideoServiceError(f"URL frame QC non autorisée par le manifest: {raw}")
                url = raw
            if not url:
                raise VideoServiceError(f"frame QC introuvable dans les artefacts cloud: {raw}")
            name = Path(raw).name or "frame.jpg"
            target = target_dir / name
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    data = response.read(8 * 1024 * 1024 + 1)
            except Exception as exc:
                raise VideoServiceError(f"téléchargement frame QC échoué: {name}: {exc}") from exc
            if len(data) > 8 * 1024 * 1024:
                raise VideoServiceError(f"frame QC trop volumineuse: {name}")
            target.write_bytes(data)
            materialized.append(str(target))
        metrics["frames"] = materialized

    @staticmethod
    def _write_control_manifest(offer_id: str, result: VideoResult) -> None:
        root = Path(os.environ.get("PODALUX_ROOT", Path.cwd()))
        target = root / "out" / offer_id / "cloud_result.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1", "job_id": result.job_id, "offer_id": offer_id,
            "status": result.status.value, "video_url": result.video_url,
            "qc": dict(result.qc), "artifacts": [artifact.to_dict() for artifact in result.artifacts],
        }
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
