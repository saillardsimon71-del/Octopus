"""Tests unitaires du cockpit sans ouvrir de fenêtre Tk."""
from __future__ import annotations

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


def test_age_format_is_human_readable():
    import time

    now = time.time()
    assert PodaluxApp._format_age(now) == "à l'instant"
    assert "min" in PodaluxApp._format_age(now - 120)
    assert "h" in PodaluxApp._format_age(now - 7200)
