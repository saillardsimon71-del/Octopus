"""Persistance stratégique (migration v5) : CRUD, cycles de vie, isolation business, liens, événements."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from octopus import journal, strategy, tasks
from octopus.strategy import StrategyError


def _chain(business: str = "podalux"):
    objective = strategy.create("objective", business, "Premiers clients", created_by="human",
                                statement="Obtenir 3 clients payants", success_criteria="3 paiements confirmés")
    hypothesis = strategy.create("hypothesis", business, "Les freelances veulent des relances", created_by="orbit",
                                 parent_id=objective, statement="Un modèle de relance se vend 9 EUR",
                                 stop_criterion="0 vente après 200 vues")
    experiment = strategy.create("experiment", business, "Short relance", created_by="orbit", parent_id=hypothesis,
                                 action="Publier 3 shorts avec CTA", budget_limit=2.0, budget_currency="usd")
    return objective, hypothesis, experiment


# --- migration ---------------------------------------------------------------------------

def test_old_database_migrates_to_the_current_schema_without_losing_data(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for target, script in journal._MIGRATIONS[:4]:
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version={target}")
    conn.execute("INSERT INTO tasks (business, kind, created_at, updated_at) VALUES ('podalux', 'k', 1, 1)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("OCTOPUS_DB", str(path))

    conn = journal.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == journal.SCHEMA_VERSION
        assert conn.execute("SELECT business FROM tasks").fetchone()[0] == "podalux"
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"strategy_objectives", "strategy_hypotheses", "strategy_experiments", "strategy_decisions",
            "strategy_reviews", "strategy_evidence", "strategy_links", "resources"} <= names


def test_partial_v9_migration_resumes_without_duplicate_columns(tmp_path, monkeypatch):
    path = tmp_path / "partial-v9.db"
    conn = sqlite3.connect(path)

    for target, script in journal._MIGRATIONS[:8]:
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version={target}")

    conn.execute("ALTER TABLE llm_calls ADD COLUMN requested_model TEXT")
    conn.commit()
    conn.close()

    monkeypatch.setenv("OCTOPUS_DB", str(path))

    conn = journal.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == journal.SCHEMA_VERSION
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(llm_calls)")
        }
    finally:
        conn.close()

    assert {
        "requested_model",
        "resolved_model",
        "resolved_provider",
        "request_id",
        "provider_cost_usd",
    } <= columns

    # A second connection must be a no-op, not another migration attempt.
    conn = journal.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == journal.SCHEMA_VERSION
    finally:
        conn.close()


def test_no_metric_columns_are_stored_as_strategy_facts():
    forbidden = ("revenue", "revenu", "margin", "marge", "profit", "views", "likes", "conversion", "ctr",
                 "followers", "reach", "engagement", "cost_usd")
    conn = journal.connect()
    try:
        for spec in strategy.KINDS.values():
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({spec['table']})")]
            assert not [c for c in columns if any(word in c for word in forbidden)], spec["table"]
    finally:
        conn.close()


# --- CRUD et relations -------------------------------------------------------------------

def test_objective_hypothesis_experiment_chain():
    objective, hypothesis, experiment = _chain()
    assert strategy.get("objective", objective, "podalux")["status"] == "draft"
    assert strategy.get("hypothesis", hypothesis, "podalux")["objective_id"] == objective
    exp = strategy.get("experiment", experiment, "podalux")
    assert exp["hypothesis_id"] == hypothesis and exp["status"] == "planned" and exp["budget_limit"] == 2.0 and exp["budget_currency"] == "USD"
    assert [h["id"] for h in strategy.list_items("hypothesis", "podalux", parent_id=objective)] == [hypothesis]
    assert [e["id"] for e in strategy.list_items("experiment", "podalux", status="planned")] == [experiment]


def test_create_decision_and_review_and_update():
    decision = strategy.create("decision", "podalux", "Continuer la relance", created_by="orbit",
                               decision="Poursuivre 2 semaines", rationale="Signal faible mais positif")
    review = strategy.create("review", "podalux", "Revue hebdo", created_by="schedule", due_at=2_000_000_000.0)
    strategy.update("decision", decision, "podalux", summary="Continuer", alternatives="Arrêter")
    row = strategy.get("decision", decision, "podalux")
    assert row["summary"] == "Continuer" and row["alternatives"] == "Arrêter" and row["decided_by"] is None
    assert strategy.get("review", review, "podalux")["status"] == "scheduled"


@pytest.mark.parametrize("kwargs", [
    dict(kind="objective", summary="x", statement=""),
    dict(kind="objective", summary="", statement="x"),
    dict(kind="objective", summary="x", statement="x", revenue_usd=100),
    dict(kind="experiment", summary="x", action="x"),  # parent manquant
    dict(kind="decision", summary="x", decision="x", parent_id=1),
    dict(kind="unknown", summary="x"),
])
def test_invalid_creations_are_rejected(kwargs):
    kind = kwargs.pop("kind")
    summary = kwargs.pop("summary")
    with pytest.raises(StrategyError):
        strategy.create(kind, "podalux", summary, created_by="human", **kwargs)


def test_budget_limit_must_be_a_non_negative_number():
    _, hypothesis, _ = _chain()
    for bad in (-1, "10", True):
        with pytest.raises(StrategyError):
            strategy.create("experiment", "podalux", "x", created_by="human", parent_id=hypothesis,
                            action="x", budget_limit=bad, budget_currency="USD")


# --- cycles de vie ---------------------------------------------------------------------------

def test_lifecycle_transitions_are_enforced():
    objective, hypothesis, experiment = _chain()
    with pytest.raises(StrategyError):
        strategy.transition("objective", objective, "podalux", "achieved", actor="orbit")  # draft -> achieved
    strategy.transition("objective", objective, "podalux", "active", actor="human")
    strategy.transition("hypothesis", hypothesis, "podalux", "testing", actor="orbit")
    strategy.transition("experiment", experiment, "podalux", "running", actor="orbit")
    with pytest.raises(StrategyError):
        strategy.transition("experiment", experiment, "podalux", "completed", actor="orbit")  # outcome manquant
    strategy.transition("experiment", experiment, "podalux", "completed", actor="orbit", outcome="inconclusive",
                        actual_result="Aucune vente observée ; pas assez de vues")
    row = strategy.get("experiment", experiment, "podalux")
    assert (row["status"], row["outcome"]) == ("completed", "inconclusive")
    with pytest.raises(StrategyError):
        strategy.update("experiment", experiment, "podalux", action="changer après coup")
    with pytest.raises(StrategyError):
        strategy.transition("experiment", experiment, "podalux", "running", actor="orbit")


def test_closed_parent_refuses_children():
    objective, hypothesis, _ = _chain()
    strategy.transition("hypothesis", hypothesis, "podalux", "abandoned", actor="human")
    with pytest.raises(StrategyError):
        strategy.create("experiment", "podalux", "x", created_by="orbit", parent_id=hypothesis, action="x")
    strategy.transition("objective", objective, "podalux", "abandoned", actor="human")
    with pytest.raises(StrategyError):
        strategy.create("hypothesis", "podalux", "x", created_by="orbit", parent_id=objective, statement="x")


def test_only_a_human_approves_a_decision_that_commits_money():
    free = strategy.create("decision", "podalux", "Changer d'angle", created_by="orbit", decision="x")
    strategy.transition("decision", free, "podalux", "approved", actor="orbit")
    assert strategy.get("decision", free, "podalux")["decided_by"] == "orbit"
    paid = strategy.create("decision", "podalux", "Réallouer", created_by="orbit", decision="x",
                           spend_amount=50, spend_currency="eur")
    assert strategy.get("decision", paid, "podalux")["spend_currency"] == "EUR"
    with pytest.raises(StrategyError):
        strategy.transition("decision", paid, "podalux", "approved", actor="orbit")
    strategy.transition("decision", paid, "podalux", "approved", actor="human")
    row = strategy.get("decision", paid, "podalux")
    assert row["status"] == "approved" and row["decided_by"] == "human" and row["decided_at"]
    with pytest.raises(StrategyError):
        strategy.create("decision", "podalux", "x", created_by="orbit", decision="x", spend_amount=5)  # sans devise


def test_outcome_only_on_experiment_completion():
    objective, _, _ = _chain()
    with pytest.raises(StrategyError):
        strategy.transition("objective", objective, "podalux", "active", actor="human", outcome="supports")


def test_concurrent_transitions_only_one_wins():
    objective = strategy.create("objective", "podalux", "o", created_by="human", statement="o")
    barrier = threading.Barrier(4)
    results = []

    def attempt(target):
        barrier.wait()
        try:
            strategy.transition("objective", objective, "podalux", target, actor="human")
            results.append(target)
        except StrategyError:
            results.append("refused")

    threads = [threading.Thread(target=attempt, args=(t,)) for t in ("active", "abandoned", "active", "abandoned")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = strategy.get("objective", objective, "podalux")["status"]
    winners = [r for r in results if r != "refused"]
    # draft -> active puis active -> abandoned est une suite légale : chaque succès part de l'état réel.
    assert final in ("active", "abandoned") and 1 <= len(winners) <= 2 and len(results) == 4


# --- isolation business ----------------------------------------------------------------------

def test_business_isolation_on_read_parent_and_links():
    objective, _, experiment = _chain("podalux")
    other = strategy.create("objective", "veille", "Veille", created_by="human", statement="Brief hebdo")
    assert strategy.get("objective", objective, "veille") is None
    assert [o["id"] for o in strategy.list_items("objective", "veille")] == [other]
    with pytest.raises(StrategyError):
        strategy.create("hypothesis", "veille", "x", created_by="orbit", parent_id=objective, statement="x")
    with pytest.raises(StrategyError):
        strategy.transition("objective", objective, "veille", "active", actor="human")
    with pytest.raises(StrategyError):
        strategy.link("podalux", "experiment", experiment, "objective", other, "informs")


@pytest.mark.parametrize("business", ["", "   ", "all", None])
def test_global_view_and_empty_business_own_nothing(business):
    with pytest.raises(StrategyError):
        strategy.create("objective", business, "x", created_by="human", statement="x")


def test_links_to_strategy_objects_and_tasks_are_idempotent_and_scoped():
    objective, _, experiment = _chain()
    review = strategy.create("review", "podalux", "Revue", created_by="schedule")
    first = strategy.link("podalux", "review", review, "objective", objective, "reviews")
    assert strategy.link("podalux", "review", review, "objective", objective, "reviews") == first
    task = tasks.enqueue("podalux", "podalux.mission", {"goal": "x"})
    strategy.link("podalux", "experiment", experiment, "task", task, "executed_by")
    foreign = tasks.enqueue("veille", "veille.brief", {})
    with pytest.raises(StrategyError):
        strategy.link("podalux", "experiment", experiment, "task", foreign, "executed_by")
    with pytest.raises(StrategyError):
        strategy.link("podalux", "experiment", experiment, "task", 99999, "executed_by")
    assert {(l["from_type"], l["to_type"]) for l in strategy.links("podalux", "experiment", experiment)} == {
        ("experiment", "task")}
    assert strategy.links("veille", "experiment", experiment) == []


def test_origin_task_must_belong_to_business():
    task = tasks.enqueue("veille", "veille.brief", {})
    with pytest.raises(StrategyError):
        strategy.create("objective", "podalux", "x", created_by="orbit", origin_task_id=task, statement="x")
    created = strategy.create("objective", "veille", "x", created_by="orbit", origin_task_id=task, statement="x")
    assert strategy.get("objective", created, "veille")["origin_task_id"] == task


# --- traçabilité ------------------------------------------------------------------------------

def test_every_write_leaves_an_event():
    objective = strategy.create("objective", "podalux", "o", created_by="human", statement="o")
    strategy.update("objective", objective, "podalux", timeframe="T4")
    strategy.transition("objective", objective, "podalux", "active", actor="human", note="go")
    types = [e["type"] for e in tasks.events() if e["type"].startswith("strategy.")]
    assert types == ["strategy.objective.created", "strategy.objective.updated", "strategy.objective.active"]
    last = [e for e in tasks.events() if e["type"] == "strategy.objective.active"][0]
    assert last["business"] == "podalux" and last["data"]["from"] == "draft" and last["data"]["actor"] == "human"


def test_failed_write_leaves_no_row_and_no_event():
    with pytest.raises(StrategyError):
        strategy.create("hypothesis", "podalux", "x", created_by="orbit", parent_id=424242, statement="x")
    assert strategy.list_items("hypothesis", "podalux") == []
    assert not [e for e in tasks.events() if e["type"].startswith("strategy.")]


# --- preuves ------------------------------------------------------------------------------------

def _evidence(**overrides):
    fields = dict(nature="observed", source_type="web", source_ref="https://exemple.fr/etude",
                  captured_at=1_800_000_000.0, observation="La page cite 40 % de factures payées en retard",
                  confidence="medium")
    fields.update(overrides)
    created_by = fields.pop("created_by", "agent:SOUT")
    return strategy.create("evidence", "podalux", "Retards de paiement", created_by=created_by, **fields)


def test_evidence_nature_is_explicit_and_validated():
    fact = _evidence()
    assert strategy.get("evidence", fact, "podalux")["nature"] == "observed"
    inference = _evidence(nature="inferred", source_type="task_output", source_ref=None, captured_at=None)
    assert strategy.get("evidence", inference, "podalux")["source_ref"] is None
    with pytest.raises(StrategyError):
        _evidence(nature="fact")
    with pytest.raises(StrategyError):
        _evidence(source_ref="")  # fait observé sans source
    with pytest.raises(StrategyError):
        _evidence(captured_at=None)
    with pytest.raises(StrategyError):
        _evidence(nature="computed", source_ref=None)  # un calcul cite ses entrées
    with pytest.raises(StrategyError):
        _evidence(value=3.0)  # une valeur sans métrique
    assert _evidence(nature="unverified", source_type="human", source_ref=None, captured_at=None, created_by="human",
                     metric="ventes", value=2, unit="commandes")
    with pytest.raises(StrategyError):
        _evidence(confidence="certaine")


def test_evidence_is_immutable_but_retractable_and_linkable():
    objective, hypothesis, experiment = _chain()
    evidence = _evidence()
    with pytest.raises(StrategyError):
        strategy.update("evidence", evidence, "podalux", observation="réécrite")
    strategy.link("podalux", "evidence", evidence, "hypothesis", hypothesis, "supports")
    decision = strategy.create("decision", "podalux", "Tester le prix", created_by="orbit", decision="x")
    strategy.link("podalux", "decision", decision, "evidence", evidence, "considers")
    assert {(l["from_type"], l["to_type"]) for l in strategy.links("podalux", "evidence", evidence)} == {
        ("evidence", "hypothesis"), ("decision", "evidence")}
    strategy.transition("evidence", evidence, "podalux", "retracted", actor="human", note="source retirée")
    assert strategy.get("evidence", evidence, "podalux")["status"] == "retracted"


# --- contexte de mission et revues --------------------------------------------------------------

def test_mission_context_resolves_chain_from_experiment():
    objective, hypothesis, experiment = _chain()
    context = strategy.mission_context("podalux", experiment_id=experiment)
    assert (context["objective_id"], context["hypothesis_id"], context["experiment_id"]) == (
        objective, hypothesis, experiment)
    assert "Obtenir 3 clients payants" in context["brief"] and "2.00 USD" in context["brief"]
    assert "limite de budget : 2.00 USD" in context["brief"]
    with pytest.raises(StrategyError):
        strategy.mission_context("veille", experiment_id=experiment)
    other = strategy.create("objective", "podalux", "autre", created_by="human", statement="autre")
    with pytest.raises(StrategyError):
        strategy.mission_context("podalux", objective_id=other, hypothesis_id=hypothesis)
    with pytest.raises(StrategyError):
        strategy.mission_context("podalux")


def test_review_snapshot_counts_only_persisted_state_and_flags_missing_data():
    objective, _, experiment = _chain()
    strategy.transition("objective", objective, "podalux", "active", actor="human")
    strategy.update("experiment", experiment, "podalux", deadline_at=1_000.0)
    strategy.create("decision", "podalux", "d", created_by="orbit", decision="d")
    strategy.create("objective", "veille", "v", created_by="human", statement="v")
    snap = strategy.review_snapshot("podalux")
    assert snap["objectives"] == {"active": 1} and snap["overdue_experiments"] == [experiment]
    assert len(snap["pending_decisions"]) == 1
    assert "non connectées, non évaluées" in snap["text"] and "revenus, coûts et marges" in snap["text"]
    assert not any(ch.isdigit() for ch in snap["text"].split("Données externes")[1])


def test_scheduled_review_runs_through_the_existing_queue(monkeypatch):
    from octopus import builtin_handlers, worker  # noqa: F401  (enregistre strategy.review)
    objective, _, _ = _chain()
    strategy.transition("objective", objective, "podalux", "active", actor="human")
    review, task = strategy.schedule_review("podalux", due_in_s=3600, created_by="human")
    assert tasks.get(task)["not_before"] > tasks.get(task)["created_at"] + 3000
    # La tâche différée n'est pas prise avant l'échéance.
    assert worker.run_one("w", kinds=["strategy.review"], log=lambda s: None) is None
    with tasks._tx() as conn:
        conn.execute("UPDATE tasks SET not_before=0 WHERE id=?", (task,))
    done = worker.run_one("w", kinds=["strategy.review"], log=lambda s: None)
    assert done["id"] == task and done["status"] == "done"
    row = strategy.get("review", review, "podalux")
    assert row["status"] == "done" and "objectifs : active 1" in row["evidence_summary"]
    assert ("review", "objective") in {(l["from_type"], l["to_type"]) for l in strategy.links("podalux", "review", review)}
    assert strategy.complete_review("podalux", review, actor="schedule")["already_closed"] is True


def test_recurring_review_creates_a_review_per_occurrence():
    from octopus import builtin_handlers, worker  # noqa: F401
    tasks.schedule("veille", "strategy.review", 7 * 86400)
    created = tasks.materialize_due()
    assert len(created) == 1
    done = worker.run_one("w", kinds=["strategy.review"], log=lambda s: None)
    assert done["status"] == "done"
    reviews = strategy.list_items("review", "veille")
    assert len(reviews) == 1 and reviews[0]["status"] == "done" and reviews[0]["origin_task_id"] == created[0]
    assert strategy.list_items("review", "podalux") == []
