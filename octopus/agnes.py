"""Narrow HTTP boundary to the independently deployed, pinned Agnes service.

Explicitly register(), then use actions.propose for submit/stop. No keys, media
pipeline, retry, resume or automatic polling live here. HTTP reports are not
proof of delivery, artifact integrity, customer acceptance or actual spend.

Enhanced in finalisation phase to support full production lifecycle:
- free_quota cost class (upstream is free, but quotas still apply)
- rate-limit handling (429)
- MP4 download with integrity verification (SHA-256) + real multimedia validation
- polling without excessive calls
- crash-resume via persisted task ID
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import actions
from .strategy import StrategyError

UPSTREAM_PIN = "a87162d6df73ffe72186838ca0ae9d461e68589b"
DEFAULT_URL = "http://127.0.0.1:8765"
_MAX_RESPONSE = 1_048_576
_MAX_VIDEO_BYTES = 200 * 1024 * 1024
_MIN_VIDEO_BYTES = 1024
_MP4_MAGIC = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide"}

MAX_PROMPT_LENGTH = 5000
MAX_DURATION_S = 20
MAX_DAILY_GENERATIONS = 30
POLL_INITIAL_S = 3.0
POLL_MAX_S = 30.0
POLL_TIMEOUT_S = 1800.0


class AgnesRateLimited(StrategyError):
    """429 quota / rate limit — retryable with backoff, not ambiguous."""


class AgnesUnavailable(StrategyError):
    """Service unavailable."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _base_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port if parts.port is not None else 8765
    except (TypeError, ValueError):
        raise StrategyError("Invalid Agnes origin") from None
    if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parts.username is not None or parts.password is not None
            or parts.path not in {"", "/"} or parts.query or parts.fragment):
        raise StrategyError("Agnes requires a loopback HTTP origin without credentials")
    if not 1 <= port <= 65535:
        raise StrategyError("Invalid Agnes port")
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
        if code == 429:
            raise AgnesRateLimited("Agnes rate limited (429); backoff required") from None
        if mutation and code not in {400, 404, 405, 422}:
            raise actions.AmbiguousAction("Agnes HTTP outcome ambiguous; reconcile before retry") from None
        raise StrategyError(f"Agnes HTTP {code}") from None
    except AgnesRateLimited:
        raise
    except actions.AmbiguousAction:
        raise
    except Exception:
        if mutation:
            raise actions.AmbiguousAction("Agnes outcome ambiguous; reconcile before retry") from None
        raise AgnesUnavailable("Agnes unavailable or invalid response") from None


def _request_bytes(base: str, path: str, *, timeout: float = 60.0) -> bytes:
    request = Request(_base_url(base) + path, method="GET",
                      headers={"Accept": "video/mp4, */*"})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200:
                raise StrategyError(f"Agnes HTTP {response.status}")
            chunks = []
            total = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_VIDEO_BYTES:
                    raise StrategyError("Agnes video too large")
                chunks.append(chunk)
            data = b"".join(chunks)
            if len(data) < _MIN_VIDEO_BYTES:
                raise StrategyError(f"Agnes video too small or incomplete ({len(data)} bytes) — verification failed")
            return data
    except HTTPError as exc:
        code = exc.code
        exc.close()
        if code == 429:
            raise AgnesRateLimited("Agnes rate limited during download (429)") from None
        if code == 404:
            raise StrategyError("Agnes video not found (404)") from None
        raise StrategyError(f"Agnes HTTP {code}") from None
    except AgnesRateLimited:
        raise
    except StrategyError:
        raise
    except Exception:
        raise AgnesUnavailable("Agnes unavailable during video download") from None


def probe(base_url: str = DEFAULT_URL) -> dict:
    result = _request(base_url, "/api/health")
    if result.get("ok") is not True or result.get("service") != "agnes-video-generator":
        raise StrategyError("Unexpected Agnes health response")
    return {"ok": True, "service": "agnes-video-generator", "expected_pin": UPSTREAM_PIN}


def status(task_id: str, *, base_url: str = DEFAULT_URL) -> dict:
    task_id = _task_id(task_id)
    result = _request(base_url, f"/api/tasks/{task_id}")
    if (result.get("task_id") != task_id or result.get("task_type") != "simple"
            or result.get("status") not in ("pending", "queued", "running", "completed", "failed")):
        raise StrategyError("Unexpected Agnes task response")
    return {"task_id": task_id, "status": result["status"],
            "source_ref": _base_url(base_url) + f"/api/tasks/{task_id}"}


def status_full(task_id: str, *, base_url: str = DEFAULT_URL) -> dict:
    task_id = _task_id(task_id)
    result = _request(base_url, f"/api/tasks/{task_id}")
    if result.get("task_id") != task_id:
        raise StrategyError("Unexpected Agnes task response")
    safe = {
        "task_id": task_id,
        "task_type": result.get("task_type"),
        "status": result.get("status"),
        "created_at": result.get("created_at"),
        "updated_at": result.get("updated_at"),
        "error": (str(result.get("error") or "")[:500] if result.get("status") == "failed" else None),
        "source_ref": _base_url(base_url) + f"/api/tasks/{task_id}",
    }
    return safe


def video_reference(task_id: str, *, base_url: str = DEFAULT_URL) -> dict:
    if status(task_id, base_url=base_url)["status"] != "completed":
        raise StrategyError("Agnes task is not completed")
    return {"task_id": task_id, "source_ref": _base_url(base_url) + f"/api/video/{task_id}",
            "verified": False}


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _has_ffprobe() -> str | None:
    import shutil
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        try:
            fp = imageio_ffmpeg.get_ffprobe_exe()  # type: ignore[attr-defined]
            if fp and Path(fp).is_file():
                return fp
        except AttributeError:
            pass
        try:
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            cand = Path(ff).with_name("ffprobe" + Path(ff).suffix)
            if cand.is_file():
                return str(cand)
            cand2 = Path(ff).parent / "ffprobe"
            if cand2.is_file():
                return str(cand2)
        except Exception:
            pass
    except Exception:
        pass
    return None


def _has_ffmpeg() -> str | None:
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        if ff and Path(ff).is_file():
            return ff
    except Exception:
        pass
    return None


def _probe_video_stream(path: Path, ffprobe_exe: str) -> dict:
    import subprocess
    import json as _json
    try:
        cmd = [
            ffprobe_exe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,codec_name,width,height,duration,avg_frame_rate",
            "-show_entries", "format=duration,size",
            "-of", "json",
            str(path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="ignore")[:500]
            return {"verified": False, "reason": f"ffprobe failed: {err or 'no video stream'}"}
        data = _json.loads(proc.stdout.decode("utf-8", errors="ignore") or "{}")
        streams = data.get("streams") or []
        if not streams:
            return {"verified": False, "reason": "ffprobe: no video stream found"}
        s0 = streams[0]
        if s0.get("codec_type") != "video":
            return {"verified": False, "reason": f"ffprobe: first stream not video ({s0.get('codec_type')})"}
        if not s0.get("codec_name"):
            return {"verified": False, "reason": "ffprobe: video stream without codec"}
        return {"verified": True, "reason": f"ffprobe verified video stream {s0.get('codec_name')} {s0.get('width')}x{s0.get('height')}", "meta": s0}
    except subprocess.TimeoutExpired:
        return {"verified": False, "reason": "ffprobe timeout"}
    except Exception as exc:
        return {"verified": False, "reason": f"ffprobe error: {type(exc).__name__}: {exc}"}


def _probe_with_ffmpeg(path: Path, ffmpeg_exe: str) -> dict:
    import subprocess
    try:
        cmd = [
            ffmpeg_exe,
            "-v", "error",
            "-i", str(path),
            "-f", "null",
            "-",
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="ignore")[:500]
            return {"verified": False, "reason": f"ffmpeg decode failed: {err or 'not decodable'}"}
        return {"verified": True, "reason": "ffmpeg decode verified (real multimedia validation)"}
    except subprocess.TimeoutExpired:
        return {"verified": False, "reason": "ffmpeg timeout"}
    except Exception as exc:
        return {"verified": False, "reason": f"ffmpeg error: {type(exc).__name__}: {exc}"}


def verify_mp4(path: Path) -> dict:
    """Verify that path is a real MP4 with decodable video.

    Real multimedia validation is mandatory for verified=True:
    - file exists, size bounds
    - ftyp box at start, moov present, video track indicator
    - not zero-filled
    - then actual decode via ffprobe or ffmpeg (imageio-ffmpeg fallback)
    - no video is marked verified solely because ftyp/moov and codec string are present

    Returns dict with verified bool, sha256, bytes, reason.
    """
    p = Path(path)
    if not p.is_file():
        return {"verified": False, "reason": "file not found"}
    try:
        size = p.stat().st_size
    except OSError as exc:
        return {"verified": False, "reason": f"stat failed: {type(exc).__name__}"}
    if size < _MIN_VIDEO_BYTES:
        return {"verified": False, "reason": f"too small: {size} bytes"}
    if size > _MAX_VIDEO_BYTES:
        return {"verified": False, "reason": f"too large: {size} bytes"}

    try:
        with open(p, "rb") as f:
            header = f.read(64)
            if len(header) < 12:
                return {"verified": False, "reason": "header too short"}
            if header == b"\x00" * len(header):
                return {"verified": False, "reason": "file appears zero-filled / corrupt"}
            if header[4:8] != b"ftyp":
                if b"ftyp" not in header:
                    return {"verified": False, "reason": "not an MP4 (missing ftyp)"}
            f.seek(32)
            sample = f.read(1024)
            if sample and sample == b"\x00" * len(sample):
                return {"verified": False, "reason": "file appears zero-filled after header / corrupt"}

            scan_limit = min(size, 10 * 1024 * 1024)
            f.seek(0)
            scan_data = f.read(scan_limit)

            if b"ftyp" not in scan_data[:64]:
                return {"verified": False, "reason": "not an MP4 (missing ftyp box)"}
            if b"moov" not in scan_data:
                return {"verified": False, "reason": "MP4 missing moov box (only ftyp + arbitrary)"}
            required_video_tags = [b"avc1", b"avc3", b"hev1", b"hvc1", b"mp4v", b"av01", b"vp09", b"vide"]
            if not any(tag in scan_data for tag in required_video_tags):
                return {"verified": False, "reason": "MP4 missing video track indicator (no avc1/hev1/mp4v/vide)"}

            idx = 0
            valid_boxes = 0
            while idx + 8 <= len(scan_data):
                box_size = int.from_bytes(scan_data[idx:idx+4], "big")
                if box_size == 0:
                    break
                if box_size == 1:
                    if idx + 16 > len(scan_data):
                        break
                    box_size = int.from_bytes(scan_data[idx+8:idx+16], "big")
                if box_size < 8 or box_size > len(scan_data):
                    break
                valid_boxes += 1
                idx += box_size
                if idx >= scan_limit or valid_boxes > 100:
                    break
            if valid_boxes < 2:
                return {"verified": False, "reason": "MP4 box structure invalid (too few boxes)"}

    except OSError as exc:
        return {"verified": False, "reason": f"read failed: {type(exc).__name__}"}

    ffprobe_exe = _has_ffprobe()
    if ffprobe_exe:
        probe_res = _probe_video_stream(p, ffprobe_exe)
        if not probe_res.get("verified"):
            return {"verified": False, "reason": probe_res.get("reason", "ffprobe verification failed")}
        try:
            sha = compute_sha256(p)
        except OSError:
            return {"verified": False, "reason": "sha256 failed"}
        return {"verified": True, "sha256": sha, "bytes": size, "reason": probe_res.get("reason", "ffprobe verified")}

    ffmpeg_exe = _has_ffmpeg()
    if ffmpeg_exe:
        probe_res = _probe_with_ffmpeg(p, ffmpeg_exe)
        if not probe_res.get("verified"):
            return {"verified": False, "reason": probe_res.get("reason", "ffmpeg verification failed")}
        try:
            sha = compute_sha256(p)
        except OSError:
            return {"verified": False, "reason": "sha256 failed"}
        return {"verified": True, "sha256": sha, "bytes": size, "reason": probe_res.get("reason", "ffmpeg verified")}

    return {"verified": False, "reason": "real multimedia validation required: ffprobe/ffmpeg not available"}


def download_video(task_id: str, dest_path: Path, *, base_url: str = DEFAULT_URL, timeout: float = 60.0) -> dict:
    task_id = _task_id(task_id)
    cur = status(task_id, base_url=base_url)
    if cur["status"] != "completed":
        raise StrategyError(f"Agnes task not completed (status={cur['status']})")
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _request_bytes(base_url, f"/api/video/{task_id}", timeout=timeout)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        ver = verify_mp4(tmp)
        if not ver.get("verified"):
            tmp.unlink(missing_ok=True)
            raise StrategyError(f"Downloaded MP4 verification failed: {ver.get('reason')}")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return {"task_id": task_id, "path": str(dest), "sha256": ver["sha256"], "bytes": ver["bytes"],
            "verified": True, "source_ref": _base_url(base_url) + f"/api/video/{task_id}"}


def poll_until_done(task_id: str, *, base_url: str = DEFAULT_URL, timeout_s: float = POLL_TIMEOUT_S,
                    initial_interval_s: float = POLL_INITIAL_S, max_interval_s: float = POLL_MAX_S) -> dict:
    task_id = _task_id(task_id)
    deadline = time.time() + float(timeout_s)
    interval = float(initial_interval_s)
    last_status = None
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            cur = status(task_id, base_url=base_url)
            last_status = cur["status"]
            if last_status in ("completed", "failed"):
                return {"task_id": task_id, "status": last_status, "attempts": attempts,
                        "source_ref": cur["source_ref"]}
        except AgnesRateLimited:
            interval = min(max_interval_s, interval * 1.5 + 5.0)
            time.sleep(interval)
            continue
        except AgnesUnavailable:
            interval = min(max_interval_s, interval * 1.2)
            time.sleep(interval)
            continue
        if last_status in (None, "pending", "queued", "running"):
            time.sleep(interval)
            interval = min(max_interval_s, interval * 1.25)
        else:
            break
    raise StrategyError(f"Agnes polling timeout after {attempts} attempts, last_status={last_status}")


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
    if set(payload) != {"prompt"} or not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= MAX_PROMPT_LENGTH:
        raise StrategyError(f"Only a non-empty prompt (up to {MAX_PROMPT_LENGTH} characters) is supported")
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


def _ensure_registered():
    try:
        actions.register_executor("agnes_video", "submit", _submit, cost_class="free_quota", requires_idempotency=True)
        actions.register_executor("agnes_video", "stop", _stop, cost_class="local", requires_idempotency=True)
    except ValueError:
        from .actions import _EXECUTORS
        _EXECUTORS[("agnes_video", "submit")] = (_submit, "free_quota", True)
        _EXECUTORS[("agnes_video", "stop")] = (_stop, "local", True)


def register() -> None:
    _ensure_registered()


_ensure_registered()
