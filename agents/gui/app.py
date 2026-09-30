"""Point d'entrée GUI conservé pour compatibilité.

Le Workbench entrepreneurial est désormais l'interface officielle. Les anciens appels
vers `agents.gui.app.main` continuent donc d'ouvrir le même centre de travail.
"""
from __future__ import annotations

from .workbench_v2 import WorkbenchV2, main

EntrepreneurialWorkbench = WorkbenchV2
PodaluxWorkbench = WorkbenchV2

__all__ = ["EntrepreneurialWorkbench", "PodaluxWorkbench", "main"]
