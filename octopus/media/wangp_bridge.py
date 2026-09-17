"""Pont WanGP : exécuté par le Python de WanGP (venv Pinokio), jamais importé par OCTOPUS.

Il appelle l'API Python officielle de WanGP (`shared/api.py`), sans navigateur ni Gradio :

  python wangp_bridge.py probe --root <WanGP app> --out probe.json
  python wangp_bridge.py run   --root <WanGP app> --job job.json --workdir <dossier>

`run` lit une liste de réglages WanGP (manifest), écrit dans <workdir> :
  events.jsonl  progression normalisée (une ligne JSON par événement, horodatée)
  preview.jpg   dernière prévisualisation (au plus toutes les 5 s)
  bridge.log    sortie console de WanGP
  result.json   fichiers générés, erreurs, durées
et surveille <workdir>/cancel : s'il apparaît, la génération est annulée proprement.

Codes de sortie : 0 succès, 2 échec de génération, 3 annulé, 1 erreur du pont.
Dépendances : bibliothèque standard + WanGP. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path

PREVIEW_EVERY_S = 5.0


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _ram_total_gb() -> float | None:
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(status.ullTotalPhys / 2**30, 1)
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            return round(int(fh.readline().split()[1]) / 2**20, 1)
    except OSError:
        return None


def _hardware() -> dict:
    info = {"python": sys.version.split()[0], "platform": platform.platform(), "ram_total_gb": _ram_total_gb(),
            "cpu_count": os.cpu_count()}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            info["gpu"] = {"name": props.name, "vram_total_gb": round(props.total_memory / 2**30, 1),
                           "capability": f"{props.major}.{props.minor}", "cuda": torch.version.cuda}
    except Exception as exc:  # torch absent ou cassé : le probe le dit au lieu de planter
        info["torch_error"] = f"{type(exc).__name__}: {exc}"
    return info


def _session(root: Path, output_dir: Path | None, cli_args: list[str]):
    os.chdir(root)
    sys.path.insert(0, str(root))
    from shared.api import init
    return init(root=root, output_dir=output_dir, cli_args=cli_args, console_output=False, console_isatty=False)


def cmd_probe(args) -> int:
    started = time.time()
    root = Path(args.root).resolve()
    out: dict = {"ok": False, "root": str(root), "hardware": _hardware()}
    try:
        session = _session(root, None, json.loads(args.cli_args))
        runtime = session._ensure_runtime() if hasattr(session, "_ensure_runtime") else getattr(session, "_runtime", None)
        module = getattr(runtime, "module", None)
        out["wangp_version"] = getattr(module, "WanGP_version", None)
        prefixes = tuple(p.strip() for p in args.families.split(",") if p.strip())
        models = []
        for meta in session.list_model_metadata(include_availability=True):
            model_type = str(meta.get("model_type", ""))
            if prefixes and not model_type.startswith(prefixes):
                continue
            models.append({"model_type": model_type, "name": meta.get("name"),
                           "availability": (meta.get("availability") or {}).get("status")
                           if isinstance(meta.get("availability"), dict) else meta.get("availability"),
                           "description": meta.get("description"),
                           "capabilities": meta.get("capabilities")})
        out["models"] = models
        out["defaults"], out["schemas"] = {}, {}
        for model in models:
            try:
                out["defaults"][model["model_type"]] = session.get_default_settings(model["model_type"])
                out["schemas"][model["model_type"]] = session.get_model_schema(model["model_type"])
            except Exception as exc:
                out["defaults"][model["model_type"]] = {"_error": f"{type(exc).__name__}: {exc}"}
        out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc(limit=8)
    out["probe_seconds"] = round(time.time() - started, 1)
    _write_json(Path(args.out), out)
    return 0 if out["ok"] else 1


class _Events:
    def __init__(self, workdir: Path):
        self.path = workdir / "events.jsonl"
        self.fh = self.path.open("a", encoding="utf-8")

    def write(self, kind: str, **data) -> None:
        self.fh.write(json.dumps({"t": round(time.time(), 3), "kind": kind, **data}, ensure_ascii=False, default=str) + "\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()


def _watch_downloads(root: Path, events: "_Events", stop, every_s: float = 5.0) -> None:
    """WanGP télécharge les poids manquants au premier lancement, sans événement de progression :
    on mesure les fichiers partiels de huggingface_hub pour afficher la taille et le débit."""
    folder = root / "ckpts"
    previous: dict[str, int] = {}
    while not stop.wait(every_s):
        partial: dict[str, tuple[int, str]] = {}
        try:
            for path in folder.rglob("*.incomplete"):
                stat = path.stat()
                if time.time() - stat.st_mtime < 120:  # ignorer les téléchargements abandonnés
                    repo = path.parent.name if path.parent.name != "download" else ""
                    partial[str(path)] = (stat.st_size, repo)
        except OSError:
            continue
        for key, (size, repo) in partial.items():
            rate = max(0, size - previous.get(key, size)) / every_s
            events.write("download", file=repo or Path(key).name[:24], bytes=size, rate_bps=int(rate))
        previous = {k: v[0] for k, v in partial.items()}


def cmd_run(args) -> int:
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path(args.job).read_text(encoding="utf-8"))
    if isinstance(manifest, dict):
        manifest = [manifest]
    events = _Events(workdir)
    log = (workdir / "bridge.log").open("a", encoding="utf-8")
    cancel_flag = workdir / "cancel"
    started = time.time()
    result: dict = {"success": False, "generated_files": [], "errors": [], "cancelled": False}
    code = 1
    try:
        events.write("bridge_started", pid=os.getpid(), tasks=len(manifest), hardware=_hardware())
        import threading
        watcher_stop = threading.Event()
        threading.Thread(target=_watch_downloads, args=(Path(args.root).resolve(), events, watcher_stop),
                         daemon=True).start()
        session = _session(Path(args.root).resolve(), workdir / "outputs", json.loads(args.cli_args))
        events.write("session_ready", seconds=round(time.time() - started, 1))
        job = session.submit_manifest(manifest) if len(manifest) > 1 else session.submit_task(manifest[0])
        last_preview = 0.0
        cancel_sent = False
        while True:
            if not cancel_sent and cancel_flag.exists():
                job.cancel()
                cancel_sent = True
                events.write("cancel_requested")
            event = job.events.get(timeout=0.5)
            if event is None:
                if job.done or job.events.closed:
                    break
                continue
            kind, data = event.kind, event.data
            if kind == "progress":
                events.write("progress", phase=data.phase, progress=data.progress, step=data.current_step,
                             total=data.total_steps, status=data.status)
            elif kind == "preview" and getattr(data, "image", None) is not None:
                if time.time() - last_preview >= PREVIEW_EVERY_S:
                    try:
                        data.image.convert("RGB").save(workdir / "preview.jpg", quality=80)
                        last_preview = time.time()
                        events.write("preview", progress=data.progress)
                    except Exception:
                        pass
            elif kind == "stream":
                log.write(f"[{data.stream}] {data.text}\n")
                log.flush()
            elif kind in ("status", "info"):
                events.write(kind, text=str(data)[:500])
            elif kind == "error":
                events.write("error", message=data.message, stage=data.stage, task_index=data.task_index)
            elif kind == "completed":
                break
        outcome = job.result(timeout=120)
        result.update(success=outcome.success, generated_files=[str(p) for p in outcome.generated_files],
                      errors=[{"message": e.message, "stage": e.stage, "task_index": e.task_index}
                              for e in outcome.errors],
                      total_tasks=outcome.total_tasks, successful_tasks=outcome.successful_tasks,
                      failed_tasks=outcome.failed_tasks, cancelled=cancel_sent)
        code = 0 if outcome.success else (3 if cancel_sent else 2)
    except Exception as exc:
        result["errors"].append({"message": f"{type(exc).__name__}: {exc}", "stage": "bridge"})
        result["traceback"] = traceback.format_exc(limit=10)
        code = 1
    finally:
        if "watcher_stop" in locals():
            watcher_stop.set()
        result["seconds"] = round(time.time() - started, 1)
        _write_json(workdir / "result.json", result)
        events.write("bridge_finished", code=code, seconds=result["seconds"])
        events.close()
        log.close()
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pont OCTOPUS -> WanGP (API Python officielle)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--families", default="minimax_h3,ltx2,wan,hunyuan", help="préfixes de model_type à décrire")
    p.add_argument("--cli-args", default="[]", help='options de lancement WanGP en JSON, ex. ["--attention", "sdpa"]')
    p = sub.add_parser("run")
    p.add_argument("--root", required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--cli-args", default="[]", help='options de lancement WanGP en JSON, ex. ["--attention", "sdpa"]')
    args = parser.parse_args(argv)
    return {"probe": cmd_probe, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
