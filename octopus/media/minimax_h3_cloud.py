"""MiniMax-H3 cloud backend.

Le poste local ne charge jamais H3. OCTOPUS envoie un workflow ComfyUI à un endpoint
RunPod Serverless compatible avec les workers H3 publics de référence.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

FPS = 24
DEFAULT_WIDTH = 768
DEFAULT_HEIGHT = 1344
DEFAULT_DURATION_S = 5.0
MAX_DURATION_S = 15.0

H3_MODELS = {
    "diffusion": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
}


class MiniMaxH3CloudError(RuntimeError):
    pass


def align_frames(duration_s: float) -> int:
    if duration_s <= 0 or duration_s > MAX_DURATION_S:
        raise ValueError(f"durée H3 hors limites: 0 < durée <= {MAX_DURATION_S}s")
    target = max(5, round(duration_s * FPS))
    while target % 17 != 5:
        target += 1
    return target


def _canvas(width: int, height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("dimensions H3 invalides")
    ratio = width / height
    short = 768
    max_pixels = 768 * 1344
    if ratio >= 1:
        h = float(short)
        w = h * ratio
    else:
        w = float(short)
        h = w / ratio
    if w * h > max_pixels:
        scale = (max_pixels / (w * h)) ** 0.5
        w *= scale
        h *= scale
    return max(32, round(w / 32) * 32), max(32, round(h / 32) * 32)


def build_t2v_workflow(prompt: str, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
                       duration_s: float = DEFAULT_DURATION_S, seed: int | None = None,
                       steps: int = 8, output_prefix: str = "octopus_h3") -> dict[str, Any]:
    """Construit le graphe H3 T2V à partir du graphe ComfyUI officiel, sans keyframes."""
    prompt = str(prompt).strip()
    if not prompt:
        raise ValueError("prompt H3 vide")
    if not 1 <= steps <= 24:
        raise ValueError("steps H3 hors limites [1,24]")
    width, height = _canvas(int(width), int(height))
    length = align_frames(float(duration_s))
    seed = int(seed if seed is not None else 42)

    workflow: dict[str, Any] = {
        "1": {
            "inputs": {"unet_name": H3_MODELS["diffusion"], "weight_dtype": "default"},
            "class_type": "UNETLoader",
            "_meta": {"title": "MiniMax H3 diffusion"},
        },
        "3": {
            "inputs": {"clip_name": H3_MODELS["text_encoder"], "type": "minimax", "device": "default"},
            "class_type": "CLIPLoader",
            "_meta": {"title": "MiniMax H3 text encoder"},
        },
        "4": {
            "inputs": {"vae_name": H3_MODELS["video_vae"]},
            "class_type": "VAELoader",
            "_meta": {"title": "MiniMax H3 video VAE"},
        },
        "5": {
            "inputs": {"vae_name": H3_MODELS["audio_vae"]},
            "class_type": "VAELoader",
            "_meta": {"title": "MiniMax H3 audio VAE"},
        },
        "10": {
            "inputs": {"noise_seed": seed},
            "class_type": "RandomNoise",
        },
        "11": {
            "inputs": {"sampler_name": "res_multistep"},
            "class_type": "KSamplerSelect",
        },
        "12": {
            "inputs": {"scheduler": "simple", "steps": int(steps), "denoise": 1.0, "model": ["1", 0]},
            "class_type": "BasicScheduler",
        },
        "13": {
            "inputs": {"model": ["1", 0], "conditioning": ["20", 0]},
            "class_type": "BasicGuider",
        },
        "20": {
            "inputs": {
                "prompt": prompt,
                "width": width,
                "height": height,
                "length": length,
                "clip": ["3", 0],
                "vae": ["4", 0],
            },
            "class_type": "MiniMaxH3ImageToVideo",
            "_meta": {"title": "MiniMax H3 T2V"},
        },
        "14": {
            "inputs": {
                "noise": ["10", 0],
                "guider": ["13", 0],
                "sampler": ["11", 0],
                "sigmas": ["12", 0],
                "latent_image": ["20", 1],
            },
            "class_type": "SamplerCustomAdvanced",
        },
        "50": {
            "inputs": {"samples": ["14", 0], "vae": ["4", 0]},
            "class_type": "VAEDecode",
        },
        "51": {
            "inputs": {"samples": ["14", 0], "vae": ["5", 0]},
            "class_type": "VAEDecodeAudio",
        },
        "52": {
            "inputs": {"fps": FPS, "bit_depth": 8, "images": ["50", 0], "audio": ["51", 0]},
            "class_type": "CreateVideo",
        },
        "53": {
            "inputs": {
                "frame_rate": FPS,
                "loop_count": 0,
                "filename_prefix": f"video/{output_prefix}",
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 19,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
                "images": ["50", 0],
                "audio": ["51", 0],
            },
            "class_type": "VHS_VideoCombine",
        },
    }
    return workflow


@dataclass(frozen=True)
class MiniMaxH3Config:
    endpoint_id: str
    api_token: str
    api_base_url: str = "https://api.runpod.ai/v2"
    request_timeout_s: float = 60.0
    poll_s: float = 3.0
    max_poll_s: float = 10.0
    timeout_s: float = 45 * 60.0

    @classmethod
    def from_env(cls) -> "MiniMaxH3Config":
        endpoint = os.environ.get("OCTOPUS_MINIMAX_H3_ENDPOINT_ID", "").strip()
        token = os.environ.get("OCTOPUS_MINIMAX_H3_API_TOKEN", "").strip()
        if not endpoint or not token:
            raise MiniMaxH3CloudError(
                "MiniMax H3 cloud non configuré: OCTOPUS_MINIMAX_H3_ENDPOINT_ID et "
                "OCTOPUS_MINIMAX_H3_API_TOKEN sont obligatoires"
            )
        return cls(
            endpoint_id=endpoint,
            api_token=token,
            api_base_url=os.environ.get("OCTOPUS_MINIMAX_H3_API_BASE_URL", cls.api_base_url).rstrip("/"),
            request_timeout_s=float(os.environ.get("OCTOPUS_MINIMAX_H3_REQUEST_TIMEOUT_S", "60")),
            poll_s=float(os.environ.get("OCTOPUS_MINIMAX_H3_POLL_S", "3")),
            max_poll_s=float(os.environ.get("OCTOPUS_MINIMAX_H3_MAX_POLL_S", "10")),
            timeout_s=float(os.environ.get("OCTOPUS_MINIMAX_H3_TIMEOUT_S", str(45 * 60))),
        )


@dataclass(frozen=True)
class H3Result:
    remote_id: str
    video_url: str | None = None
    video_bytes: bytes | None = None
    filename: str = "h3.mp4"
    raw: Mapping[str, Any] = None


class MiniMaxH3RunPodClient:
    def __init__(self, config: MiniMaxH3Config):
        self.config = config
        self.base = f"{config.api_base_url}/{config.endpoint_id}"

    def _request(self, method: str, url: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Authorization": f"Bearer {self.config.api_token}"}
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.config.request_timeout_s) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise MiniMaxH3CloudError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MiniMaxH3CloudError(f"réseau H3: {exc}") from exc
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise MiniMaxH3CloudError("réponse H3 non JSON") from exc
        if not isinstance(value, dict):
            raise MiniMaxH3CloudError("réponse H3 inattendue")
        return value

    @staticmethod
    def _remote_id(data: Mapping[str, Any]) -> str:
        value = data.get("id") or data.get("job_id")
        if not value:
            raise MiniMaxH3CloudError("RunPod H3 sans identifiant")
        return str(value)

    def submit(self, workflow: Mapping[str, Any]) -> str:
        data = self._request("POST", f"{self.base}/run", {"input": {"workflow": workflow}})
        return self._remote_id(data)

    def status(self, remote_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self.base}/status/{remote_id}")

    def wait(self, remote_id: str) -> H3Result:
        deadline = time.monotonic() + self.config.timeout_s
        delay = max(0.5, self.config.poll_s)
        while time.monotonic() < deadline:
            data = self.status(remote_id)
            status = str(data.get("status") or "IN_PROGRESS").upper()
            if status in {"COMPLETED", "SUCCEEDED"}:
                output = data.get("output") if isinstance(data.get("output"), Mapping) else data.get("result", {})
                if not isinstance(output, Mapping):
                    output = {}
                url = output.get("video_url") or data.get("video_url")
                if url:
                    return H3Result(remote_id, str(url), None, "h3.mp4", data)
                files = output.get("files") or data.get("files") or []
                if isinstance(files, list):
                    for item in files:
                        if not isinstance(item, Mapping):
                            continue
                        mime = str(item.get("type") or item.get("mime") or "")
                        name = str(item.get("filename") or "h3.mp4")
                        encoded = item.get("data")
                        if encoded and (mime.startswith("video/") or Path(name).suffix.lower() == ".mp4"):
                            try:
                                return H3Result(remote_id, None, base64.b64decode(str(encoded)), name, data)
                            except Exception as exc:
                                raise MiniMaxH3CloudError(f"vidéo H3 base64 invalide: {exc}") from exc
                raise MiniMaxH3CloudError("H3 terminé sans vidéo exploitable")
            if status in {"FAILED", "ERROR", "CANCELLED", "CANCELED", "TIMED_OUT", "EXPIRED"}:
                detail = data.get("error") or data.get("message") or status
                raise MiniMaxH3CloudError(f"H3 {status}: {detail}")
            time.sleep(delay)
            delay = min(self.config.max_poll_s, delay * 1.4)
        raise MiniMaxH3CloudError(f"timeout H3 distant: {remote_id}")

    def generate(self, prompt: str, *, width: int, height: int, duration_s: float, seed: int | None = None,
                 steps: int = 8) -> H3Result:
        workflow = build_t2v_workflow(prompt, width=width, height=height, duration_s=duration_s,
                                      seed=seed, steps=steps, output_prefix=f"octopus_{uuid.uuid4().hex[:12]}")
        remote_id = self.submit(workflow)
        return self.wait(remote_id)


def save_result(result: H3Result, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if result.video_bytes is not None:
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(result.video_bytes)
        tmp.replace(target)
        return target
    if result.video_url:
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(result.video_url, timeout=120) as response, tmp.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            if tmp.stat().st_size <= 0:
                raise MiniMaxH3CloudError("vidéo H3 distante vide")
            tmp.replace(target)
            return target
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise MiniMaxH3CloudError(f"récupération vidéo H3 impossible: {exc}") from exc
    raise MiniMaxH3CloudError("résultat H3 sans bytes ni URL")


__all__ = ["MiniMaxH3CloudConfig", "MiniMaxH3Config", "MiniMaxH3CloudError", "MiniMaxH3RunPodClient",
           "H3Result", "align_frames", "build_t2v_workflow", "save_result"]
