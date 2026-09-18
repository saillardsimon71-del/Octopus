"""Tâche `media.video_generate` : WanGP local pour les petits modèles, MiniMax H3 cloud.

Entrée : {"prompt": "...", "preset"?: brouillon|standard|qualite|h3, "model_type"?, "settings"?: {...réglages WanGP...}, "duration_s"?, "resolution"?,
          "seed"?, "variants"?: 1-8, "business"?, "parent_id"?, "tags"?, "allow_with_webui"?, "timeout_s"?}
Sortie : identifiants de la bibliothèque et fichiers produits.

MiniMax H3 ne passe jamais par la détection WanGP locale : le modèle est généré sur un endpoint
RunPod Serverless configuré par environnement, puis le fichier final est téléchargé dans la
bibliothèque locale pour les étapes OCTOPUS suivantes.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import time
from pathlib import Path

from ..worker import TaskCancelled, handler
from . import library, perf, presets, prompts, requirements, wangp
from .minimax_h3_cloud import MiniMaxH3CloudError, MiniMaxH3Config, MiniMaxH3RunPodClient, save_result
from ..journal import current_run
from ..video.state import AmbiguousSubmissionError

MODEL_PREFERENCE = ("t2v_nexus_1.3B", "t2v_1.3B")
H3_MODEL_MARKERS = ("minimax_h3", "minmax_h3")
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}
MAX_VARIANTS = 8
_PROMPT_INDEX = re.compile(r"Prompt\s+(\d+)\s*/\s*(\d+)", re.I)


def _is_h3(model_type: str) -> bool:
    return str(model_type or "").strip().lower().startswith(H3_MODEL_MARKERS)


def default_model() -> str:
    """Choisit un modèle réellement local ; MiniMax H3 n'est jamais auto-sélectionné ici."""
    env = os.environ.get("OCTOPUS_VIDEO_MODEL", "").strip()
    if env:
        return env
    probe = wangp.cached_probe() or {}
    available = {m["model_type"] for m in probe.get("models", []) if m.get("availability") == "available"}
    for model in MODEL_PREFERENCE:
        if model in available:
            return model
    return MODEL_PREFERENCE[0]


def build_settings(model_type: str, prompt: str, *, settings: dict | None = None, duration_s: float | None = None,
                   resolution: str | None = None, seed: int | None = None) -> dict:
    """Réglages par défaut du modèle (diagnostic en cache), puis choix de la demande."""
    probe = wangp.cached_probe() or {}
    base = dict(probe.get("defaults", {}).get(model_type) or {})
    base.pop("_error", None)
    merged = {**base, **(settings or {}), "model_type": model_type, "prompt": prompts.prepare(model_type, prompt)}
    if duration_s:
        merged["video_length"] = f"{float(duration_s):g}s"
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
    except Exception as exc:
        ctx.emit("media.performance_error", {"error": f"{type(exc).__name__}: {exc}"})


def _h3_dimensions(inp: dict) -> tuple[int, int]:
    raw = str(inp.get("resolution") or "768x1344")
    match = re.fullmatch(r"(\d{3,4})x(\d{3,4})", raw)
    if not match:
        raise ValueError(f"résolution H3 invalide : {raw!r}")
    return int(match.group(1)), int(match.group(2))


def _run_h3_cloud(ctx, inp: dict, prompt: str, variants: int, business: str):
    """Exécute H3 distant sans toucher à WanGP/VRAM locale."""
    client = MiniMaxH3RunPodClient(MiniMaxH3Config.from_env())
    width, height = _h3_dimensions(inp)
    duration_s = float(inp.get("duration_s") or 5)
    steps = int((inp.get("settings") or {}).get("steps", 8))
    base_seed = inp.get("seed")
    if base_seed is None:
        base_seed = random.randint(0, 2**31 - 1024)

    batch = f"task-{ctx.id}"
    generation_ids = [library.create(
        business, "runpod", str(inp.get("model_type")), prompt, {
            "backend": "runpod",
            "width": width,
            "height": height,
            "duration_s": duration_s,
            "steps": steps,
            "seed": int(base_seed) + i,
        }, task_id=ctx.id, parent_id=inp.get("parent_id"), batch_key=batch,
        variant=i, tags=inp.get("tags")
    ) for i in range(variants)]

    produced = []
    state_dir = Path(os.environ.get("OCTOPUS_VIDEO_H3_STATE_DIR", "" ).strip() or
                      (Path(os.environ.get("PODALUX_ROOT", Path.cwd())) / "out" / ".h3_cloud_state"))
    state_dir.mkdir(parents=True, exist_ok=True)

    for index, gen_id in enumerate(generation_ids):
        if ctx.cancelled():
            for pending in generation_ids[index:]:
                library.update(pending, status="cancelled", phase="cancelled")
            raise TaskCancelled("annulation demandée pendant H3 cloud")
        library.update(gen_id, status="running", phase="submitting", progress=0, error=None)
        job_key = f"task-{ctx.id}-v{index + 1}"
        state_path = state_dir / f"{job_key}.json"
        remote_id = None
        if state_path.is_file():
            try:
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                if saved.get("status") == "COMPLETED" and saved.get("path") and Path(saved["path"]).is_file():
                    target = Path(saved["path"])
                    meta = library.probe_media(target)
                    library.update(gen_id, status="done", phase="done", progress=100, output_path=str(target), error=None, **meta)
                    produced.append({"generation_id": gen_id, "path": str(target), **meta})
                    continue
                if saved.get("remote_id") and saved.get("status") in {"QUEUED", "RUNNING", "RENDERING"}:
                    remote_id = str(saved["remote_id"])
                elif saved.get("status") == "SUBMITTING" and not saved.get("remote_id"):
                    message = (f"soumission cloud ambiguë pour {job_key} (sans remote_id); "
                               "ne pas resoumettre automatiquement, vérifier le fournisseur puis réconcilier l'état local")
                    library.update(gen_id, status="failed", phase="ambiguous_submission", error=message)
                    raise AmbiguousSubmissionError(message)
            except (OSError, ValueError, json.JSONDecodeError):
                remote_id = None

        try:
            if remote_id is None:
                workflow = None
                # Reuse the client builder through a private stable method to keep the worker contract explicit.
                from .minimax_h3_cloud import build_t2v_workflow
                workflow = build_t2v_workflow(
                    prompts.prepare("minimax_h3_fl2va_pruned_cloud", prompt),
                    width=width, height=height, duration_s=duration_s,
                    seed=int(base_seed) + index, steps=steps,
                    output_prefix=f"octopus_{ctx.id}_{index + 1}",
                )
                # Appel payant : estimation fixée par l'humain + enveloppe de dépense, sinon aucune soumission.
                from octopus import economy
                economy.gate_paid_call(ctx.business, f"MiniMax H3 cloud {job_key}", estimate_env="OCTOPUS_H3_JOB_COST_ESTIMATE",
                                       requested_by="media.h3", experiment_id=inp.get("experiment_id"))
                state_path.write_text(json.dumps({"schema_version": "1", "remote_id": None, "status": "SUBMITTING"}), encoding="utf-8")
                try:
                    remote_id = client.submit(workflow)
                except Exception as exc:
                    message = (f"soumission cloud ambiguë pour {job_key} (sans remote_id après {type(exc).__name__}: {exc}); "
                               "ne pas resoumettre automatiquement, vérifier le fournisseur puis réconcilier l'état local")
                    library.update(gen_id, status="failed", phase="ambiguous_submission", error=message)
                    raise AmbiguousSubmissionError(message) from exc
                state_path.write_text(json.dumps({"schema_version": "1", "remote_id": remote_id, "status": "QUEUED"}), encoding="utf-8")
            library.update(gen_id, phase="rendering", progress=50, status_text=f"RunPod {remote_id}")
            result = client.wait(remote_id)
            target = library.generation_dir(gen_id) / f"{library.slug(prompt)}-h3-v{index + 1}.mp4"
            save_result(result, target)
            meta = library.probe_media(target)
            state_path.write_text(json.dumps({"schema_version": "1", "remote_id": remote_id,
                                               "status": "COMPLETED", "path": str(target)}), encoding="utf-8")
            library.update(gen_id, status="done", phase="done", progress=100, output_path=str(target), error=None, **meta)
            produced.append({"generation_id": gen_id, "path": str(target), "remote_id": remote_id, **meta})
        except AmbiguousSubmissionError:
            raise
        except MiniMaxH3CloudError as exc:
            library.update(gen_id, status="failed", phase="failed", error=str(exc))
            state_path.write_text(json.dumps({"schema_version": "1", "remote_id": remote_id, "status": "FAILED", "error": str(exc)}), encoding="utf-8")
        except Exception as exc:
            library.update(gen_id, status="failed", phase="failed", error=f"{type(exc).__name__}: {exc}")
            state_path.write_text(json.dumps({"schema_version": "1", "remote_id": remote_id, "status": "FAILED", "error": str(exc)}), encoding="utf-8")

    ctx.emit("media.video_generated", {"count": len(produced), "model_type": inp.get("model_type"),
                                       "backend": "runpod_minimax_h3"})
    if not produced:
        raise MiniMaxH3CloudError("aucune vidéo H3 produite")
    return {"model_type": inp.get("model_type"), "backend": "runpod_minimax_h3",
            "generations": generation_ids, "videos": produced,
            "failed": variants - len(produced)}


@handler("media.video_generate", resource="gpu", max_attempts=2, retry_delay_s=300)
def video_generate(ctx):
    inp = presets.apply(ctx.input)
    prompt = str(inp.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("prompt vide")
    variants = max(1, min(MAX_VARIANTS, int(inp.get("variants", 1))))
    model_type = inp.get("model_type") or default_model()
    business = inp.get("business") or ctx.business

    # MiniMax H3 : chemin cloud obligatoire, aucune découverte/preflight WanGP locale.
    if _is_h3(model_type):
        return _run_h3_cloud(ctx, inp, prompt, variants, business)

    install = wangp.discover()
    if not install.ok:
        raise wangp.WanGPError("; ".join(install.problems))
    busy = wangp.running_instances()
    if busy and not inp.get("allow_with_webui"):
        pids = ", ".join(str(b["pid"]) for b in busy)
        raise wangp.WanGPBusy(f"WanGP est déjà lancé (PID {pids}, interface Pinokio ?) : l'arrêter avant une génération "
                              "automatique, sinon deux copies du modèle se disputent la mémoire "
                              "(ou relancer avec allow_with_webui=true)")
    check = requirements.preflight(model_type, (wangp.cached_probe() or {}).get("hardware"), seconds=inp.get("duration_s"))
    if check["level"] == "blocked" and not inp.get("force"):
        raise wangp.HardwareInsufficient(requirements.describe(check) + " (relancer avec force=true pour essayer quand même)")
    if check["level"] in ("warning", "blocked"):
        ctx.emit("media.hardware_warning", check)
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
                    for done in range(index):
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
                                       "backend": "wangp_local", "seconds": result.get("wall_seconds")})
    if not produced:
        raise wangp.WanGPError(f"aucune vidéo produite : {errors or 'voir ' + str(workdir / 'bridge.log')}")
    return {"model_type": model_type, "generations": gen_ids, "videos": produced,
            "failed": variants - len(produced), "seconds": result.get("wall_seconds")}
