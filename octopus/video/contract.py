"""Contrat réseau stable pour les jobs vidéo."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

SCHEMA_VERSION = "1"
_SECRET_FRAGMENTS = (
    "api_key", "apikey", "secret", "password", "passwd", "token", "access_key",
    "private_key", "client_secret", "authorization", "cookie", "credentials",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class VideoContractError(ValueError):
    """Payload vidéo invalide ou contenant un secret."""


class VideoStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RENDERING = "RENDERING"
    QC = "QC"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES = frozenset({VideoStatus.COMPLETED, VideoStatus.FAILED, VideoStatus.CANCELLED, VideoStatus.EXPIRED})


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    kind: str = "file"
    content_type: str | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "url": self.url, "kind": self.kind}
        if self.content_type:
            out["content_type"] = self.content_type
        if self.sha256:
            out["sha256"] = self.sha256
        return out


@dataclass(frozen=True)
class RemoteJob:
    remote_id: str
    status: VideoStatus
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoJob:
    job_id: str
    offer_id: str
    template: str
    language: str
    duration_seconds: float
    script: Mapping[str, Any]
    voice: Mapping[str, Any]
    assets: list[Mapping[str, Any]] = field(default_factory=list)
    quality: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_legacy_job(cls, job: Mapping[str, Any], *, job_id: str, template: str = "forge-v4") -> "VideoJob":
        narration = job.get("narration")
        if not isinstance(narration, list) or not narration:
            raise VideoContractError("job legacy sans narration")
        job_id = _validate_id(job_id, "job_id")
        offer_id = _validate_id(str(job.get("offer_id") or ""), "offer_id")
        duration = job.get("duree_cible_s", 24)
        try:
            duration_f = float(duration)
        except (TypeError, ValueError) as exc:
            raise VideoContractError("duree_cible_s invalide") from exc
        script = {
            "titre": job.get("titre", ""),
            "hook": job.get("hook", ""),
            "douleur": job.get("douleur", ""),
            "preuve": job.get("preuve", ""),
            "soulagement": job.get("soulagement", ""),
            "cta": job.get("cta", ""),
            "prix": job.get("prix", ""),
            "stripe_link": job.get("stripe_link", ""),
            "sub_id": job.get("sub_id", ""),
            "segments": narration,
        }
        voice = dict(job.get("voix") or {})
        metadata = {
            "keywords": list(job.get("keywords") or []),
            "visuel": job.get("visuel"),
            "palette": job.get("palette"),
        }
        return cls(
            job_id=job_id,
            offer_id=offer_id,
            template=template,
            language=str(job.get("langue") or "fr"),
            duration_seconds=duration_f,
            script=script,
            voice=voice,
            assets=list(job.get("assets") or []),
            quality={"target_lufs": -14.0, "min_lra": 5.0, "min_score": 24,
                     "resolution": "1080x1920", "fps": 30},
            metadata=metadata,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VideoJob":
        if not isinstance(payload, Mapping):
            raise VideoContractError("job doit être un objet JSON")
        job_id = _validate_id(str(payload.get("job_id") or ""), "job_id")
        offer_id = _validate_id(str(payload.get("offer_id") or ""), "offer_id")
        schema_version = str(payload.get("schema_version") or SCHEMA_VERSION)
        if schema_version != SCHEMA_VERSION:
            raise VideoContractError(f"schema_version non supportée: {schema_version}")
        try:
            duration = float(payload.get("duration_seconds"))
        except (TypeError, ValueError) as exc:
            raise VideoContractError("duration_seconds invalide") from exc
        if not 1 <= duration <= 300:
            raise VideoContractError("duration_seconds hors limites [1, 300]")
        script = payload.get("script")
        voice = payload.get("voice")
        if not isinstance(script, Mapping) or not isinstance(script.get("segments"), list):
            raise VideoContractError("script.segments est obligatoire")
        if not isinstance(voice, Mapping):
            raise VideoContractError("voice doit être un objet")
        assets = payload.get("assets") or []
        if not isinstance(assets, list):
            raise VideoContractError("assets doit être une liste")
        quality = payload.get("quality") or {}
        metadata = payload.get("metadata") or {}
        for name, value in (("job", payload), ("script", script), ("voice", voice), ("assets", assets), ("quality", quality), ("metadata", metadata)):
            assert_no_secrets(value, path=name)
        return cls(job_id=job_id, offer_id=offer_id,
                   template=str(payload.get("template") or "forge-v4"),
                   language=str(payload.get("language") or "fr"),
                   duration_seconds=duration, script=script, voice=voice,
                   assets=[x for x in assets if isinstance(x, Mapping)], quality=quality,
                   metadata=metadata, schema_version=schema_version)

    def to_dict(self) -> dict[str, Any]:
        payload = {"schema_version": self.schema_version, "job_id": self.job_id,
                   "offer_id": self.offer_id, "template": self.template,
                   "language": self.language, "duration_seconds": self.duration_seconds,
                   "script": dict(self.script), "voice": dict(self.voice),
                   "assets": [dict(x) for x in self.assets], "quality": dict(self.quality),
                   "metadata": dict(self.metadata)}
        assert_no_secrets(payload)
        return payload

    def to_legacy_job(self) -> dict[str, Any]:
        """Reconstruit le job attendu par les scripts FORGE historiques dans un worker isolé."""
        script = dict(self.script)
        legacy = {"offer_id": self.offer_id, "langue": self.language,
                  "duree_cible_s": self.duration_seconds, "titre": script.get("titre", ""),
                  "hook": script.get("hook", ""), "douleur": script.get("douleur", ""),
                  "preuve": script.get("preuve", ""), "soulagement": script.get("soulagement", ""),
                  "cta": script.get("cta", ""), "prix": script.get("prix", ""),
                  "stripe_link": script.get("stripe_link", ""), "sub_id": script.get("sub_id", ""),
                  "voix": dict(self.voice), "keywords": list(self.metadata.get("keywords") or []),
                  "palette": self.metadata.get("palette"), "visuel": self.metadata.get("visuel"),
                  "narration": list(script.get("segments") or [])}
        assert_no_secrets(legacy)
        return legacy


@dataclass(frozen=True)
class VideoResult:
    job_id: str
    status: VideoStatus
    video_url: str | None = None
    artifacts: tuple[Artifact, ...] = ()
    qc: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "job_id": self.job_id,
                "status": self.status.value, "video_url": self.video_url,
                "artifacts": [a.to_dict() for a in self.artifacts], "qc": dict(self.qc)}


def _validate_id(value: str, field_name: str) -> str:
    if not value or not _ID_RE.fullmatch(value) or ".." in value:
        raise VideoContractError(f"{field_name} invalide")
    return value


def assert_no_secrets(value: Any, *, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(fragment in normalized for fragment in _SECRET_FRAGMENTS):
                raise VideoContractError(f"secret interdit dans le job: {path}.{key}")
            assert_no_secrets(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_secrets(child, path=f"{path}[{index}]")
