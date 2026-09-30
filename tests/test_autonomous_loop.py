"""Phase G — boucle autonome supervisée : critère F (obligatoire), E, G et D dans la même chaîne.

Scénarios hors ligne, sans réseau ni LLM réel : la mission est remplacée par un faux runtime et les
outils par des traces d'acquisition fixées. Aucune commande CLI n'est exécutée entre les transitions
une fois le runtime démarré : c'est exactement ce que le contrat de la phase G exige.

    objectif persistant -> superviseur -> travail -> résultat/preuve -> évaluation
      -> décision (suite / clôture / attente humaine)
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from agents import runtime, task_handlers  # noqa: F401  (enregistre les handlers existants)
from octopus import builtin_handlers, journal, strategy, supervisor, tasks, worker  # noqa: F401

BUSINESS = "octopus"
QUIET = dict(log=lambda s: None)
DEADLINE = 15.0


@pytest.fixture
def handlers(monkeypatch):
    saved = dict(worker.HANDLERS)
    worker.load_handlers(["octopus.builtin_handlers"])
    yield worker.HANDLERS
    worker.HANDLERS.clear()
    worker.HANDLERS.update(saved)


def _acquisition(url: str = "https://example.com/demande-ouverte") -> dict:
    """Trace d'une acquisition publique textuelle exploitable (aucun réseau)."""
    return {"tool": "browse", "args": {"url": url},
            "result": json.dumps({"url": url, "texte": "besoin explicité par l'acheteur. " * 40})}


def _fake_mission(calls: list, *, urls=(), execution_status="completed", synthesis_status="validated"):
    def run_mission(goal, **kwargs):
        calls.append({"goal": goal, **kwargs})
        return {"results": [{"steps": [_acquisition(url) for url in urls]}], "plan": [{"id": 1}],
                "execution_status": execution_status, "synthesis_status": synthesis_status,
                "business_signals": []}
    return run_mission


def _active_objective(criteria: str | None = "usable_browse_count>=1", statement="Trouver une demande ouverte"):
    objective_id = strategy.create("objective", BUSINESS, "Premier client", created_by="human",
                                   statement=statement, success_criteria=criteria)
    strategy.transition("objective", objective_id, BUSINESS, "active", actor="human")
    return objective_id


def _wait(predicate, timeout: float = DEADLINE):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _run_runtime(stop: threading.Event, max_tasks: int | None = None) -> threading.Thread:
    thread = threading.Thread(target=worker.loop,
                              kwargs={"stop": stop, "poll_s": 0.01, "max_tasks": max_tasks, **QUIET},
                              daemon=True)
    thread.start()
    return thread


# --- critère F : boucle autonome complète ------------------------------------------------------

def test_autonomous_loop_reaches_objective_without_any_manual_command(handlers, monkeypatch):
    """F.1 : objectif -> travail -> preuve -> évaluation -> objectif atteint, sans commande intermédiaire."""
    calls: list = []
    monkeypatch.setattr(runtime, "run_mission", _fake_mission(calls, urls=["https://example.com/a"]))
    objective_id = _active_objective()
    assert supervisor.bootstrap(BUSINESS, tick_every_s=0.05) is not None

    stop = threading.Event()
    thread = _run_runtime(stop, max_tasks=6)
    try:
        assert _wait(lambda: strategy.get("objective", objective_id, BUSINESS)["status"] == "achieved")
    finally:
        stop.set()
        thread.join(timeout=5)

    assert len(calls) == 1, "une seule mission pour un objectif atteint"
    assert calls[0]["business"] == BUSINESS
    (decision,) = strategy.list_items("decision", BUSINESS)
    assert decision["decision"] == "satisfied" and decision["status"] == "approved"
    (evidence,) = strategy.list_items("evidence", BUSINESS)
    assert evidence["nature"] == "computed" and evidence["metric"] == "objective_work_observed"
    assert evidence["value"] == 1
    works = supervisor.work_tasks(BUSINESS, objective_id)
    assert len(works) == 1 and works[0]["kind"] == supervisor.WORK_KIND
    assert works[0]["output"]["success"] is True and works[0]["output"]["citations"] == ["https://example.com/a"]
    # La chaîne du superviseur survit à la clôture de l'objectif : un tick suivant est déjà en file.
    assert any(task["kind"] == supervisor.TICK_KIND and task["status"] in tasks.ACTIVE
               for task in tasks.list_tasks(business=BUSINESS))


def test_autonomous_loop_creates_next_work_when_result_is_inconclusive(handlers, monkeypatch):
    """F.1 (variante) : absence de mesure concluante -> prochaine tâche créée automatiquement."""
    calls: list = []
    monkeypatch.setattr(runtime, "run_mission", _fake_mission(calls, urls=[]))
    objective_id = _active_objective()
    supervisor.bootstrap(BUSINESS, tick_every_s=0.05)

    stop = threading.Event()
    thread = _run_runtime(stop, max_tasks=4)
    try:
        assert _wait(lambda: len(supervisor.work_tasks(BUSINESS, objective_id)) >= 2)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert strategy.get("objective", objective_id, BUSINESS)["status"] == "active"
    (decision,) = [d for d in strategy.list_items("decision", BUSINESS) if d["decision"] == "retry"]
    assert "aucune mesure concluante" in decision["rationale"]
    works = supervisor.work_tasks(BUSINESS, objective_id)
    assert works[0]["status"] not in tasks.ACTIVE
    assert works[1]["status"] == "queued" and works[1]["not_before"] >= works[1]["created_at"]


def test_autonomous_loop_pauses_objective_after_exhausted_attempts(handlers, monkeypatch):
    """F.3 : budget de tentatives atteint sans mesure -> objectif suspendu, réactivation humaine."""
    calls: list = []
    monkeypatch.setattr(runtime, "run_mission", _fake_mission(calls, urls=[]))
    objective_id = _active_objective()
    assert supervisor.bootstrap(BUSINESS, tick_every_s=0.05, max_attempts=1) is not None

    stop = threading.Event()
    thread = _run_runtime(stop, max_tasks=4)
    try:
        assert _wait(lambda: strategy.get("objective", objective_id, BUSINESS)["status"] == "paused")
    finally:
        stop.set()
        thread.join(timeout=5)

    assert len(calls) == 1, "le budget de tentatives doit borner le travail"
    outcomes = [d["decision"] for d in strategy.list_items("decision", BUSINESS)]
    assert outcomes == ["exhausted"]
    assert len(supervisor.work_tasks(BUSINESS, objective_id)) == 1


def test_autonomous_loop_never_declares_success_without_a_measurement(handlers, monkeypatch):
    """Garde épistémique : sans critère mesurable, l'objectif n'est jamais déclaré atteint."""
    calls: list = []
    monkeypatch.setattr(runtime, "run_mission", _fake_mission(calls, urls=["https://example.com/a"]))
    objective_id = _active_objective(criteria="signer un premier client")  # non mesurable sans LLM
    supervisor.bootstrap(BUSINESS, tick_every_s=0.05, max_attempts=1)

    stop = threading.Event()
    thread = _run_runtime(stop, max_tasks=4)
    try:
        assert _wait(lambda: strategy.get("objective", objective_id, BUSINESS)["status"] == "paused")
    finally:
        stop.set()
        thread.join(timeout=5)

    (work,) = supervisor.work_tasks(BUSINESS, objective_id)
    assert work["output"]["success"] is None and work["output"]["measured"] is False
    assert work["output"]["criterion"] is None
    assert not strategy.list_items("evidence", BUSINESS)


def test_autonomous_loop_requests_human_boundary_then_resumes_without_repeating_work(handlers, monkeypatch):
    """F.4 + D : frontière humaine réelle, réponse, reprise sans rejouer le travail déjà persisté."""
    calls: list = []
    monkeypatch.setattr(runtime, "run_mission", _fake_mission(calls, execution_status="llm_unavailable",
                                                              synthesis_status="degraded"))
    objective_id = _active_objective()
    supervisor.bootstrap(BUSINESS, tick_every_s=0.05)

    stop = threading.Event()
    thread = _run_runtime(stop)
    try:
        assert _wait(lambda: bool(tasks.pending_human_requests(BUSINESS)))
        (request,) = tasks.pending_human_requests(BUSINESS)
        assert request["key"] == supervisor.boundary_key(objective_id)
        assert "route LLM gratuite" in request["question"]
        assert len(calls) == 1
        # Le tick suivant ne duplique jamais la demande humaine déjà ouverte.
        assert supervisor.tick(businesses=[BUSINESS])["human_boundaries"][0]["action"] == "waiting_human"
        assert len(tasks.pending_human_requests(BUSINESS)) == 1
        # Réponse humaine = autorisation : le travail reprend, sans rejouer la mission mémoïsée.
        assert tasks.answer(request["id"], "route gratuite rétablie") == request["task_id"]
        assert _wait(lambda: len(supervisor.work_tasks(BUSINESS, objective_id)) >= 2
                     and supervisor.work_tasks(BUSINESS, objective_id)[1]["status"] not in tasks.ACTIVE)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert len(calls) == 2, "une mission par tâche de travail : aucune étape persistée n'est rejouée"
    decisions = {d["decision"] for d in strategy.list_items("decision", BUSINESS)}
    assert {"human_boundary", "human_answer"} <= decisions
    assert strategy.get("objective", objective_id, BUSINESS)["status"] == "active"


def test_objective_work_initializes_shared_agent_state_before_a_mission(handlers, monkeypatch):
    """Régression réelle du point d'entrée : la mission exige l'état partagé des agents.

    Reproduit avec un domicile neuf (table `state` absente) : sans le pont existant des handlers de
    mission, le travail échouait sur `no such table: state` au lieu de produire une preuve.
    """
    from agents import db as agent_db

    def probe(goal, **kwargs):
        # Le runtime lit/écrit réellement la table `state` (arrêt demandé, verrou de run).
        assert agent_db.get_state("stop") in (None, "0")
        agent_db.clear_stop()
        return {"results": [], "execution_status": "completed", "synthesis_status": "degraded"}

    monkeypatch.setattr(runtime, "run_mission", probe)
    conn = agent_db._conn()
    try:
        conn.execute("DROP TABLE IF EXISTS state")
        conn.commit()
    finally:
        conn.close()

    objective_id = _active_objective(criteria="texte non mesurable")
    work_id = supervisor.plan_work(BUSINESS, strategy.get("objective", objective_id, BUSINESS))
    done = worker.run_one("w1", **QUIET)
    assert done["id"] == work_id and done["status"] == "done_degraded", done["error"]
    assert "no such table" not in (done["error"] or "")


def test_supervisor_bootstrap_is_idempotent(handlers):
    first = supervisor.bootstrap(BUSINESS, tick_every_s=1.0)
    assert supervisor.bootstrap(BUSINESS, tick_every_s=1.0) == first
    ticks = [task for task in tasks.list_tasks(business=BUSINESS) if task["kind"] == supervisor.TICK_KIND]
    assert len(ticks) == 1


@pytest.mark.parametrize("kwargs", [
    {"tick_every_s": 0}, {"tick_every_s": -1}, {"max_attempts": 0}, {"retry_delay_s": -5},
])
def test_supervisor_bootstrap_rejects_invalid_bounds(kwargs):
    with pytest.raises(supervisor.SupervisorError):
        supervisor.bootstrap(BUSINESS, **kwargs)


def test_criterion_parsing_only_accepts_measurable_metrics():
    assert supervisor.parse_criterion("usable_browse_count>=3") == {"metric": "usable_browse_count", "gte": 3}
    assert supervisor.parse_criterion("signer un client") is None
    assert supervisor.parse_criterion("revenue_eur>=1000") is None
    assert supervisor.parse_criterion(None) is None
    with pytest.raises(supervisor.SupervisorError):
        supervisor.criterion_for({"success_criteria": None}, {"metric": "cash", "gte": 1})


# --- critère E : durabilité du worker ----------------------------------------------------------

def test_single_worker_start_claims_objective_work_while_idle(handlers, monkeypatch):
    """E : un seul démarrage, file vide, worker sain au repos, puis exécution automatique."""
    calls: list = []
    monkeypatch.setattr(runtime, "run_mission", _fake_mission(calls, urls=["https://example.com/a"]))
    assert tasks.list_tasks() == []
    stop = threading.Event()
    thread = _run_runtime(stop, max_tasks=3)
    try:
        time.sleep(0.2)
        assert thread.is_alive(), "le worker doit rester vivant pendant l'inactivité"
        assert tasks.list_tasks() == [], "rien à faire avant l'objectif"
        # L'objectif apparaît pendant que le worker tourne : aucune commande supplémentaire.
        objective_id = _active_objective()
        supervisor.bootstrap(BUSINESS, tick_every_s=0.05)
        assert _wait(lambda: strategy.get("objective", objective_id, BUSINESS)["status"] == "achieved")
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive(), "le worker rendu au repos après son quota"


# --- critère G : redémarrage et reprise --------------------------------------------------------

def test_runtime_restart_recovers_pending_work_without_repeating_completed_steps(handlers, monkeypatch):
    """G : arrêt après un travail commencé -> redémarrage -> étapes faites non rejouées."""
    missions: list = []
    steps: list = []
    monkeypatch.setattr(runtime, "run_mission", _fake_mission(missions, urls=["https://example.com/a"]))
    original = supervisor.execute_objective_work
    attempts: list = []

    def crashing(ctx):
        # Étape coûteuse mémoïsée : elle ne doit être payée qu'une seule fois.
        ctx.memo("acquired_source", lambda: steps.append(1) or "source")
        if not attempts:
            attempts.append(1)
            original(ctx)  # la mission tourne, son résultat est mémoïsé...
            raise RuntimeError("panique simulée après une étape persistée")
        return original(ctx)  # ...la reprise ne la rejoue pas

    monkeypatch.setattr(supervisor, "execute_objective_work", crashing)
    objective_id = _active_objective()
    work_id = supervisor.plan_work(BUSINESS, strategy.get("objective", objective_id, BUSINESS))

    first = worker.run_one("w1", **QUIET)
    assert first["id"] == work_id and first["status"] == "queued", "panique -> nouvelle tentative différée"
    assert steps == [1] and len(missions) == 1

    # Le délai de nouvelle tentative s'écoule, puis un worker neuf redémarre et reprend la tâche.
    conn = journal.connect()
    try:
        conn.execute("UPDATE tasks SET not_before=0 WHERE id=?", (work_id,))
        conn.commit()
    finally:
        conn.close()
    done = worker.run_one("w2", **QUIET)
    assert done["id"] == work_id and done["status"] == "done"
    assert steps == [1], "l'étape mémoïsée n'est pas rejouée après redémarrage"
    assert len(missions) == 1, "la mission déjà persistée n'est pas rejouée après redémarrage"
    assert done["output"]["success"] is True


def test_killed_worker_leaves_no_running_task_or_run_behind(handlers):
    """C/G : après la mort d'un worker, ni la tâche ni ses runs ne restent `running` indéfiniment."""
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1})
    now = time.time()
    conn = journal.connect()
    try:  # état exact laissé par un SIGKILL : bail périmé, runs jamais clos
        conn.execute("UPDATE tasks SET status='running', attempts=1, max_attempts=1, lease_owner='dead', "
                     "lease_until=? WHERE id=?", (now - 30, task_id))
        conn.execute("INSERT INTO runs (root_id, business, kind, label, status, started_at, pid) "
                     "VALUES (1, ?, 'task:supervisor.objective_work', 'tâche', 'running', ?, 999999)",
                     (BUSINESS, now - 30))
        conn.execute("UPDATE runs SET root_id=id WHERE id=1")
        conn.execute("INSERT INTO runs (parent_id, root_id, business, kind, label, status, started_at, pid) "
                     "VALUES (1, 1, ?, 'mission', 'sous-run', 'running', ?, 999999)", (BUSINESS, now - 29))
        conn.execute("UPDATE tasks SET run_id=1 WHERE id=?", (task_id,))
        conn.commit()
    finally:
        conn.close()

    reaped = tasks.reap()
    assert reaped["failed"] == [task_id]
    assert sorted(reaped["abandoned_runs"]) == [1, 2]
    assert tasks.get(task_id)["status"] == "failed"
    assert [row["status"] for row in journal.query("SELECT status FROM runs ORDER BY id")] == ["abandoned"] * 2
    assert "run.abandoned" in [event["type"] for event in tasks.events(task_id=task_id)]
    assert tasks.reap()["abandoned_runs"] == [], "la clôture des runs orphelins est idempotente"
