"""Handlers génériques du moteur."""
from __future__ import annotations

from . import report
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
