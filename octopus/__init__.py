"""OCTOPUS : noyau commun des activites autonomes.

Journal d'execution (SQLite), passerelle LLM (profils de cout, budgets par run,
justification des appels payants), banc d'evaluation, file de taches et worker.

Coupe-circuit : la variable d'environnement OCTOPUS=off desactive le journal et la
passerelle ; le code metier retrouve alors son comportement historique.
"""
from __future__ import annotations

import os

__version__ = "0.2.0"


def enabled() -> bool:
    return os.environ.get("OCTOPUS", "on").strip().lower() not in ("off", "0", "false", "non", "no")
