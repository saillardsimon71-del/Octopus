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
    """publish.json de l'offre ; s'il manque, il est généré depuis le job et le QC (à relire avant upload).

    Avant : aucun code ne produisait publish.json, la publication échouait pour toute offre
    sortie du cycle (audit, section 3).
    """
    p = config.PROJECT_ROOT / "out" / offer_id / "publish.json"
    if not p.exists():
        pub = draft_publish(offer_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")
        db.post("GROWTH", f"publish.json généré pour {offer_id} (brouillon à relire)")
        return pub
    return json.loads(p.read_text(encoding="utf-8"))


def draft_publish(offer_id: str) -> dict:
    job_path = config.JOBS_DIR / f"{offer_id}.json"
    if not job_path.exists():
        raise FileNotFoundError(f"ni publish.json ni job pour {offer_id} : lancer un cycle d'abord")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    out = config.PROJECT_ROOT / "out" / offer_id
    metrics_path = out / "qc_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    last = db.last_metric(offer_id) or {}
    prix = job.get("prix", "")
    narration = " ".join(s.get("texte", "") for s in job.get("narration", []) if isinstance(s, dict))
    description = " ".join(x for x in (job.get("hook"), job.get("soulagement"), job.get("cta")) if x) or narration
    link = job.get("stripe_link", "")
    return {
        "offer_id": offer_id,
        "title_fr": f"{job.get('titre') or offer_id} ({prix})" if prix else (job.get("titre") or offer_id),
        "description": f"{description} {link}".strip(),
        "tags": [k for k in job.get("keywords", []) if isinstance(k, str)][:15],
        "thumb_brief": "",
        "sub_id": job.get("sub_id", ""),
        "stripe_link": link,
        "duration_s": metrics.get("duration_s"),
        "resolution": metrics.get("resolution"),
        "voix": (job.get("voix") or {}).get("moteur"),
        "score_qc": {"total": last.get("score"), "humanite": last.get("humanite"), "verdict": last.get("verdict")},
        "brouillon": True,
    }


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
