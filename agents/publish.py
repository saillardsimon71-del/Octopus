"""Publication (Phase 6) : plan de publication + garde-fou sortant.

- `publish(offer_id, dry_run=True)` génère le plan (titre/description/tags/thumb)
  et le logge. Rien ne sort de la machine en dry-run.
- Le vrai upload (YouTube Data API / Stripe) nécessite des clés officielles
  + une confirmation humaine — jamais automatique.
"""
from __future__ import annotations

import json

from . import config, db


def read_publish(offer_id: str) -> dict:
    p = config.PROJECT_ROOT / "out" / offer_id / "publish.json"
    if not p.exists():
        raise FileNotFoundError(f"publish.json introuvable pour {offer_id}")
    return json.loads(p.read_text(encoding="utf-8"))


def build_plan(offer_id: str) -> dict:
    pub = read_publish(offer_id)
    return {
        "offer_id": offer_id,
        "title": (pub.get("title_fr") or offer_id)[:100],
        "description": pub.get("description", ""),
        "tags": pub.get("tags", []),
        "thumbnail_brief": pub.get("thumb_brief", ""),
        "stripe_link": pub.get("stripe_link", ""),
        "sub_id": pub.get("sub_id", ""),
        "duration_s": pub.get("duration_s"),
        "score_qc": pub.get("score_qc", {}),
    }


def publish(offer_id: str, dry_run: bool = True) -> dict:
    """Génère le plan de publication. dry_run=True par défaut (aucun sortant)."""
    plan = build_plan(offer_id)
    db.decide("GROWTH", "publish_plan", plan)
    if dry_run:
        db.post("GROWTH", f"PUBLICATION (dry-run) planifié : {plan['title']}")
        return {"dry_run": True, "plan": plan,
                "note": "rien n'a été envoyé (dry-run). Valider avant upload réel."}
    # Upload réel : YouTube Data API / Stripe (clés officielles + confirmation humaine).
    db.post("GROWTH", f"PUBLICATION RÉELLE demandée pour {offer_id} — "
                      "nécessite clés API officielles + confirmation humaine.")
    return {"dry_run": False, "plan": plan,
            "warning": "upload réel non câblé : brancher YouTube Data API / Stripe + validation humaine."}
