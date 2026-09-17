"""Adaptateur RunPod Serverless pour le contrat vidéo OCTOPUS.

RunPod reste un détail du provider : le reste du code ne voit que VideoJob/VideoResult.
La route asynchrone `/run` est utilisée par défaut.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .client import CloudVideoClient, CloudVideoConfig, CloudVideoError
from .contract import RemoteJob, VideoJob


@dataclass(frozen=True)
class RunPodConfig:
    endpoint_id: str
    api_token: str
    api_base_url: str = "https://api.runpod.ai/v2"

    @classmethod
    def from_env(cls) -> "RunPodConfig":
        endpoint_id = os.environ.get("PODALUX_RUNPOD_ENDPOINT_ID", "").strip()
        token = os.environ.get("PODALUX_RUNPOD_API_TOKEN", "").strip()
        if not endpoint_id or not token:
            raise CloudVideoError(
                "RunPod non configuré: PODALUX_RUNPOD_ENDPOINT_ID et "
                "PODALUX_RUNPOD_API_TOKEN sont obligatoires"
            )
        return cls(
            endpoint_id=endpoint_id,
            api_token=token,
            api_base_url=os.environ.get("PODALUX_RUNPOD_API_BASE_URL", cls.api_base_url).rstrip("/"),
        )


class RunPodServerlessClient(CloudVideoClient):
    """Client RunPod Serverless basé sur l'API queue `/run` + `/status/{id}`."""

    def __init__(self, config: RunPodConfig, **kwargs: Any):
        base = f"{config.api_base_url}/{config.endpoint_id}"
        super().__init__(
            CloudVideoConfig(
                submit_url=f"{base}/run",
                status_url_template=f"{base}/status/{{job_id}}",
                token=config.api_token,
            ),
            **kwargs,
        )

    def submit(self, job: VideoJob) -> RemoteJob:
        data = self._request("POST", self.config.submit_url, {"input": job.to_dict()})
        remote_id = self._remote_id(data)
        return RemoteJob(remote_id, self._status(data.get("status", "IN_QUEUE")), data)


__all__ = ["RunPodConfig", "RunPodServerlessClient"]
