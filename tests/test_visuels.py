"""Audit C6 : libelles du template Remotion propres a chaque offre."""
from __future__ import annotations

import importlib.util
import json

import pytest

from agents import agents as ag
from agents import evals
from conftest import PROJECT

ROLES = ("hook", "douleur", "preuve", "soulagement", "cta")


def test_every_offer_has_complete_visuals():
    assert set(ag.VISUELS) == set(ag.CATALOG)
    for offer_id, visuel in ag.VISUELS.items():
        assert set(visuel) == set(ROLES), offer_id
        assert all(visuel[r]["label"] and visuel[r]["icon"] for r in ROLES), offer_id
        assert visuel["preuve"]["card_title"] and len(visuel["preuve"]["card_rows"]) >= 2
        assert all(len(row) == 2 for row in visuel["preuve"]["card_rows"])


@pytest.mark.parametrize("offer_id", sorted(ag.CATALOG))
def test_visuals_do_not_borrow_another_offer(offer_id):
    text = evals.norm(json.dumps(ag.VISUELS[offer_id], ensure_ascii=False))
    foreign = [a for other, anchors in evals.OFFER_ANCHORS.items() if other != offer_id for a in anchors if a in text]
    assert foreign == [], f"{offer_id} affiche des termes d'une autre offre : {foreign}"


def test_convert_writes_visuals_into_the_job(monkeypatch):
    good = {"titre": "t", "hook": "h", "cta": "37 €", "keywords": [],
            "narration": [{"role": r, "texte": "x"} for r in ag.ROLES_ORDER]}
    monkeypatch.setattr(ag.deepseek, "call_json", lambda *a, **k: good)
    assert ag.CONVERT.run("cash_devis_cgv01", "passer pour un pro")["visuel"]["hook"]["label"] == "DEVIS BRICOLÉ"


def test_role_name_copied_into_narration_is_rejected():
    narration = [{"role": r, "texte": "x"} for r in ag.ROLES_ORDER]
    narration[5]["texte"] = "Soulagement : l'argent rentre enfin."
    with pytest.raises(ag.InvalidLLMOutput, match="nom de rôle"):
        ag.validate_job({"titre": "t", "hook": "h", "cta": "c", "keywords": [], "narration": narration})
    narration[5]["texte"] = "Après : un outil qui relance pour toi."
    ag.validate_job({"titre": "t", "hook": "h", "cta": "c", "keywords": [], "narration": narration})


def test_job_ts_carries_visuals():
    pytest.importorskip("numpy")
    spec = importlib.util.spec_from_file_location("audio_mix", PROJECT / "tools" / "make_audio_chatterbox_full.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    job = {"titre": "t", "prix": "37 €", "cta": "c", "hook": "h", "palette": ag.PALETTE, "keywords": ["devis"],
           "visuel": ag.VISUELS["cash_devis_cgv01"]}
    ts = module.render_job_ts(job)
    assert ts.startswith("export const JOB = ") and ts.endswith(" as const;\n")
    data = json.loads(ts[len("export const JOB = "):-len(" as const;\n")])
    assert data["visuel"]["preuve"]["card_title"] == "DEVIS N°2026-012"
    assert json.loads(module.render_job_ts({**job, "visuel": None})[19:-11])["visuel"] is None  # anciens jobs : defauts
