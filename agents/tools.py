"""Outils exposés aux agents : shell en liste blanche + briques du pipeline vidéo.

Garde-fous :
- Shell limité à la liste blanche `SHELL_WHITELIST` (aucun drapeau destructif).
- Les briques haut niveau (render, qc) encapsulent les commandes exactes.
- Rien de sortant (upload/email/achat) : tout est `--dry-run` par défaut.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config


def run_shell(cmd: list[str], cwd: str | None = None) -> str:
    """Exécute une commande de la liste blanche. Refuse tout le reste."""
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
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd or str(config.PROJECT_ROOT))
    return (r.stdout or "") + (r.stderr or "")


def make_audio(job_json: str, offer_id: str) -> str:
    """Génère VO Chatterbox + captions + mix (Phase 3)."""
    out = run_shell([
        config.PYTHON, "tools/make_audio_chatterbox_full.py",
        job_json, offer_id, config.CHATTERBOX_VOICE, "0.6", "0.4",
    ])
    return out


def remotion_render(offer_id: str) -> str:
    """Rend le composant CashShort (lit captions.ts/job.ts générés)."""
    remotion_dir = config.PROJECT_ROOT / "remotion"
    npx = "npx.cmd"
    out = subprocess.run(
        [npx, "remotion", "render", "CashShort",
         f"../out/{offer_id}/video.mp4"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(remotion_dir),
    )
    return (out.stdout or "") + (out.stderr or "")


def mux(offer_id: str) -> str:
    """Mux vidéo + mix audio avec gain linéaire (préserve le LRA)."""
    out_dir = config.PROJECT_ROOT / "out" / offer_id
    mix = out_dir / "audio" / "mix.wav"
    video = out_dir / "video.mp4"
    final = out_dir / "final.mp4"
    # mesure LUFS puis gain linéaire
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(mix), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    import re
    mm = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", probe)
    if not mm:
        raise ValueError("LUFS introuvable")
    lufs = float(mm[-1])
    gain = round(-14 - lufs, 2)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(mix),
         "-af", f"volume={gain}dB", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(final)],
        check=True)
    return f"mux ok gain={gain}dB -> {final}"


def qc_metrics(offer_id: str) -> dict:
    """Métriques ffmpeg + 6 frames (écrit qc_metrics.json)."""
    out_dir = config.PROJECT_ROOT / "out" / offer_id
    final = out_dir / "final.mp4"
    run_shell([config.PYTHON, "tools/qc_metrics.py", str(final),
               str(out_dir), "--label", offer_id])
    return json.loads((out_dir / "qc_metrics.json").read_text(encoding="utf-8"))


def qc_vision(offer_id: str, narration: str) -> dict:
    """QC vision DeepSeek (écrit qc_vision.json)."""
    out_dir = config.PROJECT_ROOT / "out" / offer_id
    frames_dir = out_dir / "frames"
    out_json = out_dir / "qc_vision.json"
    run_shell([config.PYTHON, "tools/qc_vision.py", str(frames_dir),
               narration, str(out_json), "--duree", "26"])
    return json.loads(out_json.read_text(encoding="utf-8"))
