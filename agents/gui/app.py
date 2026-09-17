"""Point d'entrée GUI conservé pour compatibilité.

Le Workbench est désormais l'interface officielle. Les anciens appels vers
`agents.gui.app.main` continuent donc d'ouvrir le même centre de travail.
"""
from __future__ import annotations

from .workbench import PodaluxWorkbench, main

__all__ = ["PodaluxWorkbench", "main"]
