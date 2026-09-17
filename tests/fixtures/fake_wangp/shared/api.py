"""Faux `shared.api` de WanGP : même contrat que l'API officielle (sessions, jobs, flux d'événements).

Mode choisi par FAKE_WANGP_MODE : ok | fail_second | fail_all | slow | crash.
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace


@dataclass
class ProgressUpdate:
    phase: str
    status: str
    progress: int
    current_step: int | None = None
    total_steps: int | None = None


@dataclass
class StreamMessage:
    stream: str
    text: str


@dataclass
class GenerationError:
    message: str
    task_index: int | None = None
    task_id: object = None
    stage: str | None = None


@dataclass
class GenerationResult:
    success: bool
    generated_files: list
    errors: list
    total_tasks: int
    successful_tasks: int
    failed_tasks: int
    artifacts: tuple = ()


@dataclass
class SessionEvent:
    kind: str
    data: object = None
    timestamp: float = field(default_factory=time.time)


class SessionStream:
    def __init__(self):
        self._q, self._closed, self._end = queue.Queue(), threading.Event(), object()

    def put(self, kind, data=None):
        self._q.put(SessionEvent(kind, data))

    def close(self):
        self._closed.set()
        self._q.put(self._end)

    def get(self, timeout=None):
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        return None if item is self._end else item

    @property
    def closed(self):
        return self._closed.is_set()


class SessionJob:
    def __init__(self):
        self.events, self._done, self._cancel, self._result = SessionStream(), threading.Event(), threading.Event(), None

    def cancel(self):
        self._cancel.set()

    def result(self, timeout=None):
        if not self._done.wait(timeout):
            raise TimeoutError
        return self._result

    @property
    def done(self):
        return self._done.is_set()


def _write_video(path: Path) -> None:
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=480x832:rate=24",
                        "-t", "1", "-pix_fmt", "yuv420p", str(path)], check=True)
    else:
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")


class WanGPSession:
    def __init__(self, root=None, output_dir=None, **kwargs):
        self.output_dir = Path(output_dir or Path(root) / "outputs")
        self._runtime = SimpleNamespace(module=SimpleNamespace(WanGP_version="fake-1.0"))

    def list_model_metadata(self, include_availability=False, **filters):
        return [{"model_type": "minimax_h3_fl2va", "name": "MiniMax H3 FL2VA 20B", "availability": "available"},
                {"model_type": "minimax_h3_ref2va_pruned", "name": "MiniMax H3 Ref2VA", "availability": "missing"},
                {"model_type": "flux", "name": "Flux", "availability": "available"}]

    def get_default_settings(self, model_type):
        return {"model_type": model_type, "resolution": "832x480", "num_inference_steps": 30, "video_length": 81,
                "seed": -1}

    def get_model_schema(self, model_type):
        return {"name": model_type, "fps": 24}

    def submit_task(self, settings):
        return self.submit_manifest([settings])

    def submit_manifest(self, settings_list):
        job, mode = SessionJob(), os.environ.get("FAKE_WANGP_MODE", "ok")
        if mode == "crash":
            os._exit(9)

        def work():
            files, errors, n = [], [], len(settings_list)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            for i, settings in enumerate(settings_list):
                steps = 40 if mode == "slow" else 4
                cancelled = False
                for step in range(1, steps + 1):
                    if job._cancel.is_set():
                        cancelled = True
                        break
                    job.events.put("progress", ProgressUpdate("inference", f"Prompt {i + 1}/{n} | Denoising",
                                                              int(step * 100 / steps), step, steps))
                    job.events.put("stream", StreamMessage("stdout", f"step {step} seed={settings.get('seed')}"))
                    time.sleep(0.25 if mode == "slow" else 0.01)
                if cancelled:
                    errors.append(GenerationError("cancelled", i + 1, stage="cancelled"))
                    break
                if mode == "fail_all" or (mode == "fail_second" and i == 1):
                    err = GenerationError(f"CUDA out of memory (tâche {i + 1})", i + 1, stage="generation")
                    errors.append(err)
                    job.events.put("error", err)
                    continue
                out = self.output_dir / f"clip_{i + 1:03d}.mp4"
                _write_video(out)
                files.append(str(out))
            job._result = GenerationResult(not errors, files, errors, n, len(files), n - len(files))
            job.events.put("completed", job._result)
            job._done.set()
            job.events.close()

        threading.Thread(target=work, daemon=True).start()
        return job


def init(**kwargs):
    return WanGPSession(**kwargs)
