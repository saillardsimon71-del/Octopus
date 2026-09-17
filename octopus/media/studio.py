"""Logique du studio vidéo, indépendante de l'interface : formulaire -> tâche, historique, réutilisation."""
from __future__ import annotations

import re

from .. import tasks
from . import handlers, library, wangp

RESOLUTIONS = {
    "Short 9:16 (480x832)": "480x832",
    "Short 9:16 HD (720x1280)": "720x1280",
    "Paysage (832x480)": "832x480",
    "Paysage HD (1280x720)": "1280x720",
    "Carré (640x640)": "640x640",
    "Défaut du modèle": "",
}
BUSY_STATUSES = ("queued", "running")


class FormError(ValueError):
    pass


def model_choices() -> list[tuple[str, str]]:
    """(libellé, model_type) : modèles MiniMax H3 du dernier diagnostic, disponibles d'abord."""
    probe = wangp.cached_probe() or {}
    models = [m for m in probe.get("models", []) if str(m.get("model_type", "")).startswith("minimax_h3")]
    if not models:
        return [(f"{m} (diagnostic à lancer)", m) for m in handlers.MODEL_PREFERENCE]
    order = {"available": 0, "partial": 1}
    models.sort(key=lambda m: (order.get(m.get("availability"), 2), m["model_type"]))
    marks = {"available": "prêt", "partial": "incomplet"}
    return [(f"{m.get('name') or m['model_type']} [{marks.get(m.get('availability'), 'à télécharger')}]", m["model_type"])
            for m in models]


def form_to_input(form: dict) -> dict:
    """Valide le formulaire du studio et produit l'entrée de la tâche media.video_generate."""
    prompt = str(form.get("prompt", "")).strip()
    if len(prompt) < 3:
        raise FormError("prompt trop court")
    inp: dict = {"prompt": prompt, "model_type": form.get("model_type") or None, "business": form.get("business") or "studio"}
    for key, label, cast, low, high in (("duration_s", "durée", float, 1, 60), ("steps", "étapes", int, 1, 100),
                                        ("seed", "graine", int, 0, 2**31 - 1), ("variants", "variantes", int, 1, 8)):
        raw = str(form.get(key, "") or "").strip()
        if not raw:
            continue
        try:
            value = cast(raw.replace(",", "."))
        except ValueError:
            raise FormError(f"{label} invalide : {raw!r}") from None
        if not low <= value <= high:
            raise FormError(f"{label} hors limites ({low} à {high})")
        inp[key] = value
    steps = inp.pop("steps", None)
    if steps:
        inp["settings"] = {"num_inference_steps": steps}
    resolution = RESOLUTIONS.get(form.get("resolution", ""), form.get("resolution", ""))
    if resolution:
        if not re.fullmatch(r"\d{3,4}x\d{3,4}", resolution):
            raise FormError(f"résolution invalide : {resolution!r}")
        inp["resolution"] = resolution
    if form.get("allow_with_webui"):
        inp["allow_with_webui"] = True
    if form.get("parent_id"):
        inp["parent_id"] = int(form["parent_id"])
    return {k: v for k, v in inp.items() if v is not None}


def submit(form: dict) -> int:
    from .. import worker
    inp = form_to_input(form)
    worker.load_handlers(["octopus.media.handlers"])
    return worker.enqueue(inp["business"], "media.video_generate", inp)


def form_from_generation(gen: dict) -> dict:
    """Préremplit le formulaire depuis une génération (réutilisation du prompt et des réglages)."""
    settings = gen.get("settings") or {}
    resolution = settings.get("resolution", "")
    label = next((k for k, v in RESOLUTIONS.items() if v == resolution), resolution)
    return {"prompt": gen["prompt"], "model_type": gen["model_type"], "resolution": label,
            "steps": str(settings.get("num_inference_steps", "") or ""), "seed": "", "variants": "1",
            "duration_s": _seconds(settings.get("video_length")), "parent_id": gen["id"]}


def _seconds(video_length) -> str:
    if isinstance(video_length, str) and video_length.endswith("s"):
        return video_length[:-1]
    return ""


def history(limit: int = 30) -> list[dict]:
    rows = []
    for gen in library.recent(limit):
        task = tasks.get(gen["task_id"]) if gen.get("task_id") else None
        rows.append({**gen, "task_status": task["status"] if task else None,
                     "task_error": ((task or {}).get("error") or "").splitlines()[0][:160] if task and task.get("error") else ""})
    return rows


def pending_without_generation(limit: int = 10) -> list[dict]:
    """Tâches vidéo en file dont les lignes de bibliothèque ne sont pas encore créées (worker pas encore passé)."""
    known = {g["task_id"] for g in library.recent(200) if g.get("task_id")}
    return [t for t in tasks.list_tasks(limit=100) if t["kind"] == "media.video_generate"
            and t["status"] in ("queued", "running", "failed") and t["id"] not in known][:limit]


def cancel_generation(gen: dict) -> str:
    if not gen.get("task_id"):
        return "aucune tâche"
    return tasks.cancel(gen["task_id"], "annulée depuis le studio")


def hardware_summary() -> str:
    probe = wangp.cached_probe()
    if not probe:
        return "Diagnostic WanGP jamais lancé"
    hw = probe.get("hardware", {})
    gpu = hw.get("gpu") or {}
    text = (f"WanGP {probe.get('wangp_version') or '?'} · GPU {gpu.get('name', 'non détecté')} "
            f"{gpu.get('vram_total_gb', '?')} Go · RAM {hw.get('ram_total_gb', '?')} Go")
    return text + (f" · erreur : {probe['error'][:80]}" if probe.get("error") else "")
