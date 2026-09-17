"""Tests unitaires du cockpit sans ouvrir de fenêtre Tk."""
from __future__ import annotations

import time

from agents.gui import main
from agents.gui.app import PAGE_META, PodaluxApp


def test_cockpit_navigation_metadata_is_complete():
    assert tuple(PAGE_META) == (
        "Cockpit",
        "Missions",
        "Agents",
        "Production",
        "Humain",
        "Navigateur",
        "Système",
    )
    assert PAGE_META["Cockpit"][0] == "Centre de contrôle"
    assert PAGE_META["Production"][0] == "Production vidéo"


def test_navigation_labels_and_slugs():
    assert PodaluxApp._nav_label("Cockpit") == "⌂  Cockpit"
    assert PodaluxApp._nav_label("Système") == "⚙  Système"
    assert PodaluxApp._slug("Production") == "production"
    assert PodaluxApp._slug("Système") == "systeme"


def test_stable_entrypoint_is_callable():
    import agents.gui.app as app_module

    assert callable(main)
    assert app_module.main is main


def test_age_format_is_human_readable():
    now = time.time()
    assert PodaluxApp._format_age(now) == "à l'instant"
    assert "min" in PodaluxApp._format_age(now - 120)
    assert "h" in PodaluxApp._format_age(now - 7200)
