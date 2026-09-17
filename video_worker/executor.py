"""Exécution isolée du pipeline FORGE existant.

Cette couche réutilise le générateur TTS, le projet Remotion et le QC technique déjà
présents dans le dépôt. Elle ne contient pas de second moteur vidéo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from octopus.video.contract import VideoJob, VideoResult, VideoStatus
from octopus.video.storage import ArtifactStore, ArtifactStoreError


class ExecutorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutorConfig:
    project_root: Path = Path("/app")
    work_root: Path = Path("/tmp/podalux-video")
    step_timeout_s: int = 900
    tts_voice_default: str = "vivienne-fr"


class ForgeExecutor:
    def __init__(self, store: ArtifactStore, config: ExecutorConfig | None = None,
                 runner: Callable[..., str] | None = None):
        self.store = store
        self.config = config or ExecutorConfig()
        self.runner = runner or self._run_checked

    def render(self, job: VideoJob) -> VideoResult:
        manifest_key = self._key(job, "manifest.json")
        existing = self.store.read_json(manifest_key)
        if existing and existing.get("status") == VideoStatus.COMPLETED.value and existing.get("video_url"):
            return VideoResult(job.job_id, VideoStatus.COMPLETED,
                                str(existing["video_url"]), self._artifacts(existing),
                                existing.get("qc") or {}, existing)

        workspace = self.config.work_root / job.job_id
        self._prepare_workspace(workspace)
        started = time.time()
        try:
            legacy = job.to_legacy_job()
            job_path = workspace / "job.json"
            job_path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
            offer = job.offer_id
            out_dir = workspace / "out" / offer
            out_dir.mkdir(parents=True, exist_ok=True)

            self._copy_pipeline_sources(workspace)
            voice = str(job.voice.get("nom") or job.voice.get("voice") or self.config.tts_voice_default)
            env = os.environ.copy()
            if os.environ.get("CHATTERBOX_URL"):
                env["CHATTERBOX_URL"] = os.environ["CHATTERBOX_URL"]

            self._run(["python3", "tools/make_audio_chatterbox_full.py", str(job_path), offer, voice, "0.6", "0.4"],
                      workspace, env=env, log=out_dir / "logs" / "audio.log")
            self._require_nonempty(out_dir / "audio" / "mix.wav", workspace / "remotion" / "src" / "data" / "captions.ts",
                                   workspace / "remotion" / "src" / "data" / "job.ts")

            self._run(["npx", "remotion", "render", job.template, f"../out/{offer}/video.mp4"],
                      workspace / "remotion", env=env, log=out_dir / "logs" / "render.log")
            video = out_dir / "video.mp4"
            self._require_nonempty(video)

            self._mux(offer, workspace, out_dir)
            final = out_dir / "final.mp4"
            self._require_nonempty(final)

            self._run(["python3", "tools/qc_metrics.py", str(final), str(out_dir), "--label", job.job_id],
                      workspace, env=env, log=out_dir / "logs" / "qc_metrics.log")
            qc_path = out_dir / "qc_metrics.json"
            self._require_nonempty(qc_path)
            qc = json.loads(qc_path.read_text(encoding="utf-8"))
            if not isinstance(qc, dict):
                raise ExecutorError("qc_metrics.json n'est pas un objet JSON")

            artifacts = self._upload_artifacts(job, out_dir, final, qc_path)
            video_artifact = next((a for a in artifacts if a.kind == "video"), None)
            if video_artifact is None:
                raise ExecutorError("final.mp4 non publié")

            manifest = {
                "schema_version": "1",
                "job_id": job.job_id,
                "offer_id": job.offer_id,
                "template": job.template,
                "status": VideoStatus.COMPLETED.value,
                "video_url": video_artifact.url,
                "artifacts": [a.to_dict() for a in artifacts],
                "qc": qc,
                "elapsed_seconds": round(time.time() - started, 3),
            }
            self.store.put_json(manifest, manifest_key)
            return VideoResult(job.job_id, VideoStatus.COMPLETED, video_artifact.url,
                               tuple(artifacts), qc, manifest)
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError,
                ExecutorError, ArtifactStoreError) as exc:
            raise ExecutorError(f"rendu {job.job_id} échoué: {exc}") from exc
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _prepare_workspace(self, workspace: Path) -> None:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True)

    def _copy_pipeline_sources(self, workspace: Path) -> None:
        root = self.config.project_root
        remotion = root / "remotion"
        tools_dir = root / "tools"
        required = [remotion / "package.json", remotion / "package-lock.json",
                    tools_dir / "make_audio_chatterbox_full.py", tools_dir / "qc_metrics.py"]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            raise ExecutorError("sources worker manquantes: " + ", ".join(missing))
        shutil.copytree(remotion, workspace / "remotion")
        (workspace / "tools").mkdir()
        for name in ("make_audio_chatterbox_full.py", "qc_metrics.py"):
            shutil.copy2(tools_dir / name, workspace / "tools" / name)

    def _run(self, cmd: list[str], cwd: Path, *, env: dict[str, str], log: Path) -> str:
        return self.runner(cmd, cwd, self.config.step_timeout_s, env, log)

    @staticmethod
    def _run_checked(cmd: list[str], cwd: Path, timeout: int, env: dict[str, str], log: Path) -> str:
        log.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.write_text(f"$ {' '.join(cmd)}\n# exception: {exc}\n", encoding="utf-8")
            raise ExecutorError(f"commande échouée: {cmd[0]}: {exc}") from exc
        output = (proc.stdout or "") + (proc.stderr or "")
        log.write_text(f"$ {' '.join(cmd)}\n# code {proc.returncode}, {time.time() - started:.1f}s\n\n{output}", encoding="utf-8")
        if proc.returncode != 0:
            raise ExecutorError(f"{Path(cmd[0]).name} code {proc.returncode}: {output[-1200:].strip()}")
        return output

    @staticmethod
    def _require_nonempty(*paths: Path) -> None:
        for path in paths:
            if not path.is_file() or path.stat().st_size <= 0:
                raise ExecutorError(f"artefact absent ou vide: {path}")

    def _mux(self, offer: str, workspace: Path, out_dir: Path) -> None:
        mix = out_dir / "audio" / "mix.wav"
        video = out_dir / "video.mp4"
        final = out_dir / "final.mp4"
        probe = self._run(["ffmpeg", "-hide_banner", "-i", str(mix), "-af", "ebur128", "-f", "null", "-"],
                          workspace, env=os.environ.copy(), log=out_dir / "logs" / "mux_lufs.log")
        matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", probe)
        if not matches:
            raise ExecutorError("LUFS introuvable avant mux")
        gain = round(-14 - float(matches[-1]), 2)
        self._run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(mix),
                   "-af", f"volume={gain}dB", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                   "-shortest", str(final)], workspace, env=os.environ.copy(), log=out_dir / "logs" / "mux.log")

    def _upload_artifacts(self, job: VideoJob, out_dir: Path, final: Path, qc_path: Path):
        prefix = f"{job.offer_id}/{job.job_id}"
        artifacts = [self.store.put_file(final, f"{prefix}/final.mp4", content_type="video/mp4")]
        artifacts[0] = type(artifacts[0])(artifacts[0].name, artifacts[0].url, "video",
                                           artifacts[0].content_type, artifacts[0].sha256)
        artifacts.append(self.store.put_file(qc_path, f"{prefix}/qc_metrics.json", content_type="application/json"))
        frames_dir = out_dir / "frames"
        if frames_dir.is_dir():
            for frame in sorted(frames_dir.glob("*.jpg")):
                artifacts.append(self.store.put_file(frame, f"{prefix}/frames/{frame.name}", content_type="image/jpeg"))
        logs_dir = out_dir / "logs"
        if logs_dir.is_dir():
            for log in sorted(logs_dir.glob("*.log")):
                artifacts.append(self.store.put_file(log, f"{prefix}/logs/{log.name}", content_type="text/plain"))
        return artifacts

    @staticmethod
    def _key(job: VideoJob, name: str) -> str:
        return f"{job.offer_id}/{job.job_id}/{name}"

    @staticmethod
    def _artifacts(manifest: dict[str, Any]):
        from octopus.video.contract import Artifact
        return tuple(Artifact(str(x.get("name", "artifact")), str(x["url"]), str(x.get("kind", "file")),
                             x.get("content_type"), x.get("sha256"))
                     for x in manifest.get("artifacts", []) if isinstance(x, dict) and x.get("url"))
