"""Façade de production utilisée par FORGE/cycle.

Le mode local reste inchangé. Le mode cloud convertit le job vers le contrat vidéo,
attend le résultat distant puis rematérialise les artefacts nécessaires au pipeline
historique lorsque le worker les fournit.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from .contract import Artifact, VideoJob, VideoResult
from .renderers import VideoRenderer


class VideoServiceError(RuntimeError):
    """Erreur de façade vidéo ou d'artefact distant."""


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
        declared_offer = str(job.get("offer_id") or "")
        if declared_offer and declared_offer != offer_id:
            raise VideoServiceError(
                f"incohérence offer_id: argument={offer_id!r}, job={declared_offer!r}"
            )
        if not declared_offer:
            raise VideoServiceError("job cloud sans offer_id")

        video_job = VideoJob.from_legacy_job(job, job_id=stable_job_id(job))
        if video_job.offer_id != offer_id:
            raise VideoServiceError("offer_id normalisé différent de l'offre demandée")
        result = self.cloud_renderer.render(video_job)
        if result.status.value != "COMPLETED":
            raise VideoServiceError(f"renderer cloud non terminé: {result.status.value}")
        if not result.video_url:
            raise VideoServiceError("renderer cloud terminé sans video_url")

        metrics = dict(result.qc)
        metrics["cloud_video_url"] = result.video_url
        metrics["cloud_job_id"] = result.job_id
        if result.artifacts:
            self._materialize_compatibility_artifacts(offer_id, metrics, result)
        self._write_control_manifest(offer_id, result)
        return metrics

    @staticmethod
    def _materialize_compatibility_artifacts(
        offer_id: str, metrics: dict[str, Any], result: VideoResult
    ) -> None:
        root = Path(os.environ.get("PODALUX_ROOT", Path.cwd()))
        base = root / "out" / offer_id
        base.mkdir(parents=True, exist_ok=True)

        by_name = {artifact.name: artifact for artifact in result.artifacts if artifact.url}
        if "final.mp4" not in by_name and result.video_url:
            by_name["final.mp4"] = Artifact("final.mp4", result.video_url, "video", "video/mp4")

        destinations = {
            "final.mp4": (base / "final.mp4", 2 * 1024 * 1024 * 1024),
            "video.mp4": (base / "video.mp4", 2 * 1024 * 1024 * 1024),
            "qc_metrics.json": (base / "qc_metrics.json", 10 * 1024 * 1024),
        }

        for name in ("final.mp4", "qc_metrics.json"):
            artifact = by_name.get(name)
            if artifact is None:
                if name == "qc_metrics.json":
                    continue
                raise VideoServiceError("artefact cloud requis absent: final.mp4")
            destination, max_size = destinations[name]
            VideoService._download_artifact(artifact.url, destination, max_size)

        frames = metrics.get("frames")
        if isinstance(frames, list) and frames:
            frame_artifacts = {artifact.name: artifact for artifact in result.artifacts if artifact.kind == "image"}
            materialized: list[str] = []
            frames_dir = base / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            for item in frames:
                name = Path(str(item)).name
                artifact = frame_artifacts.get(name)
                if artifact is None and str(item).startswith(("https://", "http://")):
                    allowed = {a.url for a in frame_artifacts.values()}
                    if str(item) not in allowed:
                        raise VideoServiceError(f"URL frame QC non autorisée: {item}")
                    artifact = Artifact(name, str(item), "image")
                if artifact is None:
                    raise VideoServiceError(f"frame QC introuvable dans le manifest: {item}")
                destination = frames_dir / name
                VideoService._download_artifact(artifact.url, destination, 8 * 1024 * 1024)
                materialized.append(str(destination))
            metrics["frames"] = materialized

    @staticmethod
    def _download_artifact(url: str, destination: Path, max_size: int) -> None:
        if not url.startswith(("https://", "http://", "file://")):
            raise VideoServiceError(f"schéma d'URL artefact refusé: {url}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        total = 0
        try:
            with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_size:
                        raise VideoServiceError(
                            f"artefact trop volumineux: {destination.name} ({total} > {max_size})"
                        )
                    out.write(chunk)
            if total <= 0:
                raise VideoServiceError(f"artefact vide: {destination.name}")
            tmp.replace(destination)
        except VideoServiceError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise VideoServiceError(f"téléchargement artefact échoué {destination.name}: {exc}") from exc

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
