from __future__ import annotations

from octopus.media.wangp_mcp import WanGPMCPClient, WanGPMCPConfig


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
