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

# --- Décisions du cycle (calculées en code) ---
# ORBIT : "code" = règle écrite, reproductible, sans appel LLM ; "llm" = arbitrage deepseek-v4-pro historique.
ORBIT_DECISION = os.environ.get("PODALUX_ORBIT_DECISION", "code").strip().lower()
QC_SHIP_SCORE = 24     # note /35 minimale pour publier
QC_MIN_HUMANITE = 3    # WARM_PASS
# Contrôles objectifs bloquants (ffmpeg, tools/qc_metrics.py) : (min, max) ou valeur exacte.
MEDIA_GATES = {
    "duration_s": (18.0, 35.0),
    "resolution": "1080x1920",
    "lufs_integrated": (-16.0, -12.0),
    "freezes_gt1_2s": (0, 0),
}
# Défauts corrigeables en réécrivant le script (les autres demandent une intervention sur le pipeline).
MEDIA_FIXABLE_BY_SCRIPT = {"duration_s"}


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

# Python du projet : aucun chemin utilisateur codé en dur. Le venv local reste prioritaire,
# sinon on utilise l'interpréteur qui exécute actuellement le control-plane.
if os.name == "nt":
    _VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
else:
    _VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
PYTHON = str(_VENV_PYTHON if _VENV_PYTHON.exists() else Path(sys.executable))

# Serveur Chatterbox (Phase 3)
CHATTERBOX_URL = "http://127.0.0.1:4123/v1/audio/speech"
CHATTERBOX_VOICE = "vivienne-fr"

# Recherche web : clés API optionnelles (gratuites). Sans clé → Google News RSS + Wikipedia.
# - BRAVE_API_KEY : https://brave.com/search/api/ (2000 req/mois, 1 req/s)
# - TAVILY_API_KEY : https://tavily.com (1000 crédits/mois, optimisé pour l'IA)
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

# --- Navigateur ---
# Domaines ouverts avec le profil connecté (lecture seule). Tout le reste : contexte éphémère sans
# cookies. Après lecture d'un de ces comptes, l'agent ne peut plus ouvrir de page publique (agents/web_guard.py).
ACCOUNT_DOMAINS = (
    "stripe.com", "youtube.com", "mail.google.com", "accounts.google.com", "myaccount.google.com",
    "fiverr.com", "reddit.com", "x.com", "twitter.com", "linkedin.com", "gumroad.com",
)

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
