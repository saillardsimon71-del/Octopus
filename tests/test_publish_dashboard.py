"""Publication (publish.json absent), metriques et cout affiches par la GUI."""
from __future__ import annotations

import json
import time

import pytest

from agents import agents as ag
from agents import config, db, publish
from octopus import journal


def write_job(root, offer_id="cash_devis_cgv01"):
    job = {"offer_id": offer_id, "titre": "Devis & CGV pro", "prix": "37 €", "hook": "Un client demande un devis ?",
           "soulagement": "Tu passes pour un pro.", "cta": "37 €, lien en description.", "keywords": ["devis", "CGV"],
           "stripe_link": ag.STRIPE_LINK, "sub_id": ag.SUB_ID, "voix": {"moteur": "chatterbox"},
           "narration": [{"role": "hook", "texte": "Un client demande un devis ?"}]}
    (root / "jobs").mkdir(exist_ok=True)
    (root / "jobs" / f"{offer_id}.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    out = root / "out" / offer_id
    out.mkdir(parents=True)
    (out / "qc_metrics.json").write_text(json.dumps({"duration_s": 19.54, "resolution": "1080x1920"}), encoding="utf-8")
    return out


def test_publish_plan_is_drafted_when_publish_json_is_missing(isolated):
    out = write_job(isolated)
    db.record_metric("cash_devis_cgv01", 28, 4, "WARM_PASS", {})
    result = publish.publish("cash_devis_cgv01", dry_run=True)
    plan = result["plan"]
    assert result["dry_run"] is True
    assert plan["title"] == "Devis & CGV pro (37 €)" and plan["stripe_link"] == ag.STRIPE_LINK
    assert plan["tags"] == ["devis", "CGV"] and plan["duration_s"] == 19.54 and plan["score_qc"]["total"] == 28
    saved = json.loads((out / "publish.json").read_text(encoding="utf-8"))
    assert saved["brouillon"] is True


def test_existing_publish_json_is_kept(isolated):
    out = write_job(isolated)
    (out / "publish.json").write_text(json.dumps({"title_fr": "Titre relu"}), encoding="utf-8")
    assert publish.publish("cash_devis_cgv01")["plan"]["title"] == "Titre relu"


def test_publish_without_job_explains(isolated):
    with pytest.raises(FileNotFoundError, match="lancer un cycle"):
        publish.publish("cash_linkedin_rdv01")


def test_metrics_panel_hides_test_rows():
    db.record_metric("test_offer", 30, 4, "WARM_PASS", {})
    db.record_metric("cash_impayes_relance01", 30, 4, "WARM_PASS", {})
    assert [m["offer_id"] for m in db.metrics_list()] == ["cash_impayes_relance01"]


def test_cost_today_ignores_older_days():
    base = {"task": "t", "profile": "legacy", "model": "deepseek/flash", "provider": "deepseek",
            "cost_class": "paid", "status": "ok"}
    journal.record_llm_call({**base, "ts": time.time(), "cost_usd": 0.01})
    journal.record_llm_call({**base, "ts": time.time() - 2 * 86400, "cost_usd": 3.0})
    assert db.cost_today() == pytest.approx(0.01)
