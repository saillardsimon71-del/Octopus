"""Ouverture réelle du Workbench (Tk) sur une base vide : toutes les pages, rafraîchissement périodique.

Régressions couvertes : crash au premier lancement (table state absente), mélange pack/grid sur les
pages Cockpit, Missions et Navigateur, arrêt du rafraîchissement après la page Missions.
Ignoré sans affichage (CI Linux sans serveur X).
"""
from __future__ import annotations

import os
import sys
import time
from copy import deepcopy

import pytest

ctk = pytest.importorskip("customtkinter")
if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("aucun affichage disponible", allow_module_level=True)

from agents import config  # noqa: E402
from agents.gui.intelligence import EntrepreneurialWorkbench  # noqa: E402
from agents.gui.workbench_v2 import ADVANCED, META, PRIMARY, WorkbenchV2  # noqa: E402
from agents.gui.workbench import PAGE_META  # noqa: E402
from agents.gui.workspaces import Business  # noqa: E402
from octopus import businesses, economy, journal, paths, strategy, tasks  # noqa: E402
from agents.gui.workbench_v2_data import read_snapshot  # noqa: E402


def _pump(app, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def _snapshot_result(app, snapshot):
    app._background_results.put(("v2_snapshot", app.selected_business_id, snapshot))
    app._refresh()


def _event(index):
    return {"type": "task.started", "data": {"kind": f"refresh-{index}"},
            "ts": time.time(), "business": "octopus", "task_id": index}


def test_v2_identical_snapshot_keeps_current_widgets(isolated, monkeypatch):
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    app = WorkbenchV2()
    try:
        snapshot = read_snapshot(app.selected_business_id)
        _snapshot_result(app, snapshot)
        app._show_page("Activité")
        body = app._body
        _snapshot_result(app, deepcopy(snapshot))
        assert app._body is body
        assert body.winfo_exists()
    finally:
        app.destroy()


def test_v2_changed_activity_updates_without_losing_scroll(isolated, monkeypatch):
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    app = WorkbenchV2()
    try:
        snapshot = read_snapshot(app.selected_business_id)
        snapshot["events"] = [_event(i) for i in range(60)]
        _snapshot_result(app, snapshot)
        app._show_page("Activité")
        app.update()
        app._body._parent_canvas.yview_moveto(0.5)
        app.update()
        before = app._body._parent_canvas.yview()[0]
        changed = deepcopy(snapshot)
        changed["events"].insert(0, _event(61))
        _snapshot_result(app, changed)
        app.update()
        assert any("refresh-61" in child.cget("text") for card in app._body.winfo_children()
                   for child in card.winfo_children() if isinstance(child, ctk.CTkLabel))
        assert abs(app._body._parent_canvas.yview()[0] - before) < 0.1
    finally:
        app.destroy()


def test_v2_mission_draft_and_focus_survive_changed_snapshot(isolated, monkeypatch):
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    app = WorkbenchV2()
    try:
        snapshot = read_snapshot(app.selected_business_id)
        _snapshot_result(app, snapshot)
        app._show_page("Missions")
        app.mission_prompt.insert("1.0", "Mon objectif en cours")
        app.update()
        app.mission_prompt._textbox.focus_force()
        assert app.focus_lastfor() == app.mission_prompt._textbox
        body = app._body
        unrelated = deepcopy(snapshot)
        unrelated["events"] = [_event(2)]
        _snapshot_result(app, unrelated)
        assert app._body is body
        strategy.create("objective", "octopus", "Nouvel objectif", created_by="human", statement="x")
        changed = read_snapshot(app.selected_business_id)
        _snapshot_result(app, changed)
        assert app.mission_prompt.get("1.0", "end").strip() == "Mon objectif en cours"
        assert app.focus_lastfor() == app.mission_prompt._textbox
        assert any("Nouvel objectif" in child.cget("text") for card in app._body.winfo_children()
                   for child in card.winfo_children() if isinstance(child, ctk.CTkLabel))
    finally:
        app.destroy()


def test_v2_refresh_error_keeps_mission_draft(isolated, monkeypatch):
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    app = WorkbenchV2()
    try:
        _snapshot_result(app, read_snapshot(app.selected_business_id))
        app._show_page("Missions")
        app.mission_prompt.insert("1.0", "Texte à conserver")
        body = app._body
        app._background_results.put(("v2_error", "lecture impossible"))
        app._refresh()
        assert app._body is body
        assert app.mission_prompt.get("1.0", "end").strip() == "Texte à conserver"
    finally:
        app.destroy()


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
                assert app.page_title.cget("text") == META[page][0]
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


def test_v2_keeps_system_discovery_explicit_and_secondary(isolated):
    channel = economy.add_channel("octopus", "agnes_video", "Agnes local", created_by="human",
                                  locator="http://127.0.0.1:8765", capabilities=["agnes_submit"])
    economy.update_channel("octopus", channel, actor="human", status="active", access="act")
    app = WorkbenchV2()
    try:
        _pump(app, 1.8)
        assert "octopus" in app._snapshot["businesses"]
        app.registry._businesses["podalux"] = Business("podalux", "Podalux")
        app._sync_business_menu()
        assert app.business_menu.cget("values") == ["Toutes les activités", "Discovery autonome (historique)"]
    finally:
        app.destroy()


def test_v2_readonly_mission_stays_disabled_after_refresh(isolated, monkeypatch):
    monkeypatch.setenv("OCTOPUS_WORKBENCH_READONLY", "1")
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    app = WorkbenchV2()
    try:
        snapshot = read_snapshot(app.selected_business_id)
        _snapshot_result(app, snapshot)
        app._show_page("Missions")
        assert app.mission_button.cget("state") == "disabled"
        changed = deepcopy(snapshot)
        changed["objectives"] = [{"id": 1, "summary": "Autre objectif", "created_by": "human",
                                  "status": "active", "statement": "x", "work_tasks": [],
                                  "success_criteria": "test"}]
        _snapshot_result(app, changed)
        assert app.mission_button.cget("state") == "disabled"
    finally:
        app.destroy()


def _widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from _widgets(child)


def test_v2_activity_form_creates_without_starting_and_survives_restart(isolated, monkeypatch):
    from agents import procs
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    monkeypatch.setattr(procs, "spawn", lambda *_a, **_k: pytest.fail("Create launched a process"))
    monkeypatch.setattr("octopus.supervisor.start_pursuit", lambda *_a, **_k: pytest.fail("Create started pursuit"))
    app = WorkbenchV2()
    try:
        _snapshot_result(app, read_snapshot())
        add = next(widget for widget in _widgets(app._body) if isinstance(widget, ctk.CTkButton)
                   and widget.cget("text") == "+ Ajouter une activité")
        add.invoke()
        app.update()
        dialog = next(widget for widget in app.winfo_children() if isinstance(widget, ctk.CTkToplevel))
        name = next(widget for widget in dialog.winfo_children() if isinstance(widget, ctk.CTkEntry))
        description = next(widget for widget in dialog.winfo_children() if isinstance(widget, ctk.CTkTextbox))
        name.insert(0, "Vidéo B2B proactive")
        description.insert("1.0", "Créer et vendre du contenu vidéo à des professionnels")
        next(widget for widget in dialog.winfo_children() if isinstance(widget, ctk.CTkButton)).invoke()
        item = next(iter(businesses.discover().values()))
        assert app.selected_business_id == item.id and not dialog.winfo_exists()
        assert not tasks.list_tasks() and not journal.query("SELECT id FROM llm_calls")
    finally:
        app.destroy()
    second = WorkbenchV2()
    try:
        _snapshot_result(second, read_snapshot())
        assert any("Vidéo B2B proactive" in label for label in second.business_menu.cget("values"))
    finally:
        second.destroy()


def test_v2_all_primary_views_switch_scope_without_stale_content(isolated, monkeypatch):
    import json
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    a = businesses.create_activity("Première", "Terrain ONLY_A")
    b = businesses.create_activity("Seconde", "Terrain ONLY_B")
    for item, marker in ((a, "ONLY_A"), (b, "ONLY_B")):
        strategy.create("objective", item.id, marker, created_by="human", statement=marker)
        strategy.create("evidence", item.id, marker, created_by="human", nature="observed",
                        source_type="fixture", source_ref=marker, captured_at=1, observation=marker)
        strategy.create("decision", item.id, marker, created_by="human", decision=marker, rationale=marker)
        economy.add_channel(item.id, "website", marker, created_by="human")
        tid = tasks.enqueue(item.id, "fixture", {"goal": marker})
        with journal.connect() as conn:
            conn.execute("UPDATE tasks SET output=? WHERE id=?", (json.dumps({"rapport": marker}), tid))
            observation = {"session": "closed", "at": 1, "url": "https://fixture.example/"+marker,
                           "title": marker, "action": "snapshot", "refused": False, "ok": True, "snapshot": marker}
            conn.execute("INSERT INTO task_steps(task_id,key,value,ts) VALUES (?, 'browser.observation', ?, 1)",
                         (tid, json.dumps(observation)))
    app = WorkbenchV2()
    try:
        for page in PRIMARY:
            app._select_business_then(page, a.id)
            _snapshot_result(app, read_snapshot(a.id))
            app._select_business_then(page, b.id)
            assert app._snapshot is None
            _snapshot_result(app, read_snapshot(b.id))
            app.update()
            texts = []
            for widget in _widgets(app._body):
                if isinstance(widget, ctk.CTkLabel):
                    texts.append(str(widget.cget("text")))
                elif isinstance(widget, ctk.CTkTextbox):
                    texts.append(widget.get("1.0", "end"))
            assert "ONLY_A" not in "\n".join(texts)
            assert any("ONLY_B" in text for text in texts) or page == "Activité"
    finally:
        app.destroy()


def test_v2_readonly_disables_activity_creation_and_start(isolated, monkeypatch):
    item = businesses.create_activity("Activité", "Terrain")
    monkeypatch.setenv("OCTOPUS_WORKBENCH_READONLY", "1")
    monkeypatch.setattr(WorkbenchV2, "_load_snapshot", lambda self: None)
    app = WorkbenchV2()
    try:
        _snapshot_result(app, read_snapshot())
        buttons = [widget for widget in _widgets(app._body) if isinstance(widget, ctk.CTkButton)]
        assert next(widget for widget in buttons if widget.cget("text") == "+ Ajouter une activité").cget("state") == "disabled"
        assert all(widget.cget("state") == "disabled" for widget in buttons if widget.cget("text") == "Démarrer / reprendre")
        app._select_business(item.id)
        _snapshot_result(app, read_snapshot(item.id))
        start = next(widget for widget in _widgets(app._body) if isinstance(widget, ctk.CTkButton)
                     and widget.cget("text") == "Démarrer / reprendre l'activité")
        assert start.cget("state") == "disabled"
        assert app._create_activity("interdit", "terrain") is None
        app._start_pursuit()
        assert not paths.journal_path().exists() and len(businesses.discover()) == 1
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
