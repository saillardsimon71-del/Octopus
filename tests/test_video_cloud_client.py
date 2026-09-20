"""Tests du protocole cloud fournisseur-agnostique et RunPod."""
from __future__ import annotations

import json

import pytest

from octopus.video.client import CloudVideoConfig, CloudVideoError, CloudVideoClient
from octopus.video.contract import VideoJob, VideoStatus
from octopus.video.runpod import RunPodConfig, RunPodServerlessClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return FakeResponse(self.responses.pop(0))


def job():
    return VideoJob(
        job_id="video-abc", offer_id="cash_devis_cgv01", template="forge-v4", language="fr",
        duration_seconds=24,
        script={"segments": [{"role": "hook", "texte": "Bonjour"}]},
        voice={"moteur": "chatterbox", "nom": "vivienne-fr"},
    )


def test_generic_submit_wait_maps_completed():
    opener = FakeOpener([
        {"id": "remote-1", "status": "QUEUED"},
        {"id": "remote-1", "status": "COMPLETED", "output": {"video_url": "https://example/video.mp4", "qc": {"duration_s": 24}}},
    ])
    client = CloudVideoClient(
        CloudVideoConfig("https://example/run", "https://example/status/{job_id}", poll_initial_s=0.01, poll_max_s=0.01),
        opener=opener,
    )
    remote = client.submit(job())
    result = client.wait(remote.remote_id, timeout_s=1)
    assert result.status == VideoStatus.COMPLETED
    assert result.video_url == "https://example/video.mp4"
    assert json.loads(opener.requests[0].data.decode())["job_id"] == "video-abc"


def test_generic_completed_without_video_fails():
    client = CloudVideoClient(
        CloudVideoConfig("https://example/run", "https://example/status/{job_id}"),
        opener=FakeOpener([{"id": "r", "status": "COMPLETED", "output": {}}]),
    )
    try:
        client.wait("r", timeout_s=1)
    except CloudVideoError as exc:
        assert "video_url" in str(exc)
    else:
        raise AssertionError("COMPLETED sans video_url doit échouer")


def test_runpod_requires_explicit_legacy_opt_in(monkeypatch):
    monkeypatch.delenv("OCTOPUS_ALLOW_LEGACY_RUNPOD", raising=False)
    with pytest.raises(CloudVideoError, match="legacy.*désactivé"):
        RunPodServerlessClient(RunPodConfig("endpoint-1", "secret-token"))


def test_runpod_wraps_payload_in_input(monkeypatch):
    monkeypatch.setenv("OCTOPUS_ALLOW_LEGACY_RUNPOD", "1")
    opener = FakeOpener([{ "id": "rp-1", "status": "IN_QUEUE" }])
    client = RunPodServerlessClient(RunPodConfig("endpoint-1", "secret-token"), opener=opener)
    client.submit(job())
    body = json.loads(opener.requests[0].data.decode())
    assert body == {"input": job().to_dict()}
    assert opener.requests[0].full_url == "https://api.runpod.ai/v2/endpoint-1/run"
    assert opener.requests[0].headers.get("Authorization") == "Bearer secret-token"
