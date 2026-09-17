"""Etape 2 de l'audit : decisions du cycle en code, sorties LLM validees, offre forcee coherente."""
from __future__ import annotations

import json

import pytest

from agents import agents as ag
from agents import config, cycle, db, deepseek

AXES_OK = {"hook": 4, "douleur": 3, "preuve": 3, "cta": 3, "lisibilite": 3, "humanite": 4, "motion": 3, "son": 2, "pacing": 2}
MEDIA_OK = {"duration_s": 25.18, "resolution": "1080x1920", "fps": 30.0, "lufs_integrated": -14.0, "freezes_gt1_2s": 0,
            "frames": []}  # valeurs reelles de cash_impayes_relance01 (16/09)


def job(**over):
    base = {"titre": "t", "hook": "h", "cta": "9 €, lien en description", "keywords": ["facture"],
            "narration": [{"role": r, "texte": f"texte {i}"} for i, r in enumerate(ag.ROLES_ORDER)]}
    return {**base, **over}


# --- validation des sorties LLM ----------------------------------------------------------------

def test_validate_verdict_accepts_and_normalizes():
    v = ag.validate_verdict({**AXES_OK, "hook": "4", "motion": 3.0, "defauts": "aucun"})
    assert v["hook"] == 4 and v["motion"] == 3 and v["defauts"] == [] and v["fixes"] == []
    assert sum(v[k] for k in ag.AXES) == 27


@pytest.mark.parametrize("over, message", [
    ({"hook": 40}, "hook=40 hors de \\[0, 5\\]"),        # sonde 8
    ({"son": -1}, "son=-1 hors"),
    ({"humanite": True}, "humanite=True n'est pas un entier"),
    ({"pacing": "deux"}, "pacing='deux'"),
    ({"preuve": None}, "preuve=None"),
])
def test_validate_verdict_rejects(over, message):
    with pytest.raises(ag.InvalidLLMOutput, match=message):
        ag.validate_verdict({**AXES_OK, **over})


def test_growth_retries_once_then_scores(monkeypatch):
    answers = iter([{**AXES_OK, "hook": 40}, {**AXES_OK, "fixes": ["plus court"]}])
    monkeypatch.setattr(deepseek, "vision", lambda *a, **k: next(answers))
    v = ag.GROWTH.run("o", job(), {"frames": []})
    assert v["total_calcule"] == 27 and v["warm_pass"] is True and v["fixes"] == ["plus court"]


def test_growth_fails_after_two_invalid_answers(monkeypatch):
    calls = []

    def vision(*a, **k):
        calls.append(1)
        raise ValueError("JSON introuvable dans la réponse vision")

    monkeypatch.setattr(deepseek, "vision", vision)
    with pytest.raises(ag.InvalidLLMOutput, match="après 2 essais"):
        ag.GROWTH.run("o", job(), {"frames": []})
    assert len(calls) == 2


def test_growth_warm_requires_humanite(monkeypatch):
    monkeypatch.setattr(deepseek, "vision", lambda *a, **k: {**AXES_OK, "hook": 5, "motion": 4, "humanite": 2})
    v = ag.GROWTH.run("o", job(), {"frames": []})
    assert v["total_calcule"] == 27 and v["ship_pass"] is True and v["warm_pass"] is False


@pytest.mark.parametrize("bad, message", [
    (job(narration=job()["narration"][:6]), "6 segments"),
    (job(narration=list(reversed(job()["narration"]))), "rôles"),
    (job(titre=""), "titre vide"),
    (job(keywords="devis"), "keywords"),
    ({**job(), "narration": [{"role": r, "texte": " "} for r in ag.ROLES_ORDER]}, "segment sans texte"),
])
def test_validate_job_rejects(bad, message):
    with pytest.raises(ag.InvalidLLMOutput, match=message):
        ag.validate_job(bad)


def test_convert_retries_with_the_error_then_writes_job(monkeypatch, isolated):
    prompts = []
    answers = iter([job(narration=[]), job()])

    def call_json(agent, task, model, messages, **kw):
        prompts.append(messages[-1]["content"])
        return next(answers)

    monkeypatch.setattr(deepseek, "call_json", call_json)
    written = ag.CONVERT.run("cash_impayes_relance01", "le cash qui ne rentre pas")
    assert len(prompts) == 2 and "0 segments au lieu de 7" in prompts[1]
    assert json.loads((isolated / "jobs" / "cash_impayes_relance01.json").read_text(encoding="utf-8")) == written


def test_convert_refuses_unknown_offer_without_calling_the_model(monkeypatch):
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: pytest.fail("appel LLM inutile"))
    with pytest.raises(ValueError, match="offre inconnue"):
        ag.CONVERT.run("cash_inexistante", "angle")


# --- LEDGER et ORBIT ---------------------------------------------------------------------------

def test_media_gate_on_real_metrics():
    assert ag.LEDGER.media_gate(MEDIA_OK) == []
    devis = {"duration_s": 19.54, "resolution": "1080x1920", "lufs_integrated": -14.3, "freezes_gt1_2s": 0}
    assert ag.LEDGER.media_gate(devis) == []
    point_zero = {"duration_s": 18.27, "resolution": "1080x1920", "lufs_integrated": -20.6, "freezes_gt1_2s": 0}
    assert ag.LEDGER.media_gate(point_zero) == [("lufs_integrated", "lufs_integrated -20.6 hors de [-16, -12]")]
    assert [k for k, _ in ag.LEDGER.media_gate({})] == list(config.MEDIA_GATES)
    assert ag.LEDGER.media_gate({**MEDIA_OK, "resolution": "1920x1080"})[0][0] == "resolution"


def test_ledger_blocks_publication_on_media_defect_and_suggests_script_fix():
    growth = {"total_calcule": 30, "humanite": 4, "warm_pass": True}
    long_video = ag.LEDGER.run("o", {**MEDIA_OK, "duration_s": 41.0}, growth)
    assert long_video["go"] is False and long_video["blocking_keys"] == ["duration_s"]
    assert "raccourcir" in long_video["fixes"][0]
    assert ag.LEDGER.run("o", MEDIA_OK, growth)["go"] is True


LEDGER_OK = {"score": 27, "humanite": 4, "warm_pass": True, "budget_ok": True, "go": True, "blocking_keys": [], "blocking": []}


@pytest.mark.parametrize("ledger, left, decision", [
    (LEDGER_OK, 2, "done"),                                             # ORBIT v4-pro avait dit "iterate" (27, 26)
    ({**LEDGER_OK, "score": 23, "warm_pass": False, "go": False}, 2, "iterate"),
    ({**LEDGER_OK, "score": 23, "warm_pass": False, "go": False}, 0, "stop"),
    ({**LEDGER_OK, "budget_ok": False, "go": False}, 2, "stop"),
    ({**LEDGER_OK, "go": False, "blocking_keys": ["lufs_integrated"], "blocking": ["lufs -20"]}, 2, "stop"),
    ({**LEDGER_OK, "go": False, "blocking_keys": ["duration_s"], "blocking": ["duree 41"]}, 1, "iterate"),
])
def test_orbit_rule(ledger, left, decision):
    assert ag.ORBIT.rule(ledger, left)["decision"] == decision


def test_orbit_code_mode_makes_no_llm_call(monkeypatch):
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: pytest.fail("appel LLM inutile"))
    r = ag.ORBIT.run("o", LEDGER_OK, iterations_left=2)
    assert r["decision"] == "done" and r["ledger"] == LEDGER_OK


# --- cycle complet (etapes simulees) -----------------------------------------------------------

@pytest.fixture
def pipeline(monkeypatch):
    seen = {"convert": [], "scores": iter([23, 27]), "sout": 0, "asked": []}

    def sout(already):
        seen["sout"] += 1
        return {"offer_id": "cash_avenant_scope01", "angle": "le client qui élargit sans payer"}

    def convert(offer_id, angle, fixes=None):
        seen["convert"].append((offer_id, angle, fixes))
        return job()

    def growth(offer_id, j, metrics):
        total = next(seen["scores"])
        return {"total_calcule": total, "humanite": 4, "warm_pass": total >= 24, "fixes": [f"fix-{total}"]}

    def ask_human(agent, kind, question, timeout_s=300):
        seen["asked"].append(question)
        return seen.get("answer", "oui")

    monkeypatch.setattr(ag.SOUT, "run", staticmethod(sout))
    monkeypatch.setattr(ag.CONVERT, "run", staticmethod(convert))
    monkeypatch.setattr(ag.FORGE, "run", staticmethod(lambda offer_id, j: dict(MEDIA_OK)))
    monkeypatch.setattr(ag.GROWTH, "run", staticmethod(growth))
    monkeypatch.setattr(db, "ask_human", ask_human)
    monkeypatch.setattr(deepseek, "call_json", lambda *a, **k: pytest.fail("aucun appel LLM attendu"))
    return seen


def test_forced_offer_skips_sout_and_uses_its_own_angle(pipeline):
    result = cycle.run_cycle(offer_id="cash_impayes_relance01", max_iterations=3)  # sonde 7
    assert pipeline["sout"] == 0
    assert pipeline["convert"][0] == ("cash_impayes_relance01", "le cash qui ne rentre pas", None)
    assert [i["decision"] for i in result["iterations"]] == ["iterate", "done"]
    assert pipeline["convert"][1][2] == ["fix-23"]  # corrections du QC transmises a l'iteration suivante
    assert pipeline["asked"] and db.run_lock_holder() is None
    decisions = [r["decision"] for r in db._conn().execute("SELECT decision FROM decisions")]
    assert "publish_approved" in decisions


def test_unknown_forced_offer_is_rejected_before_anything(pipeline):
    with pytest.raises(ValueError, match="offre inconnue"):
        cycle.run_cycle(offer_id="cash_inexistante")
    assert db.current_run() is None and pipeline["convert"] == []


def test_sout_out_of_catalog_falls_back(pipeline, monkeypatch):
    monkeypatch.setattr(ag.SOUT, "run", staticmethod(lambda already: {"offer_id": "offre_inventee", "angle": "x"}))
    result = cycle.run_cycle(max_iterations=1)
    assert result["offer_id"] == "cash_impayes_relance01"
    assert pipeline["convert"][0][1] == ag.CATALOG["cash_impayes_relance01"]["angle"]


def test_ambiguous_answer_does_not_approve_publication(pipeline):
    pipeline["answer"] = "on verra demain"
    pipeline["scores"] = iter([30])
    cycle.run_cycle(offer_id="cash_devis_cgv01", max_iterations=1)
    decisions = [r["decision"] for r in db._conn().execute("SELECT decision FROM decisions")]
    assert "publish_approved" not in decisions


def test_stop_request_keeps_stopped_status(pipeline, monkeypatch):
    real_convert = ag.CONVERT.run

    def convert_then_stop(offer_id, angle, fixes=None):
        db.request_stop()
        return real_convert(offer_id, angle, fixes)

    monkeypatch.setattr(ag.CONVERT, "run", staticmethod(convert_then_stop))
    cycle.run_cycle(offer_id="cash_devis_cgv01", max_iterations=3)
    run = db.current_run()
    assert run["status"] == "stopped" and len(pipeline["convert"]) == 1


@pytest.mark.parametrize("answer, expected", [("oui", True), ("Oui.", True), (" OK ", True), ("y", True),
                                              ("non", False), ("on verra", False), ("oui mais non", False),
                                              ("", False), (None, False)])
def test_is_yes(answer, expected):
    assert cycle.is_yes(answer) is expected
