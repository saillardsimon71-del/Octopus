"""Boucle économique : canaux génériques, grand livre, dépenses autorisées, verdict d'expérience, réinvestissement."""
from __future__ import annotations

import time

import pytest

from octopus import economy, journal, strategy, tasks
from octopus.strategy import StrategyError

B = "atelier_test"
DAY = 86400


def _experiment(business=B, **fields):
    objective = strategy.create("objective", business, "Cash", created_by="human", statement="Encaisser")
    hypothesis = strategy.create("hypothesis", business, "H", created_by="orbit", parent_id=objective, statement="h")
    experiment = strategy.create("experiment", business, "E", created_by="orbit", parent_id=hypothesis, action="a",
                                 **fields)
    strategy.transition("experiment", experiment, business, "running", actor="orbit")
    return experiment


def _cash_in(amount, currency="EUR", **kw):
    return economy.record_cash(B, "in", amount, currency, "vente", nature="observed", created_by="human",
                               source_ref="export-stripe-2026-09.csv", **kw)


# --- canaux ------------------------------------------------------------------------------------------

def test_channels_are_free_form_and_qualified_over_time():
    site = economy.add_channel(B, "Website", "Boutique", created_by="agent:SOUT", locator="https://exemple.fr",
                               capabilities=["Sell", "observe"])
    economy.add_channel(B, "email", "Liste", created_by="human", capabilities=["message"])
    assert [c["id"] for c in economy.channels(B, capability="sell")] == [site]
    assert economy.channels(B)[0]["nature"] == "unverified" and economy.channels(B)[0]["access"] == "none"
    with pytest.raises(StrategyError):
        economy.update_channel(B, site, actor="orbit", nature="observed")  # pas de source
    economy.update_channel(B, site, actor="orbit", nature="observed", source_ref="https://exemple.fr", status="active",
                           access="observe")
    with pytest.raises(StrategyError):
        economy.update_channel(B, site, actor="orbit", status="discovered")
    assert economy.channels("podalux") == []
    with pytest.raises(StrategyError):
        economy.update_channel("podalux", site, actor="orbit", status="abandoned")


# --- grand livre ---------------------------------------------------------------------------------------

def test_ledger_counts_only_observed_cash_and_keeps_currencies_apart():
    _cash_in(100)
    _cash_in(30, "USD")
    economy.record_cash(B, "out", 40, "eur", "outil", nature="observed", created_by="human", source_ref="facture-12")
    economy.record_cash(B, "in", 999, "EUR", "vente annoncée", nature="unverified", created_by="agent:GROWTH")
    summary = economy.cash_summary(B)
    assert summary["EUR"] == {"in_observed": 100.0, "out_observed": 40.0, "in_unverified": 999.0,
                              "out_unverified": 0.0, "net_observed": 60.0}
    assert summary["USD"]["net_observed"] == 30.0
    with pytest.raises(StrategyError):
        economy.record_cash(B, "in", 5, "EUR", "vente", nature="observed", created_by="orbit")  # sans source
    with pytest.raises(StrategyError):
        economy.record_cash(B, "in", 5, "EUR", "vente", nature="inferred", created_by="orbit")
    with pytest.raises(StrategyError):
        _cash_in(-5)
    assert economy.cash_summary("podalux") == {}


def test_csv_export_import_is_observed_signed_and_idempotent(tmp_path):
    bank = tmp_path / "releve.csv"
    bank.write_text("Date;Libellé;Montant;Devise\n17/09/2026;Vente guide;1 234,50;EUR\n"
                    "18/09/2026;Abonnement outil;-19,99;EUR\n18/09/2026;Ligne vide;;EUR\n", encoding="utf-8")
    first = economy.import_cash_csv(B, str(bank), created_by="human")
    assert first["imported"] == 2
    again = economy.import_cash_csv(B, str(bank), created_by="human")
    assert again == {"imported": 0, "skipped_duplicates": 2, "entry_ids": []}
    assert economy.cash_summary(B)["EUR"]["net_observed"] == pytest.approx(1214.51)
    stripe = tmp_path / "stripe.csv"
    stripe.write_text("id,created,net,description\ntxn_1,2026-09-18 10:00:00,42.00,charge\n", encoding="utf-8")
    with pytest.raises(StrategyError):
        economy.import_cash_csv(B, str(stripe), created_by="human")  # devise inconnue
    economy.import_cash_csv(B, str(stripe), created_by="human", currency="usd")
    row = journal.query("SELECT * FROM ledger_entries WHERE currency='USD'")[0]
    assert row["source_ref"] == "stripe.csv#txn_1" and row["nature"] == "observed" and row["direction"] == "in"
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(StrategyError):
        economy.import_cash_csv(B, str(bad), created_by="human", currency="EUR")


def test_correction_is_a_reversal_not_an_edit():
    entry = _cash_in(100)
    economy.record_cash(B, "out", 100, "EUR", "remboursement", nature="observed", created_by="human",
                        source_ref="export", reverses_id=entry)
    assert economy.cash_summary(B)["EUR"]["net_observed"] == 0
    with pytest.raises(StrategyError):
        economy.record_cash(B, "out", 1, "USD", "x", nature="observed", created_by="human", source_ref="s",
                            reverses_id=entry)


# --- dépenses ------------------------------------------------------------------------------------------

def test_no_spend_without_an_allowance_and_allowance_is_consumed():
    denied = economy.authorize_spend(B, 10, "EUR", "publicité test", requested_by="agent:GROWTH")
    assert denied["status"] == "denied" and "aucune enveloppe" in denied["reason"]
    with pytest.raises(StrategyError):
        economy.grant_allowance(B, 50, "EUR", granted_by="orbit", rationale="je le veux")
    allowance = economy.grant_allowance(B, 50, "EUR", granted_by="human", rationale="premier test")
    first = economy.authorize_spend(B, 30, "EUR", "publicité", requested_by="agent:GROWTH")
    assert first["status"] == "authorized" and first["allowance_id"] == allowance
    assert economy.authorize_spend(B, 30, "EUR", "encore", requested_by="agent:GROWTH")["status"] == "denied"
    assert economy.authorize_spend(B, 5, "USD", "autre devise", requested_by="agent:GROWTH")["status"] == "denied"
    economy.cancel_spend(B, first["request_id"], actor="human")
    assert economy.authorize_spend(B, 30, "EUR", "après annulation", requested_by="agent:GROWTH")["status"] == "authorized"
    rows = journal.query("SELECT status FROM spend_requests WHERE business=? ORDER BY id", (B,))
    assert [r["status"] for r in rows] == ["denied", "cancelled", "denied", "denied", "authorized"]


def test_experiment_budget_caps_spending_and_execution_is_traced():
    experiment = _experiment(budget_limit=20, budget_currency="EUR")
    economy.grant_allowance(B, 100, "EUR", granted_by="human", rationale="enveloppe générale")
    ok = economy.authorize_spend(B, 15, "EUR", "annonce", requested_by="orbit", experiment_id=experiment)
    assert ok["status"] == "authorized"
    over = economy.authorize_spend(B, 10, "EUR", "annonce 2", requested_by="orbit", experiment_id=experiment)
    assert over["status"] == "denied" and "limite" in over["reason"]
    with pytest.raises(StrategyError):  # dépasse la demande autorisée
        economy.record_cash(B, "out", 16, "EUR", "pub", nature="observed", created_by="human", source_ref="facture",
                            experiment_id=experiment, spend_request_id=ok["request_id"])
    economy.record_cash(B, "out", 14.5, "EUR", "pub", nature="observed", created_by="human", source_ref="facture",
                        experiment_id=experiment, spend_request_id=ok["request_id"])
    status = journal.query("SELECT status FROM spend_requests WHERE id=?", (ok["request_id"],))[0]["status"]
    assert status == "executed"


def test_daily_spend_summary_groups_engaged_and_actual_by_category():
    economy.grant_allowance(B, 100, "EUR", granted_by="human", rationale="test")
    pending = economy.authorize_spend(B, 15, "EUR", "outil", requested_by="test")
    executed = economy.authorize_spend(B, 20, "EUR", "publicité", requested_by="test")
    economy.record_cash(B, "out", 12, "EUR", "advertising", nature="observed", created_by="test",
                        source_ref="invoice-1", spend_request_id=executed["request_id"])

    rows = {(row["category"], row["currency"]): row for row in economy.daily_spend_summary(B)}
    assert rows[("unclassified", "EUR")]["engaged"] == pytest.approx(15)
    assert rows[("advertising", "EUR")]["actual_observed"] == pytest.approx(12)
    assert pending["status"] == "authorized"


def test_daily_spend_summary_includes_entry_created_at_current_tick(monkeypatch):
    tick = 1789855477.1928973
    monkeypatch.setattr(economy.time, "time", lambda: tick)

    economy.grant_allowance(B, 100, "EUR", granted_by="human", rationale="boundary")
    economy.authorize_spend(B, 15, "EUR", "outil", requested_by="test")
    executed = economy.authorize_spend(B, 20, "EUR", "publicit?", requested_by="test")
    economy.record_cash(B, "out", 12, "EUR", "advertising", nature="observed",
                        created_by="test", source_ref="invoice-boundary",
                        spend_request_id=executed["request_id"])

    current = {
        (row["category"], row["currency"]): row
        for row in economy.daily_spend_summary(B)
    }

    assert current[("unclassified", "EUR")]["engaged"] == pytest.approx(15)
    assert current[("advertising", "EUR")]["actual_observed"] == pytest.approx(12)

    explicit_boundary = {
        (row["category"], row["currency"]): row
        for row in economy.daily_spend_summary(
            B, since=tick - 1, until=tick
        )
    }

    assert ("advertising", "EUR") not in explicit_boundary


def test_spend_refused_for_inactive_experiment():
    experiment = _experiment()
    economy.grant_allowance(B, 100, "EUR", granted_by="human", rationale="x")
    strategy.transition("experiment", experiment, B, "cancelled", actor="human")
    result = economy.authorize_spend(B, 1, "EUR", "x", requested_by="orbit", experiment_id=experiment)
    assert result["status"] == "denied" and "non active" in result["reason"]


# --- verdicts ------------------------------------------------------------------------------------------

def test_cash_metric_success_concludes_experiment_and_proposes_next_decision():
    experiment = _experiment(metric="cash_net:EUR", target_value=50, stop_value=0, deadline_at=time.time() + DAY)
    assert economy.evaluate_experiment(B, experiment)["verdict"] == "pending"
    _cash_in(80, experiment_id=experiment)
    economy.record_cash(B, "out", 20, "EUR", "outil", nature="observed", created_by="human", source_ref="f",
                        experiment_id=experiment)
    result = economy.evaluate_experiment(B, experiment)
    assert result["verdict"] == "supports" and result["value"] == 60.0
    row = strategy.get("experiment", experiment, B)
    assert row["status"] == "completed" and row["outcome"] == "supports"
    evidence = strategy.get("evidence", result["evidence_id"], B)
    assert evidence["nature"] == "computed" and evidence["value"] == 60.0 and evidence["experiment_id"] == experiment
    decision = strategy.get("decision", result["decision_id"], B)
    assert decision["status"] == "proposed" and decision["spend_amount"] is None
    strategy.transition("decision", decision["id"], B, "approved", actor="policy:evaluate")  # sans engagement d'argent


def test_observed_metric_refutes_at_deadline_and_unverified_values_do_not_count():
    now = time.time()
    experiment = _experiment(metric="commandes", target_value=5, stop_value=1, deadline_at=now + DAY)
    strategy.create("evidence", B, "3 commandes annoncées", created_by="agent:GROWTH", nature="unverified",
                    source_type="agent", observation="x", experiment_id=experiment, metric="commandes", value=3)
    strategy.create("evidence", B, "1 commande", created_by="agent:GROWTH", nature="observed", source_type="web",
                    source_ref="https://exemple.fr/admin/orders", captured_at=now, observation="x",
                    experiment_id=experiment, metric="commandes", value=1)
    assert economy.evaluate_experiment(B, experiment, now=now)["verdict"] == "pending"  # seuil atteint, échéance non
    result = economy.evaluate_experiment(B, experiment, now=now + 2 * DAY)
    assert result["verdict"] == "refutes" and result["value"] == 1


def test_deadline_without_any_measure_refutes_and_budget_exhaustion_stops():
    silent = _experiment(metric="inscrits", target_value=10, deadline_at=time.time() - 1)
    assert economy.evaluate_experiment(B, silent)["verdict"] == "refutes"
    costly = _experiment(metric="cash_net:EUR", target_value=100, budget_limit=10, budget_currency="EUR")
    economy.record_cash(B, "out", 10, "EUR", "pub", nature="observed", created_by="human", source_ref="f",
                        experiment_id=costly)
    result = economy.evaluate_experiment(B, costly)
    assert result["verdict"] == "refutes" and "budget" in result["reason"]


def test_llm_cost_is_attributed_to_the_experiment_through_its_tasks():
    experiment = _experiment()
    task = tasks.enqueue(B, "orbit.mission", {"goal": "x"})
    other = tasks.enqueue(B, "orbit.mission", {"goal": "y"})
    base = {"task": "t", "profile": "zero_cost", "model": "m", "provider": "p", "cost_class": "paid", "status": "ok"}
    for task_id, cost in ((task, 0.25), (other, 5.0)):
        with journal.run(B, "task:orbit.mission") as run:
            tasks.set_run(task_id, run.id)
            journal.record_llm_call({**base, "ts": time.time(), "run_id": run.id, "business": B, "cost_usd": cost})
    strategy.link(B, "experiment", experiment, "task", task, "executed_by")
    assert economy.llm_cost_usd(B, experiment_id=experiment) == 0.25
    assert economy.llm_cost_usd(B) == 5.25


# --- réinvestissement ----------------------------------------------------------------------------------

def test_reinvestment_grants_a_bounded_allowance_once_per_period_from_observed_profit():
    now = time.time()
    assert economy.reinvest(B, now=now)["status"] == "no_policy"
    with pytest.raises(StrategyError):
        economy.set_reinvest_policy(B, share=0.5, max_amount=100, currency="EUR", period_days=7, set_by="orbit")
    economy.set_reinvest_policy(B, share=0.5, max_amount=100, currency="EUR", period_days=7, set_by="human")
    economy.record_cash(B, "in", 999, "EUR", "annoncé", nature="unverified", created_by="orbit")
    assert economy.reinvest(B, now=now)["status"] == "no_profit"
    _cash_in(300, occurred_at=now - DAY)
    economy.record_cash(B, "out", 60, "EUR", "outil", nature="observed", created_by="human", source_ref="f",
                        occurred_at=now - DAY)
    granted = economy.reinvest(B, now=now)
    assert granted["status"] == "granted" and granted["amount"] == 100 and granted["net_observed"] == 240
    assert economy.reinvest(B, now=now + DAY)["status"] == "already_granted"
    spend = economy.authorize_spend(B, 80, "EUR", "réinvestissement", requested_by="orbit")
    assert spend["status"] == "authorized" and spend["allowance_id"] == granted["allowance_id"]
    assert economy.reinvest("podalux", now=now)["status"] == "no_policy"


def test_cycle_evaluates_running_experiments_and_reinvests_without_llm(transport):
    now = time.time()
    done = _experiment(metric="cash_net:EUR", target_value=10)
    waiting = _experiment(metric="cash_net:EUR", target_value=10, deadline_at=now + DAY)
    _cash_in(20, experiment_id=done, occurred_at=now - 10)
    economy.set_reinvest_policy(B, share=0.1, max_amount=5, currency="EUR", period_days=30, set_by="human")
    result = economy.cycle(B, now=now)
    verdicts = {e["experiment_id"]: e["verdict"] for e in result["evaluated"]}
    assert verdicts == {done: "supports", waiting: "pending"}
    assert result["reinvest"]["status"] == "granted" and result["reinvest"]["amount"] == 2.0
    assert transport.calls == []
    status = economy.status(B)
    assert [e["id"] for e in status["running_experiments"]] == [waiting]
    assert status["cash"]["by_currency"]["EUR"]["net_observed"] == 20


def test_drive_queues_one_budgeted_exploration_mission_without_prescribing_a_business_model(transport):
    now = time.time()
    assert "drive" not in economy.cycle(B, now=now)  # pas d'autonomie sans consentement explicite
    first = economy.cycle(B, now=now, drive_orbit=True, budget_usd=0.02)["drive"]
    assert first["status"] == "mission_queued"
    task = tasks.get(first["task_id"])
    assert task["kind"] == "orbit.mission" and task["budget_usd"] == 0.02 and task["business"] == B
    assert "cash net réellement encaissé" in task["input"]["goal"]
    assert economy.drive(B, now=now)["status"] == "mission_active"
    tasks.cancel(first["task_id"])
    assert economy.drive(B, now=now + 60)["status"] == "already_driven"
    assert economy.drive(B, now=now + 2 * DAY)["status"] == "mission_queued"
    assert transport.calls == []
