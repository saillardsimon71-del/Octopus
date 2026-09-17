"""Point d'entrée GUI conservé pour compatibilité.

Le Workbench entrepreneurial est désormais l'interface officielle. Les anciens appels
vers `agents.gui.app.main` continuent donc d'ouvrir le même centre de travail.
"""
from __future__ import annotations

from .intelligence import EntrepreneurialWorkbench, main

PodaluxWorkbench = EntrepreneurialWorkbench

__all__ = ["EntrepreneurialWorkbench", "PodaluxWorkbench", "main"]
