"""Local control diagnostics do not depend on the removed video engine."""
from __future__ import annotations

import sys

import pytest

from agents import config, doctor


@pytest.mark.parametrize("legacy_mode", ["local", "cloud"])
def test_doctor_requires_control_dependencies_only(isolated, monkeypatch, legacy_mode):
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", legacy_mode)
    monkeypatch.delenv("OCTOPUS_PROFILE", raising=False)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object() if name == "playwright" else None)
    monkeypatch.setattr(doctor, "_chromium_executable", lambda: sys.executable)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    from octopus import llm
    original_secret = llm.secret
    monkeypatch.setattr(llm, "secret", lambda name: "" if name == "DEEPSEEK_API_KEY" else original_secret(name))
    monkeypatch.setattr(config, "api_key", lambda: "")
    checks = {c.name: c for c in doctor.run_checks()}
    assert checks["Python contrôle"].ok
    assert checks["Playwright"].ok
    assert checks["Chromium Playwright"].ok
    assert checks["Catalogue OpenRouter"].ok
    assert checks["Journal OCTOPUS"].ok
    assert not checks["Cle DeepSeek"].ok and not checks["Cle DeepSeek"].blocking
    assert not any(word in name.lower() for name in checks
                   for word in ("runpod", "remotion", "chatterbox", "tts", "ffmpeg", "npx"))
    text, code = doctor.render(list(checks.values()))
    assert code == 0 and "Contrôle local prêt." in text


def test_missing_control_dependencies_still_block(isolated, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    checks = {c.name: c for c in doctor.run_checks()}
    assert not checks["Playwright"].ok and checks["Playwright"].blocking
    assert not checks["Cle OpenRouter"].ok and checks["Cle OpenRouter"].blocking
    assert doctor.render(list(checks.values()))[1] == 1
