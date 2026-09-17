"""Worker OCTOPUS : exécute les tâches de la file, une à la fois, avec bail, budget et reprise.

Un handler est une fonction `fn(ctx) -> sortie JSON`, enregistrée pour un type de tâche :

    @handler("podalux.video_cycle", resource="cpu_heavy", budget_usd=1.0)
    def video_cycle(ctx):
        ...

- Chaque tâche tourne dans un run du journal (budget par tâche, coûts rattachés).
- `ctx.ask_human(key, question)` : renvoie la réponse si elle existe, sinon met la tâche en attente ;
  elle sera relancée depuis le début après la réponse (les handlers doivent être rejouables :
  `ctx.memo(clé, fonction)` conserve le résultat des étapes coûteuses).
- `ctx.cancelled()` : annulation demandée (coopérative). `ctx.enqueue(...)` : tâche suivante.
"""
from __future__ import annotations

import importlib
import os
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from . import journal, tasks


@dataclass
class Handler:
    kind: str
    fn: Callable
    resource: str | None = None
    budget_usd: float | None = None
    max_attempts: int = 1
    retry_delay_s: float = 60


HANDLERS: dict[str, Handler] = {}


def handler(kind: str, *, resource: str | None = None, budget_usd: float | None = None, max_attempts: int = 1,
            retry_delay_s: float = 60):
    def decorator(fn):
        HANDLERS[kind] = Handler(kind, fn, resource, budget_usd, max_attempts, retry_delay_s)
        return fn
    return decorator


def load_handlers(modules: list[str] | None = None) -> dict[str, Handler]:
    names = modules if modules is not None else [
        m.strip() for m in os.environ.get("OCTOPUS_HANDLERS", "octopus.builtin_handlers,agents.task_handlers,businesses.veille.handlers").split(",")
        if m.strip()]
    for name in names:
        importlib.import_module(name)
    return HANDLERS


def enqueue(business: str, kind: str, input: dict | None = None, **kwargs) -> int:
    """Ajoute une tâche avec la ressource, le budget et les tentatives déclarés par son handler."""
    spec = HANDLERS.get(kind)
    if spec:
        kwargs.setdefault("resource", spec.resource)
        kwargs.setdefault("budget_usd", spec.budget_usd)
        kwargs.setdefault("max_attempts", spec.max_attempts)
    return tasks.enqueue(business, kind, input, **kwargs)


class WaitingHuman(Exception):
    run_status = "waiting_human"

    def __init__(self, request_id: int):
        super().__init__(f"en attente de la demande humaine #{request_id}")
        self.request_id = request_id


class TaskCancelled(Exception):
    run_status = "cancelled"


@dataclass
class TaskContext:
    task: dict
    owner: str
    lease_s: float
    _cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def input(self) -> dict:
        return self.task.get("input") or {}

    @property
    def id(self) -> int:
        return self.task["id"]

    @property
    def business(self) -> str:
        return self.task["business"]

    def cancelled(self) -> bool:
        return self._cancel.is_set() or tasks.cancel_requested(self.id)

    def check_cancel(self) -> None:
        if self.cancelled():
            raise TaskCancelled("annulation demandée")

    def memo(self, key: str, compute: Callable):
        """Résultat d'une étape conservé en base : une tâche rejouée (après une réponse humaine, une
        nouvelle tentative) ne refait pas les étapes déjà faites, ni leurs appels payants."""
        value = tasks.step_value(self.id, key, None)
        if value is None:
            value = compute()
            tasks.save_step(self.id, key, value)
        return value

    def ask_human(self, key: str, question: str, *, context: dict | None = None, expires_s: float | None = None) -> str:
        known = tasks.answer_for(self.id, key)
        if known is not None:
            return known
        raise WaitingHuman(tasks.request_human(self.id, self.owner, key, question, context=context,
                                               expires_s=expires_s))

    def enqueue(self, kind: str, input: dict | None = None, **kwargs) -> int:
        kwargs.setdefault("parent_id", self.id)
        return enqueue(self.business, kind, input, **kwargs)

    def emit(self, type_: str, data: dict | None = None) -> None:
        tasks.emit(self.business, self.id, type_, data)


def default_owner() -> str:
    return f"{socket.gethostname()}-pid{os.getpid()}"


def _heartbeat_loop(ctx: TaskContext, stop: threading.Event) -> None:
    interval = max(1.0, ctx.lease_s / 3)
    while not stop.wait(interval):
        try:
            if tasks.heartbeat(ctx.id, ctx.owner, ctx.lease_s):
                ctx._cancel.set()
        except tasks.LeaseLost:
            ctx._cancel.set()
            return
        except Exception:  # base occupée : nouvel essai au prochain battement
            continue


def run_one(owner: str | None = None, *, lease_s: float = 60, kinds: list[str] | None = None,
            log: Callable[[str], None] = print) -> dict | None:
    """Maintenance, puis exécution d'une tâche prête. Renvoie la tâche traitée (état final) ou None."""
    owner = owner or default_owner()
    tasks.reap()
    tasks.materialize_due(resources={k: h.resource for k, h in HANDLERS.items() if h.resource})
    task = tasks.claim(owner, lease_s=lease_s, kinds=kinds or list(HANDLERS) or None)
    if task is None:
        return None
    spec = HANDLERS.get(task["kind"])
    if spec is None:
        tasks.fail(task["id"], owner, f"aucun handler pour {task['kind']}", retry_delay_s=0)
        return tasks.get(task["id"])
    ctx = TaskContext(task, owner, lease_s)
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat_loop, args=(ctx, stop), daemon=True)
    beat.start()
    log(f"[worker] #{task['id']} {task['kind']} (tentative {task['attempts']}/{task['max_attempts']})")
    started = time.time()
    try:
        with journal.run(task["business"], f"task:{task['kind']}", label=f"tâche #{task['id']}",
                         budget_usd=task.get("budget_usd")) as run:
            if run is not None:
                tasks.set_run(task["id"], run.id)
            output = spec.fn(ctx)
        tasks.complete(task["id"], owner, output)
    except WaitingHuman as waiting:
        log(f"[worker] #{task['id']} {waiting}")
    except TaskCancelled as exc:
        _safe(lambda: tasks.mark_cancelled(task["id"], owner, str(exc)))
    except tasks.LeaseLost as exc:
        log(f"[worker] #{task['id']} abandonnée : {exc}")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}"
        status = _safe(lambda: tasks.fail(task["id"], owner, detail, retry_delay_s=spec.retry_delay_s))
        log(f"[worker] #{task['id']} échec ({status}) : {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        stop.set()
        beat.join(timeout=5)
    result = tasks.get(task["id"])
    log(f"[worker] #{task['id']} -> {result['status']} en {time.time() - started:.1f} s")
    return result


def _safe(fn):
    try:
        return fn()
    except tasks.LeaseLost:
        return "lease_lost"


def loop(owner: str | None = None, *, poll_s: float = 2.0, lease_s: float = 60, stop: threading.Event | None = None,
         max_tasks: int | None = None, log: Callable[[str], None] = print) -> int:
    """Boucle du worker. S'arrête sur `stop`, après `max_tasks` tâches, ou Ctrl+C."""
    owner = owner or default_owner()
    stop = stop or threading.Event()
    done = 0
    log(f"[worker] {owner} démarré ; handlers : {', '.join(sorted(HANDLERS)) or '(aucun)'}")
    try:
        while not stop.is_set():
            if run_one(owner, lease_s=lease_s, log=log) is None:
                stop.wait(poll_s)
                continue
            done += 1
            if max_tasks is not None and done >= max_tasks:
                break
    except KeyboardInterrupt:
        log("[worker] arrêt demandé (Ctrl+C)")
    return done
