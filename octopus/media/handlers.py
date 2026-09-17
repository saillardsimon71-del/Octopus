"""Tâche `media.video_generate` : génération vidéo locale (WanGP) dans la file OCTOPUS.

Entrée : {"prompt": "...", "preset"?: brouillon|standard|qualite|h3, "model_type"?, "settings"?: {...réglages WanGP...}, "duration_s"?, "resolution"?,
          "seed"?, "variants"?: 1-8, "business"?, "parent_id"?, "tags"?, "allow_with_webui"?, "timeout_s"?}
Sortie : identifiants de la bibliothèque et fichiers produits.

Toutes les variantes partent dans un seul lancement du pont : le modèle n'est chargé qu'une fois.
"""
from __future__ import annotations

import os
import random
import re
import shutil
import time
from pathlib import Path

from ..worker import TaskCancelled, handler
from . import library, perf, presets, prompts, requirements, wangp

MODEL_PREFERENCE = ("minimax_h3_fl2va_pruned", "minimax_h3_fl2va", "minimax_h3_vdn_pruned", "minimax_h3_vdn")
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
MAX_VARIANTS = 8
_PROMPT_INDEX = re.compile(r"Prompt\s+(\d+)\s*/\s*(\d+)", re.I)


def default_model() -> str:
    env = os.environ.get("OCTOPUS_VIDEO_MODEL", "").strip()
    if env:
        return env
    probe = wangp.cached_probe() or {}
    available = {m["model_type"] for m in probe.get("models", []) if m.get("availability") == "available"}
    for model in MODEL_PREFERENCE:
        if model in available:
            return model
    return MODEL_PREFERENCE[1]


def build_settings(model_type: str, prompt: str, *, settings: dict | None = None, duration_s: float | None = None,
                   resolution: str | None = None, seed: int | None = None) -> dict:
    """Réglages par défaut du modèle (diagnostic en cache), puis choix de la demande."""
    probe = wangp.cached_probe() or {}
    base = dict(probe.get("defaults", {}).get(model_type) or {})
    base.pop("_error", None)
    merged = {**base, **(settings or {}), "model_type": model_type, "prompt": prompts.prepare(model_type, prompt)}
    if duration_s:
        merged["video_length"] = f"{float(duration_s):g}s"  # l'API WanGP convertit en nombre d'images valide
    if resolution:
        if not re.fullmatch(r"\d{3,4}x\d{3,4}", resolution):
            raise ValueError(f"résolution invalide : {resolution!r} (attendu LARGEURxHAUTEUR)")
        merged["resolution"] = resolution
    if seed is not None:
        merged["seed"] = int(seed)
    return merged


def _assign_outputs(result: dict, count: int) -> dict[int, str | None]:
    """Fichier vidéo de chaque variante, dans l'ordre des tâches réussies."""
    failed = {int(e["task_index"]) - 1 for e in result.get("errors", []) if e.get("task_index")}
    files = [f for f in result.get("generated_files", []) if Path(f).suffix.lower() in VIDEO_EXTENSIONS]
    assignment, it = {}, iter(files)
    for index in range(count):
        assignment[index] = None if index in failed else next(it, None)
    return assignment


def _record_perf(ctx, workdir: Path) -> None:
    """Mesure réelle du lancement (même annulé) : sert aux estimations de durée ; ne fait jamais échouer la tâche."""
    try:
        measured = perf.record(workdir)
        if measured and measured.get("sec_per_step"):
            ctx.emit("media.performance", {k: measured.get(k) for k in (
                "model_type", "width", "height", "frames", "steps", "passes", "sec_per_step", "prepare_s", "total_s")})
    except Exception as exc:  # noqa: BLE001 - la mesure est accessoire
        ctx.emit("media.performance_error", {"error": f"{type(exc).__name__}: {exc}"})


@handler("media.video_generate", resource="gpu", max_attempts=2, retry_delay_s=300)
def video_generate(ctx):
    inp = presets.apply(ctx.input)
    prompt = str(inp.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("prompt vide")
    variants = max(1, min(MAX_VARIANTS, int(inp.get("variants", 1))))
    install = wangp.discover()
    if not install.ok:
        raise wangp.WanGPError("; ".join(install.problems))
    busy = wangp.running_instances()
    if busy and not inp.get("allow_with_webui"):
        pids = ", ".join(str(b["pid"]) for b in busy)
        raise wangp.WanGPBusy(f"WanGP est déjà lancé (PID {pids}, interface Pinokio ?) : l'arrêter avant une génération "
                              "automatique, sinon deux copies du modèle se disputent la mémoire "
                              "(ou relancer avec allow_with_webui=true)")
    model_type = inp.get("model_type") or default_model()
    check = requirements.preflight(model_type, (wangp.cached_probe() or {}).get("hardware"), seconds=inp.get("duration_s"))
    if check["level"] == "blocked" and not inp.get("force"):
        raise wangp.HardwareInsufficient(requirements.describe(check) + " (relancer avec force=true pour essayer quand même)")
    if check["level"] in ("warning", "blocked"):
        ctx.emit("media.hardware_warning", check)
    business = inp.get("business") or ctx.business
    base_seed = inp.get("seed")
    if base_seed is None and variants > 1:
        base_seed = random.randint(0, 2**31 - 1024)
    manifest = [build_settings(model_type, prompt, settings=inp.get("settings"), duration_s=inp.get("duration_s"),
                               resolution=inp.get("resolution"),
                               seed=None if base_seed is None else int(base_seed) + i) for i in range(variants)]

    def create_records():
        batch = f"task-{ctx.id}"
        return [library.create(business, wangp.PROVIDER, model_type, prompt, settings, task_id=ctx.id,
                               parent_id=inp.get("parent_id"), batch_key=batch, variant=i, tags=inp.get("tags"))
                for i, settings in enumerate(manifest)]

    gen_ids = ctx.memo("generations", create_records)
    for gen_id in gen_ids:
        library.update(gen_id, status="running", phase="starting", progress=0, error=None)
    workdir = library.media_root() / f"task-{ctx.id:06d}"
    last_write = {"t": 0.0, "pct": -1, "index": 0}

    def on_event(event: dict):
        kind = event.get("kind")
        if kind == "progress":
            match = _PROMPT_INDEX.search(event.get("status") or "")
            index = int(match.group(1)) - 1 if match else 0
            index = min(max(index, 0), variants - 1)
            pct = int(event.get("progress") or 0)
            now = time.time()
            if now - last_write["t"] >= 1.0 or abs(pct - last_write["pct"]) >= 5:
                if index != last_write["index"]:
                    for done in range(index):  # variantes précédentes terminées côté génération
                        library.update(gen_ids[done], progress=100, phase="saving")
                    for waiting in range(index + 1, variants):
                        library.update(gen_ids[waiting], phase="waiting", status_text="en attente de la variante précédente")
                library.update(gen_ids[index], phase=event.get("phase"), progress=pct,
                               status_text=(event.get("status") or "")[:200])
                last_write.update(t=now, pct=pct, index=index)
        elif kind == "download":
            now = time.time()
            if now - last_write["t"] >= 5:
                gb, rate = event.get("bytes", 0) / 2**30, event.get("rate_bps", 0) / 2**20
                for gen_id in gen_ids:
                    library.update(gen_id, phase="downloading_model",
                                   status_text=f"téléchargement {event.get('file') or 'des poids'} : {gb:.2f} Go ({rate:.0f} Mo/s)")
                last_write["t"] = now
        elif kind == "status":
            for gen_id in gen_ids[last_write["index"]:]:
                library.update(gen_id, status_text=str(event.get("text", ""))[:200])
        elif kind == "preview":
            library.update(gen_ids[last_write["index"]], preview_path=str(workdir / "preview.jpg"))
        elif kind in ("session_ready", "bridge_started"):
            for gen_id in gen_ids:
                library.update(gen_id, phase="loading_model" if kind == "session_ready" else "starting")
            if kind == "bridge_started":
                ctx.emit("media.bridge_started", {"hardware": event.get("hardware")})
        elif kind == "error":
            ctx.emit("media.error", {"message": event.get("message"), "stage": event.get("stage")})

    try:
        result = wangp.run_bridge(install, manifest, workdir, on_event=on_event, should_cancel=ctx.cancelled,
                                  timeout_s=float(inp.get("timeout_s", 3 * 3600)))
    except wangp.WanGPCancelled as exc:
        for gen_id in gen_ids:
            library.update(gen_id, status="cancelled", phase="cancelled")
        raise TaskCancelled(str(exc)) from exc
    finally:
        _record_perf(ctx, workdir)
    outputs = _assign_outputs(result, variants)
    errors = "; ".join(e.get("message", "") for e in result.get("errors", []))[:1000]
    produced = []
    per_variant_seconds = round(result.get("wall_seconds", 0) / variants, 1)
    for index, gen_id in enumerate(gen_ids):
        source = outputs.get(index)
        if not source or not Path(source).exists():
            library.update(gen_id, status="failed", phase="failed", error=errors or "aucun fichier produit")
            continue
        target = library.generation_dir(gen_id) / f"{library.slug(prompt)}-v{index + 1}{Path(source).suffix}"
        shutil.move(source, target)
        meta = library.probe_media(target)
        library.update(gen_id, status="done", phase="done", progress=100, output_path=str(target),
                       generation_seconds=per_variant_seconds, error=None, **meta)
        produced.append({"generation_id": gen_id, "path": str(target), **meta})
    ctx.emit("media.video_generated", {"count": len(produced), "model_type": model_type,
                                       "seconds": result.get("wall_seconds")})
    if not produced:
        raise wangp.WanGPError(f"aucune vidéo produite : {errors or 'voir ' + str(workdir / 'bridge.log')}")
    return {"model_type": model_type, "generations": gen_ids, "videos": produced,
            "failed": variants - len(produced), "seconds": result.get("wall_seconds")}
