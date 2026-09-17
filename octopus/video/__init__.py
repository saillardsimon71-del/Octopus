"""Contrats et client d'exécution vidéo cloud pour OCTOPUS.

Le reste du produit ne dépend ni d'un fournisseur cloud ni d'un SDK vidéo.
"""

from .contract import Artifact, RemoteJob, VideoJob, VideoResult, VideoStatus
from .renderers import CloudVideoRenderer, LocalVideoRenderer, VideoRenderer, get_renderer

__all__ = [
    "Artifact",
    "CloudVideoRenderer",
    "LocalVideoRenderer",
    "RemoteJob",
    "VideoJob",
    "VideoRenderer",
    "VideoResult",
    "VideoStatus",
    "get_renderer",
]
