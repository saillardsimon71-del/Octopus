"""Tests du backend d'artefacts local, sans credentials ni réseau."""
from __future__ import annotations

from pathlib import Path

from octopus.video.storage import FilesystemArtifactStore


def test_filesystem_store_roundtrip_and_sha256(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store", public_base_url="https://storage.test/")
    source = tmp_path / "final.mp4"
    source.write_bytes(b"abc")

    artifact = store.put_file(source, "offer/job/final.mp4", content_type="video/mp4")
    assert artifact.url == "https://storage.test/offer/job/final.mp4"
    assert len(artifact.sha256) == 64
    assert store.exists("offer/job/final.mp4")


def test_filesystem_json_is_atomic_and_readable(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    artifact = store.put_json({"status": "COMPLETED", "video_url": "https://example/video.mp4"},
                              "offer/job/manifest.json")
    assert artifact.content_type == "application/json"
    assert store.read_json("offer/job/manifest.json")["status"] == "COMPLETED"
    assert not any(Path(tmp_path / "store" / "offer/job").glob("*.tmp"))


def test_filesystem_store_rejects_path_escape(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    source = tmp_path / "source.bin"
    source.write_bytes(b"x")
    try:
        store.put_file(source, "../escape.bin")
    except ValueError:
        pass
    else:
        raise AssertionError("un chemin d'artefact hors du root doit être refusé")
