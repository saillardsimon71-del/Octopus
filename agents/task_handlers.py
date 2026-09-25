"""Tâches Podalux exécutables par le worker OCTOPUS (python -m octopus worker).

Parité avec la CLI : chaque tâche appelle le code existant (cycle, agent, mission). Le découpage du
cycle en étapes (write_job, tts, render...) viendra avec le module business short_video.
"""
from __future__ import annotations

import json
import re
import threading

from octopus import journal
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
            item = {
                "role": role,
                "step": step.get("step"),
                "tool": step.get("tool"),
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
                "result": str(step.get("result") or "")[:result_chars],
            }
            if isinstance(step.get("result_urls"), list):
                item["result_urls"] = list(step["result_urls"])
            if isinstance(step.get("browse_meta"), dict):
                item["browse_meta"] = dict(step["browse_meta"])
            if step.get("lockstep_forced"):
                item["lockstep_forced"] = True
            if isinstance(step.get("lockstep_selection"), dict):
                item["lockstep_selection"] = dict(step["lockstep_selection"])
            trace.append(item)
    return trace


def _trace_result_text(value) -> str:
    """Texte brut d'un résultat de trace, y compris quand runtime l'a JSON-encodé.

    Les résultats de search sont stockés via json.dumps(str), donc leurs sauts de ligne
    deviennent littéralement "\\n". Les décoder avant l'extraction d'URL évite de mesurer
    des pseudo-URL du type "https://site.tld\\n...".
    """
    text = str(value or "")
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text

    def flatten(item):
        if isinstance(item, str):
            return [item]
        if isinstance(item, dict):
            out = []
            for child in item.values():
                out.extend(flatten(child))
            return out
        if isinstance(item, (list, tuple)):
            out = []
            for child in item:
                out.extend(flatten(child))
            return out
        return []

    parts = flatten(decoded)
    return "\n".join(parts) if parts else text


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
    lockstep_forced_browses = 0
    lockstep_selected_ranks = []
    lockstep_selector_counts = {}
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
                explicit_urls = step.get("result_urls")
                if isinstance(explicit_urls, list):
                    urls = [str(url).strip() for url in explicit_urls if str(url).strip()]
                else:
                    result_text = _trace_result_text(step.get("result"))
                    urls = [
                        url.rstrip(".,;:")
                        for url in re.findall(r"https?://[^\s\]\[<>()\"']+", result_text)
                    ]
                for url in urls:
                    search_url_roles.setdefault(url, set()).add(role)
            elif tool == "browse":
                url = str(args.get("url") or "").strip()
                if step.get("lockstep_forced"):
                    lockstep_forced_browses += 1
                    selection = step.get("lockstep_selection")
                    if isinstance(selection, dict):
                        rank = selection.get("rank")
                        if isinstance(rank, int):
                            lockstep_selected_ranks.append(rank)
                        selector = str(selection.get("selector") or "")
                        if selector:
                            lockstep_selector_counts[selector] = lockstep_selector_counts.get(selector, 0) + 1
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
    summary = {
        "totals": totals,
        "browse_search_ratio": (totals["browse"] / searches) if searches else None,
        "by_role": by_role,
        "max_steps_roles": max_steps_roles,
        "search_queries": search_queries,
        "repeated_search_queries": len(search_query_keys) - len(set(search_query_keys)),
        "browse_urls": browse_urls,
        "browsed_from_search": browsed_from_search,
        "cross_role_browsed_from_search": cross_role_browsed_from_search,
        "lockstep_forced_browses": lockstep_forced_browses,
    }
    if lockstep_selected_ranks:
        summary["lockstep_selected_ranks"] = lockstep_selected_ranks
    if lockstep_selector_counts:
        summary["lockstep_selector_counts"] = lockstep_selector_counts
    return summary


_BROWSE_BLOCK_MARKERS = (
    "performing security verification",
    "verify you are not a bot",
    "security service to protect against malicious bots",
    "just a moment",
    "verification successful. waiting",
    "access denied",
    "403 error",
    "request blocked",
    "captcha",
    "chrome-error://",
)


def _usable_browse_urls(results) -> list[str]:
    """Proxy technique conservateur : acquisitions publiques textuelles réellement exploitables."""
    from . import browser
    usable = []
    for subtask in results or []:
        for step in subtask.get("steps") or []:
            if step.get("tool") != "browse":
                continue

            data = step.get("result_data")
            page = data.get("page") if isinstance(data, dict) and isinstance(data.get("page"), dict) else None
            if page is not None:
                url = str(page.get("final_url") or (step.get("args") or {}).get("url") or "").strip()
                is_usable = browser.is_public_text_acquisition(page)
            else:
                meta = step.get("browse_meta")
                if isinstance(meta, dict):
                    url = str(meta.get("url") or (step.get("args") or {}).get("url") or "").strip()
                    is_usable = browser.is_public_text_acquisition_meta(meta)
                else:
                    # Compatibilité avec les anciennes traces qui ne contiennent pas encore browse_meta.
                    raw = str(step.get("result") or "").strip()
                    if not raw or raw.lower().startswith("erreur :"):
                        continue
                    try:
                        payload = json.loads(raw)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    url = str(payload.get("url") or (step.get("args") or {}).get("url") or "").strip()
                    text = str(payload.get("texte") or "").strip()
                    lowered = f"{url}\n{text}".lower()
                    is_usable = (
                        len(text) >= browser.PUBLIC_MIN_TEXT_CHARS
                        and not text.lstrip("\ufeff\x00\t\r\n ").startswith("%PDF-")
                        and not any(marker in lowered for marker in browser.PUBLIC_BLOCK_MARKERS)
                    )

            if not url.startswith(("http://", "https://")) or not is_usable:
                continue
            if url not in usable:
                usable.append(url)
    return usable


def _mission_objective_result(results, criterion) -> dict | None:
    """Évalue un critère post-run sans influencer le comportement des agents."""
    if criterion in (None, {}):
        return None
    if not isinstance(criterion, dict):
        raise ValueError("success_criterion doit être un objet")
    metric = str(criterion.get("metric") or "")
    if metric != "usable_browse_count":
        raise ValueError(f"success_criterion.metric non supporté : {metric or '(vide)'}")
    try:
        target = int(criterion["gte"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("success_criterion.gte doit être un entier positif") from exc
    if target <= 0:
        raise ValueError("success_criterion.gte doit être un entier positif")
    urls = _usable_browse_urls(results)
    observed = len(urls)
    return {
        "metric": metric,
        "observed": observed,
        "target": target,
        "success": observed >= target,
        "usable_browse_urls": urls,
        "scope": "technical_proxy",
        "note": (
            "Compte les URLs distinctes avec une acquisition textuelle publique substantielle, "
            "sans blocage technique ni erreur d'extraction. Ne garantit pas à lui seul la "
            "pertinence sémantique ni la distinction entre voies économiques."
        ),
    }


def _mission_llm_summary(root_run_id: int | None) -> dict:
    """Contrôle H2 des pannes provider/modèle dans l'arbre de run courant."""
    if root_run_id is None:
        return {"calls": 0, "by_status": {}, "non_ok": 0, "http_errors": {}, "errors": []}
    rows = journal.query(
        "SELECT status, error, model, provider, task FROM llm_calls "
        "WHERE root_run_id=? ORDER BY id",
        (int(root_run_id),),
    )
    by_status = {}
    http_errors = {}
    errors = []
    for row in rows:
        status = str(row["status"] or "")
        by_status[status] = by_status.get(status, 0) + 1
        if status == "ok":
            continue
        error = str(row["error"] or "")
        for code in re.findall(r"(?<!\d)(4\d\d|5\d\d)(?!\d)", error):
            http_errors[code] = http_errors.get(code, 0) + 1
        errors.append({
            "status": status,
            "model": str(row["model"] or ""),
            "provider": str(row["provider"] or ""),
            "task": str(row["task"] or ""),
            "error": error[:300],
        })
    return {
        "calls": len(rows),
        "by_status": by_status,
        "non_ok": sum(count for status, count in by_status.items() if status != "ok"),
        "http_errors": http_errors,
        "errors": errors,
    }


@handler("orbit.mission", resource="llm")
def orbit_mission(ctx):
    """Mission ORBIT pour n'importe quel business, rattachable à un objectif, une hypothèse ou une expérience.

    Entrée : {"goal": "...", "objective_id"?, "hypothesis_id"?, "experiment_id"?, "max_steps"?,
              "allowed_tools"?: ["search", ...], "profile"?: "flash_fallback",
              "search_browse_lockstep"?: bool,
              "search_browse_selector"?: "first"|"evidence_relevance",
              "business_signal_focus"?: bool, "business_signal_target"?: int,
              "success_criterion"?: {"metric": "usable_browse_count", "gte": 4}}.
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
    result = _run(ctx, lambda: run_mission(
        goal,
        max_steps_per_agent=int(ctx.input.get("max_steps", 8)),
        business=ctx.business,
        allowed_tools=set(allowed_tools) if allowed_tools is not None else None,
        profile=ctx.input.get("profile"),
        search_browse_lockstep=bool(ctx.input.get("search_browse_lockstep", False)),
        search_browse_selector=str(ctx.input.get("search_browse_selector") or "first"),
        business_signal_focus=bool(ctx.input.get("business_signal_focus", False)),
        business_signal_target=max(1, int(ctx.input.get("business_signal_target", 3))),
    ))
    synthesis_status = result.get("synthesis_status", "validated")
    output = {
        "business": ctx.business,
        "rapport": result.get("rapport"),
        "rapport_nature": "inferred" if synthesis_status == "validated" else "unavailable",
        "subtasks": len(result.get("plan") or []),
        "synthesis_status": synthesis_status,
    }
    objective_result = _mission_objective_result(
        result.get("results") or [],
        ctx.input.get("success_criterion"),
    )
    if objective_result is not None:
        output["objective_result"] = objective_result
    if ctx.input.get("business_signal_focus"):
        target = max(1, int(ctx.input.get("business_signal_target", 3)))
        signals = result.get("business_signals") or []
        rejected = result.get("business_signal_rejections") or []
        output["business_signals"] = signals
        output["business_signal_rejections"] = rejected
        output["business_signal_result"] = {
            "metric": "qualified_business_signal_count",
            "observed": len(signals),
            "minimum_target": target,
            "success": len(signals) >= target,
            "rejected": len(rejected),
            "scope": "acquired_text_gate",
            "evaluation_status": "evaluated" if synthesis_status == "validated" else "unavailable",
            "note": (
                "success signifie uniquement que le seuil de signaux structurellement soutenus par une acquisition "
                "et des citations présentes dans son texte est atteint. La présence littérale ne valide pas "
                "l'interprétation de buyer/pain/money_signal/evidence_summary : revue humaine nécessaire. "
                "Ce n'est pas une preuve de demande, de conversion ni de revenu. "
                "test_channel/test_offer/next_test restent des inférences. rejected compte les propositions "
                "refusées, pas les pages examinées ; zéro peut signifier aucune proposition. "
                "synthesis_status=validated conserve son sens technique, pas une validation des faits."
            ),
        }
        # Couche de MESURE après #94, exposée séparément : le gate structurel ci-dessus
        # conserve exactement qualified_business_signal_count. business_signal_reviews
        # rapporte, pour chaque signal structurellement valide, la lecture indépendante
        # de son actionnabilité ; actionable_business_signal_count ne compte que les
        # revues classées actionable_now. Si la revue est dégradée, la classification
        # reste nulle : rien n'est inventé pour préserver le comptage.
        output["business_signal_reviews"] = result.get("business_signal_reviews") or []
        output["actionable_business_signal_count"] = int(
            result.get("actionable_business_signal_count") or 0)
        output["business_signal_review_status"] = result.get(
            "business_signal_review_status", "unavailable")
    flags = {}
    if ctx.input.get("search_browse_lockstep"):
        flags.update({
            "search_browse_lockstep": True,
            "search_browse_selector": str(ctx.input.get("search_browse_selector") or "first"),
        })
    if ctx.input.get("business_signal_focus"):
        flags.update({
            "business_signal_focus": True,
            "business_signal_target": max(1, int(ctx.input.get("business_signal_target", 3))),
        })
    if flags:
        output["experiment_flags"] = flags
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
        output["llm_trace_summary"] = _mission_llm_summary(
            journal.current_run().root_id if journal.current_run() is not None else None
        )
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
