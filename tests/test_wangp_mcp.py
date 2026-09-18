from __future__ import annotations

import io
from pathlib import Path

import pytest

from octopus.media.wangp_mcp import WanGPMCPClient, WanGPMCPConfig, WanGPMCPError


class FakeTools:
    def __init__(self):
        self.calls = []
        self.jobs = 0

    def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "wangp_models":
            return {"models": [
                {"model_type": "wan-fast", "name": "Wan Fast", "main_output": ["video"], "inputs": ["text"]},
                {"model_type": "wan-image", "name": "Wan Image", "main_output": ["image"], "inputs": ["text"]},
            ], "total": 2}
        if name == "wangp_model" and arguments["view"] == "schema":
            return {"model_type": arguments["model_type"], "main_output": ["video"], "inputs": ["text"],
                    "settings": {"steps": {"type": "integer", "minimum": 1}}}
        if name == "wangp_model" and arguments["view"] == "defaults":
            return {"num_inference_steps": 6, "resolution": "512x288"}
        if name == "wangp_generate":
            return {"job_id": "job-1", "done": False, "events": [], "result": None}
        if name == "wangp_get_job":
            self.jobs += 1
            if self.jobs == 1:
                return {"job_id": "job-1", "done": False, "events": [{"kind": "progress"}], "result": None}
            return {"job_id": "job-1", "done": True, "events": [], "result": {
                "success": True, "generated_files": ["outputs/clip.mp4"], "errors": [],
                "gallery_items": [{"media_id": "video:abc", "filename": "clip.mp4", "media_type": "video"}],
            }}
        if name == "wangp_get_deepy_template_settings":
            return {"tool_id": "gen_video", "template": "Wan 2.2", "default": True,
                    "settings": {"model_type": "wan22_5B", "resolution": "1280x720"}}
        if name == "wangp_create_gallery_download":
            return {"media_id": arguments["media_id"], "download_url": "/wangp_api/gallery/download/token-1",
                    "filename": "clip.mp4", "media_type": "video", "size": 11}
        raise AssertionError((name, arguments))


def test_runtime_discovery_and_settings_come_from_wangp():
    tools = FakeTools()
    client = WanGPMCPClient(WanGPMCPConfig("https://wangp.example/mcp"), call_tool=tools)

    models = client.discover(query="wan", output="video")
    settings = client.prepare("wan-fast", "a fox in snow", {"num_inference_steps": 8, "seed": 42})

    assert [model["model_type"] for model in models] == ["wan-fast"]
    assert settings == {
        "num_inference_steps": 8, "resolution": "512x288", "model_type": "wan-fast",
        "prompt": "a fox in snow", "seed": 42,
    }
    assert tools.calls[:3] == [
        ("wangp_models", {"query": "wan", "filters": {"main_output": "video"}, "limit": 50, "offset": 0}),
        ("wangp_model", {"model_type": "wan-fast", "view": "schema"}),
        ("wangp_model", {"model_type": "wan-fast", "view": "defaults"}),
    ]


def test_submit_and_poll_return_observed_remote_result():
    tools = FakeTools()
    sleeps = []
    client = WanGPMCPClient(WanGPMCPConfig("https://wangp.example/mcp", poll_s=0.01),
                            call_tool=tools, sleep=sleeps.append)

    submitted = client.submit({"model_type": "wan-fast", "prompt": "test"})
    result = client.wait(submitted["job_id"], timeout_s=2)

    assert submitted["job_id"] == "job-1"
    assert result["success"] is True
    assert result["generated_files"] == ["outputs/clip.mp4"]
    assert result["gallery_items"][0]["media_id"] == "video:abc"
    assert sleeps == [0.01]


class FakeResponse(io.BytesIO):
    headers = {"Content-Length": "11"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_template_settings_and_atomic_gallery_download(tmp_path):
    tools = FakeTools()
    requests = []

    def open_url(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(b"video-bytes")

    client = WanGPMCPClient(
        WanGPMCPConfig("https://wangp.example/mcp", username="octopus", password="secret"),
        call_tool=tools,
        open_url=open_url,
    )

    template = client.template_settings()
    target = client.download("video:abc", tmp_path / "result.mp4", max_bytes=20)

    assert template["settings"]["model_type"] == "wan22_5B"
    assert target == tmp_path / "result.mp4"
    assert target.read_bytes() == b"video-bytes"
    assert not Path(f"{target}.part").exists()
    request, timeout = requests[0]
    assert request.full_url == "https://wangp.example/wangp_api/gallery/download/token-1"
    assert request.get_header("Authorization").startswith("Basic ")
    assert timeout == 120.0


def test_download_rejects_oversized_media_before_http(tmp_path):
    tools = FakeTools()
    client = WanGPMCPClient(WanGPMCPConfig("https://wangp.example/mcp"), call_tool=tools,
                            open_url=lambda *_: (_ for _ in ()).throw(AssertionError("HTTP interdit")))

    with pytest.raises(WanGPMCPError, match="trop volumineux"):
        client.download("video:abc", tmp_path / "result.mp4", max_bytes=10)


def test_config_rejects_conflicting_or_incomplete_authentication():
    with pytest.raises(ValueError, match="incomplets"):
        WanGPMCPConfig("https://wangp.example/mcp", username="octopus")
    with pytest.raises(ValueError, match="une seule authentification"):
        WanGPMCPConfig("https://wangp.example/mcp", username="octopus", password="secret", bearer_token="token")
