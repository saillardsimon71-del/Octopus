"""Diagnostic avant un cycle réel : python -m agents.run doctor.

Le diagnostic est aligné sur le mode cloud-first : le poste local porte le contrôle, les agents,
le navigateur et OmniRoute ; le rendu vidéo lourd est distant par défaut.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import config, db


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    blocking: bool = True


def _port_open(url: str, timeout: float = 1.5) -> bool:
    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, api_key: str = "", timeout: float = 3.0) -> tuple[bool, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"injoignable ({type(exc).__name__})"


def _remotion_browser() -> str | None:
    cfg = config.PROJECT_ROOT / "remotion" / "remotion.config.ts"
    if not cfg.exists():
        return None
    match = re.search(r"setBrowserExecutable\(\s*['\"](.+?)['\"]", cfg.read_text(encoding="utf-8"), re.S)
    return match.group(1).replace("\\\\", "\\") if match else None


def _chromium_executable() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            return pw.chromium.executable_path
    except Exception:
        return None


def _add_local_renderer_checks(checks: list[Check], blocking: bool) -> None:
    python = Path(config.PYTHON)
    checks.append(Check("Python renderer local", python.exists(), str(python),
                        "définir PODALUX_PYTHON vers un environnement local valide", blocking=blocking))
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        checks.append(Check(tool, bool(found), found or "introuvable dans le PATH",
                            "installer ffmpeg et l'ajouter au PATH", blocking=blocking))
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    checks.append(Check("npx renderer local", bool(npx), npx or "introuvable",
                        "installer Node.js", blocking=blocking))
    modules = config.PROJECT_ROOT / "remotion" / "node_modules" / "remotion"
    checks.append(Check("Remotion local", modules.exists(), str(modules.parent),
                        "cd remotion && npm ci", blocking=blocking))
    browser = _remotion_browser()
    if browser:
        checks.append(Check("Navigateur Remotion local", Path(browser).exists(), browser,
                            "supprimer l'ancien chemin fixe ou définir PODALUX_REMOTION_BROWSER", blocking=blocking))
    else:
        checks.append(Check("Navigateur Remotion local", True, "auto-détection Remotion", blocking=blocking))
    checks.append(Check("Serveur Chatterbox local", _port_open(config.CHATTERBOX_URL), config.CHATTERBOX_URL,
                        "démarrer Chatterbox uniquement pour PODALUX_VIDEO_RENDERER=local", blocking=blocking))


def run_checks() -> list[Check]:
    checks: list[Check] = []
    video_mode = os.environ.get("PODALUX_VIDEO_RENDERER", "cloud").strip().lower() or "cloud"
    omni_enabled = os.environ.get("OMNIROUTE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}

    controller = Path(sys.executable)
    checks.append(Check("Python contrôle", controller.exists(), str(controller),
                        "installer Python 3.11+ puis relancer le terminal"))

    has_pw = importlib.util.find_spec("playwright") is not None
    checks.append(Check("Playwright", has_pw, "installé" if has_pw else "absent",
                        "python -m pip install playwright && python -m playwright install chromium", blocking=True))
    chromium = _chromium_executable() if has_pw else None
    checks.append(Check("Chromium Playwright", bool(chromium and Path(chromium).exists()),
                        chromium or "binaire Chromium introuvable",
                        "python -m playwright install chromium", blocking=True))

    try:
        db.init_db()
        conn = db._conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        checks.append(Check("Base podalux.db", True, f"{config.DB_PATH} (journal {mode})"))
    except sqlite3.Error as exc:
        checks.append(Check("Base podalux.db", False, str(exc), "fermer les programmes qui la verrouillent"))

    try:
        from octopus import journal
        journal.connect().close()
        checks.append(Check("Journal OCTOPUS", True, "accessible"))
    except Exception as exc:  # noqa: BLE001 - diagnostic
        checks.append(Check("Journal OCTOPUS", False, f"{type(exc).__name__}: {exc}"))

    holder = db.run_lock_holder()
    checks.append(Check("Verrou de production", holder is None, f"pris par {holder}" if holder else "libre",
                        "un cycle tourne déjà ; l'arrêter avant un nouveau cycle", blocking=False))

    if omni_enabled:
        base = os.environ.get("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/api/v1").rstrip("/")
        key = os.environ.get("OMNIROUTE_API_KEY", "").strip()
        checks.append(Check("Clé OmniRoute", bool(key), "présente" if key else "absente",
                            "définir OMNIROUTE_API_KEY dans l'environnement utilisateur Windows"))
        if key:
            ok, detail = _http_ok(f"{base}/models", key)
            checks.append(Check("OmniRoute", ok, f"{base} · {detail}",
                                "démarrer Docker/OmniRoute et vérifier son endpoint /models"))
        else:
            checks.append(Check("OmniRoute", False, f"{base} · clé absente",
                                "définir OMNIROUTE_API_KEY puis relancer le terminal"))
    else:
        checks.append(Check("OmniRoute", True, "désactivé par OMNIROUTE_ENABLED=0", blocking=False))

    if video_mode == "cloud":
        endpoint = os.environ.get("PODALUX_RUNPOD_ENDPOINT_ID", "").strip()
        token = os.environ.get("PODALUX_RUNPOD_API_TOKEN", "").strip()
        checks.append(Check("RunPod vidéo", bool(endpoint and token),
                            f"endpoint={'présent' if endpoint else 'absent'}, token={'présent' if token else 'absent'}",
                            "définir PODALUX_RUNPOD_ENDPOINT_ID et PODALUX_RUNPOD_API_TOKEN", blocking=True))
        checks.append(Check("Rendu vidéo", True, "cloud-first (RunPod) · aucun GPU local requis", blocking=False))
        checks.append(Check("Chatterbox local", True, "non requis en cloud", blocking=False))
        checks.append(Check("Remotion/FFmpeg local", True, "non requis pour le cycle cloud", blocking=False))
    elif video_mode == "local":
        _add_local_renderer_checks(checks, blocking=True)
    else:
        checks.append(Check("Rendu vidéo", False, f"mode inconnu: {video_mode!r}",
                            "utiliser PODALUX_VIDEO_RENDERER=cloud ou local"))

    deepseek_key = bool(config.api_key())
    deepseek_required = (not omni_enabled) or os.environ.get("OCTOPUS_PROFILE", "").strip() == "legacy"
    checks.append(Check("Clé DeepSeek", deepseek_key, "présente" if deepseek_key else "absente",
                        "DEEPSEEK_API_KEY uniquement si OmniRoute est désactivé/legacy",
                        blocking=deepseek_required))

    if shutil.which("git"):
        try:
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=config.PROJECT_ROOT, capture_output=True,
                                   text=True, timeout=10).stdout.strip()
            checks.append(Check("Git", not dirty,
                                "propre" if not dirty else f"{len(dirty.splitlines())} fichier(s) modifié(s)",
                                "committer ou annuler avant un cycle, pour connaître le code exécuté",
                                blocking=False))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return checks


def render(checks: list[Check]) -> tuple[str, int]:
    lines = []
    for c in checks:
        mark = "OK " if c.ok else ("KO " if c.blocking else "!! ")
        lines.append(f"[{mark}] {c.name:32} {c.detail}" + (f"\n       -> {c.fix}" if not c.ok and c.fix else ""))
    blocking = [c for c in checks if not c.ok and c.blocking]
    lines.append("")
    lines.append("Prêt pour un cycle réel." if not blocking else f"{len(blocking)} problème(s) bloquant(s) avant un cycle réel.")
    return "\n".join(lines), (1 if blocking else 0)
