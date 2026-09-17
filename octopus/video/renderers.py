"""Abstraction renderer : le code métier ne connaît pas le fournisseur cloud."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Mapping

from .client import CloudVideoClient
from .contract import VideoJob, VideoResult, VideoStatus


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
    """Adaptateur de transition.

    Il ne réimplémente pas FORGE : le pipeline local existant reste la source de vérité
    jusqu'à ce que le worker cloud soit validé. Cette classe sert seulement de point
    d'extension pour une future migration sans changer les agents.
    """

    def render(self, job: VideoJob) -> VideoResult:
        raise RuntimeError(
            "LocalVideoRenderer n'est pas encore branché intentionnellement. "
            "Utiliser le FORGE historique ou CloudVideoRenderer."
        )


def get_renderer(*, mode: str | None = None) -> VideoRenderer:
    selected = (mode or os.environ.get("PODALUX_VIDEO_RENDERER", "local")).strip().lower()
    if selected == "cloud":
        from .client import CloudVideoConfig
        return CloudVideoRenderer(CloudVideoClient(CloudVideoConfig.from_env()))
    if selected == "local":
        return LocalVideoRenderer()
    raise ValueError(f"PODALUX_VIDEO_RENDERER inconnu: {selected!r}; valeurs: local, cloud")
