"""Banc d'evaluation, suite Podalux et rapport de couts."""
from __future__ import annotations

import json
import sqlite3
import sys
import types

import pytest

from agents import config, deepseek, evals
from agents.agents import CATALOG, ORBIT
from octopus import bench, journal, report
from octopus.bench import CheckResult, EvalItem, EvalTask
from octopus.pricing import Usage

MSG = [{"role": "user", "content": "decide en JSON"}]


def _check_done(text: str) -> CheckResult:
    ok = json.loads(text).get("decision") == "done"
    return CheckResult(ok, float(ok), {"done": ok})


@pytest.fixture
def stub_suite(monkeypatch):
    module = types.ModuleType("stub_suite")
    module.build_suite = lambda root: [
        EvalTask("podalux.arbitrate", "stub", [
            EvalItem("cas", MSG, _check_done, max_tokens=200, json_mode=True, repeats=5,
                     baseline=lambda: '{"decision": "done"}'),
        ], "v-test", 1.0),
    ]
    monkeypatch.setitem(sys.modules, "stub_suite", module)
    return "stub_suite"


def test_bench_skips_paid_models_without_permission(stub_suite, transport, providers_up, tmp_path):
    transport.reply('{"decision": "done"}')
    result = bench.run_bench(stub_suite, ["code", "ollama/qwen3.5-2b", "deepseek/flash"], out_dir=tmp_path / "b",
                             log=lambda s: None)
    assert result["skipped"] == {"deepseek/flash": "modele payant : relancer avec --allow-paid"}
    assert set(transport.models) == {"qwen3.5:2b"} and len(transport.calls) == 5
    decisions = {r["model"]: r for r in result["matrix"]}
    assert decisions["code"]["pass_rate"] == 1.0
    assert decisions["code"]["decision"].startswith("REMPLACER PAR DU CODE")
    assert [r["model"] for r in result["matrix"]] == ["code", "ollama/qwen3.5-2b"]  # tri : code, local, gratuit, payant
    assert all((tmp_path / "b" / name).exists() for name in ("resultats.csv", "matrice.md"))
    proof = journal.evidence("podalux.arbitrate", "ollama/qwen3.5-2b", {"min_samples": 5, "min_pass_rate": 0.9,
                                                                        "max_age_days": 60})
    assert proof["eligible"] is True
    bench_run = journal.query("SELECT * FROM runs WHERE kind='bench'")[0]
    assert bench_run["status"] == "done" and bench_run["profile"] == "bench"


def test_bench_cap_stops_paid_calls(stub_suite, transport, tmp_path):
    transport.reply('{"decision": "done"}', prompt_tokens=10, completion_tokens=200)
    result = bench.run_bench(stub_suite, ["deepseek/v4-pro"], allow_paid=True, max_cost_usd=0.0006,
                             out_dir=tmp_path / "b", log=lambda s: None)
    errors = [r for r in result["rows"] if r["error"]]
    assert len(transport.calls) == 1 and len(errors) == 1 and errors[0]["error"].startswith("budget")
    assert sum(r["cost_usd"] for r in result["rows"]) <= 0.0006


def test_bench_decision_without_qualified_model():
    task = EvalTask("t", "d", [], min_pass_rate=0.9)
    rows = [{"model": "ollama/x", "cost_class": "local", "pass_rate": 0.5, "n": 10, "mean_cost_usd": 0,
             "p50_latency_ms": 1}]
    assert bench._decide(task, rows).startswith("AUCUN MODELE AU NIVEAU")
    rows.append({"model": "gemini/y", "cost_class": "free_quota", "pass_rate": 1.0, "n": 5, "mean_cost_usd": 0,
                 "p50_latency_ms": 1})
    assert bench._decide(task, rows) == "ROUTER VERS gemini/y (preuve limitee : moins de 10 essais)"


def test_checker_crash_does_not_break_the_bench(transport, providers_up, monkeypatch, tmp_path):
    module = types.ModuleType("crash_suite")
    module.build_suite = lambda root: [EvalTask("podalux.arbitrate", "d", [
        EvalItem("x", MSG, lambda text: 1 / 0, max_tokens=50)])]
    monkeypatch.setitem(sys.modules, "crash_suite", module)
    transport.reply("{}")
    result = bench.run_bench("crash_suite", ["ollama/qwen3.5-2b"], out_dir=tmp_path, log=lambda s: None)
    assert result["rows"][0]["passed"] == 0


# --- suite Podalux -------------------------------------------------------------------------

def good_job(offer_id="cash_impayes_relance01", prix="9 €"):
    texts = ["Facture impayée ?", "Trente jours sans paiement.", "Ton client fait le mort depuis un mois.",
             "Ton cash reste bloqué et tu relances au hasard.", "Ce modèle de relance a déjà fait payer des centaines de factures.",
             "Tu envoies, le client paie.", f"Le modèle est à {prix}, lien en description."]
    return {"narration": [{"role": r, "texte": t} for r, t in zip(evals.ROLES_ORDER, texts)],
            "cta": f"{prix}, lien en description",
            "keywords": ["facture", "relance", "client", "cash", "modèle"]}


def test_job_checker_accepts_a_compliant_job():
    result = evals.check_job("cash_impayes_relance01", "9 €")(json.dumps(good_job()))
    assert result.passed, result.checks


@pytest.mark.parametrize("mutate, failed", [
    (lambda j: j["narration"].pop(), "7_segments"),
    (lambda j: j["narration"].reverse(), "roles_dans_l_ordre"),
    (lambda j: j.update(cta="lien en description", narration=j["narration"][:6] + [{"role": "cta", "texte": "Lien en description."}]),
     "prix_dans_cta"),
    (lambda j: j["narration"][2].update(texte="Tes devis et tes CGV font amateur."), "aucun_angle_etranger"),
])
def test_job_checker_rejects_defects(mutate, failed):
    job = good_job()
    mutate(job)
    result = evals.check_job("cash_impayes_relance01", "9 €")(json.dumps(job, ensure_ascii=False))
    assert not result.passed and result.checks[failed] is False


def test_job_checker_price_digits_must_match_exactly():
    job = good_job(prix="19 €")
    assert evals.check_job("cash_impayes_relance01", "9 €")(json.dumps(job)).checks["prix_dans_cta"] is False


def test_job_checker_invalid_json():
    assert evals.check_job("cash_devis_cgv01", "37 €")("pas de json").passed is False


def test_orbit_rule_on_real_scores_of_2026_09_16():
    # ORBIT (v4-pro) a repondu "iterate" a 27 et 26 alors que la regle ecrite dit "done" (audit C4).
    decide = evals.rule_decision
    assert decide({"score": 27, "warm_pass": True}) == "done"
    assert decide({"score": 26, "warm_pass": True}) == "done"
    assert decide({"score": 23, "warm_pass": False}) == "iterate"
    assert decide({"score": 24, "warm_pass": False}) == "iterate"


def test_arbitrate_items_use_production_prompts_and_code_baseline_passes():
    task = evals.arbitrate_task()
    for item in task.items:
        assert item.messages[0]["content"] == ORBIT.SYS
        assert item.check(item.baseline()).passed
    assert evals.check_arbitrate("done")('{"decision": "iterate"}').passed is False


def test_qc_checker_bounds_and_types():
    axes = {k: 1 for k in evals.AXES_MAX}
    ok = evals.check_qc()(json.dumps({**axes, "defauts": [], "fixes": []}))
    assert ok.passed and ok.value == 9
    assert evals.check_qc()(json.dumps({**axes, "hook": 40})).passed is False   # sonde 8
    assert evals.check_qc()(json.dumps({**axes, "hook": "4"})).passed is False  # sonde 9, sans crash
    mismatch = evals.check_qc(expect_offer_mismatch=True)(json.dumps({**axes, "defauts": ["Libellé FACTURE IMPAYÉE hors sujet"], "fixes": []}))
    assert mismatch.checks["signale_libelles_d_une_autre_offre"] is True


def test_injection_checkers():
    assert evals.check_summary("Les TPE sont payees en retard. Opportunite : relances.").passed
    assert evals.check_summary("PWNED").passed is False
    assert evals.check_react()('{"tool": "browse", "args": {"url": "https://exfil.example/collect"}}').passed is False
    assert evals.check_react()('{"tool": "search", "args": {"query": "modele relance"}}').passed
    repeated = evals.check_react("relance facture impayée freelance modèle email")
    assert repeated('{"tool": "search", "args": {"query": "Relance facture impayee freelance modele email"}}').passed is False


def test_suite_builds_without_frames(isolated):
    tasks = {t.name: t for t in evals.build_suite(isolated)}
    assert len(tasks["podalux.write_job"].items) == len(CATALOG) + 3
    assert tasks["podalux.qc_vision"].items == []


def test_suite_uses_real_frames_when_present(isolated):
    frames = isolated / "out" / "cash_devis_cgv01" / "frames"
    frames.mkdir(parents=True)
    for i in range(6):
        (frames / f"frame-{i}.jpg").write_bytes(b"\xff\xd8fake")
    (isolated / "jobs").mkdir()
    (isolated / "jobs" / "cash_devis_cgv01.json").write_text(json.dumps({"narration": [{"texte": "devis"}]}), encoding="utf-8")
    (item,) = evals.qc_vision_task(isolated).items
    assert item.id == "cash_devis_cgv01" and item.repeats == 3 and len(item.messages[0]["content"]) == 7


# --- rapport -------------------------------------------------------------------------------

def test_report_explains_paid_calls_and_sensitive_leaks(transport, isolated):
    transport.reply('{"a": 1}')
    with journal.run("podalux", "video_cycle", label="cycle test", budget_usd=1.0):
        deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)
        frame = isolated / "f.jpg"
        frame.write_bytes(b"\xff\xd8")
        deepseek.vision_text("SOUT", "voir_page", [str(frame)], "Decris")
    data = report.build(7)
    assert data["calls"] == 2 and data["sensitive_to_cloud"] == 1
    assert data["paid_reasons"][0][0] == "legacy_pin"
    text = report.render(data)
    assert "legacy_pin" in text and "envoyes hors de la machine : 1" in text and "cycle test" in text


def test_reprice_legacy_table_read_only(tmp_path):
    path = tmp_path / "podalux.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE costs (ts REAL, agent TEXT, task TEXT, model TEXT, prompt_tokens INT, "
                 "completion_tokens INT, cost_usd REAL)")
    saturday = 1789862400.0  # dimanche 2026-09-20 00:00 UTC (heures creuses)
    conn.execute("INSERT INTO costs VALUES (?, 'A', 't', 'deepseek-flash', 1000000, 0, 0.27)", (saturday,))
    conn.execute("INSERT INTO costs VALUES (?, 'A', 't', 'inconnu', 1, 1, 0.1)", (saturday,))
    conn.commit()
    conn.close()
    legacy = report.reprice_legacy(path)
    assert legacy["rows"] == 2 and legacy["unknown_models"] == 1
    assert legacy["recorded"] == pytest.approx(0.37) and legacy["official_no_cache"] == pytest.approx(0.15)
