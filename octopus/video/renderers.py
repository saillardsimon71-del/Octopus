"""Abstraction renderer : le code métier ne connaît pas le fournisseur cloud."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .client import CloudVideoClient
from .contract import VideoJob, VideoResult
from .runpod import RunPodConfig, RunPodServerlessClient


class VideoRenderer(ABC):
    @abstractmethod
    def render(self, job: VideoJob) -> VideoResult:
        raise NotImplementedError


class CloudVideoRenderer(VideoRenderer):
    def __init__(self, client: CloudVideoClient):
        self.client = client

    def render(self, job: VideoJob) -> VideoResult:
        remote = self.client.submit(job)
        return self.client.wait(remote.remote_id)


class LocalVideoRenderer(VideoRenderer):
    """Point d'extension uniquement ; FORGE conserve son pipeline local historique."""

    def render(self, job: VideoJob) -> VideoResult:
        raise RuntimeError(
            "LocalVideoRenderer n'est pas appelé directement pendant la migration. "
            "Le service vidéo délègue au pipeline FORGE historique."
        )


def get_renderer(*, mode: str | None = None, provider: str | None = None) -> VideoRenderer:
    selected = (mode or os.environ.get("PODALUX_VIDEO_RENDERER", "local")).strip().lower()
    if selected == "local":
        return LocalVideoRenderer()
    if selected != "cloud":
        raise ValueError(f"PODALUX_VIDEO_RENDERER inconnu: {selected!r}; valeurs: local, cloud")

    selected_provider = (provider or os.environ.get("PODALUX_VIDEO_PROVIDER", "runpod")).strip().lower()
    if selected_provider == "runpod":
        return CloudVideoRenderer(RunPodServerlessClient(RunPodConfig.from_env()))
    if selected_provider == "http":
        from .client import CloudVideoConfig
        return CloudVideoRenderer(CloudVideoClient(CloudVideoConfig.from_env()))
    raise ValueError(f"PODALUX_VIDEO_PROVIDER inconnu: {selected_provider!r}; valeurs: runpod, http")
