"""Vérification matérielle et prompts H3 (mesures réelles de la machine du 17/09/2026)."""
from __future__ import annotations

import pytest

from octopus.media import prompts, requirements

GTX1060 = {"cuda_available": True, "gpu": {"name": "NVIDIA GeForce GTX 1060 3GB", "vram_total_gb": 3.0, "capability": "6.1"},
           "ram_total_gb": 15.9, "cpu_count": 4}
RTX4090 = {"cuda_available": True, "gpu": {"name": "RTX 4090", "vram_total_gb": 24.0, "capability": "8.9"}, "ram_total_gb": 64}


@pytest.mark.parametrize("model, hardware, level", [
    ("minimax_h3_fl2va", GTX1060, "blocked"),
    ("minimax_h3_fl2va_pruned", GTX1060, "blocked"),
    ("ltx2_22B_distilled", GTX1060, "blocked"),
    ("t2v_1.3B", GTX1060, "warning"),
    ("minimax_h3_fl2va", RTX4090, "ok"),
    ("flux", GTX1060, "unknown"),
    ("minimax_h3_fl2va", None, "unknown"),
    ("minimax_h3_fl2va", {"cuda_available": False}, "blocked"),
])
def test_preflight(model, hardware, level):
    assert requirements.preflight(model, hardware)["level"] == level


def test_long_h3_video_needs_more_vram():
    hw = {**RTX4090, "gpu": {"vram_total_gb": 7.0, "capability": "8.6"}}
    assert requirements.preflight("minimax_h3_fl2va", hw, seconds=5)["level"] == "ok"
    assert requirements.preflight("minimax_h3_fl2va", hw, seconds=15)["level"] == "warning"


def test_h3_prompt_is_structured_once():
    wrapped = prompts.prepare("minimax_h3_fl2va", "A fox runs\nthrough snow.")
    assert wrapped.splitlines() == ["integrated_multimodal_description: [Shot 1] A fox runs through snow.",
                                    f"overall_soundscape: {prompts.DEFAULT_SOUNDSCAPE}", "non_diegetic_music: N/A"]
    assert prompts.prepare("minimax_h3_fl2va", wrapped) == wrapped
    assert prompts.prepare("minimax_h3_ref2va_pruned", "free text") == "free text"
    assert prompts.prepare("t2v_1.3B", "free text") == "free text"
