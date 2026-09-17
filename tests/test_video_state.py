"""Tests hors-réseau de la persistance d'identité de rendu cloud."""
from __future__ import annotations

import time

import pytest

from octopus.video.client import CloudVideoError
from octopus.video.contract import RemoteJob, VideoJob, VideoResult, VideoStatus
from octopus.video.renderers import CloudVideoRenderer
from octopus.video.state import AmbiguousSubmissionError, RenderState, RenderStateStore


def job() -> VideoJob:
    return VideoJob(
        job_id="video-state-1", offer_id="offer", template="CashShort", language="fr",
        duration_seconds=24, script={"segments": [{"role": "hook", "texte": "Bonjour"}]},
        voice={"moteur": "chatterbox", "nom": "vivienne-fr"},
    )


def test_submitting_without_remote_id_is_ambiguous(tmp_path):
    store = RenderStateStore(tmp_path)
    store.save(RenderState("video-state-1", "runpod", "SUBMITTING", None, 1, time.time()))
    with pytest.raises(AmbiguousSubmissionError):
        store.require_resume_safe(store.load("video-state-1"))


def test_renderer_resumes_known_remote_job_without_submit(tmp_path):
    state = RenderStateStore(tmp_path)

    class FakeClient:
        def __init__(self):
            self.submits = 0
            self.waits = 0
        def submit(self, video_job):
            self.submits += 1
            return RemoteJob("remote-1", VideoStatus.QUEUED)
        def wait(self, remote_id):
            self.waits += 1
            return VideoResult("video-state-1", VideoStatus.COMPLETED, "file:///final.mp4", qc={"duration_s": 24})

    client = FakeClient()
    state.save(RenderState("video-state-1", "runpod", "RUNNING", "remote-1", 1, time.time()))
    result = CloudVideoRenderer(client, state_store=state).render(job())
    assert result.status == VideoStatus.COMPLETED
    assert client.submits == 0
    assert client.waits == 1
    assert state.load("video-state-1").state == "COMPLETED"


def test_renderer_does_not_hide_submit_failure(tmp_path):
    state = RenderStateStore(tmp_path)

    class FailingClient:
        def submit(self, video_job):
            raise CloudVideoError("timeout")

    renderer = CloudVideoRenderer(FailingClient(), state_store=state)
    with pytest.raises(AmbiguousSubmissionError):
        renderer.render(job())
    saved = state.load("video-state-1")
    assert saved.state == "SUBMITTING"
    assert saved.remote_id is None
    assert saved.attempt == 1


def test_renderer_retries_terminal_failure_once(tmp_path):
    state = RenderStateStore(tmp_path)

    class RetryClient:
        def __init__(self):
            self.submits = 0
            self.waits = []
        def submit(self, video_job):
            self.submits += 1
            return RemoteJob(f"remote-{self.submits}", VideoStatus.QUEUED)
        def wait(self, remote_id):
            self.waits.append(remote_id)
            return VideoResult("video-state-1", VideoStatus.COMPLETED, "file:///final.mp4", qc={"ok": True})

    client = RetryClient()
    state.save(RenderState("video-state-1", "runpod", "FAILED", "remote-old", 1, time.time()))
    result = CloudVideoRenderer(client, state_store=state, max_attempts=2).render(job())
    assert result.status == VideoStatus.COMPLETED
    assert client.submits == 1
    assert client.waits == ["remote-1"]
    assert state.load("video-state-1").attempt == 2


def test_renderer_stops_after_attempt_limit(tmp_path):
    state = RenderStateStore(tmp_path)

    class NoSubmitClient:
        def submit(self, video_job):
            raise AssertionError("aucune nouvelle soumission ne doit être faite")
        def wait(self, remote_id):
            raise AssertionError("aucun polling ne doit être fait")

    state.save(RenderState("video-state-1", "runpod", "FAILED", "remote-old", 2, time.time()))
    with pytest.raises(CloudVideoError, match="limite de 2"):
        CloudVideoRenderer(NoSubmitClient(), state_store=state, max_attempts=2).render(job())
