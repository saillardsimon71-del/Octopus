"""Abstraction de stockage des artefacts vidéo.

Le worker écrit uniquement dans cette abstraction. Le backend concret peut être S3,
un stockage S3-compatible ou un filesystem de test. Les URLs de sortie sont produites
par le backend ; le job vidéo ne contient pas de secret de stockage.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .contract import Artifact


class ArtifactStore(Protocol):
    def put_file(self, path: Path, key: str, *, content_type: str | None = None) -> Artifact:
        ...


class ArtifactStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class FilesystemArtifactStore:
    """Backend local déterministe utilisé pour les tests du worker."""

    root: Path
    public_base_url: str = "file://"

    def put_file(self, path: Path, key: str, *, content_type: str | None = None) -> Artifact:
        source = Path(path)
        if not source.is_file() or source.stat().st_size <= 0:
            raise ArtifactStoreError(f"artefact absent/vide: {source}")
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        url = f"{self.public_base_url}{destination.as_posix()}"
        return Artifact(name=key.rsplit("/", 1)[-1], url=url, content_type=content_type, sha256=digest)


def filesystem_store_from_env() -> FilesystemArtifactStore:
    root = Path(os.environ.get("PODALUX_VIDEO_ARTIFACT_ROOT", "/tmp/podalux-video-artifacts"))
    base = os.environ.get("PODALUX_VIDEO_ARTIFACT_BASE_URL", "file://")
    return FilesystemArtifactStore(root=root, public_base_url=base)


__all__ = ["ArtifactStore", "ArtifactStoreError", "FilesystemArtifactStore", "filesystem_store_from_env"]
