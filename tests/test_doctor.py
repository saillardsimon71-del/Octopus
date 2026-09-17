"""Tests du diagnostic local cloud-first."""
from __future__ import annotations

import sys

from agents import config, db, doctor


def test_doctor_cloud_first_requires_omniroute_and_runpod(isolated, monkeypatch):
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", "cloud")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.delenv("OCTOPUS_PROFILE", raising=False)  # installation reelle : profil non force
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    monkeypatch.setenv("PODALUX_RUNPOD_ENDPOINT_ID", "endpoint")
    monkeypatch.setenv("PODALUX_RUNPOD_API_TOKEN", "runpod-test")
    monkeypatch.delenv("OMNIROUTE_BASE_URL", raising=False)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object() if name == "playwright" else None)
    monkeypatch.setattr(doctor, "_chromium_executable", lambda: sys.executable)
    seen = {}

    def fake_http(url, api_key="", timeout=3.0):
        seen["url"] = url
        seen["api_key"] = api_key
        return True, "HTTP 200"

    monkeypatch.setattr(doctor, "_http_ok", fake_http)
    monkeypatch.setattr(config, "api_key", lambda: "")

    checks = {c.name: c for c in doctor.run_checks()}
    assert seen["url"] == "http://127.0.0.1:20128/v1/models"
    assert seen["api_key"] == "test-key"
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
    assert not checks["Voix (chaine TTS)"].ok  # aucun fournisseur : ni cle, ni serveur, ni piper
    assert checks["Voix (chaine TTS)"].fix
    assert checks["npx renderer local"].ok
    assert checks["Remotion local"].ok


def test_doctor_voice_chain_reports_available_providers(isolated, monkeypatch):
    """La chaine de voix est verte des qu'un fournisseur est utilisable ; piper suffit, sans compte."""
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", "local")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    monkeypatch.delenv("TTS_CHAIN", raising=False)
    monkeypatch.delenv("CHATTERBOX_URL", raising=False)
    monkeypatch.setattr(config, "PYTHON", sys.executable)
    monkeypatch.setattr(config, "api_key", lambda: "")
    monkeypatch.setattr(doctor, "_port_open", lambda url: False)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: sys.executable if name in {"ffmpeg", "ffprobe", "npx"} else None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec",
                        lambda name: object() if name in {"playwright", "piper"} else None)
    monkeypatch.setattr(doctor, "_chromium_executable", lambda: sys.executable)
    remotion = isolated / "remotion"
    (remotion / "node_modules" / "remotion").mkdir(parents=True)
    (remotion / "remotion.config.ts").write_text("// auto detection", encoding="utf-8")

    checks = {c.name: c for c in doctor.run_checks()}
    voice = checks["Voix (chaine TTS)"]
    assert voice.ok and "piper" in voice.detail
    assert "Serveur Chatterbox local" not in checks

    monkeypatch.setattr(doctor.importlib.util, "find_spec",
                        lambda name: object() if name == "playwright" else None)  # plus aucun fournisseur
    none_ready = {c.name: c for c in doctor.run_checks()}["Voix (chaine TTS)"]
    assert not none_ready.ok and "aucun" in none_ready.detail
