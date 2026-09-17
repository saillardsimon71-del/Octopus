"""Fournisseur vidéo local WanGP (installé par Pinokio) : découverte, diagnostic, génération.

OCTOPUS ne charge jamais PyTorch : il lance `wangp_bridge.py` avec le Python de WanGP, lit la
progression écrite par le pont, relaie l'annulation et récupère les fichiers.

Découverte, dans l'ordre :
1. OCTOPUS_WANGP_ROOT (dossier contenant wgp.py) et, optionnel, OCTOPUS_WANGP_PYTHON ;
2. ~/.pinokio/config.json -> "home" -> api/*/app/wgp.py ;
3. C:/pinokio/api/*/app/wgp.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BRIDGE = Path(__file__).resolve().parent / "wangp_bridge.py"
PROVIDER = "wangp"


class WanGPError(RuntimeError):
    pass


class WanGPBusy(WanGPError):
    """Une autre instance de WanGP (interface Pinokio) occupe déjà la mémoire du GPU."""


class WanGPCancelled(WanGPError):
    pass


@dataclass
class Install:
    app_dir: Path | None = None
    python: Path | None = None
    pinokio_home: Path | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"app_dir": str(self.app_dir) if self.app_dir else None,
                "python": str(self.python) if self.python else None,
                "pinokio_home": str(self.pinokio_home) if self.pinokio_home else None, "problems": self.problems}


def _venv_python(app_dir: Path) -> Path:
    windows = app_dir / "venv" / "Scripts" / "python.exe"
    return windows if windows.exists() or sys.platform == "win32" else app_dir / "venv" / "bin" / "python"


def _pinokio_home() -> Path | None:
    config = Path.home() / ".pinokio" / "config.json"
    try:
        home = json.loads(config.read_text(encoding="utf-8")).get("home")
        if home and Path(home).exists():
            return Path(home)
    except (OSError, ValueError):
        pass
    fallback = Path("C:/pinokio")
    return fallback if fallback.exists() else None


def discover() -> Install:
    env_root = os.environ.get("OCTOPUS_WANGP_ROOT", "").strip()
    install = Install()
    if env_root:
        install.app_dir = Path(env_root)
    else:
        install.pinokio_home = _pinokio_home()
        if install.pinokio_home is None:
            install.problems.append("Pinokio introuvable (~/.pinokio/config.json sans dossier « home » existant)")
            return install
        candidates = sorted((install.pinokio_home / "api").glob("*/app/wgp.py"),
                            key=lambda p: (0 if "wan" in p.parent.parent.name.lower() else 1, str(p)))
        if not candidates:
            install.problems.append(f"WanGP introuvable dans {install.pinokio_home / 'api'} (installer Wan2GP dans Pinokio)")
            return install
        install.app_dir = candidates[0].parent
    if not (install.app_dir / "wgp.py").exists():
        install.problems.append(f"wgp.py absent de {install.app_dir}")
    if not (install.app_dir / "shared" / "api.py").exists():
        install.problems.append("shared/api.py absent : version de WanGP trop ancienne (mettre à jour dans Pinokio)")
    env_python = os.environ.get("OCTOPUS_WANGP_PYTHON", "").strip()
    install.python = Path(env_python) if env_python else _venv_python(install.app_dir)
    if not install.python.exists():
        install.problems.append(f"Python de WanGP absent : {install.python} (installation Pinokio incomplète)")
    return install


def bridge_path() -> Path:
    override = os.environ.get("OCTOPUS_WANGP_BRIDGE", "").strip()
    return Path(override) if override else BRIDGE


def child_env(install: Install) -> dict:
    """Environnement proche de celui de Pinokio : ses binaires (conda, ffmpeg, git) en tête du PATH."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    extra = []
    if install.pinokio_home:
        for rel in ("bin/miniforge", "bin/miniforge/Library/bin", "bin/miniforge/Scripts", "bin/py", "bin/npm"):
            candidate = install.pinokio_home / rel
            if candidate.exists():
                extra.append(str(candidate))
    if install.python:
        extra.insert(0, str(install.python.parent))
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


# --- instances déjà lancées ------------------------------------------------------------------

def running_instances() -> list[dict]:
    """Processus WanGP en cours (interface web, CLI), hors pont OCTOPUS."""
    found = []
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
               "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
            rows = json.loads(out) if out else []
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return []
        rows = rows if isinstance(rows, list) else [rows]
        processes = [(r.get("ProcessId"), r.get("CommandLine") or "") for r in rows]
    else:
        processes = []
        for proc in Path("/proc").glob("[0-9]*"):
            try:
                processes.append((int(proc.name), (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")))
            except OSError:
                continue
    for pid, cmdline in processes:
        if re.search(r"\bwgp\.py\b", cmdline) and "wangp_bridge" not in cmdline and pid != os.getpid():
            found.append({"pid": pid, "cmdline": cmdline.strip()[:300]})
    return found


# --- diagnostic ------------------------------------------------------------------------------

def probe_cache_path() -> Path:
    from .. import paths
    d = paths.data_dir() / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d / "wangp_probe.json"


def cached_probe() -> dict | None:
    try:
        return json.loads(probe_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def probe(install: Install | None = None, *, timeout_s: float = 1200, families: str = "minimax_h3,ltx2") -> dict:
    """Démarre le runtime WanGP (sans générer) : matériel, version, modèles et réglages par défaut."""
    install = install or discover()
    if not install.ok:
        raise WanGPError("; ".join(install.problems))
    out = probe_cache_path()
    cmd = [str(install.python), str(bridge_path()), "probe", "--root", str(install.app_dir), "--out", str(out),
           "--families", families]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(install.app_dir), env=child_env(install), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise WanGPError(f"diagnostic WanGP trop long (> {timeout_s:.0f} s)") from exc
    data = cached_probe() or {}
    data["install"] = install.as_dict()
    data["probed_at"] = time.time()
    data["wall_seconds"] = round(time.time() - started, 1)
    if proc.returncode != 0 and not data.get("error"):
        data["error"] = f"pont WanGP code {proc.returncode} : {(proc.stderr or proc.stdout)[-800:]}"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return data


# --- génération ------------------------------------------------------------------------------

def read_new_events(path: Path, offset: int) -> tuple[list[dict], int]:
    """Lignes complètes ajoutées depuis `offset` (une ligne en cours d'écriture est laissée pour plus tard)."""
    if not path.exists():
        return [], offset
    with path.open("rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    events = []
    for line in chunk[:end].splitlines():
        try:
            events.append(json.loads(line.decode("utf-8")))
        except ValueError:
            continue
    return events, offset + end + 1


def run_bridge(install: Install, manifest: list[dict], workdir: Path, *, on_event: Callable[[dict], None] | None = None,
               should_cancel: Callable[[], bool] | None = None, timeout_s: float = 3 * 3600,
               cancel_grace_s: float = 90, poll_s: float = 1.0) -> dict:
    """Lance le pont, relaie progression et annulation, renvoie le contenu de result.json.

    Annulation : le fichier `cancel` demande à WanGP d'arrêter proprement ; après `cancel_grace_s`,
    l'arbre de processus est tué. Même règle au dépassement de `timeout_s`.
    """
    if not install.ok:
        raise WanGPError("; ".join(install.problems))
    workdir.mkdir(parents=True, exist_ok=True)
    for stale in ("cancel", "result.json", "events.jsonl"):
        (workdir / stale).unlink(missing_ok=True)
    job_path = workdir / "job.json"
    job_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    cmd = [str(install.python), str(bridge_path()), "run", "--root", str(install.app_dir), "--job", str(job_path),
           "--workdir", str(workdir)]
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" \
        else {"start_new_session": True}
    with (workdir / "bridge.stdout.log").open("ab") as console:
        proc = subprocess.Popen(cmd, cwd=str(install.app_dir), env=child_env(install), stdout=console,
                                stderr=subprocess.STDOUT, **options)
    started, offset, cancel_at, reason = time.time(), 0, None, None
    events_path = workdir / "events.jsonl"
    while True:
        exited = proc.poll() is not None
        events, offset = read_new_events(events_path, offset)
        for event in events:
            if on_event:
                on_event(event)
        if exited:
            break
        if cancel_at is None:
            if should_cancel and should_cancel():
                reason = "annulation demandée"
            elif time.time() - started > timeout_s:
                reason = f"délai dépassé ({timeout_s:.0f} s)"
            if reason:
                (workdir / "cancel").write_text(reason, encoding="utf-8")
                cancel_at = time.time()
        elif time.time() - cancel_at > cancel_grace_s:
            _kill_tree(proc)
            break
        time.sleep(poll_s)
    code = proc.wait()
    try:
        result = json.loads((workdir / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tail = (workdir / "bridge.stdout.log").read_text(encoding="utf-8", errors="replace")[-1500:] \
            if (workdir / "bridge.stdout.log").exists() else ""
        result = {"success": False, "generated_files": [], "errors": [{"message": f"pont arrêté sans résultat (code {code})",
                                                                        "stage": "bridge"}], "console_tail": tail}
    result["exit_code"] = code
    result["wall_seconds"] = round(time.time() - started, 1)
    if reason:
        result["stop_reason"] = reason
        if "annulation" in reason:
            raise WanGPCancelled(reason)
    return result


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
