"""Vérification complète du système Podalux (imports, DB, DeepSeek, outils, agents, navigateur, cycle).

Usage : python -m agents._verify
"""
import json
import os
import sys
import traceback

from . import config, db, deepseek, tools
from .agents import SOUT, CONVERT, FORGE, GROWTH, LEDGER, ORBIT
from . import cycle as cycle_mod

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  OK   {name}")
    except Exception as e:
        FAIL.append(name)
        print(f"  ECHEC {name}: {e}")
        traceback.print_exc()


def main():
    if "--live" not in sys.argv and os.environ.get("PODALUX_VERIFY_LIVE") != "1":
        print("Ce script fait des appels payants, écrit dans la base de production et écrase "
              "jobs/cash_impayes_relance01.json.")
        print("Vérifications hors-ligne : python -m pytest tests")
        print("Pour le lancer quand même : python -m agents._verify --live")
        raise SystemExit(2)
    print("=== 1. DB (SQLite) ===")
    def t_db():
        db.init_db()
        db.post("TEST", "hello", mention="TEST")
        c = db.log_cost("TEST", "task", config.MODEL_FLASH, 100, 50)
        assert c > 0
        db.record_metric("test_offer", 30, 4, "WARM_PASS", {"x": 1})
        db.decide("TEST", "test", {"x": 1})
        assert db.total_cost() > 0
        assert len(db.recent("TEST")) > 0
    check("init + post + cost + metric + decide", t_db)

    print("=== 2. DeepSeek ===")
    def t_flash_text():
        r = deepseek.call("TEST", "smoke", config.MODEL_FLASH,
                          [{"role": "user", "content": "Réponds exactement: OK"}], max_tokens=20)
        assert r.strip()
    check("flash texte", t_flash_text)

    def t_flash_json():
        r = deepseek.call_json("TEST", "smoke_json", config.MODEL_FLASH,
                               [{"role": "system", "content": "Réponds en JSON."},
                                {"role": "user", "content": '{"a":1}'}], max_tokens=100)
        assert isinstance(r, dict)
    check("flash JSON (thinking désactivé)", t_flash_json)

    def t_pro_json():
        r = deepseek.call_json("TEST", "smoke_pro", config.MODEL_PRO,
                               [{"role": "system", "content": "Réponds en JSON."},
                                {"role": "user", "content": '{"decision":"done"}'}],
                               max_tokens=2000, reasoning="high")
        assert isinstance(r, dict)
    check("pro JSON (reasoning high + thinking)", t_pro_json)

    print("=== 3. Outils (liste blanche shell) ===")
    def t_whitelist_ok():
        out = tools.run_shell(["ffmpeg", "-version"])
        assert "ffmpeg" in out.lower()
    check("ffmpeg accepté", t_whitelist_ok)

    def t_whitelist_reject():
        try:
            tools.run_shell(["rm", "-rf", "/"])
            raise AssertionError("aurait dû refuser")
        except ValueError:
            pass
    check("commande destructive refusée", t_whitelist_reject)

    def t_whitelist_reject_unknown():
        try:
            tools.run_shell(["notarealcmd", "x"])
            raise AssertionError("aurait dû refuser")
        except ValueError:
            pass
    check("exécutable inconnu refusé", t_whitelist_reject_unknown)

    print("=== 4. Agents (structure + appels réels) ===")
    def t_agents_defined():
        for a in (SOUT, CONVERT, FORGE, GROWTH, LEDGER, ORBIT):
            assert hasattr(a, "run"), a.NAME
    check("les 6 agents ont run()", t_agents_defined)

    def t_sout():
        r = SOUT.run(cycle_mod.already_produced())
        assert r.get("offer_id"), "offer_id vide"
    check("SOUT.run (flash réel)", t_sout)

    def t_convert():
        r = CONVERT.run("cash_impayes_relance01", "le cash qui ne rentre pas")
        assert r.get("narration"), "narration vide"
        assert len(r["narration"]) == 7
        roles = [s["role"] for s in r["narration"]]
        assert roles == ["hook", "hook", "douleur", "douleur", "preuve", "soulagement", "cta"], roles
    check("CONVERT.run (flash réel, 7 segments)", t_convert)

    def t_orbit():
        r = ORBIT.run("test", {"score": 30, "humanite": 4, "warm_pass": True, "cost_usd": 0.01, "go": True})
        assert r.get("decision") in ("done", "iterate", "stop")
    check("ORBIT.run (pro réel, arbitrage)", t_orbit)

    def t_ledger():
        r = LEDGER.run("test", {"lufs_integrated": -14, "lra_lu": 3, "satavg_mean": 40},
                       {"total_calcule": 30, "humanite": 4, "warm_pass": True})
        assert r.get("go") is True
    check("LEDGER.run (go/no-go en code)", t_ledger)

    print("=== 5. Navigateur (headless) ===")
    def t_browser():
        from .browser import new_browser
        b = new_browser(headless=True)
        try:
            b.goto("https://example.com")
            assert "example" in b.url().lower()
            snap = b.snapshot()
            assert snap
            links = b.links()
            assert links
            shot = b.screenshot()
            assert shot.exists()
        finally:
            b.stop()
    check("goto + snapshot + links + screenshot", t_browser)

    print("=== 6. Cycle (câblage) ===")
    def t_cycle():
        ap = cycle_mod.already_produced()
        assert isinstance(ap, list)
        assert hasattr(cycle_mod, "run_cycle")
    check("already_produced + run_cycle présents", t_cycle)

    print(f"\n=== RÉSULTAT : {len(PASS)} OK / {len(FAIL)} ÉCHECS ===")
    if FAIL:
        print("ÉCHECS:", FAIL)
        raise SystemExit(1)
    print("TOUT EST OK.")


if __name__ == "__main__":
    main()
