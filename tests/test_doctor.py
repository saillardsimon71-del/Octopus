"""Local control diagnostics do not depend on the removed video engine."""
from __future__ import annotations

import sys

import pytest

from agents import config, doctor


@pytest.mark.parametrize("legacy_mode", ["local", "cloud"])
def test_doctor_requires_control_dependencies_only(isolated, monkeypatch, legacy_mode):
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", legacy_mode)
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.delenv("OCTOPUS_PROFILE", raising=False)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    monkeypatch.delenv("OMNIROUTE_BASE_URL", raising=False)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object() if name == "playwright" else None)
    monkeypatch.setattr(doctor, "_chromium_executable", lambda: sys.executable)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    seen = []

    def fake_http(url, api_key="", timeout=3.0):
        seen.append((url, api_key))
        return True, "HTTP 200"

    monkeypatch.setattr(doctor, "_http_ok", fake_http)
    monkeypatch.setattr(config, "api_key", lambda: "")
    checks = {c.name: c for c in doctor.run_checks()}
    assert seen == [("http://127.0.0.1:20128/v1/models", "test-key")]
    assert checks["Python contrôle"].ok
    assert checks["Playwright"].ok
    assert checks["Chromium Playwright"].ok
    assert checks["OmniRoute"].ok
    assert checks["Journal OCTOPUS"].ok
    assert not checks["Clé DeepSeek"].ok and not checks["Clé DeepSeek"].blocking
    assert not any(word in name.lower() for name in checks
                   for word in ("runpod", "remotion", "chatterbox", "tts", "ffmpeg", "npx"))
    text, code = doctor.render(list(checks.values()))
    assert code == 0 and "Contrôle local prêt." in text


def test_missing_control_dependencies_still_block(isolated, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor, "_http_ok", lambda *a, **k: pytest.fail("No probe without credentials"))
    checks = {c.name: c for c in doctor.run_checks()}
    assert not checks["Playwright"].ok and checks["Playwright"].blocking
    assert not checks["Clé OmniRoute"].ok and checks["Clé OmniRoute"].blocking
    assert doctor.render(list(checks.values()))[1] == 1
