"""Emplacements OCTOPUS (surchargeables par variables d'environnement)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def home() -> Path:
    """Racine : OCTOPUS_HOME, sinon dossier du projet (y compris exe PyInstaller)."""
    env = os.environ.get("OCTOPUS_HOME", "").strip()
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = home() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def journal_path() -> Path:
    env = os.environ.get("OCTOPUS_DB", "").strip()
    return Path(env) if env else data_dir() / "octopus.db"


def catalog_path() -> Path:
    env = os.environ.get("OCTOPUS_CATALOG", "").strip()
    return Path(env) if env else Path(__file__).resolve().parent / "config" / "catalog.json"
