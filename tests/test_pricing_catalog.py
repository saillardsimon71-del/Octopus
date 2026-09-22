from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from octopus import catalog, pricing
from octopus.pricing import Usage

from conftest import REAL_IS_PEAK

UTC = dt.timezone.utc
WINDOWS = ["01:00-04:00", "06:00-10:00"]
FLASH = {"input_cache_hit": 0.003, "input_cache_miss": 0.15, "output": 0.60, "peak_multiplier": 2.0}


def ts(*args) -> float:
    return dt.datetime(*args, tzinfo=UTC).timestamp()


@pytest.mark.parametrize("moment, expected", [
    ((2026, 9, 14, 2, 0), True),     # lundi, fenetre 01-04
    ((2026, 9, 14, 4, 0), False),    # borne haute exclue
    ((2026, 9, 14, 5, 30), False),   # entre deux fenetres
    ((2026, 9, 14, 9, 59), True),
    ((2026, 9, 18, 7, 0), True),     # vendredi
    ((2026, 9, 19, 2, 0), False),    # samedi
    ((2026, 9, 20, 7, 0), False),    # dimanche
])
def test_peak_windows(moment, expected):
    assert REAL_IS_PEAK(ts(*moment), WINDOWS) is expected


def test_no_peak_windows_means_off_peak():
    assert REAL_IS_PEAK(ts(2026, 9, 14, 2, 0), None) is False
    assert REAL_IS_PEAK(ts(2026, 9, 14, 2, 0), []) is False


def test_call_cost_with_cache_and_peak():
    usage = Usage(prompt_tokens=1000, completion_tokens=100, cache_hit_tokens=800)
    off = pricing.call_cost(FLASH, usage, peak=False)
    assert off == pytest.approx((800 * 0.003 + 200 * 0.15 + 100 * 0.60) / 1e6)
    assert pricing.call_cost(FLASH, usage, peak=True) == pytest.approx(2 * off)
    explicit_miss = Usage(prompt_tokens=1000, completion_tokens=0, cache_hit_tokens=0, cache_miss_tokens=1000)
    assert pricing.call_cost(FLASH, explicit_miss, False) == pytest.approx(1000 * 0.15 / 1e6)
    assert pricing.call_cost(None, usage, True) == 0.0


def test_estimate_is_an_upper_bound():
    usage = Usage(prompt_tokens=500, completion_tokens=300, cache_hit_tokens=400)
    real = pricing.call_cost(FLASH, usage, False)
    assert pricing.estimate_max_cost(FLASH, prompt_chars=1500, max_tokens=300, peak=False) >= real


# --- catalogue -----------------------------------------------------------------------------

def test_real_catalog_is_valid_and_consistent():
    cat = catalog.load()
    raw = cat.raw
    for name, task in raw["tasks"].items():
        needs = set(task.get("needs", []))
        baseline = raw["models"][task["baseline"]]
        assert needs <= set(baseline["capabilities"]), f"{name} : reference sans {needs}"
        for profile, candidates in task["candidates"].items():
            allowed = raw["profiles"][profile]["allowed_cost_classes"]
            for model_id in candidates:
                model = raw["models"][model_id]
                assert model["cost_class"] in allowed, f"{name}/{profile} : {model_id} interdit par le profil"
                assert needs <= set(model["capabilities"]), f"{name}/{profile} : {model_id} sans {needs}"
                if task.get("privacy") == "sensitive":
                    assert model["cost_class"] == "local", f"{name} : tache sensible routee vers {model_id}"
    for target in raw["legacy_tasks"].values():
        assert target in raw["tasks"], f"tache historique vers une tache inconnue : {target}"
    assert "paid" not in raw["profiles"]["zero_cost"]["allowed_cost_classes"]


def test_flash_fallback_profile_uses_only_flash_as_paid_agent_fallback(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    cat = catalog.load()

    assert cat.profile("flash_fallback")["fallback"] is True
    assert cat.profile("flash_fallback")["require_evidence"] is False

    for task_name in ("agent.react_step", "agent.plan", "agent.synthesize"):
        candidates = cat.task(task_name)["candidates"]["flash_fallback"]
        assert candidates[0] == "omniroute/devworker-groq"
        paid = [model_id for model_id in candidates if cat.model(model_id)["cost_class"] == "paid"]
        assert paid == ["deepseek/flash"]
        assert "deepseek/v4-pro" not in candidates


def test_legacy_task_mapping():
    cat = catalog.load()
    assert cat.legacy_task("GROWTH", "qc_vision") == "podalux.qc_vision"
    assert cat.legacy_task("FORGE", "action") == "agent.react_step"
    assert cat.legacy_task("ORBIT", "voir_page") == "web.describe_page"
    assert cat.legacy_task("LEDGER", "inconnue") == "legacy.ledger.inconnue"


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r.pop("profiles"), "section manquante"),
    (lambda r: r["models"]["deepseek/flash"].update(provider="nope"), "fournisseur inconnu"),
    (lambda r: r["models"]["deepseek/flash"].update(cost_class="cheap"), "classe de cout invalide"),
    (lambda r: r["models"]["deepseek/flash"].pop("price"), "payant sans prix"),
    (lambda r: r["tasks"]["podalux.write_job"].update(baseline="x/y"), "modele de reference inconnu"),
    (lambda r: r["tasks"]["podalux.write_job"]["candidates"].update(turbo=["deepseek/flash"]), "profil inconnu"),
    (lambda r: r["tasks"]["podalux.write_job"]["candidates"]["low_cost"].append("x/y"), "modele inconnu"),
    (lambda r: r.update(default_profile="turbo"), "profil par defaut"),
])
def test_invalid_catalogs_are_rejected(mutate, message):
    raw = copy.deepcopy(catalog.load().raw)
    mutate(raw)
    with pytest.raises(catalog.CatalogError, match=message):
        catalog.validate(raw)


def test_catalog_reloads_when_file_changes(tmp_path, monkeypatch):
    raw = copy.deepcopy(catalog.load().raw)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OCTOPUS_CATALOG", str(path))
    assert catalog.load().daily_budget_usd() == 2.0
    raw["budgets"]["daily_usd"] = 0.5
    path.write_text(json.dumps(raw), encoding="utf-8")
    import os
    os.utime(path, (1, 1))
    assert catalog.load().daily_budget_usd() == 0.5
