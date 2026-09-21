from __future__ import annotations

from agents import browser, config, db


def test_browser_tool_initializes_compatibility_state_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "podalux.db")

    browser.BrowserTool(headless=True, persistent=False)

    db.set_state("browser_url", "https://example.test/")
    assert db.get_state("browser_url") == "https://example.test/"
