"""Tests unitaires du Workbench sans ouvrir de fenêtre Tk."""
from __future__ import annotations

import time

from agents.gui import main
from agents.gui.app import PodaluxWorkbench, main as app_main
from agents.gui.workspaces import WorkspaceRegistry


def test_cockpit_navigation_metadata_is_complete():
    from agents.gui.workbench import PAGE_META

    assert tuple(PAGE_META) == (
        "Cockpit",
        "Business",
        "Missions",
        "Agents",
        "Production",
        "Humain",
        "Navigateur",
        "Système",
    )
    assert PAGE_META["Cockpit"][0] == "Centre de contrôle"
    assert PAGE_META["Business"][0] == "Businesses"
    assert PAGE_META["Production"][0] == "Production vidéo"


def test_navigation_labels_and_slugs():
    assert PodaluxWorkbench._nav_label("Cockpit") == "⌂  Cockpit"
    assert PodaluxWorkbench._nav_label("Business") == "▦  Business"
    assert PodaluxWorkbench._nav_label("Système") == "⚙  Système"
    assert PodaluxWorkbench._slug("Production") == "production"
    assert PodaluxWorkbench._slug("Système") == "systeme"


def test_stable_entrypoint_is_callable():
    assert callable(main)
    assert app_main is main


def test_age_format_is_human_readable():
    now = time.time()
    assert PodaluxWorkbench._format_age(now) == "à l'instant"
    assert "min" in PodaluxWorkbench._format_age(now - 120)
    assert "h" in PodaluxWorkbench._format_age(now - 7200)


def test_workspace_registry_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("agents.gui.workspaces.config.JOBS_DIR", tmp_path / "jobs")
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "cash_offer01.json").write_text("{}", encoding="utf-8")
    path = tmp_path / "workspaces.json"

    registry = WorkspaceRegistry(path)
    assert [b.id for b in registry.all()] == ["cash"]
    registry.upsert("agency_b2b", "Agence B2B", "Prospection", ["agency_offer01"])

    reloaded = WorkspaceRegistry(path)
    business = reloaded.get("agency_b2b")
    assert business is not None
    assert business.label() == "Agence B2B"
    assert business.offers == ["agency_offer01"]


def test_workspace_id_normalization_refuses_reserved_all(tmp_path):
    registry = WorkspaceRegistry(tmp_path / "workspaces.json")
    try:
        registry.upsert("all", "Tous", "")
    except ValueError as exc:
        assert "réservé" in str(exc)
    else:
        raise AssertionError("le workspace réservé doit être refusé")
