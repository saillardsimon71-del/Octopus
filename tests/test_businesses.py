"""Registre des activités : découverte des business.toml, handlers déclarés, plafond LLM par activité."""
from __future__ import annotations

import os
import time

import pytest

from agents import config, deepseek
from octopus import businesses, journal, llm, tasks, worker

MSG = [{"role": "user", "content": "Ecris le job en JSON."}]


def declare(root, business_id: str, body: str) -> None:
    folder = root / "businesses" / business_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "business.toml").write_text(f'id = "{business_id}"\n{body}', encoding="utf-8")


def test_discover_reads_declarations(isolated):
    declare(isolated, "alpha", 'name = "Alpha"\nhandlers = ["mod.a"]\nbudget_daily_usd = 0.5\nkpis = ["vues"]\n')
    declare(isolated, "beta", 'handlers = "mod.b"\n')
    found = businesses.discover()
    assert set(found) == {"alpha", "beta"}
    assert found["alpha"].name == "Alpha" and found["alpha"].budget_daily_usd == 0.5
    assert found["alpha"].kpis == ["vues"]
    assert found["beta"].handlers == ["mod.b"] and found["beta"].name == "beta"
    assert found["beta"].budget_daily_usd is None


def test_discover_cache_follows_file_changes(isolated):
    declare(isolated, "alpha", "budget_daily_usd = 0.5\n")
    assert businesses.get("alpha").budget_daily_usd == 0.5
    path = isolated / "businesses" / "alpha" / "business.toml"
    path.write_text('id = "alpha"\nbudget_daily_usd = 2.0\n', encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert businesses.get("alpha").budget_daily_usd == 2.0


def test_broken_or_duplicate_declarations_are_skipped(isolated):
    declare(isolated, "alpha", "")
    (isolated / "businesses" / "copie").mkdir()
    (isolated / "businesses" / "copie" / "business.toml").write_text('id = "alpha"\n', encoding="utf-8")
    declare(isolated, "cassee", "handlers = [\n")
    found = businesses.discover()
    assert set(found) == {"alpha"}
    assert any("deux fois" in p for p in businesses.problems())
    assert any("illisible" in p for p in businesses.problems())


def test_handler_modules_engine_first_without_duplicates(isolated):
    declare(isolated, "alpha", 'handlers = ["octopus.media.handlers", "mod.a"]\n')
    declare(isolated, "beta", 'handlers = ["mod.a", "mod.b"]\n')
    assert businesses.handler_modules() == [*businesses.ENGINE_HANDLERS, "mod.a", "mod.b"]


def test_worker_loads_declared_handlers(isolated, monkeypatch):
    package = isolated / "demo_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "handlers.py").write_text(
        "from octopus.worker import handler\n\n@handler('demo.ping')\ndef ping(ctx, data):\n    return {'pong': True}\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(isolated))
    monkeypatch.delenv("OCTOPUS_HANDLERS", raising=False)
    declare(isolated, "demo", 'handlers = ["demo_pkg.handlers"]\n')
    monkeypatch.setattr(worker, "HANDLERS", dict(worker.HANDLERS))
    loaded = worker.load_handlers()
    assert "demo.ping" in loaded and "media.video_generate" in loaded


def test_env_overrides_declared_handlers(isolated, monkeypatch):
    declare(isolated, "demo", 'handlers = ["module.inexistant"]\n')
    monkeypatch.setenv("OCTOPUS_HANDLERS", "octopus.builtin_handlers")
    worker.load_handlers()  # le module déclaré n'est pas importé : pas d'ImportError


def test_business_daily_cap_blocks_only_that_business(isolated, transport):
    declare(isolated, "podalux", "budget_daily_usd = 0.0005\n")
    transport.reply('{"a": 1}', prompt_tokens=10, completion_tokens=100)
    deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG, max_tokens=100)
    with pytest.raises(llm.BudgetExceeded, match="activite podalux"):
        with journal.run("podalux", "video_cycle"):
            deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG)
    with journal.run("autre", "essai"):  # activité sans plafond : le plafond global (absent ici) seul compte
        deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG, max_tokens=100)
    assert len(transport.calls) == 2


def test_overview_summarises_each_business(isolated):
    declare(isolated, "alpha", 'name = "Alpha"\nbudget_daily_usd = 1.0\n')
    tasks.enqueue("alpha", "demo.ping", {})
    tasks.enqueue("orpheline", "demo.ping", {})
    rows = {r["id"]: r for r in businesses.overview(days=7)}
    assert rows["alpha"]["declared"] and rows["alpha"]["tasks"] == {"queued": 1}
    assert rows["alpha"]["budget_daily_usd"] == 1.0
    assert not rows["orpheline"]["declared"]
    text = businesses.render(list(rows.values()), 7)
    assert "Alpha [alpha]" in text and "(non déclarée)" in text
