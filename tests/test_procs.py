"""Sous-processus lances par la GUI : sorties conservees dans agents/data/logs (audit M5)."""
from __future__ import annotations

import sys

from agents import config, procs


def test_spawn_writes_output_and_errors_to_a_log(isolated, monkeypatch):
    package = isolated / "agents"
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "run.py").write_text(
        "import os, sys\nprint('args', sys.argv[1:], os.environ['PYTHONUTF8'])\n"
        "sys.stderr.write('Traceback: boum')\nsys.exit(2)\n", encoding="utf-8")
    monkeypatch.setattr(config, "PYTHON", sys.executable)
    monkeypatch.setattr(config, "api_key", lambda: "k")
    proc, log = procs.spawn(["msg", "ORBIT", "bonjour à tous"], "msg-ORBIT")
    assert proc.wait(timeout=60) == 2
    text = log.read_text(encoding="utf-8")
    assert "args ['msg', 'ORBIT', 'bonjour à tous'] 1" in text and "Traceback: boum" in text
    assert log.parent == config.DATA_DIR / "logs" and "msg-ORBIT" in log.name


def test_old_logs_are_pruned(isolated):
    directory = procs.logs_dir()
    for i in range(procs.KEEP_LOGS + 5):
        (directory / f"old-{i:03d}.log").write_text("x", encoding="utf-8")
    procs._prune(directory)
    assert len(list(directory.glob("*.log"))) == procs.KEEP_LOGS
