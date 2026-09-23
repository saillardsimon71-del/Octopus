"""Tâches Podalux exécutables par le worker OCTOPUS (python -m octopus worker).

Parité avec la CLI : chaque tâche appelle le code existant (cycle, agent, mission). Le découpage du
cycle en étapes (write_job, tts, render...) viendra avec le module business short_video.
"""
from __future__ import annotations

import re
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
    output = {
        "rapport": result.get("rapport"),
        "subtasks": len(result.get("plan") or []),
        "synthesis_status": result.get("synthesis_status", "validated"),
    }
    if output["synthesis_status"] == "degraded":
        output["synthesis_error"] = result.get("synthesis_error")
        output["results"] = result.get("results") or []
    return output


def _mission_tool_trace(results, *, tools=("search", "browse"), result_chars=1200):
    """Trace diagnostique compacte, sans modifier le comportement de la mission."""
    wanted = set(tools)
    trace = []
    for subtask in results or []:
        role = subtask.get("role")
        for step in subtask.get("steps") or []:
            if step.get("tool") not in wanted:
                continue
            trace.append({
                "role": role,
                "step": step.get("step"),
                "tool": step.get("tool"),
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
                "result": str(step.get("result") or "")[:result_chars],
            })
    return trace


def _mission_trace_summary(results) -> dict:
    """Mesures descriptives pour comparer des missions sans changer leur exécution."""
    by_role = {}
    totals = {"search": 0, "browse": 0}
    max_steps_roles = []
    search_queries = []
    search_query_keys = []
    search_url_roles = {}
    browse_urls = []
    browsed_from_search = []
    cross_role_browsed_from_search = []
    from .runtime import query_key
    for subtask in results or []:
        role = str(subtask.get("role") or "")
        counts = by_role.setdefault(role, {"search": 0, "browse": 0})
        for step in subtask.get("steps") or []:
            tool = step.get("tool")
            if tool in totals:
                totals[tool] += 1
                counts[tool] += 1
            args = step.get("args") if isinstance(step.get("args"), dict) else {}
            if tool == "search":
                query = " ".join(str(args.get("query") or "").lower().split())
                if query:
                    search_queries.append(query)
                    search_query_keys.append(query_key(query))
                for url in re.findall(r"https?://[^\s\]\[<>()\"']+", str(step.get("result") or "")):
                    search_url_roles.setdefault(url.rstrip(".,;:"), set()).add(role)
            elif tool == "browse":
                url = str(args.get("url") or "").strip()
                if url:
                    browse_urls.append(url)
                    source_roles = search_url_roles.get(url)
                    if source_roles:
                        browsed_from_search.append(url)
                        if any(source_role != role for source_role in source_roles):
                            cross_role_browsed_from_search.append(url)
        if subtask.get("final") == "(max steps atteint)":
            max_steps_roles.append(role)
    searches = totals["search"]
    return {
        "totals": totals,
        "browse_search_ratio": (totals["browse"] / searches) if searches else None,
        "by_role": by_role,
        "max_steps_roles": max_steps_roles,
        "search_queries": search_queries,
        "repeated_search_queries": len(search_query_keys) - len(set(search_query_keys)),
        "browse_urls": browse_urls,
        "browsed_from_search": browsed_from_search,
        "cross_role_browsed_from_search": cross_role_browsed_from_search,
    }


@handler("orbit.mission", resource="llm")
def orbit_mission(ctx):
    """Mission ORBIT pour n'importe quel business, rattachable à un objectif, une hypothèse ou une expérience.

    Entrée : {"goal": "...", "objective_id"?, "hypothesis_id"?, "experiment_id"?, "max_steps"?,
              "allowed_tools"?: ["search", ...], "profile"?: "flash_fallback"}.
    Le rapport est une inférence du modèle : il n'est jamais écrit comme résultat mesuré d'une expérience.
    """
    from octopus import strategy

    from .runtime import run_mission
    goal = str(ctx.input["goal"])
    refs = {key: ctx.input.get(key) for key in ("objective_id", "hypothesis_id", "experiment_id")}
    context = None
    if any(value is not None for value in refs.values()):
        context = strategy.mission_context(ctx.business, **refs)
        for kind in ("objective", "hypothesis", "experiment"):
            if context[f"{kind}_id"] is not None:
                strategy.link(ctx.business, kind, context[f"{kind}_id"], "task", ctx.id, "executed_by")
        goal = f"{context['brief']}\n\n{goal}"
    allowed_tools = ctx.input.get("allowed_tools")
    result = _run(ctx, lambda: run_mission(goal, max_steps_per_agent=int(ctx.input.get("max_steps", 8)),
                                           business=ctx.business,
                                           allowed_tools=set(allowed_tools) if allowed_tools is not None else None,
                                           profile=ctx.input.get("profile")))
    synthesis_status = result.get("synthesis_status", "validated")
    output = {
        "business": ctx.business,
        "rapport": result.get("rapport"),
        "rapport_nature": "inferred" if synthesis_status == "validated" else "unavailable",
        "subtasks": len(result.get("plan") or []),
        "synthesis_status": synthesis_status,
    }
    if synthesis_status == "degraded":
        # Le handler ne doit pas jeter les preuves brutes que runtime a preservees.
        output["synthesis_error"] = result.get("synthesis_error")
        output["results"] = result.get("results") or []

    if ctx.input.get("trace_tools"):
        output["plan_trace"] = [
            {"role": str(item.get("role") or ""), "task": str(item.get("task") or "")}
            for item in (result.get("plan") or [])
            if isinstance(item, dict)
        ]
        output["tool_trace"] = _mission_tool_trace(result.get("results") or [])
        output["trace_summary"] = _mission_trace_summary(result.get("results") or [])
        output["subtask_trace"] = [
            {
                "role": str(item.get("role") or ""),
                "task": str(item.get("task") or ""),
                "final": str(item.get("final") or ""),
                "steps": len(item.get("steps") or []),
            }
            for item in (result.get("results") or [])
            if isinstance(item, dict)
        ]

    if context:
        output["strategy"] = {k: context[k] for k in ("objective_id", "hypothesis_id", "experiment_id")}

    if context and output["rapport"] and synthesis_status == "validated":
        target = next(kind for kind in ("experiment", "hypothesis", "objective") if context[f"{kind}_id"] is not None)

        def record():
            evidence_id = strategy.create(
                "evidence", ctx.business, f"Rapport de mission ORBIT (tâche #{ctx.id})", created_by="orbit",
                origin_task_id=ctx.id, nature="inferred", source_type="task_output",
                source_ref=f"task#{ctx.id}", observation=str(output["rapport"]), confidence="low")
            strategy.link(ctx.business, "evidence", evidence_id, target, context[f"{target}_id"], "informs")
            return evidence_id

        output["strategy"]["evidence_id"] = ctx.memo("strategy_evidence", record)  # une seule preuve par tâche
        ctx.emit("strategy.mission.done", output["strategy"])
    elif context and synthesis_status == "degraded":
        ctx.emit("strategy.mission.degraded", output["strategy"])
    return output
