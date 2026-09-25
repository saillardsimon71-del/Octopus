"""Diagnostic du contrôle local : Python, navigateur, journal et accès LLM."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config, db


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    blocking: bool = True


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


def _chromium_executable() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            return pw.chromium.executable_path
    except Exception:
        return None


def run_checks() -> list[Check]:
    checks: list[Check] = []
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
        base = os.environ.get("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/")
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
    lines.append("Contrôle local prêt." if not blocking else f"{len(blocking)} problème(s) bloquant(s) dans le contrôle local.")
    return "\n".join(lines), (1 if blocking else 0)
