"""Tests hors-réseau de l'adaptateur MiniMax H3 cloud."""
from __future__ import annotations

import base64

from octopus.media.minimax_h3_cloud import H3Result, MiniMaxH3Config, align_frames, build_t2v_workflow, save_result
from octopus.media import handlers, presets


def test_h3_frame_alignment():
    assert align_frames(5) == 124
    assert align_frames(10) == 243
    assert align_frames(15) == 362


def test_h3_workflow_matches_official_api_shape():
    workflow = build_t2v_workflow("un homme marche", width=1080, height=1920, duration_s=5, seed=123)
    assert workflow["1"]["class_type"] == "UNETLoader"
    assert workflow["2"]["class_type"] == "LoraLoaderModelOnly"
    assert workflow["2"]["inputs"]["model"] == ["1", 0]
    assert workflow["3"]["class_type"] == "CLIPLoader"
    assert workflow["20"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert workflow["20"]["inputs"]["length"] == 124
    assert workflow["20"]["inputs"]["width"] <= 768
    assert workflow["20"]["inputs"]["height"] <= 1344
    assert workflow["10"]["inputs"]["noise_seed"] == 123
    assert workflow["12"]["inputs"]["model"] == ["2", 0]
    assert workflow["13"]["inputs"]["model"] == ["2", 0]
    assert workflow["14"]["inputs"]["latent_image"] == ["20", 1]
    assert workflow["52"]["class_type"] == "CreateVideo"
    assert workflow["53"]["class_type"] == "SaveVideo"


def test_h3_result_can_materialize_base64_video(tmp_path):
    payload = base64.b64encode(b"mp4").decode()
    result = H3Result("remote-1", video_bytes=base64.b64decode(payload), filename="h3.mp4", raw={})
    target = save_result(result, tmp_path / "clip.mp4")
    assert target.read_bytes() == b"mp4"


def test_h3_preset_is_explicit_cloud():
    result = presets.apply({"preset": "h3", "prompt": "test"})
    assert result["model_type"] == "minimax_h3_fl2va_pruned_cloud"
    assert result["resolution"] == "768x1344"
    assert result["duration_s"] == 5


def test_h3_handler_bypasses_local_wangp(monkeypatch, isolated):
    monkeypatch.setenv("OCTOPUS_MINIMAX_H3_ENDPOINT_ID", "endpoint")
    monkeypatch.setenv("OCTOPUS_MINIMAX_H3_API_TOKEN", "secret")

    class FakeClient:
        def __init__(self, config):
            self.config = config
            self.submits = 0
        def submit(self, workflow):
            self.submits += 1
            assert workflow["20"]["class_type"] == "MiniMaxH3ImageToVideo"
            assert workflow["53"]["class_type"] == "SaveVideo"
            return "remote-1"
        def wait(self, remote_id):
            target = isolated / "h3.mp4"
            target.write_bytes(b"mp4")
            return H3Result(remote_id, video_url=target.as_uri())

    monkeypatch.setattr(handlers, "MiniMaxH3RunPodClient", FakeClient)
    monkeypatch.setattr(handlers.wangp, "discover", lambda: (_ for _ in ()).throw(AssertionError("WanGP ne doit pas être appelé")))
    monkeypatch.setattr(handlers.library, "probe_media", lambda path: {"duration_s": 5, "width": 768, "height": 1344})
    class Ctx:
        id = 7
        input = {"preset": "h3", "prompt": "test", "business": "podalux"}
        business = "podalux"
        def cancelled(self):
            return False
        def emit(self, *args, **kwargs):
            pass
    output = handlers.video_generate(Ctx())
    assert output["backend"] == "runpod_minimax_h3"
    assert len(output["videos"]) == 1
