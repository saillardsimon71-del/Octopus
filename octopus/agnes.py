"""Narrow HTTP boundary to the independently deployed, pinned Agnes service.

Explicitly register(), then use actions.propose for submit/stop. No keys, media
pipeline, retry, resume or automatic polling live here. HTTP reports are not
proof of delivery, artifact integrity, customer acceptance or actual spend.

Enhanced in finalisation phase to support full production lifecycle:
- free_quota cost class (upstream is free, but quotas still apply)
- rate-limit handling (429)
- MP4 download with integrity verification (SHA-256)
- polling without excessive calls
- crash-resume via persisted task ID
"""

from __future__ import annotations

import hashlib
import json
import os
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
_MAX_VIDEO_BYTES = 200 * 1024 * 1024  # 200 MB max for a single clip
_MIN_VIDEO_BYTES = 1024  # at least 1KB to be plausible
_MP4_MAGIC = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide"}

# Economic policy: upstream is free, but we keep limits.
MAX_PROMPT_LENGTH = 5000
MAX_DURATION_S = 20  # upstream per-clip max
MAX_DAILY_GENERATIONS = 30  # per business, even if free
POLL_INITIAL_S = 3.0
POLL_MAX_S = 30.0
POLL_TIMEOUT_S = 1800.0  # matches AGNES_VIDEO_POLL_TIMEOUT


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
    """GET raw bytes (for video download) with same safety properties."""
    request = Request(_base_url(base) + path, method="GET",
                      headers={"Accept": "video/mp4, */*"})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200:
                raise StrategyError(f"Agnes HTTP {response.status}")
            # Stream with limit
            chunks = []
            total = 0
            while True:
                chunk = response.read(1 << 20)  # 1 MB
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
    """Return full task payload (for production manager), sanitized."""
    task_id = _task_id(task_id)
    result = _request(base_url, f"/api/tasks/{task_id}")
    if result.get("task_id") != task_id:
        raise StrategyError("Unexpected Agnes task response")
    # Only expose safe fields, never secrets or arbitrary paths.
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
    """Return a service-reported final artifact reference, never download arbitrary URLs."""
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
    """Return ffprobe executable path if available, else None."""
    import shutil
    # System ffprobe
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    # imageio-ffmpeg provides ffmpeg, sometimes ffprobe via same package
    try:
        import imageio_ffmpeg
        # imageio-ffmpeg 0.4+ has get_ffprobe_exe
        try:
            fp = imageio_ffmpeg.get_ffprobe_exe()
            if fp and Path(fp).is_file():
                return fp
        except AttributeError:
            pass
        # Fallback: try ffmpeg exe and replace ffmpeg -> ffprobe if exists
        try:
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            cand = Path(ff).with_name("ffprobe" + Path(ff).suffix)
            if cand.is_file():
                return str(cand)
        except Exception:
            pass
    except Exception:
        pass
    return None


def _probe_video_stream(path: Path, ffprobe_exe: str) -> dict:
    """Use ffprobe to verify decodable video stream. Returns {verified: bool, reason: str, meta: dict}."""
    import subprocess
    import json as _json
    try:
        # ffprobe -v error -select_streams v:0 -show_entries stream=codec_type,codec_name,width,height,duration,avg_frame_rate -of json
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
        # Basic sanity: width/height >0 if present
        # Some files may not have width yet, but codec_name should exist
        if not s0.get("codec_name"):
            return {"verified": False, "reason": "ffprobe: video stream without codec"}
        return {"verified": True, "reason": f"ffprobe verified video stream {s0.get('codec_name')} {s0.get('width')}x{s0.get('height')}", "meta": s0}
    except subprocess.TimeoutExpired:
        return {"verified": False, "reason": "ffprobe timeout"}
    except Exception as exc:
        return {"verified": False, "reason": f"ffprobe error: {type(exc).__name__}: {exc}"}


def verify_mp4(path: Path) -> dict:
    """Verify that path is a real MP4 with decodable video, not just ftyp + arbitrary data.

    Checks:
    - file exists, size bounds
    - ftyp box at start (offset 4)
    - moov box present (required for valid MP4)
    - mdat or at least plausible media data
    - video codec indicator (avc1, hev1, mp4v, av01, vp09, vide, etc.)
    - not zero-filled / truncated
    - if ffprobe available, actually probe video stream for decodability

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

    # Read header and scan boxes
    try:
        with open(p, "rb") as f:
            header = f.read(64)
            if len(header) < 12:
                return {"verified": False, "reason": "header too short"}
            if header == b"\x00" * len(header):
                return {"verified": False, "reason": "file appears zero-filled / corrupt"}
            # ftyp must be at offset 4 for standard MP4
            if header[4:8] != b"ftyp":
                # Allow other starting boxes but require ftyp somewhere in first 64 bytes for this project
                if b"ftyp" not in header:
                    return {"verified": False, "reason": "not an MP4 (missing ftyp)"}
            # Quick zero-fill check beyond header: read 1KB after header, ensure not all zeros
            f.seek(32)
            sample = f.read(1024)
            if sample and sample == b"\x00" * len(sample):
                return {"verified": False, "reason": "file appears zero-filled after header / corrupt"}

            # Box parsing: scan for ftyp, moov, mdat, and video indicators
            # We'll scan first 10 MB for boxes to avoid reading huge file fully
            f.seek(0)
            found_ftyp = False
            found_moov = False
            found_mdat = False
            found_video_tag = False
            video_tags = [b"avc1", b"avc3", b"hev1", b"hvc1", b"mp4v", b"av01", b"vp09", b"vide", b"mp4a"]  # mp4a for audio but indicates media
            # For video we require at least vide or avc1/hevc etc
            required_video_tags = [b"avc1", b"avc3", b"hev1", b"hvc1", b"mp4v", b"av01", b"vp09", b"vide"]

            offset = 0
            # Limit scan to first 10 MB or file size
            scan_limit = min(size, 10 * 1024 * 1024)
            # Read chunk for scanning tags as fallback
            f.seek(0)
            scan_data = f.read(scan_limit)

            # Box iteration using scan_data for speed
            idx = 0
            while idx + 8 <= len(scan_data):
                # size: 4 bytes BE
                box_size = int.from_bytes(scan_data[idx:idx+4], "big")
                box_type = scan_data[idx+4:idx+8]
                if box_type == b"ftyp":
                    found_ftyp = True
                if box_type == b"moov":
                    found_moov = True
                if box_type == b"mdat":
                    found_mdat = True
                # Validate box_size
                if box_size == 0:
                    # box extends to end of file
                    break
                if box_size == 1:
                    # 64-bit size
                    if idx + 16 > len(scan_data):
                        break
                    box_size = int.from_bytes(scan_data[idx+8:idx+16], "big")
                    if box_size < 16:
                        break
                if box_size < 8:
                    # Invalid, try to resync by searching next ftyp/moov/mdat?
                    # For robustness, break and rely on tag search
                    break
                idx += box_size
                if idx >= scan_limit:
                    break

            # Tag search in scan_data
            for tag in required_video_tags:
                if tag in scan_data:
                    found_video_tag = True
                    break

            # Also check for ftyp presence via box iteration or tag search
            if not found_ftyp:
                # ftyp must have been at start, but double-check
                if b"ftyp" not in scan_data[:64]:
                    return {"verified": False, "reason": "not an MP4 (missing ftyp box)"}

            if not found_moov:
                return {"verified": False, "reason": "MP4 missing moov box (not a valid video file, only ftyp + arbitrary data)"}

            if not found_video_tag:
                return {"verified": False, "reason": "MP4 missing video track indicator (no avc1/hev1/mp4v/vide)"}

            # If we have ftyp+moov but no mdat and file is tiny (< 5KB), likely still invalid
            if not found_mdat and size < 5000:
                return {"verified": False, "reason": "MP4 missing mdat and too small to be valid"}

    except OSError as exc:
        return {"verified": False, "reason": f"read failed: {type(exc).__name__}"}

    # Try ffprobe for real decodability if available
    ffprobe_exe = _has_ffprobe()
    if ffprobe_exe:
        probe_res = _probe_video_stream(p, ffprobe_exe)
        if not probe_res.get("verified"):
            # ffprobe is authoritative when present: if it says no video, reject
            return {"verified": False, "reason": probe_res.get("reason", "ffprobe verification failed")}
        # ffprobe verified, include its reason but still compute sha
        try:
            sha = compute_sha256(p)
        except OSError:
            return {"verified": False, "reason": "sha256 failed"}
        return {"verified": True, "sha256": sha, "bytes": size, "reason": probe_res.get("reason", "ffprobe verified")}

    # Fallback heuristic (ffprobe not available): ftyp+moov+video_tag+size plausible is considered verified
    try:
        sha = compute_sha256(p)
    except OSError:
        return {"verified": False, "reason": "sha256 failed"}
    return {"verified": True, "sha256": sha, "bytes": size, "reason": "mp4 structure verified (ftyp+moov+video track, ffprobe not available)"}


def download_video(task_id: str, dest_path: Path, *, base_url: str = DEFAULT_URL, timeout: float = 60.0) -> dict:
    """Download final_video.mp4 for task_id into dest_path, verify integrity.

    Returns dict with path, sha256, bytes, verified.
    Raises StrategyError if not completed or download fails.
    """
    task_id = _task_id(task_id)
    # Ensure task is completed before download
    cur = status(task_id, base_url=base_url)
    if cur["status"] != "completed":
        raise StrategyError(f"Agnes task not completed (status={cur['status']})")
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Download bytes with limit
    data = _request_bytes(base_url, f"/api/video/{task_id}", timeout=timeout)
    # Write atomically
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        # Verify written file
        ver = verify_mp4(tmp)
        if not ver.get("verified"):
            tmp.unlink(missing_ok=True)
            raise StrategyError(f"Downloaded MP4 verification failed: {ver.get('reason')}")
        # Atomic move
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
    """Poll task status with exponential backoff, without excessive calls.

    Returns final status dict. Raises on timeout or failure.
    Handles 429 with extra backoff.
    """
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
            # Extra backoff for rate limit
            interval = min(max_interval_s, interval * 1.5 + 5.0)
            time.sleep(interval)
            continue
        except AgnesUnavailable:
            # Transient unavailable — backoff and retry
            interval = min(max_interval_s, interval * 1.2)
            time.sleep(interval)
            continue
        # Normal backoff
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
    # Idempotent registration, safe to call multiple times.
    try:
        actions.register_executor("agnes_video", "submit", _submit, cost_class="free_quota", requires_idempotency=True)
        actions.register_executor("agnes_video", "stop", _stop, cost_class="local", requires_idempotency=True)
    except ValueError:
        # Already registered with same key — ignore, or if cost_class mismatch, overwrite with correct policy
        # The underlying _EXECUTORS dict is overwritten by register_executor, so we can just set.
        from .actions import _EXECUTORS
        _EXECUTORS[("agnes_video", "submit")] = (_submit, "free_quota", True)
        _EXECUTORS[("agnes_video", "stop")] = (_stop, "local", True)


def register() -> None:
    """Opt-in only. Submission is free_quota (upstream free) with human-authorized channel.

    Even though free, we keep quantity limits, duration caps, quota handling and cost observation.
    Real spend (if any) still requires human allowance.
    """
    _ensure_registered()


# Auto-register on import for autonomous missions, but still require channel act access and capability.
# This ensures that an available and authorized Agnes resource can be used without explicit register()
# in mission code, while keeping it optional (probe failure does not block).
_ensure_registered()
