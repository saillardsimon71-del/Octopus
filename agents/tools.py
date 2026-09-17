"""Outils exposés aux agents : shell en liste blanche + briques du pipeline vidéo.

Garde-fous :
- Shell limité à la liste blanche `SHELL_WHITELIST` (aucun drapeau destructif).
- Les briques haut niveau (render, qc) encapsulent les commandes exactes.
- Rien de sortant (upload/email/achat) : tout est `--dry-run` par défaut.
- En mode cloud, FORGE garde sa séquence historique mais une seule étape déclenche le
  rendu distant ; les étapes suivantes vérifient/rematérialisent les artefacts reçus.
"""
from __future__ import annotations

import json
import os
import re
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


def step_log(offer_id: str, step: str) -> Path:
    return config.PROJECT_ROOT / "out" / offer_id / "logs" / f"{step}.log"


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


def _cloud_mode() -> bool:
    return os.environ.get("PODALUX_VIDEO_RENDERER", "local").strip().lower() == "cloud"


def _cloud_service():
    from octopus.video.renderers import get_renderer
    from octopus.video.service import VideoService
    return VideoService(cloud_renderer=get_renderer(mode="cloud"), mode="cloud")


def make_audio(job_json: str, offer_id: str) -> str:
    """En local, génère l'audio. En cloud, déclenche la production distante une seule fois.

    FORGE appelle cette fonction avant Remotion ; le shim cloud rematérialise ensuite
    les artefacts produits par le worker afin que les contrôles historiques restent valides.
    """
    if _cloud_mode():
        job = json.loads(Path(job_json).read_text(encoding="utf-8"))
        metrics = _cloud_service().render(offer_id, job)
        return json.dumps({"mode": "cloud", "job_id": metrics.get("cloud_job_id"),
                           "video_url": metrics.get("cloud_video_url")}, ensure_ascii=False)
    return run_shell([
        config.PYTHON, "tools/make_audio_chatterbox_full.py",
        job_json, offer_id, config.CHATTERBOX_VOICE, "0.6", "0.4",
    ], log=step_log(offer_id, "audio"))


def remotion_render(offer_id: str) -> str:
    """Rend CashShort localement ou valide le rendu déjà rematérialisé du cloud."""
    if _cloud_mode():
        video = config.PROJECT_ROOT / "out" / offer_id / "final.mp4"
        if not video.is_file() or video.stat().st_size <= 0:
            raise StepError(f"rendu cloud absent : {video}")
        return f"rendu cloud déjà disponible : {video}"
    npx = "npx.cmd" if os.name == "nt" else "npx"
    return _run_checked([npx, "remotion", "render", "CashShort", f"../out/{offer_id}/video.mp4"],
                        str(config.PROJECT_ROOT / "remotion"), log=step_log(offer_id, "render"))


def mux(offer_id: str) -> str:
    """Mux local historique ; en cloud, final.mp4 est déjà muxé et QC-mesuré."""
    if _cloud_mode():
        final = config.PROJECT_ROOT / "out" / offer_id / "final.mp4"
        if not final.is_file() or final.stat().st_size <= 0:
            raise StepError(f"final cloud absent : {final}")
        return f"mux cloud déjà disponible : {final}"
    out_dir = config.PROJECT_ROOT / "out" / offer_id
    mix = out_dir / "audio" / "mix.wav"
    video = out_dir / "video.mp4"
    final = out_dir / "final.mp4"
    cwd = str(config.PROJECT_ROOT)
    probe = _run_checked(["ffmpeg", "-hide_banner", "-i", str(mix), "-af", "ebur128", "-f", "null", "-"],
                         cwd, log=step_log(offer_id, "mux_lufs"))
    mm = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", probe)
    if not mm:
        raise StepError(f"LUFS introuvable dans la mesure de {mix}")
    lufs = float(mm[-1])
    gain = round(-14 - lufs, 2)
    _run_checked(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(mix),
                  "-af", f"volume={gain}dB", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                  "-shortest", str(final)], cwd, log=step_log(offer_id, "mux"))
    return f"mux ok gain={gain}dB -> {final}"


def qc_metrics(offer_id: str) -> dict:
    """Métriques ffmpeg + 6 frames. Le cloud renvoie déjà le résultat QC du worker."""
    out_dir = config.PROJECT_ROOT / "out" / offer_id
    final = out_dir / "final.mp4"
    if _cloud_mode():
        path = out_dir / "qc_metrics.json"
        if not path.is_file() or path.stat().st_size <= 0:
            raise StepError(f"QC cloud absent : {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    run_shell([config.PYTHON, "tools/qc_metrics.py", str(final),
               str(out_dir), "--label", offer_id], log=step_log(offer_id, "qc_metrics"))
    return json.loads((out_dir / "qc_metrics.json").read_text(encoding="utf-8"))


def qc_vision(offer_id: str, narration: str) -> dict:
    """QC vision DeepSeek (écrit qc_vision.json)."""
    out_dir = config.PROJECT_ROOT / "out" / offer_id
    frames_dir = out_dir / "frames"
    out_json = out_dir / "qc_vision.json"
    run_shell([config.PYTHON, "tools/qc_vision.py", str(frames_dir),
               narration, str(out_json), "--duree", "26"])
    return json.loads(out_json.read_text(encoding="utf-8"))
