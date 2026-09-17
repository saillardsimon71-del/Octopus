"""Abstraction renderer : le code métier ne connaît pas le fournisseur cloud."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from .client import CloudVideoClient, CloudVideoError
from .contract import TERMINAL_STATUSES, VideoJob, VideoResult, VideoStatus
from .runpod import RunPodConfig, RunPodServerlessClient
from .state import AmbiguousSubmissionError, RenderStateStore


_RETRYABLE_TERMINAL = {
    VideoStatus.FAILED.value,
    VideoStatus.CANCELLED.value,
    VideoStatus.EXPIRED.value,
}


class VideoRenderer(ABC):
    @abstractmethod
    def render(self, job: VideoJob) -> VideoResult:
        raise NotImplementedError


class CloudVideoRenderer(VideoRenderer):
    def __init__(self, client: CloudVideoClient, *, provider: str = "cloud",
                 state_store: RenderStateStore | None = None, max_attempts: int | None = None):
        self.client = client
        self.provider = provider
        self.state_store = state_store or RenderStateStore(
            Path(os.environ.get("PODALUX_ROOT", Path.cwd())) / "out" / ".cloud_video_state"
        )
        self.max_attempts = max(1, int(max_attempts if max_attempts is not None else
                                      os.environ.get("PODALUX_VIDEO_MAX_ATTEMPTS", "2")))

    def render(self, job: VideoJob) -> VideoResult:
        state = self.state_store.load(job.job_id)
        if state is not None:
            if state.state == "SUBMITTING" and not state.remote_id:
                self.state_store.require_resume_safe(state)
            if state.remote_id and state.state not in _RETRYABLE_TERMINAL:
                return self._wait_existing(job, state)
            if state.remote_id and state.state in _RETRYABLE_TERMINAL:
                if state.attempt >= self.max_attempts:
                    raise CloudVideoError(
                        f"job {job.job_id} a atteint la limite de {self.max_attempts} tentative(s)"
                    )
                attempt = state.attempt + 1
                self.state_store.mark_submitting(job.job_id, self.provider, attempt=attempt)
                return self._submit_and_wait(job, attempt)

        self.state_store.mark_submitting(job.job_id, self.provider, attempt=1)
        return self._submit_and_wait(job, 1)

    def _wait_existing(self, job: VideoJob, state) -> VideoResult:
        try:
            result = self.client.wait(state.remote_id)
        except CloudVideoError as exc:
            self._persist_remote_terminal_failure(state)
            raise exc
        current = self.state_store.load(job.job_id)
        if current is not None:
            self.state_store.mark_status(current, result.status.value)
        return result

    def _submit_and_wait(self, job: VideoJob, attempt: int) -> VideoResult:
        try:
            remote = self.client.submit(job)
        except CloudVideoError as exc:
            raise AmbiguousSubmissionError(
                f"soumission {job.job_id} non confirmée; état conservé en SUBMITTING: {exc}"
            ) from exc
        self.state_store.mark_submitted(job.job_id, self.provider, remote.remote_id,
                                        remote.status.value, attempt=attempt)
        try:
            result = self.client.wait(remote.remote_id)
        except CloudVideoError as exc:
            current = self.state_store.load(job.job_id)
            if current is not None:
                self._persist_remote_terminal_failure(current)
            raise exc
        current = self.state_store.load(job.job_id)
        if current is not None:
            self.state_store.mark_status(current, result.status.value)
        return result

    def _persist_remote_terminal_failure(self, state) -> None:
        """Distingue un job réellement terminal d'un simple timeout réseau.

        Un appel de statut supplémentaire est volontaire : un timeout du client ne doit
        jamais être interprété comme un échec et ne doit donc pas créer un second rendu.
        """
        if not state.remote_id:
            return
        try:
            remote = self.client.status(state.remote_id)
        except CloudVideoError:
            return
        if remote.status in {
            VideoStatus.FAILED,
            VideoStatus.CANCELLED,
            VideoStatus.EXPIRED,
        }:
            self.state_store.mark_status(state, remote.status.value)


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
