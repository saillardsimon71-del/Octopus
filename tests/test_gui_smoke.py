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
from agents.gui.workbench_v2 import ADVANCED, PRIMARY, WorkbenchV2  # noqa: E402
from agents.gui.workbench import PAGE_META  # noqa: E402
from agents.gui.workspaces import Business  # noqa: E402
from octopus import economy, strategy  # noqa: E402


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def test_v2_primary_navigation_at_requested_window_sizes(isolated):
    errors = []
    app = WorkbenchV2()
    app.report_callback_exception = lambda exc, val, tb: errors.append(f"{exc.__name__}: {val}")
    try:
        for width, height in ((1280, 720), (1488, 960)):
            app.geometry(f"{width}x{height}")
            for page in PRIMARY:
                app._show_page(page)
                _pump(app, 0.12)
                assert app.page_title.cget("text") == page
                assert app.page_host.winfo_width() > 850
        for page in ADVANCED:
            app._show_page(page)
            _pump(app, 0.12)
        app._show_page("Vue d'ensemble")
        _pump(app, 1.6)
        assert app._snapshot is not None
    finally:
        app.destroy()
    assert errors == []


def test_v2_keeps_system_validation_out_of_business_and_mission_choices(isolated, monkeypatch):
    channel = economy.add_channel("octopus", "agnes_video", "Agnes local", created_by="human",
                                  locator="http://127.0.0.1:8765", capabilities=["agnes_submit"])
    economy.update_channel("octopus", channel, actor="human", status="active", access="act")
    app = WorkbenchV2()
    try:
        _pump(app, 1.8)
        assert "octopus" in app._snapshot["businesses"]
        app.registry._businesses["podalux"] = Business("podalux", "Podalux")
        app._sync_business_menu()
        assert app.business_menu.cget("values") == ["Toutes les activités"]
        app._show_page("Missions")
        assert app.mission_business.cget("values") == ["Aucune activité autorisée"]
        assert app.mission_button.cget("state") == "disabled"
        monkeypatch.setattr("agents.gui.agnes_missions.create", lambda *_a, **_kw: pytest.fail("mission créée"))
        app.mission_prompt.insert("1.0", "Test")
        app.mission_consent.set(True)
        app._create_mission()
    finally:
        app.destroy()


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
        for page in (*PAGE_META, "Intelligence", "Agnes", "Missions", "Cockpit"):
            app._show_page(page)
            _pump(app, 0.3)
        before = len(ticks)
        _pump(app, 3.5)
    finally:
        app.destroy()
    assert errors == []
    assert len(ticks) > before, "le rafraîchissement périodique s'est arrêté"


@pytest.mark.parametrize("kind", ["doctor", "orca"])
def test_late_background_result_after_leaving_system_page(monkeypatch, kind):
    # Simule déterministement une réponse lente, sans lancer de sonde externe/thread réel.
    monkeypatch.setattr(EntrepreneurialWorkbench, "_run_doctor", lambda self: None)
    app = EntrepreneurialWorkbench()
    try:
        app._show_page("Système")
        old_widget = app.system_checks if kind == "doctor" else app.orca_result
        app._show_page("Cockpit")
        assert not old_widget.winfo_exists()
        setattr(app, "_system_busy" if kind == "doctor" else "_orca_busy", True)
        result = ("doctor", [{"name": "fixture", "ok": True, "blocking": False, "detail": "offline"}]) \
            if kind == "doctor" else ("orca", "fixture", "green")
        app._background_results.put(result)
        app._refresh()  # doit vider la réponse et programmer le prochain refresh, sans TclError
        assert app._background_results.empty()
        assert not getattr(app, "_system_busy" if kind == "doctor" else "_orca_busy")
    finally:
        app.destroy()
