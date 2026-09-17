"""Lancement des sous-processus agents (GUI) : environnement commun et sorties journalisées.

Avant : stdout/stderr envoyés vers DEVNULL, donc toute erreur d'un cycle lancé depuis la GUI
était invisible (audit M5). Chaque lancement écrit maintenant dans agents/data/logs/.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from . import config

KEEP_LOGS = 200


def agent_env() -> dict:
    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = config.api_key()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def logs_dir() -> Path:
    path = config.DATA_DIR / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prune(directory: Path, keep: int = KEEP_LOGS) -> None:
    logs = sorted(directory.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[keep:]:
        try:
            old.unlink()
        except OSError:
            pass  # journal encore ouvert par un processus : on reessaiera au prochain lancement


def spawn(args: list[str], kind: str) -> tuple[subprocess.Popen, Path]:
    """Lance `python -m agents.run <args>` ; sortie complète dans agents/data/logs/<date>-<kind>.log."""
    directory = logs_dir()
    _prune(directory)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", kind)[:40]
    log = directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}-{time.time_ns() % 1_000_000:06d}.log"
    with log.open("ab") as fh:
        proc = subprocess.Popen([config.PYTHON, "-m", "agents.run", *args], cwd=str(config.PROJECT_ROOT),
                                env=agent_env(), stdout=fh, stderr=subprocess.STDOUT)
    return proc, log
