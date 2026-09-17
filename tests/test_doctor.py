"""Tests du diagnostic local cloud-first."""
from __future__ import annotations

import sys

from agents import config, db, doctor


def test_doctor_cloud_first_requires_omniroute_and_runpod(isolated, monkeypatch):
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", "cloud")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    monkeypatch.setenv("PODALUX_RUNPOD_ENDPOINT_ID", "endpoint")
    monkeypatch.setenv("PODALUX_RUNPOD_API_TOKEN", "runpod-test")
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object() if name == "playwright" else None)
    monkeypatch.setattr(doctor, "_chromium_executable", lambda: sys.executable)
    monkeypatch.setattr(doctor, "_http_ok", lambda url, api_key="", timeout=3.0: (True, "HTTP 200"))
    monkeypatch.setattr(config, "api_key", lambda: "")

    checks = {c.name: c for c in doctor.run_checks()}
    assert checks["Python contrôle"].ok
    assert checks["Playwright"].ok
    assert checks["Chromium Playwright"].ok
    assert checks["OmniRoute"].ok
    assert checks["RunPod vidéo"].ok
    assert checks["Chatterbox local"].ok and not checks["Chatterbox local"].blocking
    assert checks["Remotion/FFmpeg local"].ok and not checks["Remotion/FFmpeg local"].blocking
    assert not checks["Clé DeepSeek"].ok and not checks["Clé DeepSeek"].blocking
    text, code = doctor.render(list(checks.values()))
    assert code == 0 and "Prêt pour un cycle réel." in text


def test_doctor_local_mode_checks_local_media_dependencies(isolated, monkeypatch):
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", "local")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    monkeypatch.setattr(config, "PYTHON", sys.executable)
    monkeypatch.setattr(config, "CHATTERBOX_URL", "http://127.0.0.1:9/v1/audio/speech")
    monkeypatch.setattr(config, "api_key", lambda: "")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: sys.executable if name in {"ffmpeg", "ffprobe", "npx"} else None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object() if name == "playwright" else None)
    monkeypatch.setattr(doctor, "_chromium_executable", lambda: sys.executable)

    remotion = isolated / "remotion"
    (remotion / "node_modules" / "remotion").mkdir(parents=True)
    (remotion / "remotion.config.ts").write_text("// auto detection", encoding="utf-8")
    checks = {c.name: c for c in doctor.run_checks()}
    assert not checks["Serveur Chatterbox local"].ok
    assert not checks["Serveur Chatterbox local"].blocking or checks["Serveur Chatterbox local"].fix
    assert checks["npx renderer local"].ok
    assert checks["Remotion local"].ok
