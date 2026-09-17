"""Diagnostic avant un cycle réel : python -m agents.run doctor.

Vérifie ce qu'un cycle utilise, sans rien lancer de payant ni de lourd : interpréteur, ffmpeg,
Remotion et son navigateur, serveur Chatterbox, clé DeepSeek, Playwright, bases, verrou, git.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import socket
import sqlite3
import subprocess
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


def _remotion_browser() -> str | None:
    cfg = config.PROJECT_ROOT / "remotion" / "remotion.config.ts"
    if not cfg.exists():
        return None
    match = re.search(r"setBrowserExecutable\(\s*['\"](.+?)['\"]", cfg.read_text(encoding="utf-8"), re.S)
    return match.group(1).replace("\\\\", "\\") if match else None


def run_checks() -> list[Check]:
    checks: list[Check] = []
    python = Path(config.PYTHON)
    checks.append(Check("Python des outils", python.exists(), str(python),
                        "définir PODALUX_PYTHON vers un python avec numpy"))
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        checks.append(Check(tool, bool(found), found or "introuvable dans le PATH", "installer ffmpeg et l'ajouter au PATH"))
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    checks.append(Check("npx", bool(npx), npx or "introuvable", "installer Node.js"))
    modules = config.PROJECT_ROOT / "remotion" / "node_modules" / "remotion"
    checks.append(Check("Remotion installé", modules.exists(), str(modules.parent), "cd remotion && npm ci"))
    browser = _remotion_browser()
    if browser:
        checks.append(Check("Navigateur de rendu Remotion", Path(browser).exists(), browser,
                            "corriger setBrowserExecutable dans remotion/remotion.config.ts"))
    checks.append(Check("Serveur Chatterbox", _port_open(config.CHATTERBOX_URL), config.CHATTERBOX_URL,
                        "démarrer chatterbox-tts-api (sinon l'étape audio échoue en quelques secondes)"))
    checks.append(Check("Clé DeepSeek", bool(config.api_key()), "présente" if config.api_key() else "absente",
                        "variable d'environnement utilisateur DEEPSEEK_API_KEY"))
    has_pw = importlib.util.find_spec("playwright") is not None
    checks.append(Check("Playwright (agents navigateur)", has_pw, "installé" if has_pw else "absent",
                        "pip install playwright && python -m playwright install chromium", blocking=False))
    try:
        db.init_db()
        conn = db._conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        checks.append(Check("Base podalux.db", True, f"{config.DB_PATH} (journal {mode})"))
    except sqlite3.Error as exc:
        checks.append(Check("Base podalux.db", False, str(exc), "fermer les programmes qui la verrouillent"))
    holder = db.run_lock_holder()
    checks.append(Check("Verrou de production", holder is None, f"pris par {holder}" if holder else "libre",
                        "un cycle tourne déjà ; attendre ou l'arrêter", blocking=False))
    try:
        from octopus import journal
        journal.connect().close()
        checks.append(Check("Journal OCTOPUS", True, "accessible"))
    except Exception as exc:  # noqa: BLE001 - diagnostic
        checks.append(Check("Journal OCTOPUS", False, f"{type(exc).__name__}: {exc}"))
    if shutil.which("git"):
        try:
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=config.PROJECT_ROOT, capture_output=True,
                                   text=True, timeout=10).stdout.strip()
            checks.append(Check("Git", not dirty, "propre" if not dirty else f"{len(dirty.splitlines())} fichier(s) modifié(s)",
                                "committer ou annuler avant un cycle, pour savoir quel code a produit la vidéo",
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
