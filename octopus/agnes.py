"""Narrow HTTP boundary to the independently deployed, pinned Agnes service.

Explicitly register(), then use actions.propose for submit/stop. No keys, media
pipeline, retry, resume or automatic polling live here. HTTP reports are not
proof of delivery, artifact integrity, customer acceptance or actual spend.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import actions
from .strategy import StrategyError

UPSTREAM_PIN = "a87162d6df73ffe72186838ca0ae9d461e68589b"
DEFAULT_URL = "http://127.0.0.1:8765"
_MAX_RESPONSE = 1_048_576


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _base_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parts.username is not None or parts.password is not None
            or parts.path not in {"", "/"} or parts.query or parts.fragment):
        raise StrategyError("Agnes requires a loopback HTTP origin without credentials")
    port = parts.port or 8765
    if not 1 <= port <= 65535:
        raise StrategyError("Invalid Agnes port")
    # Resolve localhost to a literal loopback; do not depend on DNS or proxy env.
    host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{port}"


def _task_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{12}", value):
        raise StrategyError("Invalid Agnes task ID")
    return value


def _request(base: str, path: str, *, form: dict | None = None) -> dict:
    mutation = form is not None
    request = Request(_base_url(base) + path, method="POST" if mutation else "GET",
                      data=urlencode(form).encode("utf-8") if mutation else None,
                      headers={"Accept": "application/json"})
    try:
        # No ambient proxy, redirect, authentication or automatic retry.
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=15) as response:
            if response.status != 200:
                raise ValueError("Unexpected status")
            raw = response.read(_MAX_RESPONSE + 1)
            if len(raw) > _MAX_RESPONSE:
                raise ValueError("Response too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("Expected object")
            return result
    except HTTPError as exc:
        code = exc.code
        exc.close()
        # These pinned FastAPI rejections occur before task creation/stop.
        if mutation and code not in {400, 404, 405, 422}:
            raise actions.AmbiguousAction("Agnes HTTP outcome ambiguous; reconcile before retry") from None
        raise StrategyError(f"Agnes HTTP {code}") from None
    except Exception:
        # Never echo server bodies, URLs or transport exception text (may contain secrets).
        if mutation:
            raise actions.AmbiguousAction("Agnes outcome ambiguous; reconcile before retry") from None
        raise StrategyError("Agnes unavailable or invalid response") from None


def probe(base_url: str = DEFAULT_URL) -> dict:
    result = _request(base_url, "/api/health")
    if result.get("ok") is not True or result.get("service") != "agnes-video-generator":
        raise StrategyError("Unexpected Agnes health response")
    return {"ok": True, "service": "agnes-video-generator", "expected_pin": UPSTREAM_PIN}


def status(task_id: str, *, base_url: str = DEFAULT_URL) -> dict:
    task_id = _task_id(task_id)
    result = _request(base_url, f"/api/tasks/{task_id}")
    if (result.get("task_id") != task_id or result.get("task_type") != "simple"
            or result.get("status") not in {"pending", "queued", "running", "completed", "failed"}):
        raise StrategyError("Unexpected Agnes task response")
    return {"task_id": task_id, "status": result["status"],
            "source_ref": _base_url(base_url) + f"/api/tasks/{task_id}"}


def video_reference(task_id: str, *, base_url: str = DEFAULT_URL) -> dict:
    """Return a service-reported final artifact reference, never download arbitrary URLs."""
    if status(task_id, base_url=base_url)["status"] != "completed":
        raise StrategyError("Agnes task is not completed")
    # The pinned service has no structured artifact definitions for simple tasks.
    # Its simple pipeline writes final_video.mp4, served by this fixed endpoint.
    return {"task_id": task_id, "source_ref": _base_url(base_url) + f"/api/video/{task_id}",
            "verified": False}


def _channel_url(channel: dict, capability: str) -> str:
    if channel.get("kind") != "agnes_video" or channel.get("status") != "active" or channel.get("access") != "act":
        raise StrategyError("Active human-authorized Agnes channel required")
    capabilities = channel.get("capabilities", [])
    if isinstance(capabilities, str):
        capabilities = json.loads(capabilities)
    if not isinstance(capabilities, list) or capability not in capabilities:
        raise StrategyError(f"Channel capability {capability} required")
    return _base_url(channel.get("locator") or "")


def _submit(channel: dict, payload: dict) -> dict:
    base = _channel_url(channel, "agnes_submit")
    prompt = payload.get("prompt")
    if set(payload) != {"prompt"} or not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 5000:
        raise StrategyError("Only a non-empty prompt (up to 5000 characters) is supported")
    result = _request(base, "/api/tasks/simple", form={"prompt": prompt.strip(), "mode": "t2v"})
    try:
        task_id = _task_id(result.get("task_id"))
        if result.get("ok") is not True:
            raise StrategyError("Missing acknowledgement")
    except StrategyError:
        raise actions.AmbiguousAction("Agnes submission acknowledgement invalid; reconcile before retry") from None
    return {"task_id": task_id, "source_ref": base + f"/api/tasks/{task_id}",
            "observation": "Agnes acknowledged task creation; generation and delivery are not verified",
            "upstream_pin": UPSTREAM_PIN}


def _stop(channel: dict, payload: dict) -> dict:
    base = _channel_url(channel, "agnes_stop")
    if set(payload) != {"task_id"}:
        raise StrategyError("Only task_id is supported")
    task_id = _task_id(payload.get("task_id"))
    result = _request(base, f"/api/tasks/{task_id}/stop", form={})
    if result.get("ok") is not True or result.get("task_id") != task_id:
        raise actions.AmbiguousAction("Agnes stop acknowledgement invalid; reconcile before retry")
    return {"task_id": task_id, "source_ref": base + f"/api/tasks/{task_id}",
            "observation": "Agnes acknowledged stop request; cancellation and cost remain to reconcile"}


def register() -> None:
    """Opt-in only. Paid submission requires a declared amount and existing allowance."""
    actions.register_executor("agnes_video", "submit", _submit, cost_class="paid", requires_idempotency=True)
    actions.register_executor("agnes_video", "stop", _stop, cost_class="local", requires_idempotency=True)
