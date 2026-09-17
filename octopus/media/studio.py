"""Logique du studio vidéo, indépendante de l'interface : formulaire -> tâche, historique, réutilisation."""
from __future__ import annotations

import re

from .. import tasks
from . import handlers, library, perf, presets, wangp

RESOLUTIONS = {
    "Selon le préréglage / le modèle": "",
    "Short 9:16 brouillon (288x512)": "288x512",
    "Short 9:16 (480x832)": "480x832",
    "Short 9:16 HD (720x1280)": "720x1280",
    "Paysage (832x480)": "832x480",
    "Paysage HD (1280x720)": "1280x720",
    "Carré (640x640)": "640x640",
}
PRESET_CHOICES = {**{p.label: p.key for p in presets.PRESETS.values()}, "Personnalisé (réglages ci-dessous)": ""}
AUTO_MODEL = "Selon le préréglage"
BUSY_STATUSES = ("queued", "running")


class FormError(ValueError):
    pass


def model_choices() -> list[tuple[str, str]]:
    """(libellé, model_type) : « selon le préréglage », modèles des préréglages, puis MiniMax H3 du diagnostic."""
    probe = wangp.cached_probe() or {}
    by_type = {m["model_type"]: m for m in probe.get("models", [])}
    marks = {"available": "prêt", "partial": "incomplet"}
    choices = [(AUTO_MODEL, "")]
    for model_type in dict.fromkeys(p.model_type for p in presets.PRESETS.values() if p.model_type):
        meta = by_type.get(model_type, {})
        mark = marks.get(meta.get("availability"), "téléchargé au 1er lancement" if meta else "état inconnu")
        choices.append((f"{meta.get('name') or model_type} [{mark}]", model_type))
    models = [m for m in probe.get("models", []) if str(m.get("model_type", "")).startswith("minimax_h3")]
    if not models:
        return choices + [(f"{m} (diagnostic à lancer)", m) for m in handlers.MODEL_PREFERENCE]
    order = {"available": 0, "partial": 1}
    models.sort(key=lambda m: (order.get(m.get("availability"), 2), m["model_type"]))
    return choices + [(f"{m.get('name') or m['model_type']} [{marks.get(m.get('availability'), 'à télécharger')}]",
                       m["model_type"]) for m in models]


def form_to_input(form: dict) -> dict:
    """Valide le formulaire du studio et produit l'entrée de la tâche media.video_generate."""
    prompt = str(form.get("prompt", "")).strip()
    if len(prompt) < 3:
        raise FormError("prompt trop court")
    preset = PRESET_CHOICES.get(form.get("preset", ""), form.get("preset") or "")
    if preset and preset not in presets.PRESETS:
        raise FormError(f"préréglage inconnu : {preset!r}")
    inp: dict = {"prompt": prompt, "preset": preset or None, "model_type": form.get("model_type") or None,
                 "business": form.get("business") or "studio"}
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
    settings = dict(form.get("settings") or {})  # réglages conservés d'une génération réutilisée (guidage, etc.)
    steps = inp.pop("steps", None)
    if steps:
        settings["num_inference_steps"] = steps
    if settings:
        inp["settings"] = settings
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


def estimate_text(form: dict) -> str:
    """Durée prévue du formulaire en cours, d'après les générations mesurées sur cette machine."""
    try:
        inp = form_to_input({**form, "prompt": form.get("prompt") or "estimation"})
        if not inp.get("preset") and not inp.get("model_type"):
            inp["model_type"] = handlers.default_model()
        return "Durée prévue : " + perf.describe(presets.estimate_for_input(inp))
    except (FormError, ValueError) as exc:
        return f"Durée prévue : formulaire incomplet ({exc})"
    except Exception as exc:  # noqa: BLE001 - base occupée, etc. : l'estimation est accessoire
        return f"Durée prévue : indisponible ({type(exc).__name__})"


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
    return {"prompt": gen["prompt"], "preset": "Personnalisé (réglages ci-dessous)", "model_type": gen["model_type"],
            "resolution": label,
            "steps": str(settings.get("num_inference_steps", "") or ""), "seed": "", "variants": "1",
            "duration_s": _seconds(settings.get("video_length")), "parent_id": gen["id"],
            "settings": {k: v for k, v in settings.items() if k not in _FORM_KEYS}}


_FORM_KEYS = {"prompt", "model_type", "seed", "resolution", "video_length", "num_inference_steps"}


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
