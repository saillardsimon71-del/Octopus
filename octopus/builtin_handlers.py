"""Handlers génériques du moteur."""
from __future__ import annotations

from . import report, strategy
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
