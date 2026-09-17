"""Vérification matérielle avant génération : refuser ce qui ne peut pas tourner au lieu de bloquer le PC.

Chiffres sourcés (WanGP, dossier C:/pinokio/api/wan.git/app, consulté le 17/09/2026) :
- README : MiniMax H3 « 5-6GB of VRAM only for 5s (124 frames) and 8-9GB of VRAM for 15s at 832x480 » ;
- fichiers téléchargés : MiniMax-H3-FL2VA_int8_convrot 34,0 Go, VAE vidéo 5,2 Go, Qwen3-VL-32B int8 26,7 Go ;
- README : les checkpoints W4A8 plus légers exigent des kernels INT8 « RTX 30-series or newer » ;
- README : « run select models with as little as 6 GB of VRAM » (famille Wan 1.3B).
La RAM exigée est une estimation : le transformeur quantifié doit tenir en mémoire système quand
WanGP le décharge du GPU (profils mmgp 4/5). Les modèles inconnus ne sont jamais bloqués.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Needs:
    label: str
    min_vram_gb: float
    weights_ram_gb: float  # transformeur quantifié int8, chargé en RAM lors du déchargement
    source: str


FAMILIES: tuple[tuple[str, Needs], ...] = (
    ("minimax_h3_tts", Needs("MiniMax H3 audio 20B", 5.0, 21.0, "estimation : checkpoint Ref2VA élagué")),
    ("minimax_h3_ref2va_pruned", Needs("MiniMax H3 Ref2VA élagué 20B", 5.0, 21.0, "estimation 20/33 × 34 Go")),
    ("minimax_h3_fl2va_pruned", Needs("MiniMax H3 FL2VA élagué 20B", 5.0, 21.0, "estimation 20/33 × 34 Go")),
    ("minimax_h3_vdn_pruned", Needs("MiniMax H3 VDN élagué 20B", 5.5, 21.0, "estimation, VDN demande un peu plus de VRAM")),
    ("minimax_h3", Needs("MiniMax H3 33B", 5.0, 34.0, "README WanGP (5-6 Go VRAM) + fichier int8 de 34,0 Go")),
    ("ltx2", Needs("LTX-2 19-22B", 6.0, 20.0, "estimation : 19-22 milliards de paramètres en int8")),
    ("t2v_nexus_1.3B", Needs("Wan2.1 Nexus 1.3B (distillé)", 6.0, 3.0, "même architecture que Wan2.1 T2V 1.3B")),
    ("t2v_1.3B", Needs("Wan2.1 T2V 1.3B", 6.0, 3.0, "README WanGP : 6 Go pour les petits modèles")),
    ("vace_1.3B", Needs("Wan VACE 1.3B", 6.0, 3.0, "README WanGP : 6 Go pour les petits modèles")),
)


def needs_for(model_type: str) -> Needs | None:
    for prefix, needs in FAMILIES:
        if model_type.startswith(prefix):
            return needs
    return None


def preflight(model_type: str, hardware: dict | None, *, seconds: float | None = None) -> dict:
    """{"level": "ok" | "warning" | "blocked" | "unknown", "reasons": [...], "needs": {...}}"""
    needs = needs_for(model_type)
    hardware = hardware or {}
    gpu = hardware.get("gpu") or {}
    vram, ram = gpu.get("vram_total_gb"), hardware.get("ram_total_gb")
    if needs is None:
        return {"level": "unknown", "reasons": [f"pas de besoins connus pour {model_type}"], "needs": None}
    if not hardware:
        return {"level": "unknown", "reasons": ["matériel inconnu : lancer le diagnostic WanGP"], "needs": needs.__dict__}
    reasons, level = [], "ok"
    min_vram = needs.min_vram_gb + (3.0 if model_type.startswith("minimax_h3") and seconds and seconds > 5 else 0.0)
    if not hardware.get("cuda_available"):
        return {"level": "blocked", "reasons": ["aucun GPU CUDA détecté"], "needs": needs.__dict__}
    if vram is not None and vram < min_vram:
        # Petit modèle qui tient en RAM : WanGP le décharge du GPU, c'est lent mais possible (avertissement).
        fits_in_ram = ram is not None and needs.weights_ram_gb <= ram * 0.5
        severe = vram < min_vram * 0.75 and not fits_in_ram
        reasons.append(f"VRAM {vram:g} Go < {min_vram:g} Go annoncés pour {needs.label}")
        level = "blocked" if severe else "warning"
    if ram is not None and ram < needs.weights_ram_gb:
        severe = ram < needs.weights_ram_gb * 0.6
        reasons.append(f"RAM {ram:g} Go < {needs.weights_ram_gb:g} Go de poids à décharger ({needs.source})")
        level = "blocked" if severe or level == "blocked" else "warning"
    capability = gpu.get("capability")
    if capability and float(capability) < 8.0 and model_type.startswith(("minimax_h3", "ltx2")):
        reasons.append(f"GPU capacité {capability} : pas de kernels BF16/INT8 optimisés (RTX 30 ou plus récent), génération très lente")
        level = "warning" if level == "ok" else level
    return {"level": level, "reasons": reasons, "needs": needs.__dict__}


def describe(result: dict) -> str:
    labels = {"ok": "compatible", "warning": "limite", "blocked": "incompatible", "unknown": "inconnu"}
    text = f"Matériel : {labels.get(result['level'], result['level'])}"
    return text + (" — " + " ; ".join(result["reasons"]) if result["reasons"] else "")
