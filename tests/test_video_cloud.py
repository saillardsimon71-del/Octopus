"""Tests hors-réseau de la couche vidéo cloud et du shim FORGE."""
from __future__ import annotations

import json
from pathlib import Path

from octopus.video.contract import Artifact, VideoResult, VideoStatus
from octopus.video.service import VideoService, stable_job_id
from agents import config, tools


class FakeRenderer:
    def __init__(self, result: VideoResult):
        self.result = result
        self.calls = 0
        self.jobs = []

    def render(self, job):
        self.calls += 1
        self.jobs.append(job)
        return self.result


def _artifact(path: Path, kind: str = "file", content_type: str | None = None) -> Artifact:
    return Artifact(path.name, path.as_uri(), kind, content_type)


def test_stable_job_id_is_deterministic():
    a = {"offer_id": "o", "narration": [{"texte": "A", "role": "hook"}], "x": {"b": 2, "a": 1}}
    b = {"x": {"a": 1, "b": 2}, "narration": [{"role": "hook", "texte": "A"}], "offer_id": "o"}
    assert stable_job_id(a) == stable_job_id(b)


def test_cloud_service_materializes_compatibility_artifacts(isolated, monkeypatch, tmp_path):
    monkeypatch.setenv("PODALUX_ROOT", str(isolated))
    source = tmp_path / "source"
    source.mkdir()
    (source / "final.mp4").write_bytes(b"real-mp4-placeholder")
    (source / "mix.wav").write_bytes(b"real-wav-placeholder")
    (source / "captions.ts").write_text("export const DURATION_S = 24;", encoding="utf-8")
    (source / "job.ts").write_text("export const JOB = {};", encoding="utf-8")
    (source / "qc_metrics.json").write_text(json.dumps({"duration_s": 24, "frames": ["frame-0.jpg"]}), encoding="utf-8")
    (source / "frame-0.jpg").write_bytes(b"jpeg-placeholder")

    result = VideoResult(
        job_id="video-test",
        status=VideoStatus.COMPLETED,
        video_url=(source / "final.mp4").as_uri(),
        artifacts=(
            _artifact(source / "final.mp4", "video", "video/mp4"),
            _artifact(source / "mix.wav", "file", "audio/wav"),
            _artifact(source / "captions.ts", "file", "text/plain"),
            _artifact(source / "job.ts", "file", "text/plain"),
            _artifact(source / "qc_metrics.json", "file", "application/json"),
            _artifact(source / "frame-0.jpg", "image", "image/jpeg"),
        ),
        qc={"duration_s": 24, "frames": ["frame-0.jpg"]},
    )
    renderer = FakeRenderer(result)
    service = VideoService(cloud_renderer=renderer, mode="cloud")

    job = {"offer_id": "offer-1", "duree_cible_s": 24, "narration": [{"texte": "Bonjour", "role": "hook"}],
           "titre": "Test", "hook": "H", "douleur": "D", "preuve": "P", "soulagement": "S", "cta": "C",
           "voix": {"moteur": "chatterbox", "nom": "vivienne-fr"}}
    metrics = service.render("offer-1", job)

    out = isolated / "out" / "offer-1"
    assert renderer.calls == 1
    assert (out / "final.mp4").is_file()
    assert (out / "audio" / "mix.wav").is_file()
    assert (out / "remotion" / "captions.ts").is_file()
    assert (out / "remotion" / "job.ts").is_file()
    assert (out / "qc_metrics.json").is_file()
    assert (out / "frames" / "frame-0.jpg").is_file()
    assert metrics["frames"] == [str(out / "frames" / "frame-0.jpg")]
    assert json.loads((out / "cloud_result.json").read_text(encoding="utf-8"))["status"] == "COMPLETED"


def test_forge_tools_cloud_shim_does_not_render_twice(monkeypatch, isolated):
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", "cloud")
    final = isolated / "out" / "offer" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")
    qc = final.parent / "qc_metrics.json"
    qc.write_text(json.dumps({"duration_s": 24, "frames": []}), encoding="utf-8")
    job = isolated / "job.json"
    job.write_text(json.dumps({"offer_id": "offer", "narration": [{"texte": "x", "role": "hook"}]}), encoding="utf-8")

    class FakeService:
        def __init__(self):
            self.calls = 0
        def render(self, offer_id, payload):
            self.calls += 1
            return {"cloud_job_id": "provider-1", "cloud_video_url": "https://example.invalid/final.mp4"}

    fake = FakeService()
    monkeypatch.setattr(tools, "_cloud_service", lambda: fake)
    result = tools.make_audio(str(job), "offer")
    assert fake.calls == 1
    assert '"cloud"' in result
    assert tools.remotion_render("offer").startswith("rendu cloud")
    assert tools.mux("offer").startswith("mux cloud")
    assert tools.qc_metrics("offer")["duration_s"] == 24
