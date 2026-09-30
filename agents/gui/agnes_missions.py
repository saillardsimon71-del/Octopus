"""Local Agnes mission controls backed by the OCTOPUS journal."""
from __future__ import annotations

from octopus import agnes_production, economy, journal, strategy, supervisor, tasks


def create(business: str, prompt: str, *, authorized: bool) -> int:
    if not authorized:
        raise ValueError("Autorisation humaine explicite requise pour une génération Agnes")
    objective_id = supervisor.create_video_objective(business, prompt)
    supervisor.bootstrap(business)
    supervisor.supervise_objective(business, strategy.get("objective", objective_id, business),
                                   max_attempts=supervisor.DEFAULT_MAX_ATTEMPTS,
                                   retry_delay_s=supervisor.DEFAULT_RETRY_DELAY_S)
    return objective_id


def snapshot(business: str) -> dict:
    business = strategy._business(business)
    economic = economy.status(business)
    objectives = [o for o in strategy.list_items("objective", business, limit=50)
                  if supervisor.video_authorized(business, o["id"])]
    generations = agnes_production.list_generations(business, limit=50)
    for generation in generations:
        result = supervisor.video_result(business, generation["task_id"],
                                         {"metric": "kept_video_files", "gte": 1}) \
            if generation["task_id"] is not None else {"files": []}
        generation["verified"] = any(f["file"] == generation["output_path"] and
                                      f["evidence_id"] == generation["evidence_id"]
                                      for f in result["files"])
    return {
        "business": business,
        "objectives": objectives,
        "tasks": tasks.list_tasks(business=business, limit=80),
        "generations": generations,
        "channels": economic["channels"],
        "allowances": economic["allowances"],
        "cash": economic["cash"],
        "token_cost_usd": economic["llm_cost_usd"],
        "agnes_api_cost": {"nature": "unknown", "value": None},
        "ledger": [dict(r) for r in journal.query(
            "SELECT id, direction, amount, currency, category, nature, source_ref, occurred_at "
            "FROM ledger_entries WHERE business=? AND nature='observed' ORDER BY id DESC LIMIT 30", (business,))],
        "events": [dict(r) for r in journal.query(
            "SELECT id, ts, task_id, type FROM events WHERE business=? ORDER BY id DESC LIMIT 40", (business,))],
    }
