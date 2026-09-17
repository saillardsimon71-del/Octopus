"""Tâches du business veille pour le worker OCTOPUS."""
from __future__ import annotations

import datetime as dt

from octopus import llm
from octopus.worker import handler

from . import brief as B

CFG = B.load_config()
BUSINESS = CFG["id"]


def _topic(ctx) -> tuple[str, dict]:
    topic_id = str(ctx.input.get("topic", ""))
    if topic_id in CFG["topics"]:
        return topic_id, CFG["topics"][topic_id]
    if ctx.input.get("query"):  # sujet ponctuel
        query = str(ctx.input["query"])
        return B.slug(query), {"query": query, "label": ctx.input.get("label", query), "feeds_agent": None}
    raise ValueError(f"sujet inconnu : {topic_id!r} (configurés : {', '.join(CFG['topics'])}) ; ou passer « query »")


@handler("veille.brief", resource="llm", budget_usd=CFG["budget_usd_per_brief"], max_attempts=2, retry_delay_s=300)
def veille_brief(ctx):
    topic_id, topic = _topic(ctx)
    rules = CFG["brief"]
    collected = ctx.memo("sources", lambda: B.collect(topic["query"], rules["max_sources"]))
    if len(collected["sources"]) < rules["min_sources_cited"]:
        raise RuntimeError(f"{len(collected['sources'])} source(s) collectée(s) : insuffisant "
                           f"({'; '.join(collected['errors']) or 'aucune erreur signalée'})")

    def generate():
        completion = llm.complete(
            "veille.brief", B.build_messages(topic["label"], collected["sources"], rules["max_points"]),
            agent="VEILLE", business=BUSINESS, profile=ctx.input.get("profile") or CFG["profile"], json_mode=True,
            max_tokens=rules["max_tokens"],
            validate=lambda text: B.validate(llm.parse_json(text), len(collected["sources"]), CFG))
        today = dt.date.today().isoformat()
        meta = {"date": today, "model": completion.model, "cost_class": completion.justification.get("cost_class"),
                "cost_usd": completion.cost_usd}
        path = B.out_dir() / f"{today}-{topic_id}-t{ctx.id}.md"
        path.write_text(B.render_markdown(topic["label"], collected, completion.data, meta), encoding="utf-8")
        ctx.emit("artifact.created", {"path": str(path), "model": completion.model})
        return {"path": str(path), "brief": completion.data, "model": completion.model, "cost_usd": completion.cost_usd}

    result = ctx.memo("brief", generate)
    output = {"topic": topic_id, "path": result["path"], "points": len(result["brief"]["points"]),
              "model": result["model"], "cost_usd": result["cost_usd"], "transmis": False}
    agent = topic.get("feeds_agent")
    if not agent:
        return output
    answer = ctx.ask_human("transmettre", f"Brief « {topic['label']} » prêt ({result['path']}). "
                                          f"Le transmettre à {agent} comme piste non vérifiée ? oui/non",
                           context={"path": result["path"]})
    if answer.strip().lower().rstrip(".! ") in ("oui", "o", "yes", "y", "ok"):
        from agents import db
        db.init_db()
        db.remember(agent, f"veille_{topic_id}_{dt.date.today().isoformat()}",
                    B.memory_summary(topic["label"], collected, result["brief"]))
        output["transmis"] = True
    return output
