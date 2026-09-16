"""Configuration du groupe d'agents Podalux.

- Modèles : deepseek-flash (courant) / deepseek-v4-pro (arbitrages).
- Prix estimés (USD / 1M tokens) : à ajuster selon la grille DeepSeek du moment.
- Plafond de coût par cycle + outils shell en liste blanche (garde-fous).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_URL = "https://api.deepseek.com"

MODEL_FLASH = "deepseek-flash"   # tâches courantes, classement, vision
MODEL_PRO = "deepseek-v4-pro"    # arbitrages, stratégie (reasoning high)

# Prix estimés en USD pour 1 million de tokens (input, output).
# Valeurs indicatives — à recaler sur la grille officielle DeepSeek.
PRICES = {
    MODEL_FLASH: {"in": 0.27, "out": 1.10},
    MODEL_PRO: {"in": 0.55, "out": 2.19},
}

# Plafond de coût par cycle (USD). Au-delà, ORBIT stoppe.
CYCLE_BUDGET_USD = 1.00

def _project_root() -> Path:
    """Racine du projet, y compris quand le code est empaqueté dans un exe PyInstaller.

    Dans un exe onefile, `__file__` pointe vers `_MEIPASS` (dossier temporaire supprimé
    à la fermeture) → il faut résoudre la vraie racine via `sys.executable` (dist/..).
    """
    env = os.environ.get("PODALUX_ROOT", "").strip()
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        # exe dans dist/ → la racine est dist/../ (le dossier du projet)
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


# Répertoires
PROJECT_ROOT = _project_root()
AGENTS_DIR = PROJECT_ROOT / "agents"
DATA_DIR = AGENTS_DIR / "data"
DB_PATH = DATA_DIR / "podalux.db"
JOBS_DIR = PROJECT_ROOT / "jobs"

# Les 6 agents
AGENTS = ["ORBIT", "GROWTH", "LEDGER", "FORGE", "CONVERT", "SOUT"]

# Offres du catalogue (ids) — utilisé par la GUI sans importer le paquet lourd
CATALOG_OFFERS = [
    "cash_impayes_relance01",
    "cash_devis_cgv01",
    "cash_avenant_scope01",
    "cash_linkedin_rdv01",
]

# Outils shell autorisés (liste blanche stricte). Aucun drapeau destructif.
SHELL_WHITELIST = ["ffmpeg", "ffprobe", "python", "uv", "node", "npx", "git", "curl"]

# Arguments destructifs interdits (garde-fou : rien de récursif/destructif)
FORBIDDEN_ARGS = ["rm -rf", "del /s", "rd /s", "rmdir /s", "-rf ", "--force", "format"]

# Python du projet (MoneyPrinterTurbo .venv — a edge_tts, numpy, etc.)
PYTHON = str(Path(os.environ.get(
    "PODALUX_PYTHON",
    r"C:\Users\saill\Projects\MoneyPrinterTurbo\.venv\Scripts\python.exe",
)))

# Serveur Chatterbox (Phase 3)
CHATTERBOX_URL = "http://127.0.0.1:4123/v1/audio/speech"
CHATTERBOX_VOICE = "vivienne-fr"

# Recherche web : clés API optionnelles (gratuites). Sans clé → Google News RSS + Wikipedia.
# - BRAVE_API_KEY : https://brave.com/search/api/ (2000 req/mois, 1 req/s)
# - TAVILY_API_KEY : https://tavily.com (1000 crédits/mois, optimisé pour l'IA)
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

# --- Navigateur ---
# Chromium (Playwright) avec profil persistant `agents/data/browser_profile` :
# les connexions aux comptes (Stripe, Reddit, X, Fiverr, YouTube…) sont conservées
# entre les sessions. L'humain se connecte une fois dans la fenêtre, l'agent retrouve
# la session.


def api_key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not k:
        # fallback : variable d'environnement UTILISATEUR (registre Windows),
        # utile quand l'app est lancée par double-clic (.exe) sans env hérité.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                k = (winreg.QueryValueEx(key, "DEEPSEEK_API_KEY")[0] or "").strip()
        except Exception:
            k = ""
    return k
