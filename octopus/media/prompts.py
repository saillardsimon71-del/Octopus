"""Prompts MiniMax H3 : format structuré attendu par FL2VA (guide WanGP models/minimax_h3/prompt_enhancer.py).

integrated_multimodal_description: [Shot 1] ...
overall_soundscape: ...
non_diegetic_music: ...
"""
from __future__ import annotations

import re

H3_FIELDS = ("integrated_multimodal_description:", "subject_definitions:")
DEFAULT_SOUNDSCAPE = "Natural ambient sound that matches the scene, with physical sounds synchronized to the action."


def is_structured_h3(prompt: str) -> bool:
    return any(field in prompt for field in H3_FIELDS)


def to_h3_fl2va(description: str, *, soundscape: str = "", music: str = "N/A") -> str:
    """Enveloppe déterministe d'une description libre dans les trois champs FL2VA (sans lignes vides)."""
    text = re.sub(r"\s*\n\s*", " ", description.strip())
    if not text.startswith("[Shot"):
        text = f"[Shot 1] {text}"
    return (f"integrated_multimodal_description: {text}\n"
            f"overall_soundscape: {soundscape.strip() or DEFAULT_SOUNDSCAPE}\n"
            f"non_diegetic_music: {music.strip() or 'N/A'}")


def prepare(model_type: str, prompt: str, *, soundscape: str = "", music: str = "") -> str:
    """Prompt envoyé à WanGP : structuré automatiquement pour H3 FL2VA/VDN si l'utilisateur ne l'a pas fait."""
    if model_type.startswith("minimax_h3") and "ref2va" not in model_type and "tts" not in model_type \
            and not is_structured_h3(prompt):
        return to_h3_fl2va(prompt, soundscape=soundscape, music=music or "N/A")
    return prompt
