"""Diagnostic avant cycle reel."""
from __future__ import annotations

import sys

from agents import config, db, doctor


def test_doctor_reports_blocking_problems(isolated, monkeypatch):
    monkeypatch.setattr(config, "PYTHON", sys.executable)
    monkeypatch.setattr(config, "CHATTERBOX_URL", "http://127.0.0.1:9/v1/audio/speech")  # port ferme
    monkeypatch.setattr(config, "api_key", lambda: "")
    (isolated / "remotion").mkdir()
    (isolated / "remotion" / "remotion.config.ts").write_text(
        "Config.setBrowserExecutable(\n  'C:\\\\Program Files\\\\Edge\\\\msedge.exe'\n);", encoding="utf-8")
    db.acquire_run_lock("cycle-en-cours")
    checks = {c.name: c for c in doctor.run_checks()}
    assert checks["Python des outils"].ok
    assert not checks["Serveur Chatterbox"].ok and "démarrer" in checks["Serveur Chatterbox"].fix
    assert not checks["Clé DeepSeek"].ok and not checks["Remotion installé"].ok
    assert checks["Navigateur de rendu Remotion"].detail == "C:\\Program Files\\Edge\\msedge.exe"
    assert not checks["Verrou de production"].ok and not checks["Verrou de production"].blocking
    text, code = doctor.render(list(checks.values()))
    assert code == 1 and "problème(s) bloquant(s)" in text and "[KO ] Serveur Chatterbox" in text
