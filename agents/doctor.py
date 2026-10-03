"""Diagnostic du contrôle local : Python, navigateur, journal et accès LLM."""
from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
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


def _chromium_executable() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            return pw.chromium.executable_path
    except Exception:
        return None


def run_checks() -> list[Check]:
    checks: list[Check] = []

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

    from octopus import catalog, llm
    cat = catalog.load()
    state = cat.raw["openrouter_catalog"]
    key = bool(llm.secret("OPENROUTER_API_KEY"))
    checks.append(Check("Cle OpenRouter", key, "presente" if key else "absente",
                        "definir OPENROUTER_API_KEY dans l'environnement utilisateur Windows"))
    checks.append(Check("Catalogue OpenRouter", state["count"] > 0,
        f"{state['count']} modeles gratuits ; {state['state']} ; age_s={state['age_s']}"
        + (" ; " + state["refresh_error"] if state["refresh_error"] else ""),
        "python -m octopus models --refresh"))
    profile = os.environ.get("OCTOPUS_PROFILE") or cat.default_profile
    deepseek_required = "paid" in cat.profile(profile).get("allowed_cost_classes", [])
    deepseek_key = bool(llm.secret("DEEPSEEK_API_KEY"))
    checks.append(Check("Cle DeepSeek", deepseek_key, "presente" if deepseek_key else "absente",
                        "DEEPSEEK_API_KEY pour un profil payant explicitement autorise",
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
