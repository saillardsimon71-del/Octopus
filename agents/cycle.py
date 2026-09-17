"""Le cycle économique : SOUT → CONVERT → FORGE → GROWTH → LEDGER → ORBIT."""
from __future__ import annotations

import json

from octopus.journal import with_run

from . import config, db
from .agents import SOUT, CONVERT, FORGE, GROWTH, LEDGER, ORBIT


def already_produced() -> list[str]:
    """Offres déjà produites (détection par la présence de final.mp4)."""
    out_dir = config.PROJECT_ROOT / "out"
    if not out_dir.exists():
        return []
    return sorted(p.name for p in out_dir.iterdir() if (p / "final.mp4").exists())


@with_run("podalux", "video_cycle", budget_usd=config.CYCLE_BUDGET_USD)
def run_cycle(offer_id: str | None = None, max_iterations: int = 3) -> dict:
    db.init_db()
    db.clear_stop()
    rid = db.start_run(offer_id)
    db.post("ORBIT", "Démarrage du cycle SOUT → CONVERT → FORGE → GROWTH → LEDGER",
            kind="cycle")

    try:
        # 1. SOUT — sélection d'offre
        db.update_run(rid, step="SOUT : sélection d'offre")
        already = already_produced()
        sout = SOUT.run(already)
        oid = offer_id or sout.get("offer_id") or "cash_impayes_relance01"
        angle = sout.get("angle", "")
        db.update_run(rid, offer_id=oid)

        # Boucle CONVERT → FORGE → GROWTH → LEDGER → ORBIT (itère tant que ORBIT dit "iterate")
        fixes = None
        ledger = {}
        orbit = {}
        iterations = []
        for it in range(max_iterations):
            if db.stop_requested():
                db.update_run(rid, status="stopped", step="arrêt demandé")
                db.post("ORBIT", "arrêt demandé par l'humain", kind="cycle")
                break
            db.update_run(rid, step=f"itération {it + 1}/{max_iterations} · CONVERT")
            db.post("ORBIT", f"itération {it + 1}/{max_iterations} sur {oid}", kind="iteration")
            job = CONVERT.run(oid, angle, fixes=fixes)          # 2. monétisation (+ fixes du QC)
            db.update_run(rid, step=f"itération {it + 1} · FORGE (rendu)")
            metrics = FORGE.run(oid, job)                        # 3. rendu
            db.update_run(rid, step=f"itération {it + 1} · GROWTH (QC vision)")
            growth = GROWTH.run(oid, job, metrics)               # 4. QC vision
            db.update_run(rid, step=f"itération {it + 1} · LEDGER")
            ledger = LEDGER.run(oid, metrics, growth)            # 5. rubric + coût + go/no-go
            db.update_run(rid, step=f"itération {it + 1} · ORBIT (arbitrage)")
            orbit = ORBIT.run(oid, ledger)                       # 6. arbitrage
            iterations.append({
                "iteration": it + 1, "score": ledger["score"],
                "warm_pass": ledger["warm_pass"], "decision": orbit.get("decision"),
                "fixes": growth.get("fixes", []),
            })
            if orbit.get("decision") in ("done", "stop"):
                break
            fixes = growth.get("fixes", [])

        db.update_run(rid, status="done", step="terminé",
                      result=json.dumps({"score": ledger.get("score"),
                                         "decision": orbit.get("decision")}, ensure_ascii=False))

        # checkpoint humain : confirmation de publication (si ORBIT a dit "done")
        if orbit.get("decision") == "done":
            db.update_run(rid, step="attente de confirmation de publication")
            ans = db.ask_human("GROWTH", "publish_confirmation",
                               f"Publier la vidéo {oid} ({ledger.get('score')}/35) ? oui/non",
                               timeout_s=600)
            db.post("GROWTH", f"confirmation de publication : {ans}")
            if ans and ans.strip().lower().startswith(("oui", "o", "y", "yes")):
                db.decide("GROWTH", "publish_approved", {"offer_id": oid})
                db.update_run(rid, step="publication approuvée (dry-run)")
    except Exception as e:
        db.update_run(rid, status="error", step=f"erreur : {e}")
        raise

    return {
        "offer_id": oid,
        "sout": sout,
        "ledger": ledger,
        "orbit": orbit,
        "iterations": iterations,
    }
