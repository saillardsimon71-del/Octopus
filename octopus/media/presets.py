"""Préréglages de génération : choisir un compromis vitesse / qualité sans connaître les réglages WanGP.

Les réglages viennent des fiches de modèles WanGP (C:/pinokio/api/wan.git/app/defaults, 17/09/2026) :
- t2v_nexus_1.3B : finetune de Wan2.1 1.3B « Built on CausVid », fiche WanGP : 6 étapes, guidance_scale 1,
  flow_shift 5 (pas de CFG : une seule évaluation du modèle par étape) ;
- t2v_1.3B : modèle de base, guidage CFG 5.0 (deux évaluations par étape).
Les durées ne sont jamais écrites ici : elles sont estimées d'après les mesures de la machine (perf.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import perf, wangp


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    model_type: str | None  # None : modèle par défaut (MiniMax H3 disponible)
    resolution: str | None
    duration_s: float | None
    settings: dict = field(default_factory=dict)
    note: str = ""


PRESETS: dict[str, Preset] = {p.key: p for p in (
    Preset("brouillon", "Brouillon rapide", "t2v_nexus_1.3B", "288x512", 3,
           {"num_inference_steps": 6, "guidance_scale": 1, "flow_shift": 5},
           "Wan2.1 Nexus 1.3B distillé : 6 étapes sans CFG, petite définition, pour valider une idée"),
    Preset("standard", "Standard 9:16", "t2v_nexus_1.3B", "480x832", 3,
           {"num_inference_steps": 6, "guidance_scale": 1, "flow_shift": 5},
           "même modèle distillé en 480x832 (format Short)"),
    Preset("qualite", "Qualité Wan 1.3B (lent)", "t2v_1.3B", "480x832", 3,
           {"num_inference_steps": 20, "guidance_scale": 5.0},
           "modèle de base avec CFG : deux évaluations par étape"),
    Preset("h3", "MiniMax H3 (GPU 8 Go+)", None, None, 5, {},
           "vidéo + son ; demande un GPU récent (RTX 30+) et beaucoup de RAM"),
)}


def apply(inp: dict) -> dict:
    """Complète une entrée de tâche avec son préréglage ; les choix explicites de la demande restent prioritaires."""
    key = inp.get("preset")
    if not key:
        return dict(inp)
    preset = PRESETS.get(key)
    if preset is None:
        raise ValueError(f"préréglage inconnu : {key!r} (connus : {', '.join(PRESETS)})")
    out = dict(inp)
    if not out.get("model_type") and preset.model_type:
        out["model_type"] = preset.model_type
    if not out.get("resolution") and preset.resolution:
        out["resolution"] = preset.resolution
    if not out.get("duration_s") and preset.duration_s:
        out["duration_s"] = preset.duration_s
    out["settings"] = {**preset.settings, **(inp.get("settings") or {})}
    return out


def estimate_for_input(inp: dict, model_type: str | None = None) -> dict | None:
    """Durée prévue d'une entrée de tâche (après préréglage), d'après les générations mesurées sur ce GPU."""
    inp = apply(inp)
    probe = wangp.cached_probe() or {}
    model_type = model_type or inp.get("model_type")
    if not model_type:
        return None
    defaults = (probe.get("defaults") or {}).get(model_type) or {}
    settings = {**defaults, **(inp.get("settings") or {})}
    frames = perf.frames_for(model_type, f"{float(inp['duration_s']):g}s") if inp.get("duration_s") \
        else perf.frames_for(model_type, settings.get("video_length"))
    gpu = ((probe.get("hardware") or {}).get("gpu") or {}).get("name")
    return perf.estimate(model_type, resolution=inp.get("resolution") or settings.get("resolution"), frames=frames,
                         steps=settings.get("num_inference_steps"), passes=perf.passes_for(settings),
                         variants=int(inp.get("variants") or 1), gpu=gpu)
