"""Performances mesurées des générations vidéo : ce que la machine fait vraiment, et combien de temps prévoir.

Chaque lancement du pont laisse un dossier data/media/video/task-NNNNNN/ (job.json, events.jsonl, result.json).
`analyze` en tire les durées réelles : démarrage du runtime, téléchargements, chargement + encodage du prompt,
secondes par étape de débruitage, total. `record` les range dans la table media_runs (une ligne par dossier),
y compris pour une génération annulée ou échouée dès qu'au moins deux étapes ont été chronométrées.

`estimate` prévoit la durée d'une demande à partir de la mesure la plus proche sur le même GPU et la même
architecture. Le coût d'une étape est ramené au nombre de jetons latents (images × surface / 16²) à la
puissance TOKEN_EXPONENT, multiplié par 2 quand le guidage CFG est actif. Si la demande diffère de la mesure
(résolution, durée, CFG), l'estimation est marquée « extrapolée » : c'est un ordre de grandeur, pas une mesure.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from pathlib import Path

from .. import journal
from . import library

TOKEN_EXPONENT = 1.3  # entre linéaire (couches denses) et quadratique (attention) ; non calibré par modèle
FPS_BY_PREFIX = (("minimax_h3", 24), ("ltx2", 24), ("ltxv", 30), ("hunyuan", 24))
DEFAULT_FPS = 16  # famille Wan 2.1 / 2.2 1.3B-14B
# Modèles affinés qui partagent l'architecture (donc le coût par étape) d'un modèle de base.
ARCHITECTURE_ALIASES = {"t2v_nexus_1.3B": "t2v_1.3B"}
COLUMNS = ("created_at", "task_id", "workdir", "provider", "model_type", "architecture", "width", "height", "frames",
           "steps", "passes", "variants", "gpu", "vram_gb", "exit_code", "session_s", "download_s", "download_gb",
           "prepare_s", "sec_per_step", "steps_timed", "inference_s", "total_s", "finished", "data")
_PROMPT = re.compile(r"Prompt\s+(\d+)\s*/\s*(\d+)", re.I)
_TASK_DIR = re.compile(r"task-(\d+)$")


def architecture(model_type: str) -> str:
    return ARCHITECTURE_ALIASES.get(model_type, model_type)


def fps_for(model_type: str) -> int:
    return next((fps for prefix, fps in FPS_BY_PREFIX if model_type.startswith(prefix)), DEFAULT_FPS)


def frames_for(model_type: str, video_length) -> int | None:
    """Nombre d'images : entier WanGP, ou « Ns » converti comme WanGP (4n+1 pour Wan)."""
    if isinstance(video_length, (int, float)) and not isinstance(video_length, bool):
        return int(video_length)
    if isinstance(video_length, str):
        text = video_length.strip().lower()
        if text.endswith("s"):
            try:
                frames = round(float(text[:-1]) * fps_for(model_type))
            except ValueError:
                return None
            return (frames // 4) * 4 + 1 if fps_for(model_type) == DEFAULT_FPS else frames
        if text.isdigit():
            return int(text)
    return None


def latent_tokens(width: int, height: int, frames: int) -> float:
    return ((max(frames, 1) - 1) // 4 + 1) * (width / 16) * (height / 16)


def passes_for(settings: dict) -> int:
    """2 évaluations du modèle par étape avec guidage CFG, 1 sans (modèles distillés, guidance_scale = 1)."""
    guidance = settings.get("guidance_scale")
    try:
        return 1 if guidance is not None and float(guidance) == 1.0 else 2
    except (TypeError, ValueError):
        return 2


def _size(resolution) -> tuple[int | None, int | None]:
    match = re.fullmatch(r"(\d{2,4})x(\d{2,4})", str(resolution or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _read_events(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue  # ligne en cours d'écriture
    return events


def analyze(workdir: Path) -> dict | None:
    """Durées réelles d'un lancement du pont. None si le dossier n'a pas d'événements exploitables."""
    workdir = Path(workdir)
    events = _read_events(workdir / "events.jsonl")
    if not events:
        return None
    try:
        manifest = json.loads((workdir / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = []
    first = manifest[0] if manifest else {}
    model_type = str(first.get("model_type") or "")
    width, height = _size(first.get("resolution"))
    started = next((e for e in events if e.get("kind") == "bridge_started"), events[0])
    t0 = float(started.get("t", 0))
    hardware = started.get("hardware") or {}
    gpu = hardware.get("gpu") or {}
    ready = next((e for e in events if e.get("kind") == "session_ready"), None)
    finished = next((e for e in events if e.get("kind") == "bridge_finished"), None)

    downloads = [e for e in events if e.get("kind") == "download"]
    by_file: dict[str, int] = {}
    for e in downloads:
        by_file[str(e.get("file"))] = max(by_file.get(str(e.get("file")), 0), int(e.get("bytes") or 0))
    download_s = (downloads[-1]["t"] - downloads[0]["t"]) if len(downloads) > 1 else 0.0

    seen: dict[int, dict[int, float]] = {}  # prompt -> étape -> première apparition
    total_steps = None
    for e in events:
        if e.get("kind") != "progress" or e.get("step") is None or not e.get("total"):
            continue
        if str(e.get("phase") or "").lower() not in ("inference", "denoising") and \
                "denois" not in str(e.get("status") or "").lower():
            continue
        match = _PROMPT.search(str(e.get("status") or ""))
        index = int(match.group(1)) if match else 1
        total_steps = int(e["total"])
        seen.setdefault(index, {}).setdefault(int(e["step"]), float(e["t"]))
    deltas, inference_s, first_inference = [], 0.0, None
    for steps in seen.values():
        ordered = sorted(steps.items())
        first_inference = min(first_inference or ordered[0][1], ordered[0][1])
        for (s1, t1), (s2, t2) in zip(ordered, ordered[1:]):
            if s2 > s1 and t2 >= t1:
                deltas.append((t2 - t1) / (s2 - s1))
        inference_s += ordered[-1][1] - ordered[0][1]
    ready_t = (t0 + float(ready.get("seconds", 0))) if ready else None
    prepare_s = None
    if first_inference is not None and ready_t is not None:
        prepare_s = max(0.0, first_inference - ready_t - download_s)
    end_t = (t0 + float(finished["seconds"])) if finished else float(events[-1].get("t", t0))
    settings = {**first}
    return {
        "task_id": int(_TASK_DIR.search(workdir.name).group(1)) if _TASK_DIR.search(workdir.name) else None,
        "workdir": str(workdir), "provider": "wangp", "model_type": model_type, "architecture": architecture(model_type),
        "width": width, "height": height, "frames": frames_for(model_type, first.get("video_length")),
        "steps": total_steps or first.get("num_inference_steps"), "passes": passes_for(settings),
        "variants": len(manifest) or 1, "gpu": gpu.get("name"), "vram_gb": gpu.get("vram_total_gb"),
        "exit_code": finished.get("code") if finished else None,
        "session_s": ready.get("seconds") if ready else None,
        "download_s": round(download_s, 1), "download_gb": round(sum(by_file.values()) / 2**30, 2),
        "prepare_s": None if prepare_s is None else round(prepare_s, 1),
        "sec_per_step": round(statistics.median(deltas), 2) if deltas else None, "steps_timed": len(deltas),
        "inference_s": round(inference_s, 1), "total_s": round(end_t - t0, 1), "finished": finished is not None,
        "created_at": t0,
    }


def record(workdir: Path) -> dict | None:
    """Analyse et enregistre (ou met à jour) la mesure d'un dossier de lancement terminé."""
    data = analyze(workdir)
    if not data or not data["finished"]:
        return data
    row = {k: data.get(k) for k in COLUMNS if k != "data"}
    row["finished"] = int(bool(row["finished"]))
    row["data"] = json.dumps(data, ensure_ascii=False)
    conn = journal.connect()
    try:
        cols = list(row)
        conn.execute(f"INSERT OR REPLACE INTO media_runs ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                     [row[c] for c in cols])
        conn.commit()
    finally:
        conn.close()
    return data


def backfill() -> int:
    """Enregistre les dossiers de lancement terminés qui ne sont pas encore mesurés (générations passées)."""
    known = {r["workdir"] for r in journal.query("SELECT workdir FROM media_runs")}
    count = 0
    for workdir in sorted(library.media_root().glob("task-*")):
        if str(workdir) not in known and (workdir / "events.jsonl").exists():
            count += 1 if (record(workdir) or {}).get("finished") else 0
    return count


def runs(limit: int = 30) -> list[dict]:
    return [dict(r) for r in journal.query("SELECT * FROM media_runs ORDER BY created_at DESC LIMIT ?", (limit,))]


def _work(width: int, height: int, frames: int, passes: int) -> float:
    return passes * latent_tokens(width, height, frames) ** TOKEN_EXPONENT


def estimate(model_type: str, *, resolution: str | None, frames: int | None, steps: int | None, passes: int,
             variants: int = 1, gpu: str | None = None) -> dict | None:
    """Durée prévue d'une génération d'après les mesures de cette machine. None sans mesure comparable."""
    width, height = _size(resolution)
    arch = architecture(model_type)
    candidates = [r for r in runs(200) if r["architecture"] == arch and r["sec_per_step"]
                  and r["width"] and r["height"] and r["frames"] and (gpu is None or r["gpu"] == gpu)]
    if not candidates or not (width and height and frames and steps):
        return None
    target = _work(width, height, frames, passes)
    reference = min(candidates, key=lambda r: abs(1 - _work(r["width"], r["height"], r["frames"], r["passes"] or 2) / target))
    ratio = target / _work(reference["width"], reference["height"], reference["frames"], reference["passes"] or 2)
    sec_per_step = reference["sec_per_step"] * ratio
    prepare = [r["prepare_s"] for r in candidates if r["prepare_s"] is not None]
    sessions = [r["session_s"] for r in candidates if r["session_s"] is not None]
    fixed = (min(sessions) if sessions else 0) + (min(prepare) if prepare else 0)  # min : écarte les téléchargements
    seconds = fixed + sec_per_step * steps * max(1, variants)
    downloaded = any(r["model_type"] == model_type and r["finished"] and r["exit_code"] == 0 for r in candidates)
    return {"seconds": round(seconds), "sec_per_step": round(sec_per_step, 1), "fixed_s": round(fixed),
            "basis": "mesuré" if abs(ratio - 1) <= 0.1 else "extrapolé", "reference_task": reference["task_id"],
            "reference": f"{reference['model_type']} {reference['width']}x{reference['height']} "
                         f"{reference['frames']} images, {reference['sec_per_step']:g} s/étape"
                         f"{'' if (reference['passes'] or 2) == 1 else ' avec CFG'}",
            "first_download_possible": not downloaded}


def describe(est: dict | None) -> str:
    if not est:
        return "durée inconnue (aucune mesure comparable sur cette machine)"
    minutes = est["seconds"] / 60
    text = f"≈ {minutes:.0f} min" if minutes >= 2 else f"≈ {est['seconds']} s"
    text += f" ({est['basis']} d'après la tâche #{est['reference_task']} : {est['reference']})"
    if est["first_download_possible"]:
        text += " + téléchargement du modèle au premier lancement"
    return text
