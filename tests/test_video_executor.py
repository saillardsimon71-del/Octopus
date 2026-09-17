"""Tests du worker sans Node, FFmpeg, TTS ni réseau réels."""
from __future__ import annotations

import json
from pathlib import Path

from octopus.video.contract import VideoJob, VideoStatus
from octopus.video.storage import FilesystemArtifactStore
from video_worker.executor import ExecutorConfig, ForgeExecutor


def make_job() -> VideoJob:
    return VideoJob(
        job_id="video-test-1",
        offer_id="cash_devis_cgv01",
        template="forge-v4",
        language="fr",
        duration_seconds=24,
        script={
            "titre": "Titre", "hook": "Hook", "douleur": "Douleur", "preuve": "Preuve",
            "soulagement": "Soulagement", "cta": "37 €", "prix": "37 €",
            "stripe_link": "https://example.invalid/pay", "sub_id": "sub-1",
            "segments": [{"id": "hook", "role": "hook", "texte": "Bonjour"}],
        },
        voice={"moteur": "chatterbox", "nom": "vivienne-fr"},
        metadata={"keywords": ["devis"], "palette": {}, "visuel": {}},
    )


def test_executor_produces_manifest_and_is_idempotent(tmp_path):
    root = tmp_path / "project"
    (root / "remotion" / "src" / "data").mkdir(parents=True)
    (root / "remotion" / "package.json").write_text("{}")
    (root / "remotion" / "package-lock.json").write_text("{}")
    (root / "tools").mkdir()
    (root / "tools" / "make_audio_chatterbox_full.py").write_text("# test")
    (root / "tools" / "qc_metrics.py").write_text("# test")

    store = FilesystemArtifactStore(tmp_path / "artifacts", public_base_url="https://storage.test/")
    calls = []

    def runner(cmd, cwd, timeout, env, log):
        calls.append(cmd)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("ok")
        cwd = Path(cwd)
        if "make_audio_chatterbox_full.py" in cmd:
            out = cwd / "out" / "cash_devis_cgv01" / "audio"
            out.mkdir(parents=True)
            (out / "mix.wav").write_bytes(b"wav")
            data = cwd / "remotion" / "src" / "data"
            data.mkdir(parents=True, exist_ok=True)
            (data / "captions.ts").write_text("x")
            (data / "job.ts").write_text("x")
        elif cmd[0] == "npx":
            output = cwd.parent / "out" / "cash_devis_cgv01" / "video.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
        elif cmd[0] == "ffmpeg" and "ebur128" in cmd:
            return "I: -14.0 LUFS"
        elif cmd[0] == "ffmpeg":
            output = Path(cmd[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"final")
        elif "qc_metrics.py" in cmd:
            out = cwd / "out" / "cash_devis_cgv01"
            (out / "frames").mkdir(parents=True, exist_ok=True)
            for i in range(2):
                (out / "frames" / f"frame-{i}.jpg").write_bytes(b"jpg")
            (out / "qc_metrics.json").write_text(json.dumps({
                "duration_s": 24, "resolution": "1080x1920", "lufs_integrated": -14,
                "freezes_gt1_2s": 0,
            }))
            return "{}"
        return "ok"

    executor = ForgeExecutor(store, ExecutorConfig(project_root=root, work_root=tmp_path / "work"), runner=runner)
    first = executor.render(make_job())
    assert first.status == VideoStatus.COMPLETED
    assert first.video_url == "https://storage.test/cash_devis_cgv01/video-test-1/final.mp4"
    assert store.exists("cash_devis_cgv01/video-test-1/manifest.json")

    calls_before = len(calls)
    second = executor.render(make_job())
    assert second.status == VideoStatus.COMPLETED
    assert len(calls) == calls_before
