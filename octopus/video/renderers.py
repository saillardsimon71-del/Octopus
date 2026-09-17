"""Abstraction renderer : le code métier ne connaît pas le fournisseur cloud."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from .client import CloudVideoClient, CloudVideoError
from .contract import VideoJob, VideoResult
from .runpod import RunPodConfig, RunPodServerlessClient
from .state import AmbiguousSubmissionError, RenderStateStore


class VideoRenderer(ABC):
    @abstractmethod
    def render(self, job: VideoJob) -> VideoResult:
        raise NotImplementedError


class CloudVideoRenderer(VideoRenderer):
    def __init__(self, client: CloudVideoClient, *, provider: str = "cloud", state_store: RenderStateStore | None = None):
        self.client = client
        self.provider = provider
        self.state_store = state_store or RenderStateStore(
            Path(os.environ.get("PODALUX_ROOT", Path.cwd())) / "out" / ".cloud_video_state"
        )

    def render(self, job: VideoJob) -> VideoResult:
        state = self.state_store.load(job.job_id)
        if state is not None:
            if state.remote_id:
                result = self.client.wait(state.remote_id)
                current = self.state_store.load(job.job_id)
                if current is not None:
                    self.state_store.mark_status(current, result.status.value)
                return result
            self.state_store.require_resume_safe(state)

        # Écriture AVANT l'appel réseau : si le processus meurt ou si le POST expire sans
        # renvoyer son identifiant, le prochain essai ne recrée pas automatiquement un job payé.
        self.state_store.mark_submitting(job.job_id, self.provider)
        try:
            remote = self.client.submit(job)
        except CloudVideoError as exc:
            raise AmbiguousSubmissionError(
                f"soumission {job.job_id} non confirmée; état conservé en SUBMITTING: {exc}"
            ) from exc
        self.state_store.mark_submitted(job.job_id, self.provider, remote.remote_id, remote.status.value)
        result = self.client.wait(remote.remote_id)
        current = self.state_store.load(job.job_id)
        if current is not None:
            self.state_store.mark_status(current, result.status.value)
        return result


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
    state_root = Path(os.environ.get("PODALUX_VIDEO_STATE_DIR", "").strip() or
                      Path(os.environ.get("PODALUX_ROOT", Path.cwd())) / "out" / ".cloud_video_state")
    state_store = RenderStateStore(state_root)
    if selected_provider == "runpod":
        return CloudVideoRenderer(
            RunPodServerlessClient(RunPodConfig.from_env()), provider="runpod", state_store=state_store
        )
    if selected_provider == "http":
        from .client import CloudVideoConfig
        return CloudVideoRenderer(
            CloudVideoClient(CloudVideoConfig.from_env()), provider="http", state_store=state_store
        )
    raise ValueError(f"PODALUX_VIDEO_PROVIDER inconnu: {selected_provider!r}; valeurs: runpod, http")
