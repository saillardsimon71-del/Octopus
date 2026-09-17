"""Ouverture réelle du Workbench (Tk) sur une base vide : toutes les pages, rafraîchissement périodique.

Régressions couvertes : crash au premier lancement (table state absente), mélange pack/grid sur les
pages Cockpit, Missions et Navigateur, arrêt du rafraîchissement après la page Missions.
Ignoré sans affichage (CI Linux sans serveur X).
"""
from __future__ import annotations

import os
import sys
import time

import pytest

pytest.importorskip("customtkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("aucun affichage disponible", allow_module_level=True)

from agents import config  # noqa: E402
from agents.gui.intelligence import EntrepreneurialWorkbench  # noqa: E402
from agents.gui.workbench import PAGE_META  # noqa: E402
from octopus import strategy  # noqa: E402


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def test_workbench_opens_every_page_and_keeps_refreshing(isolated, monkeypatch):
    strategy.create("objective", "podalux", "Objectif visible", created_by="human", statement="x")
    config.DB_PATH.unlink()  # premier lancement : aucune base Podalux
    errors: list[str] = []
    try:
        app = EntrepreneurialWorkbench()
    except Exception as exc:  # affichage refusé par l'environnement
        if type(exc).__name__ == "TclError" and "display" in str(exc).lower():
            pytest.skip(str(exc))
        raise
    app.report_callback_exception = lambda exc, val, tb: errors.append(f"{exc.__name__}: {val}")
    ticks = []
    original = app._refresh
    monkeypatch.setattr(app, "_refresh", lambda: (ticks.append(1), original()))
    try:
        for page in (*PAGE_META, "Intelligence", "Missions", "Cockpit"):
            app._show_page(page)
            _pump(app, 0.3)
        before = len(ticks)
        _pump(app, 3.5)
    finally:
        app.destroy()
    assert errors == []
    assert len(ticks) > before, "le rafraîchissement périodique s'est arrêté"
