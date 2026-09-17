"""Tâches Podalux exécutables par le worker OCTOPUS (python -m octopus worker).

Parité avec la CLI : chaque tâche appelle le code existant (cycle, agent, mission). Le découpage du
cycle en étapes (write_job, tts, render...) viendra avec le module business short_video.
"""
from __future__ import annotations

import threading

from octopus.worker import TaskCancelled, handler

from . import db

BUSINESS = "podalux"


def _bridge_cancel(ctx) -> threading.Event:
    """Annulation de la tâche -> arrêt Podalux (agents/cancel.py), vérifié chaque seconde."""
    done = threading.Event()

    def watch():
        while not done.wait(1.0):
            if ctx.cancelled():
                db.request_stop()
                return

    threading.Thread(target=watch, daemon=True).start()
    return done


def _run(ctx, fn):
    db.init_db()
    done = _bridge_cancel(ctx)
    try:
        result = fn()
    finally:
        done.set()
    if ctx.cancelled():
        raise TaskCancelled("arrêt demandé")
    return result


@handler("podalux.video_cycle", resource="cpu_heavy", max_attempts=3, retry_delay_s=120)
def video_cycle(ctx):
    """Cycle complet. Relancé plus tard si un cycle lancé ailleurs (GUI, CLI) tient le verrou."""
    from .cycle import run_cycle
    offer_id = ctx.input.get("offer_id")
    result = _run(ctx, lambda: run_cycle(offer_id=offer_id, max_iterations=int(ctx.input.get("max_iterations", 3))))
    ledger, orbit = result.get("ledger") or {}, result.get("orbit") or {}
    return {"offer_id": result.get("offer_id"), "score": ledger.get("score"), "go": ledger.get("go"),
            "decision": orbit.get("decision"), "iterations": len(result.get("iterations") or []),
            "blocking": ledger.get("blocking", [])}


@handler("podalux.agent_message", resource="llm")
def agent_message(ctx):
    from .runtime import run_agent
    role, text = str(ctx.input.get("role", "ORBIT")).upper(), str(ctx.input["text"])
    result = _run(ctx, lambda: run_agent(role, text, max_steps=int(ctx.input.get("max_steps", 8)), conversational=True))
    return {"role": role, "final": result.get("final"), "steps": len(result.get("steps") or [])}


@handler("podalux.mission", resource="llm")
def mission(ctx):
    from .runtime import run_mission
    result = _run(ctx, lambda: run_mission(str(ctx.input["goal"]), max_steps_per_agent=int(ctx.input.get("max_steps", 8))))
    return {"rapport": result.get("rapport"), "subtasks": len(result.get("plan") or [])}
