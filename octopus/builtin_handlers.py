"""Handlers génériques du moteur."""
from __future__ import annotations

from . import report, resources, strategy
from .worker import handler

# Enregistre le handler de développement avec les handlers moteur existants.
from . import dev_worker  # noqa: F401, E402


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
    """Boucle économique sans LLM : verdict des expériences en cours, réinvestissement selon la politique.

    Récurrente : `python -m octopus schedule <business> economy.cycle --every 86400`. Avec
    `--input '{"drive": true, "mission_budget_usd": 0.05}'`, relance aussi une mission ORBIT d'exploration
    (budget LLM plafonné par tâche) quand aucune n'est active : consentement explicite à l'autonomie.
    """
    from . import economy
    budget = float(ctx.input.get("mission_budget_usd", 0.05))
    if ctx.input.get("portfolio"):  # tous les businesses, y compris ceux ouverts par les agents
        results = economy.portfolio_cycle(drive_all=bool(ctx.input.get("drive")), budget_usd=budget)
        ctx.emit("economy.portfolio.done", {"businesses": [r["business"] for r in results]})
        return {"portfolio": results}
    result = economy.cycle(ctx.business, drive_orbit=bool(ctx.input.get("drive")), budget_usd=budget)
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
                           context={"key": key, "need": need, "requested_by": ctx.input.get("requested_by")})
    state = resources.check(key)
    resources.update(key, actor="human", notes=f"reponse humaine ({need}) : {answer[:200]}")
    ctx.emit("resources.acquired", {"key": key, "need": need, "state": state["state"], "answer": answer[:200]})
    return {"key": key, "need": need, "state": state["state"], "access": state["access"],
            "detail": state["last_check_detail"], "answer": answer[:200]}
