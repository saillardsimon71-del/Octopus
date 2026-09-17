"""Les 6 agents du groupe Podalux.

Rôles : ORBIT (CEO) · GROWTH (QC+distribution) · LEDGER (data/finance) ·
FORGE (production) · CONVERT (monétisation) · SOUT (recherche).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, db, deepseek, tools

CATALOG = {
    "cash_impayes_relance01": {
        "nom": "Impayés — relance de facture impayée",
        "prix": "9 €", "angle": "le cash qui ne rentre pas",
    },
    "cash_devis_cgv01": {
        "nom": "Devis / CGV — devis pro + CGV (pack)",
        "prix": "37 €", "angle": "passer pour un pro",
    },
    "cash_avenant_scope01": {
        "nom": "Avenant — faire signer un avenant de scope",
        "prix": "27 €", "angle": "le client qui élargit sans payer",
    },
    "cash_linkedin_rdv01": {
        "nom": "LinkedIn RDV — obtenir un rendez-vous",
        "prix": "19 €", "angle": "le prospect qui ne répond jamais",
    },
}

PALETTE = {
    "bgTop": "#ff9a3c", "bgMid": "#f97316", "bgBottom": "#c2410c",
    "ink": "#2b1206", "accent": "#ff6a00", "highlight": "#fff1d6",
    "pillBg": "#fff7ed", "pillInk": "#c2410c",
}

STRIPE_LINK = "https://buy.stripe.com/8x27sM0Dg9Ipf133X11oI0a"
SUB_ID = "ai_prompts_freelance_short01_offre"

GRID = (
    "Note ce Short sur 35. Bareme STRICT : hook/5, douleur/4, preuve/4, cta/4, "
    "lisibilite/4, humanite/5 (0 si voix synthetique ; ECHEC si <3), motion/4, son/3, pacing/2. "
    "Reponds UNIQUEMENT par un objet JSON : "
    '{"hook":0,"douleur":0,"preuve":0,"cta":0,"lisibilite":0,"humanite":0,"motion":0,"son":0,'
    '"pacing":0,"impression":"...","defauts":["..."],"fixes":["..."]}'
)

AXES = ("hook", "douleur", "preuve", "cta", "lisibilite",
        "humanite", "motion", "son", "pacing")


def _flash(agent, task, sys, user):
    return deepseek.call_json(agent, task, config.MODEL_FLASH,
                              [{"role": "system", "content": sys},
                               {"role": "user", "content": user}])


def _pro(agent, task, sys, user):
    return deepseek.call_json(agent, task, config.MODEL_PRO,
                              [{"role": "system", "content": sys},
                               {"role": "user", "content": user}],
                              reasoning="high")


def _cycle_cost() -> float:
    """Coût du run OCTOPUS en cours (le cycle et ses sous-runs). Coupe-circuit : coût cumulé historique."""
    import octopus
    if not octopus.enabled():
        return db.total_cost()
    from octopus import journal
    run = journal.current_run()
    return journal.subtree_cost(run.id) if run else 0.0


class SOUT:
    NAME = "SOUT"
    SYS = ("Tu es SOUT, l'agent de recherche du groupe Podalux. "
           "Tu choisis la prochaine offre à produire pour maximiser le cash immédiat, "
           "sans refaire ce qui est déjà produit. Réponds en JSON uniquement.")

    @staticmethod
    def research_messages(results: str) -> list[dict]:
        return [{"role": "system",
                 "content": "Résume en 2 phrases l'opportunité business de ces résultats."},
                {"role": "user", "content": results[:2500]}]

    @staticmethod
    def research(topic: str, max_results: int = 5) -> dict:
        """Veille web : recherche multi-sources + synthèse business."""
        from .search import web_search
        results = web_search(topic, max_results)
        db.post("SOUT", f"veille « {topic} »")
        insight = deepseek.call(
            "SOUT", "veille_synthese", config.MODEL_FLASH,
            SOUT.research_messages(results),
            max_tokens=300)
        return {"topic": topic, "results": results, "insight": insight}

    @staticmethod
    def build_user(already):
        cat = {k: v for k, v in CATALOG.items() if k not in already}
        return ("Catalogue disponible :\n" + json.dumps(cat, ensure_ascii=False) +
                "\n\nDéjà produites : " + json.dumps(already, ensure_ascii=False) +
                "\n\nChoisis UNE offre. JSON : "
                '{"offer_id":"...","angle":"...","rationale":"1 phrase"}')

    @staticmethod
    def run(already):
        cat = {k: v for k, v in CATALOG.items() if k not in already}
        if not cat:
            return {"offer_id": "cash_impayes_relance01",
                    "angle": CATALOG["cash_impayes_relance01"]["angle"],
                    "rationale": "catalogue épuisé, repli sur l'offre de référence"}
        r = _flash("SOUT", "selection_offre", SOUT.SYS, SOUT.build_user(already))
        db.post("SOUT", f"offre choisie : {r.get('offer_id')} — {r.get('angle')}")
        db.decide("SOUT", "offer_selected", r)
        return r


class CONVERT:
    NAME = "CONVERT"
    SYS = ("Tu es CONVERT, l'agent de monétisation du groupe Podalux. "
           "Tu écris le script d'un Short vertical (1080x1920, ~24s) qui convertit. "
           "Règles : hook ≤1s, preuve ≤3s, CTA net avec prix, ton envie/soulagement. "
           "Narration française, 7 segments, rôles imposés. Réponds en JSON uniquement.")

    @staticmethod
    def build_user(offer_id, angle, fixes=None):
        meta = CATALOG.get(offer_id, CATALOG["cash_impayes_relance01"])
        fix_text = ""
        if fixes:
            fix_text = ("\n\nAméliorations EXIGÉES par le QC (intègre-les toutes) :\n"
                        + json.dumps(fixes, ensure_ascii=False))
        user = (
            f"Offre : {meta['nom']} · prix {meta['prix']} · angle « {angle} ».\n\n"
            "Génère un job.json complet. Schéma exact :\n"
            '{"titre":"...","hook":"...","douleur":"...","preuve":"...",'
            '"soulagement":"...","cta":"...","prix":"' + meta['prix'] + '",'
            '"keywords":["mot1",...],'
            '"narration":[{"id":"hook","role":"hook","rate":"-8%","texte":"..."},'
            '{"id":"hook2","role":"hook","rate":"-4%","texte":"..."},'
            '{"id":"douleur","role":"douleur","rate":"+4%","texte":"..."},'
            '{"id":"douleur2","role":"douleur","rate":"+7%","texte":"..."},'
            '{"id":"preuve","role":"preuve","rate":"+12%","texte":"..."},'
            '{"id":"soulagement","role":"soulagement","rate":"+2%","texte":"..."},'
            '{"id":"cta","role":"cta","rate":"-1%","texte":"..."}]}\n\n'
            "Contraintes : phrases courtes, CTA = prix + « lien en description », "
            "keywords = 5-8 mots clés réellement présents dans la narration."
        ) + fix_text
        return user

    @staticmethod
    def run(offer_id, angle, fixes=None):
        meta = CATALOG.get(offer_id, CATALOG["cash_impayes_relance01"])
        user = CONVERT.build_user(offer_id, angle, fixes)
        r = _flash("CONVERT", "redaction_job", CONVERT.SYS, user)
        job = {
            "offer_id": offer_id, "langue": "fr", "duree_cible_s": 24,
            "titre": r.get("titre", ""), "hook": r.get("hook", ""),
            "douleur": r.get("douleur", ""), "preuve": r.get("preuve", ""),
            "soulagement": r.get("soulagement", ""), "cta": r.get("cta", ""),
            "prix": meta["prix"], "stripe_link": STRIPE_LINK, "sub_id": SUB_ID,
            "voix": {"moteur": "chatterbox", "nom": config.CHATTERBOX_VOICE},
            "keywords": r.get("keywords", []),
            "palette": PALETTE,
            "narration": r.get("narration", []),
        }
        config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        (config.JOBS_DIR / f"{offer_id}.json").write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        db.post("CONVERT", f"job.json écrit : {offer_id}.json")
        db.decide("CONVERT", "job_written", {"offer_id": offer_id})
        return job


class FORGE:
    NAME = "FORGE"

    @staticmethod
    def run(offer_id, job):
        """Production déterministe : audio Chatterbox + rendu Remotion + mux + métriques."""
        job_path = config.JOBS_DIR / f"{offer_id}.json"
        db.post("FORGE", f"démarrage du rendu de {offer_id} (@FORGE)")
        tools.make_audio(str(job_path), offer_id)
        db.post("FORGE", "audio + captions générés (Chatterbox)")
        tools.remotion_render(offer_id)
        db.post("FORGE", "rendu Remotion terminé")
        tools.mux(offer_id)
        db.post("FORGE", "mux final.mp4 ok")
        metrics = tools.qc_metrics(offer_id)
        db.post("FORGE", f"métriques : LUFS {metrics.get('lufs_integrated')} · "
                         f"LRA {metrics.get('lra_lu')} · SATAVG {metrics.get('satavg_mean')}")
        return metrics


class GROWTH:
    NAME = "GROWTH"
    SYS = "Tu es GROWTH, propriétaire du QC SHIP+WARM. Tu juges la qualité d'un Short."

    @staticmethod
    def build_prompt(job, n_frames):
        narration = " ".join(s["texte"] for s in job.get("narration", []))
        return (
            f"Voici {n_frames} frames d'un Short vertical 1080x1920.\n"
            f"Narration exacte :\n« {narration} »\n\n"
            "Évalue-le comme un spectateur qui scrolle.\n\n" + GRID
        )

    @staticmethod
    def run(offer_id, job, metrics):
        frames = metrics.get("frames", [])
        narration = " ".join(s["texte"] for s in job.get("narration", []))
        prompt = GROWTH.build_prompt(job, len(frames))
        verdict = deepseek.vision("GROWTH", "qc_vision", frames, narration, prompt)
        total = int(sum(verdict.get(k, 0) for k in AXES))
        humanite = int(verdict.get("humanite", 0))
        verdict["total_calcule"] = total
        verdict["ship_pass"] = total >= 24
        verdict["warm_pass"] = (total >= 24) and (humanite >= 3)
        db.post("GROWTH", f"QC vision : {total}/35 · humanité {humanite}/5 · "
                          f"WARM {'PASS' if verdict['warm_pass'] else 'FAIL'}")
        db.decide("GROWTH", "qc_done", {"total": total, "humanite": humanite,
                                        "warm_pass": verdict["warm_pass"]})
        return verdict


class LEDGER:
    NAME = "LEDGER"

    @staticmethod
    def run(offer_id, metrics, growth):
        """Rubric /35 + coût du cycle + go/no-go (calculés EN CODE, pas par le modèle)."""
        total = growth.get("total_calcule", 0)
        humanite = growth.get("humanite", 0)
        warm = growth.get("warm_pass", False)
        cost = _cycle_cost()
        go = warm and (cost <= config.CYCLE_BUDGET_USD)
        payload = {
            "offer_id": offer_id,
            "score": total, "humanite": humanite, "warm_pass": warm,
            "cost_usd": round(cost, 4), "budget_usd": config.CYCLE_BUDGET_USD,
            "lufs": metrics.get("lufs_integrated"), "lra": metrics.get("lra_lu"),
            "satavg": metrics.get("satavg_mean"), "go": go,
        }
        db.record_metric(offer_id, total, humanite, "WARM_PASS" if warm else "WARM_FAIL", payload)
        db.decide("LEDGER", "ledger", payload)
        db.post("LEDGER", f"rubric {total}/35 · coût ${round(cost,4)} · go/no-go = {'GO' if go else 'NO-GO'}")
        return payload


class ORBIT:
    NAME = "ORBIT"
    SYS = ("Tu es ORBIT, le CEO du groupe Podalux. Tu arbitres : tu tiens la barre qualité. "
           "Publication à ≥24/35 (SHIP, WARM_PASS obligatoire), cible ≥30/35. "
           "Décide « done » si ≥24 WARM_PASS, « iterate » si <24 (avec les fixes), "
           "« stop » si plus d'amélioration possible. Réponds en JSON uniquement.")

    @staticmethod
    def build_user(offer_id, ledger, human):
        human_txt = ""
        if human:
            human_txt = ("\n\nMessages récents de l'humain (à prendre en compte) :\n"
                         + "\n".join(f"- {m['content']}" for m in human))
        user = (
            f"Résultat du cycle pour {offer_id} :\n" + json.dumps(ledger, ensure_ascii=False)
            + human_txt
            + "\n\nDécide : JSON {'{\"decision\":\"done|iterate|stop\",\"message\":\"1 phrase\"}'}"
        )
        return user

    @staticmethod
    def run(offer_id, ledger):
        user = ORBIT.build_user(offer_id, ledger, db.human_messages(5))
        r = _pro("ORBIT", "arbitrage", ORBIT.SYS, user)
        r["ledger"] = ledger
        db.post("ORBIT", f"arbitrage : {r.get('decision')} — {r.get('message')}")
        db.decide("ORBIT", "arbitrage", r)
        return r

