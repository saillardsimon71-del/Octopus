"""Outils shell partagés : liste blanche, timeout, annulation et journal des erreurs."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from . import cancel, config

STEP_TIMEOUT_S = 900


class StepError(RuntimeError):
    """Étape de production en échec : code retour, timeout ou artefact manquant/périmé."""


def _run_checked(cmd: list[str], cwd: str, timeout: int = STEP_TIMEOUT_S, log: Path | None = None) -> str:
    started = time.time()
    name = Path(cmd[0]).name
    group = {"start_new_session": True} if os.name != "nt" else {}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", cwd=cwd, **group)
    except OSError as e:
        raise StepError(f"lancement impossible de {name} : {e}") from e
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            stopped = cancel.requested()
            if not stopped and time.time() - started < timeout:
                continue
            kill_tree(proc)
            stdout, stderr = _drain(proc)
            _write_log(log, cmd, None, time.time() - started, stdout + stderr)
            if stopped:
                raise cancel.Cancelled(f"arrêt demandé par l'humain pendant {name}") from None
            raise StepError(f"timeout {timeout} s : {name}") from None
    out = (stdout or "") + (stderr or "")
    _write_log(log, cmd, proc.returncode, time.time() - started, out)
    if proc.returncode != 0:
        raise StepError(f"{name} code {proc.returncode} : {out[-800:].strip()}")
    return out


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _drain(proc: subprocess.Popen) -> tuple[str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=10)
        return stdout or "", stderr or ""
    except (subprocess.TimeoutExpired, ValueError):
        return "", ""


def _write_log(log: Path | None, cmd: list[str], code: int | None, seconds: float, output: str) -> None:
    if log is None:
        return
    log.parent.mkdir(parents=True, exist_ok=True)
    status = "timeout" if code is None else f"code {code}"
    log.write_text(f"$ {' '.join(map(str, cmd))}\n# {status}, {seconds:.1f} s\n\n{output}", encoding="utf-8")


def require_fresh(paths: list[Path], since: float) -> None:
    for p in paths:
        if not p.exists() or p.stat().st_size == 0 or p.stat().st_mtime < since - 1:
            raise StepError(f"artefact absent ou périmé : {p}")


def run_shell(cmd: list[str], cwd: str | None = None, timeout: int = STEP_TIMEOUT_S,
              log: Path | None = None) -> str:
    if not cmd:
        raise ValueError("commande vide")
    exe = Path(cmd[0]).name.lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if exe not in config.SHELL_WHITELIST:
        raise ValueError(f"commande refusée (hors liste blanche): {exe}")
    joined = " ".join(cmd).lower()
    for bad in config.FORBIDDEN_ARGS:
        if bad in joined:
            raise ValueError(f"commande refusée (argument interdit '{bad}'): {joined}")
    return _run_checked(cmd, cwd or str(config.PROJECT_ROOT), timeout, log)
