"""Le cycle économique : SOUT → CONVERT → FORGE → GROWTH → LEDGER → ORBIT."""
from __future__ import annotations

import json
import os
import time

from octopus.journal import with_run

from . import config, db
from .agents import CATALOG, SOUT, CONVERT, FORGE, GROWTH, LEDGER, ORBIT

YES = {"oui", "o", "yes", "y", "ok"}


def is_yes(answer: str | None) -> bool:
    """Réponse humaine explicite. « on verra » ou « oui mais… » ne valent pas accord (audit, risque 17)."""
    return bool(answer) and answer.strip().lower().rstrip(".! ") in YES


def already_produced() -> list[str]:
    """Offres déjà produites (détection par la présence de final.mp4)."""
    out_dir = config.PROJECT_ROOT / "out"
    if not out_dir.exists():
        return []
    return sorted(p.name for p in out_dir.iterdir() if (p / "final.mp4").exists())


@with_run("podalux", "video_cycle", budget_usd=config.CYCLE_BUDGET_USD)
def run_cycle(offer_id: str | None = None, max_iterations: int = 3) -> dict:
    db.init_db()
    if offer_id is not None and offer_id not in CATALOG:
        raise ValueError(f"offre inconnue : {offer_id!r} (catalogue : {', '.join(CATALOG)})")
    owner = f"pid{os.getpid()}-{time.time_ns()}"
    if not db.acquire_run_lock(owner):
        db.post("ORBIT", f"cycle refusé : un autre cycle tourne déjà ({db.run_lock_holder()})", kind="cycle")
        raise CycleBusy("un autre cycle tourne déjà")
    try:
        return _run_cycle_locked(owner, offer_id, max_iterations)
    finally:
        db.release_run_lock(owner)


class CycleBusy(RuntimeError):
    """Un autre cycle détient le verrou de production."""


def _run_cycle_locked(owner: str, offer_id: str | None, max_iterations: int) -> dict:
    db.clear_stop()  # après le verrou : un cycle refusé n'efface pas l'arrêt demandé sur le cycle en cours
    rid = db.start_run(offer_id)
    db.post("ORBIT", "Démarrage du cycle SOUT → CONVERT → FORGE → GROWTH → LEDGER",
            kind="cycle")

    try:
        # 1. SOUT — sélection d'offre
        db.update_run(rid, step="SOUT : sélection d'offre")
        already = already_produced()
        if offer_id:  # offre imposée : pas d'appel à SOUT, angle du catalogue (audit C5)
            sout = {"offer_id": offer_id, "angle": CATALOG[offer_id]["angle"], "rationale": "offre imposée"}
            db.post("SOUT", f"offre imposée : {offer_id} — {sout['angle']}")
        else:
            sout = SOUT.run(already)
            if sout.get("offer_id") not in CATALOG:
                fallback = next((k for k in CATALOG if k not in already), next(iter(CATALOG)))
                db.post("SOUT", f"offre hors catalogue proposée ({sout.get('offer_id')!r}) : repli sur {fallback}")
                sout = {"offer_id": fallback, "angle": CATALOG[fallback]["angle"],
                        "rationale": "repli : proposition hors catalogue"}
        oid = sout["offer_id"]
        angle = sout.get("angle") or CATALOG[oid]["angle"]
        db.update_run(rid, offer_id=oid)

        # Boucle CONVERT → FORGE → GROWTH → LEDGER → ORBIT (itère tant que ORBIT dit "iterate")
        fixes = None
        ledger = {}
        orbit = {}
        iterations = []
        stopped = False
        for it in range(max_iterations):
            db.acquire_run_lock(owner)  # renouvelle le bail
            if db.stop_requested():
                db.update_run(rid, status="stopped", step="arrêt demandé")
                db.post("ORBIT", "arrêt demandé par l'humain", kind="cycle")
                stopped = True
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
            orbit = ORBIT.run(oid, ledger, iterations_left=max_iterations - it - 1)  # 6. arbitrage
            fixes = growth.get("fixes", []) + ledger.get("fixes", [])
            iterations.append({
                "iteration": it + 1, "score": ledger["score"],
                "warm_pass": ledger["warm_pass"], "media_ok": ledger.get("media_ok"),
                "decision": orbit.get("decision"), "fixes": fixes,
            })
            if orbit.get("decision") in ("done", "stop"):
                break

        if not stopped:
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
            if is_yes(ans):
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
