"""Abstraction de stockage des artefacts vidéo.

Le worker écrit uniquement dans cette abstraction. Le backend concret peut être S3,
un stockage S3-compatible ou un filesystem de test. Les credentials restent hors du job.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .contract import Artifact


class ArtifactStore(Protocol):
    def exists(self, key: str) -> bool:
        ...

    def read_json(self, key: str) -> dict[str, Any] | None:
        ...

    def put_file(self, path: Path, key: str, *, content_type: str | None = None) -> Artifact:
        ...

    def put_json(self, payload: dict[str, Any], key: str) -> Artifact:
        ...

    def url_for(self, key: str) -> str:
        ...


class ArtifactStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class FilesystemArtifactStore:
    """Backend local déterministe utilisé pour les tests du worker."""

    root: Path
    public_base_url: str = "file://"

    def _dest(self, key: str) -> Path:
        destination = self.root / key
        destination.resolve().relative_to(self.root.resolve())
        return destination

    def url_for(self, key: str) -> str:
        base = self.public_base_url.rstrip("/")
        clean_key = key.strip("/")
        if self.public_base_url.startswith("file://"):
            return Path(self._dest(clean_key)).resolve().as_uri()
        return f"{base}/{clean_key}"

    def exists(self, key: str) -> bool:
        return self._dest(key).is_file()

    def read_json(self, key: str) -> dict[str, Any] | None:
        path = self._dest(key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(f"manifest illisible: {path}") from exc
        if not isinstance(value, dict):
            raise ArtifactStoreError(f"manifest non objet JSON: {path}")
        return value

    def put_file(self, path: Path, key: str, *, content_type: str | None = None) -> Artifact:
        source = Path(path)
        if not source.is_file() or source.stat().st_size <= 0:
            raise ArtifactStoreError(f"artefact absent/vide: {source}")
        clean_key = key.strip("/")
        destination = self._dest(clean_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with source.open("rb") as src, destination.open("wb") as dst:
            while chunk := src.read(1024 * 1024):
                digest.update(chunk)
                dst.write(chunk)
        return Artifact(name=destination.name, url=self.url_for(clean_key), content_type=content_type,
                        sha256=digest.hexdigest(), key=clean_key)

    def put_json(self, payload: dict[str, Any], key: str) -> Artifact:
        clean_key = key.strip("/")
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        destination = self._dest(clean_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(destination)
        digest = hashlib.sha256(data).hexdigest()
        return Artifact(name=destination.name, url=self.url_for(clean_key), content_type="application/json",
                        sha256=digest, key=clean_key)


@dataclass(frozen=True)
class S3ArtifactStore:
    """Backend S3/S3-compatible. `boto3` est importé uniquement dans le worker."""

    bucket: str
    prefix: str = "videos"
    region: str | None = None
    endpoint_url: str | None = None
    presign_seconds: int = 3600

    def _client(self):
        try:
            import boto3
        except ImportError as exc:
            raise ArtifactStoreError("boto3 absent du worker") from exc
        return boto3.client("s3", region_name=self.region, endpoint_url=self.endpoint_url)

    def _key(self, key: str) -> str:
        key = key.lstrip("/")
        prefix = self.prefix.strip("/")
        return f"{prefix}/{key}" if prefix else key

    @staticmethod
    def _not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        code = str(error.get("Code", ""))
        return code in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}

    def exists(self, key: str) -> bool:
        try:
            self._client().head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception as exc:
            if self._not_found(exc):
                return False
            raise ArtifactStoreError(f"S3 head_object échoué pour {key}: {exc}") from exc

    def read_json(self, key: str) -> dict[str, Any] | None:
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            if self._not_found(exc):
                return None
            raise ArtifactStoreError(f"S3 get_object échoué pour {key}: {exc}") from exc
        try:
            value = json.loads(response["Body"].read().decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(f"manifest S3 invalide pour {key}") from exc
        if not isinstance(value, dict):
            raise ArtifactStoreError(f"manifest S3 non objet JSON pour {key}")
        return value

    def url_for(self, key: str) -> str:
        try:
            return self._client().generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": self._key(key)},
                ExpiresIn=self.presign_seconds,
            )
        except Exception as exc:
            raise ArtifactStoreError(f"impossible de signer {key}: {exc}") from exc

    def put_file(self, path: Path, key: str, *, content_type: str | None = None) -> Artifact:
        source = Path(path)
        if not source.is_file() or source.stat().st_size <= 0:
            raise ArtifactStoreError(f"artefact absent/vide: {source}")
        clean_key = key.strip("/")
        s3key = self._key(clean_key)
        extra = {"ContentType": content_type} if content_type else None
        try:
            self._client().upload_file(str(source), self.bucket, s3key, ExtraArgs=extra or {})
        except Exception as exc:
            raise ArtifactStoreError(f"upload S3 échoué pour {key}: {exc}") from exc
        digest = hashlib.sha256()
        with source.open("rb") as src:
            while chunk := src.read(1024 * 1024):
                digest.update(chunk)
        return Artifact(name=source.name, url=self.url_for(clean_key), content_type=content_type,
                        sha256=digest.hexdigest(), key=clean_key)

    def put_json(self, payload: dict[str, Any], key: str) -> Artifact:
        clean_key = key.strip("/")
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self._client().put_object(Bucket=self.bucket, Key=self._key(clean_key), Body=data,
                                      ContentType="application/json", CacheControl="no-cache")
        except Exception as exc:
            raise ArtifactStoreError(f"upload S3 JSON échoué pour {key}: {exc}") from exc
        return Artifact(name=Path(clean_key).name, url=self.url_for(clean_key), content_type="application/json",
                        sha256=hashlib.sha256(data).hexdigest(), key=clean_key)


def store_from_env() -> ArtifactStore:
    backend = os.environ.get("PODALUX_VIDEO_STORAGE_BACKEND", "").strip().lower()
    if backend == "filesystem":
        return FilesystemArtifactStore(
            Path(os.environ.get("PODALUX_VIDEO_ARTIFACT_ROOT", "/tmp/podalux-video-artifacts")),
            os.environ.get("PODALUX_VIDEO_ARTIFACT_BASE_URL", "file://"),
        )
    if backend == "s3":
        bucket = os.environ.get("PODALUX_VIDEO_S3_BUCKET", "").strip()
        if not bucket:
            raise ArtifactStoreError("PODALUX_VIDEO_S3_BUCKET obligatoire pour backend s3")
        return S3ArtifactStore(
            bucket=bucket,
            prefix=os.environ.get("PODALUX_VIDEO_S3_PREFIX", "videos"),
            region=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or None,
            endpoint_url=os.environ.get("PODALUX_VIDEO_S3_ENDPOINT_URL") or None,
            presign_seconds=int(os.environ.get("PODALUX_VIDEO_PRESIGN_SECONDS", "3600")),
        )
    raise ArtifactStoreError("PODALUX_VIDEO_STORAGE_BACKEND doit être filesystem ou s3")


__all__ = ["ArtifactStore", "ArtifactStoreError", "FilesystemArtifactStore", "S3ArtifactStore", "store_from_env"]
