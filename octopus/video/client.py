"""Client HTTP asynchrone minimal pour un worker vidéo cloud.

Aucun SDK fournisseur n'est requis. Le transport est basé sur urllib afin que le
noyau OCTOPUS reste léger et puisse changer de fournisseur sans modifier les agents.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contract import TERMINAL_STATUSES, Artifact, RemoteJob, VideoJob, VideoResult, VideoStatus


class CloudVideoError(RuntimeError):
    """Erreur réseau, contrat fournisseur ou résultat vidéo distant invalide."""


@dataclass(frozen=True)
class CloudVideoConfig:
    submit_url: str
    status_url_template: str
    cancel_url_template: str | None = None
    token: str | None = None
    poll_initial_s: float = 2.0
    poll_max_s: float = 15.0
    request_timeout_s: float = 60.0
    job_timeout_s: float = 45 * 60.0

    @classmethod
    def from_env(cls) -> "CloudVideoConfig":
        submit = os.environ.get("PODALUX_VIDEO_SUBMIT_URL", "").strip()
        status = os.environ.get("PODALUX_VIDEO_STATUS_URL_TEMPLATE", "").strip()
        if not submit or not status:
            raise CloudVideoError(
                "cloud renderer non configuré: PODALUX_VIDEO_SUBMIT_URL et "
                "PODALUX_VIDEO_STATUS_URL_TEMPLATE sont obligatoires"
            )
        return cls(
            submit_url=submit,
            status_url_template=status,
            cancel_url_template=os.environ.get("PODALUX_VIDEO_CANCEL_URL_TEMPLATE", "").strip() or None,
            token=os.environ.get("PODALUX_VIDEO_API_TOKEN", "").strip() or None,
            poll_initial_s=float(os.environ.get("PODALUX_VIDEO_POLL_INITIAL_S", "2")),
            poll_max_s=float(os.environ.get("PODALUX_VIDEO_POLL_MAX_S", "15")),
            request_timeout_s=float(os.environ.get("PODALUX_VIDEO_REQUEST_TIMEOUT_S", "60")),
            job_timeout_s=float(os.environ.get("PODALUX_VIDEO_JOB_TIMEOUT_S", str(45 * 60))),
        )


class CloudVideoClient:
    """Client fournisseur-agnostique."""

    def __init__(self, config: CloudVideoConfig, *, opener: Callable | None = None):
        self.config = config
        self._opener = opener or urllib.request.urlopen

    def _request(self, method: str, url: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener(req, timeout=self.config.request_timeout_s) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise CloudVideoError(f"HTTP {exc.code} sur {method} {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CloudVideoError(f"réseau sur {method} {url}: {exc}") from exc
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CloudVideoError(f"réponse non JSON sur {method} {url}") from exc
        if not isinstance(data, dict):
            raise CloudVideoError(f"réponse JSON inattendue sur {method} {url}")
        return data

    @staticmethod
    def _remote_id(data: Mapping[str, Any]) -> str:
        value = data.get("id") or data.get("job_id") or data.get("request_id")
        if isinstance(value, Mapping):
            value = value.get("id") or value.get("job_id")
        if not value:
            raise CloudVideoError("le fournisseur n'a pas renvoyé d'identifiant de job")
        return str(value)

    @staticmethod
    def _status(value: Any) -> VideoStatus:
        raw = str(value or "RUNNING").upper()
        aliases = {
            "IN_QUEUE": VideoStatus.QUEUED,
            "QUEUED": VideoStatus.QUEUED,
            "IN_PROGRESS": VideoStatus.RUNNING,
            "RUNNING": VideoStatus.RUNNING,
            "PROCESSING": VideoStatus.RENDERING,
            "RENDERING": VideoStatus.RENDERING,
            "COMPLETED": VideoStatus.COMPLETED,
            "SUCCEEDED": VideoStatus.COMPLETED,
            "FAILED": VideoStatus.FAILED,
            "ERROR": VideoStatus.FAILED,
            "CANCELLED": VideoStatus.CANCELLED,
            "CANCELED": VideoStatus.CANCELLED,
            "EXPIRED": VideoStatus.EXPIRED,
            "TIMED_OUT": VideoStatus.EXPIRED,
            "TIMEOUT": VideoStatus.EXPIRED,
            "QC": VideoStatus.QC,
        }
        return aliases.get(raw, VideoStatus.RUNNING)

    def submit(self, job: VideoJob) -> RemoteJob:
        payload = job.to_dict()
        data = self._request("POST", self.config.submit_url, payload)
        remote_id = self._remote_id(data)
        return RemoteJob(remote_id, self._status(data.get("status", "QUEUED")), data)

    def status(self, remote_id: str) -> RemoteJob:
        url = self.config.status_url_template.format(job_id=remote_id)
        data = self._request("GET", url)
        return RemoteJob(remote_id, self._status(data.get("status")), data)

    def cancel(self, remote_id: str) -> None:
        if not self.config.cancel_url_template:
            raise CloudVideoError("annulation distante non configurée")
        url = self.config.cancel_url_template.format(job_id=remote_id)
        self._request("POST", url, {"job_id": remote_id})

    def wait(self, remote_id: str, *, timeout_s: float | None = None) -> VideoResult:
        deadline = time.monotonic() + (self.config.job_timeout_s if timeout_s is None else timeout_s)
        delay = max(0.1, self.config.poll_initial_s)
        last = None
        while time.monotonic() < deadline:
            remote = self.status(remote_id)
            last = remote
            if remote.status in TERMINAL_STATUSES:
                return self._result(remote)
            time.sleep(delay)
            delay = min(self.config.poll_max_s, delay * 1.5)
        raise CloudVideoError(f"timeout du job distant {remote_id}; dernier état={last.status if last else 'UNKNOWN'}")

    def _result(self, remote: RemoteJob) -> VideoResult:
        data = remote.raw
        output = data.get("output") if isinstance(data.get("output"), Mapping) else data.get("result")
        if not isinstance(output, Mapping):
            output = {}
        video_url = output.get("video_url") or data.get("video_url")
        artifacts_raw = output.get("artifacts") or data.get("artifacts") or []
        if isinstance(artifacts_raw, Mapping):
            artifacts_raw = [
                {"name": key, "url": value} for key, value in artifacts_raw.items() if isinstance(value, str)
            ]
        artifacts = []
        for item in artifacts_raw if isinstance(artifacts_raw, list) else []:
            if not isinstance(item, Mapping) or not item.get("url"):
                continue
            artifacts.append(Artifact(
                name=str(item.get("name") or "artifact"),
                url=str(item["url"]),
                kind=str(item.get("kind") or "file"),
                content_type=str(item["content_type"]) if item.get("content_type") else None,
                sha256=str(item["sha256"]) if item.get("sha256") else None,
                key=str(item["key"]) if item.get("key") else None,
            ))
        qc = output.get("qc") or data.get("qc") or {}
        if not isinstance(qc, Mapping):
            qc = {}
        if remote.status != VideoStatus.COMPLETED:
            error = output.get("error") or data.get("error") or f"job distant terminé avec {remote.status.value}"
            raise CloudVideoError(str(error))
        if not video_url:
            video_artifact = next((a for a in artifacts if a.name == "final.mp4" and a.url), None)
            video_url = video_artifact.url if video_artifact else None
        if not video_url:
            raise CloudVideoError("job COMPLETED sans video_url")
        return VideoResult(remote.remote_id, remote.status, str(video_url), tuple(artifacts), qc, data)
