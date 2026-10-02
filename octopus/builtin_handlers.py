"""Handlers génériques du moteur."""
from __future__ import annotations

from . import report, resources, strategy
from .worker import handler


@handler("octopus.cost_report")
def cost_report(ctx):
    """Résumé des coûts LLM (sans appel payant) : utile en planification quotidienne."""
    days = int(ctx.input.get("days", 1))
    data = report.build(days)
    summary = {"days": days, "calls": data["calls"], "cost_usd": round(data["cost"], 6),
               "paid_reasons": [{"reason": r, "calls": n, "cost_usd": round(c, 6)} for r, n, c, _ in data["paid_reasons"]],
               "sensitive_to_cloud": data["sensitive_to_cloud"]}
    ctx.emit("report.costs", summary)
    return summary


@handler("strategy.review", max_attempts=2, retry_delay_s=60)
def strategy_review(ctx):
    """Revue stratégique sans appel LLM : état persistant calculé, rattaché aux objectifs actifs.

    Ponctuelle : `strategy.schedule_review`. Récurrente : `python -m octopus schedule <business>
    strategy.review --every 604800` (chaque occurrence crée sa propre revue).
    """
    review_id = ctx.input.get("review_id")
    if review_id is None:
        review_id = ctx.memo("review_id", lambda: strategy.create(
            "review", ctx.business, "Revue stratégique planifiée", created_by="schedule", origin_task_id=ctx.id))
        strategy.link(ctx.business, "review", review_id, "task", ctx.id, "executed_by")
    result = strategy.complete_review(ctx.business, int(review_id), actor="schedule")
    ctx.emit("strategy.review.completed", {"review_id": int(review_id), "status": result["status"]})
    return result


@handler("economy.cycle", max_attempts=2, retry_delay_s=300)
def economy_cycle(ctx):
    from . import economy
    for flag in ("drive", "portfolio"):
        if flag in ctx.input and not isinstance(ctx.input[flag], bool):
            raise ValueError(f"{flag} must be a bool")
    budget = float(ctx.input.get("mission_budget_usd", 0.05))
    drive = ctx.input.get("drive", False)
    portfolio = ctx.input.get("portfolio", False)
    if portfolio:  # tous les businesses, y compris ceux ouverts par les agents
        results = economy.portfolio_cycle(drive_all=drive, budget_usd=budget)
        ctx.emit("economy.portfolio.done", {"businesses": [r["business"] for r in results]})
        return {"portfolio": results}
    result = economy.cycle(ctx.business, drive_orbit=drive, budget_usd=budget)
    ctx.emit("economy.cycle.done", {"evaluated": len(result["evaluated"]), "reinvest": result["reinvest"]["status"]})
    return result


@handler("resources.audit", max_attempts=2, retry_delay_s=300)
def resources_audit(ctx):
    """Constate l'etat reel de l'inventaire : synchronise les declarations, passe les sondes.

    Aucun appel LLM, aucune decision : le resultat dit ce qui repond, ce qui manque, et ce qui exige
    un humain. Recurrente : `python -m octopus schedule octopus resources.audit --every 86400`.
    """
    synced = resources.sync()
    checked = resources.check_all(kind=ctx.input.get("kind"))
    blocked = resources.blocked()
    summary = {"declared": len(synced["declared"]), "added": synced["added"], "checked": len(checked),
               "available": [r["key"] for r in checked if r["state"] in ("available", "degraded")],
               "blocked": [{"key": b["key"], "reason": b["reason"], "needs": b["needs"]} for b in blocked]}
    ctx.emit("resources.audited", summary)
    return summary


@handler("resources.acquire", max_attempts=3, retry_delay_s=60)
def resources_acquire(ctx):
    """Frontiere humaine : demande la creation, la connexion ou l'autorisation d'une ressource.

    La tache attend la reponse humaine (la tentative ne compte pas), puis repasse la sonde : si la
    ressource repond, l'operation qui l'attendait peut reprendre sans nouvelle intervention.
    """
    key = str(ctx.input["key"]).strip().lower()
    need = str(ctx.input.get("need", "create"))
    question = str(ctx.input.get("question") or f"Ressource {key} : {need} requis")
    answer = ctx.ask_human(f"resource:{key}:{need}", question,
                           context={"key": key, "need": need, "requested_by": ctx.input.get("requested_by"),
                                    "web_request": ctx.input.get("web_request")})
    if ctx.input.get("web_request"):
        # Structured decisions only: never log free-text credentials or imply permissions.
        state = resources.get(key)
        return {"key": key, "decision": "connected" if answer == "connected" else "declined",
                "session_status": (state.get("web_account") or {}).get("session_status", "absent")}
    state = resources.check(key)
    resources.update(key, actor="human", notes=f"reponse humaine ({need}) : {answer[:200]}")
    ctx.emit("resources.acquired", {"key": key, "need": need, "state": state["state"], "answer": answer[:200]})
    return {"key": key, "need": need, "state": state["state"], "access": state["access"],
            "detail": state["last_check_detail"], "answer": answer[:200]}


@handler("capability.acquire", max_attempts=2, retry_delay_s=60)
def capability_acquire(ctx):
    """Acquisition bornée d'une capacité décidée : construction locale puis validation déterministe.

    Aucune dépense, aucun effet externe, aucune permission élargie : une capacité techniquement
    acquise hors de l'ensemble autorisé reste non exécutable. Rejouable après crash (construction
    mémoïsée, clef d'idempotence) et la disponibilité est toujours recalculée depuis les registres
    réels, jamais lue dans une déclaration.
    """
    from . import capability_acquisition
    return capability_acquisition.execute_acquisition(ctx)


@handler("supervisor.tick", max_attempts=3, retry_delay_s=60)
def supervisor_tick(ctx):
    """Une passe du superviseur autonome : objectifs actifs -> travail -> évaluation -> décision.

    Le tick se réarme lui-même : après `python -m octopus runtime`, aucune commande manuelle n'est
    nécessaire entre objectif, mission, évaluation et tâche suivante. Les frontières humaines
    (ressource ou autorisation manquante) passent par `ctx.ask_human` et suspendent la tâche.
    """
    from . import supervisor
    report = supervisor.tick(ctx=ctx)
    ctx.emit("supervisor.tick", {"checked": report["checked"],
                                 "actions": {e["objective_id"]: e["action"] for e in report["objectives"]},
                                 "human_boundaries": [e["objective_id"] for e in report["human_boundaries"]],
                                 "next_tick_task_id": report["next_tick"]})
    return report


@handler("supervisor.objective_work", resource="llm", max_attempts=2, retry_delay_s=120)
def supervisor_objective_work(ctx):
    """Travail durable d'un objectif persistant, exécuté par le runtime de mission existant.

    La mission est mémoïsée (`ctx.memo`) : une réponse humaine, une nouvelle tentative ou un
    redémarrage ne rejouent pas le travail déjà fait ni ses appels.
    """
    from . import supervisor
    result = supervisor.execute_objective_work(ctx)
    ctx.emit("supervisor.work.done", {k: result.get(k) for k in
                                      ("objective_id", "execution_status", "synthesis_status", "observed",
                                       "success", "measured", "human_boundary")})
    return result


@handler('resources.account_work', max_attempts=1)
def account_work(ctx):
    """Same runtime/worker, separate network state and Chromium workspace."""
    from agents import web_guard, task_handlers
    from . import browser_workspace, mandates
    key = ctx.input['browser_resource_key']
    if not mandates.account_authority(ctx.business, key, 'read'):
        raise PermissionError('lecture compte non mandatée ou session indisponible')
    # Caller-owned input is rebuilt, even if somebody manually enqueued an unsafe task.
    ctx.input['allowed_tools'] = ['browser_navigate', 'browser_snapshot', 'browser_scroll', 'browser_back',
                                 'browser_click', 'browser_type', 'browser_select', 'browser_check',
                                 'browser_press', 'browser_verify', 'browser_upload']
    ctx.input['browser_public_only'] = False
    with web_guard.isolated_session(), browser_workspace.mission_scope(isolated=True):
        return task_handlers.orbit_mission(ctx)
