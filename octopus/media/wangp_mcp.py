"""Client WanGP distant via son API MCP v2 streamable HTTP."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class WanGPMCPError(RuntimeError):
    pass


@dataclass(frozen=True)
class WanGPMCPConfig:
    url: str
    username: str | None = None
    password: str | None = None
    bearer_token: str | None = None
    request_timeout_s: float = 120.0
    poll_s: float = 2.0

    def __post_init__(self):
        if bool(self.username) != bool(self.password):
            raise ValueError("identifiants WanGP MCP incomplets")
        if self.bearer_token and self.username:
            raise ValueError("configurer une seule authentification WanGP MCP")

    @classmethod
    def from_env(cls) -> "WanGPMCPConfig":
        url = os.environ.get("OCTOPUS_WANGP_MCP_URL", "").strip()
        if not url:
            raise WanGPMCPError("OCTOPUS_WANGP_MCP_URL absente")
        return cls(
            url=url,
            username=os.environ.get("OCTOPUS_WANGP_MCP_USER", "").strip() or None,
            password=os.environ.get("OCTOPUS_WANGP_MCP_PASSWORD", "").strip() or None,
            bearer_token=os.environ.get("OCTOPUS_WANGP_MCP_TOKEN", "").strip() or None,
            request_timeout_s=float(os.environ.get("OCTOPUS_WANGP_MCP_REQUEST_TIMEOUT_S", "120")),
            poll_s=float(os.environ.get("OCTOPUS_WANGP_MCP_POLL_S", "2")),
        )


class WanGPMCPClient:
    def __init__(self, config: WanGPMCPConfig, *, call_tool: Callable[[str, dict], dict] | None = None,
                 sleep: Callable[[float], None] = time.sleep, open_url: Callable | None = None):
        self.config = config
        self._call_tool = call_tool or self._call_remote
        self._sleep = sleep
        self._open_url = open_url or (lambda request, timeout: urlopen(request, timeout=timeout))

    def _call_remote(self, name: str, arguments: dict) -> dict:
        try:
            return asyncio.run(self._call_remote_async(name, arguments))
        except WanGPMCPError:
            raise
        except Exception as exc:
            raise WanGPMCPError(f"WanGP MCP {name}: {type(exc).__name__}: {exc}") from exc

    async def _call_remote_async(self, name: str, arguments: dict) -> dict:
        try:
            import httpx
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise WanGPMCPError("client MCP absent: installer requirements-local.txt") from exc

        headers = {}
        if self.config.bearer_token:
            headers["Authorization"] = f"Bearer {self.config.bearer_token}"
        auth = None
        if self.config.username or self.config.password:
            if not self.config.username or not self.config.password:
                raise WanGPMCPError("identifiants WanGP MCP incomplets")
            auth = (self.config.username, self.config.password)
        timeout = httpx.Timeout(self.config.request_timeout_s)
        async with httpx.AsyncClient(headers=headers, auth=auth, timeout=timeout) as http_client:
            async with streamable_http_client(self.config.url, http_client=http_client) as streams:
                async with ClientSession(streams[0], streams[1],
                                         read_timeout_seconds=timedelta(seconds=self.config.request_timeout_s)) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        if result.isError:
            detail = " ".join(str(getattr(item, "text", item)) for item in result.content)
            raise WanGPMCPError(f"outil {name} en échec: {detail[:1000]}")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            return structured
        for item in result.content:
            text = getattr(item, "text", None)
            if not text:
                continue
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return decoded
        raise WanGPMCPError(f"outil {name} sans résultat structuré")

    def discover(self, *, query: str = "", output: str = "video", limit: int = 50) -> list[dict]:
        result = self._call_tool("wangp_models", {
            "query": query,
            "filters": {"main_output": output},
            "limit": max(1, min(int(limit), 50)),
            "offset": 0,
        })
        models = result.get("models") or []
        if not isinstance(models, list):
            raise WanGPMCPError("wangp_models a renvoyé une liste invalide")
        found = []
        for model in models:
            if not isinstance(model, dict):
                continue
            outputs = model.get("main_output") or []
            outputs = [outputs] if isinstance(outputs, str) else outputs
            if output in outputs:
                found.append(model)
        return found

    def prepare(self, model_type: str, prompt: str, overrides: dict | None = None) -> dict:
        model_type = str(model_type or "").strip()
        prompt = str(prompt or "").strip()
        if not model_type or not prompt:
            raise ValueError("model_type et prompt sont obligatoires")
        schema = self._call_tool("wangp_model", {"model_type": model_type, "view": "schema"})
        if not isinstance(schema, dict) or not schema:
            raise WanGPMCPError(f"modèle WanGP inconnu: {model_type}")
        defaults = self._call_tool("wangp_model", {"model_type": model_type, "view": "defaults"})
        if not isinstance(defaults, dict):
            raise WanGPMCPError(f"réglages WanGP invalides pour {model_type}")
        return {**defaults, "model_type": model_type, "prompt": prompt, **(overrides or {})}

    def template_settings(self, tool_id: str = "gen_video", template: str = "default") -> dict:
        result = self._call_tool("wangp_get_deepy_template_settings", {
            "tool_id": str(tool_id), "template": str(template),
        })
        if not isinstance(result.get("settings"), dict):
            raise WanGPMCPError(f"réglages de template WanGP invalides: {tool_id}/{template}")
        return result

    def download(self, media_id: str, target: str | Path, *, max_bytes: int = 4 * 1024**3) -> Path:
        transfer = self._call_tool("wangp_create_gallery_download", {"media_id": str(media_id)})
        relative_url = str(transfer.get("download_url") or "")
        declared_size = int(transfer.get("size") or 0)
        if not relative_url:
            raise WanGPMCPError("WanGP n'a pas fourni d'URL de téléchargement")
        if declared_size > max_bytes:
            raise WanGPMCPError(f"média WanGP trop volumineux: {declared_size} octets")

        headers = {}
        if self.config.bearer_token:
            headers["Authorization"] = f"Bearer {self.config.bearer_token}"
        elif self.config.username:
            token = base64.b64encode(f"{self.config.username}:{self.config.password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        request = Request(urljoin(self.config.url, relative_url), headers=headers, method="GET")
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = Path(f"{destination}.part")
        written = 0
        try:
            with self._open_url(request, self.config.request_timeout_s) as response, partial.open("wb") as output:
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length > max_bytes:
                    raise WanGPMCPError(f"média WanGP trop volumineux: {content_length} octets")
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise WanGPMCPError(f"média WanGP trop volumineux: plus de {max_bytes} octets")
                    output.write(chunk)
            if not written:
                raise WanGPMCPError("WanGP a renvoyé un fichier vide")
            if declared_size and written != declared_size:
                raise WanGPMCPError(f"téléchargement WanGP incomplet: {written}/{declared_size} octets")
            partial.replace(destination)
            return destination
        except Exception as exc:
            partial.unlink(missing_ok=True)
            if isinstance(exc, WanGPMCPError):
                raise
            raise WanGPMCPError(f"téléchargement WanGP impossible: {type(exc).__name__}: {exc}") from exc

    def submit(self, settings: dict) -> dict:
        result = self._call_tool("wangp_generate", {"source": settings, "wait": False})
        if not result.get("job_id"):
            raise WanGPMCPError("WanGP n'a pas renvoyé de job_id")
        return result

    def job(self, job_id: str) -> dict:
        return self._call_tool("wangp_get_job", {"job_id": str(job_id), "event_limit": 50})

    def cancel(self, job_id: str) -> dict:
        return self._call_tool("wangp_cancel_job", {"job_id": str(job_id), "event_limit": 50})

    def wait(self, job_id: str, *, timeout_s: float) -> dict:
        deadline = time.monotonic() + float(timeout_s)
        while time.monotonic() < deadline:
            snapshot = self.job(job_id)
            if snapshot.get("done"):
                result = snapshot.get("result")
                if not isinstance(result, dict):
                    raise WanGPMCPError(f"job WanGP {job_id} terminé sans résultat")
                if not result.get("success"):
                    errors = result.get("errors") or []
                    raise WanGPMCPError(f"job WanGP {job_id} en échec: {errors}")
                return result
            self._sleep(self.config.poll_s)
        raise WanGPMCPError(f"timeout du job WanGP {job_id} après {timeout_s:g} s")
