"""Les 6 agents du groupe Podalux.

Rôles : ORBIT (CEO) · GROWTH (QC+distribution) · LEDGER (data/finance) ·
FORGE (production) · CONVERT (monétisation) · SOUT (recherche).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import cancel, config, db, deepseek, tools

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

# Libellés affichés par le template Remotion (remotion/src/CashShort.tsx), par offre et par rôle.
# Illustrations de l'usage du produit : aucun chiffre de résultat client inventé hors offre d'origine.
VISUELS = {
    "cash_impayes_relance01": {
        "hook": {"label": "FACTURE IMPAYÉE", "icon": "📄", "accent_emoji": "⏰", "accent_text": "30 JOURS"},
        "douleur": {"label": "LE CASH QUI NE RENTRE PAS", "icon": "💸", "accent_emoji": "💸", "accent_text": "CASH BLOQUÉ"},
        "preuve": {"label": "LA RELANCE PRO", "icon": "✅", "accent_emoji": "📄", "accent_text": "RELANCE N°2",
                   "card_title": "RELANCE N°2",
                   "card_rows": [["Facture #2026-041", "1 240 €"], ["Statut", "ENCAISSÉ +1 240 € ✓"]]},
        "soulagement": {"label": "LE CONTRÔLE", "icon": "🤝", "accent_emoji": "✓", "accent_text": "PAYÉE",
                        "banner": "+1 240 € ENCAISSÉ"},
        "cta": {"label": "À VOUS DE JOUER", "icon": "👇", "accent_emoji": "👇"},
    },
    "cash_devis_cgv01": {
        "hook": {"label": "DEVIS BRICOLÉ", "icon": "📝", "accent_emoji": "⚠️", "accent_text": "PAS PRO"},
        "douleur": {"label": "LE CLIENT QUI HÉSITE", "icon": "🤔", "accent_emoji": "📉", "accent_text": "DEVIS REFUSÉ"},
        "preuve": {"label": "LE PACK DEVIS + CGV", "icon": "✅", "accent_emoji": "📑", "accent_text": "CGV INCLUSES",
                   "card_title": "DEVIS N°2026-012",
                   "card_rows": [["Mentions légales", "✓"], ["CGV jointes", "✓"], ["Statut", "SIGNÉ ✓"]]},
        "soulagement": {"label": "PASSER POUR UN PRO", "icon": "🤝", "accent_emoji": "✓", "accent_text": "SIGNÉ",
                        "banner": "DEVIS SIGNÉ"},
        "cta": {"label": "À VOUS DE JOUER", "icon": "👇", "accent_emoji": "👇"},
    },
    "cash_avenant_scope01": {
        "hook": {"label": "LE SCOPE QUI GONFLE", "icon": "📈", "accent_emoji": "➕", "accent_text": "TRAVAIL GRATUIT"},
        "douleur": {"label": "LE CLIENT QUI ÉLARGIT", "icon": "😤", "accent_emoji": "⏳", "accent_text": "HEURES OFFERTES"},
        "preuve": {"label": "L'AVENANT PRO", "icon": "✅", "accent_emoji": "📄", "accent_text": "AVENANT N°1",
                   "card_title": "AVENANT N°1",
                   "card_rows": [["Périmètre ajouté", "chiffré"], ["Délai", "mis à jour"], ["Statut", "SIGNÉ ✓"]]},
        "soulagement": {"label": "CHAQUE AJOUT PAYÉ", "icon": "🤝", "accent_emoji": "✓", "accent_text": "SIGNÉ",
                        "banner": "AVENANT SIGNÉ"},
        "cta": {"label": "À VOUS DE JOUER", "icon": "👇", "accent_emoji": "👇"},
    },
    "cash_linkedin_rdv01": {
        "hook": {"label": "MESSAGE SANS RÉPONSE", "icon": "💬", "accent_emoji": "👻", "accent_text": "VU, PAS RÉPONDU"},
        "douleur": {"label": "LE PROSPECT FANTÔME", "icon": "😶", "accent_emoji": "📭", "accent_text": "0 RÉPONSE"},
        "preuve": {"label": "LE MESSAGE QUI ACCROCHE", "icon": "✅", "accent_emoji": "✉️", "accent_text": "MESSAGE N°2",
                   "card_title": "MESSAGE N°2",
                   "card_rows": [["Accroche", "personnalisée"], ["Relance", "J+3"], ["Statut", "RDV CALÉ ✓"]]},
        "soulagement": {"label": "LE RENDEZ-VOUS", "icon": "🤝", "accent_emoji": "📅", "accent_text": "RDV CALÉ",
                        "banner": "RDV CALÉ"},
        "cta": {"label": "À VOUS DE JOUER", "icon": "👇", "accent_emoji": "👇"},
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
AXES_MAX = {"hook": 5, "douleur": 4, "preuve": 4, "cta": 4, "lisibilite": 4,
            "humanite": 5, "motion": 4, "son": 3, "pacing": 2}
assert tuple(AXES_MAX) == AXES and sum(AXES_MAX.values()) == 35
ROLES_ORDER = ("hook", "hook", "douleur", "douleur", "preuve", "soulagement", "cta")


_ROLE_PREFIX = re.compile(r"^\s*(hook|accroche|douleur|preuve|soulagement|cta|appel à l'action)\s*\d?\s*:", re.I)


class InvalidLLMOutput(ValueError):
    """Sortie de modèle non conforme au schéma attendu."""


def validate_verdict(raw) -> dict:
    """Notes du QC vision : 9 axes entiers dans leurs bornes (audit C4, sondes 8 et 9)."""
    if not isinstance(raw, dict):
        raise InvalidLLMOutput("le verdict n'est pas un objet JSON")
    out, errors = dict(raw), []
    for axis, maximum in AXES_MAX.items():
        value = raw.get(axis)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{axis}={raw.get(axis)!r} n'est pas un entier")
        elif not 0 <= value <= maximum:
            errors.append(f"{axis}={value} hors de [0, {maximum}]")
        else:
            out[axis] = value
    for key in ("defauts", "fixes"):
        out[key] = [str(x) for x in raw[key]] if isinstance(raw.get(key), list) else []
    if errors:
        raise InvalidLLMOutput("; ".join(errors))
    return out


def validate_job(r) -> None:
    """Job de CONVERT : champs utilisés par l'audio et Remotion, 7 segments dans l'ordre (audit M4)."""
    if not isinstance(r, dict):
        raise InvalidLLMOutput("le job n'est pas un objet JSON")
    errors = [f"{k} vide ou absent" for k in ("titre", "hook", "cta")
              if not isinstance(r.get(k), str) or not r[k].strip()]
    narration = r.get("narration")
    if not isinstance(narration, list) or len(narration) != len(ROLES_ORDER):
        n = len(narration) if isinstance(narration, list) else 0
        errors.append(f"narration : {n} segments au lieu de {len(ROLES_ORDER)}")
    else:
        roles = tuple(s.get("role") if isinstance(s, dict) else None for s in narration)
        if roles != ROLES_ORDER:
            errors.append(f"rôles {list(roles)} au lieu de {list(ROLES_ORDER)}")
        if not all(isinstance(s, dict) and isinstance(s.get("texte"), str) and s["texte"].strip() for s in narration):
            errors.append("segment sans texte")
        else:
            prefixed = [s["texte"] for s in narration if _ROLE_PREFIX.match(s["texte"])]
            if prefixed:  # vu dans une vidéo du 16/09 : « Soulagement : l'argent » affiché à l'écran
                errors.append(f"nom de rôle recopié dans la narration : {prefixed[0][:40]!r}")
    if not isinstance(r.get("keywords"), list) or not all(isinstance(k, str) for k in r["keywords"]):
        errors.append("keywords doit être une liste de textes")
    if errors:
        raise InvalidLLMOutput("; ".join(errors))


def _flash(agent, task, sys, user, validate=None):
    kwargs = {"validate": validate} if validate is not None else {}
    return deepseek.call_json(agent, task, config.MODEL_FLASH,
                              [{"role": "system", "content": sys},
                               {"role": "user", "content": user}], **kwargs)


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
        if offer_id not in CATALOG:
            raise ValueError(f"offre inconnue : {offer_id!r} (catalogue : {', '.join(CATALOG)})")
        meta = CATALOG[offer_id]
        user = CONVERT.build_user(offer_id, angle, fixes)
        r = _flash("CONVERT", "redaction_job", CONVERT.SYS, user, validate=validate_job)
        try:
            validate_job(r)
        except InvalidLLMOutput as e:
            db.post("CONVERT", f"job invalide ({e}) : nouvelle tentative")
            r = _flash("CONVERT", "redaction_job", CONVERT.SYS,
                       user + f"\n\nTa réponse précédente était invalide : {e}. "
                              "Respecte exactement le schéma (7 segments, rôles dans l'ordre, textes non vides).",
                       validate=validate_job)
            validate_job(r)
        job = {
            "offer_id": offer_id, "langue": "fr", "duree_cible_s": 24,
            "titre": r.get("titre", ""), "hook": r.get("hook", ""),
            "douleur": r.get("douleur", ""), "preuve": r.get("preuve", ""),
            "soulagement": r.get("soulagement", ""), "cta": r.get("cta", ""),
            "prix": meta["prix"], "stripe_link": STRIPE_LINK, "sub_id": SUB_ID,
            "voix": {"moteur": "chatterbox", "nom": config.CHATTERBOX_VOICE},
            "keywords": r.get("keywords", []),
            "palette": PALETTE,
            "visuel": VISUELS[offer_id],
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
        """Production déterministe : chaque étape doit produire un artefact neuf, sinon StepError."""
        job_path = config.JOBS_DIR / f"{offer_id}.json"
        out = config.PROJECT_ROOT / "out" / offer_id
        rem = config.PROJECT_ROOT / "remotion" / "src" / "data"
        db.post("FORGE", f"démarrage du rendu de {offer_id} (@FORGE)")
        t0 = time.time()
        tools.make_audio(str(job_path), offer_id)
        tools.require_fresh([out / "audio" / "mix.wav", rem / "captions.ts", rem / "job.ts"], t0)
        db.post("FORGE", "audio + captions générés (Chatterbox)")
        cancel.checkpoint("avant le rendu")
        tools.remotion_render(offer_id)
        tools.require_fresh([out / "video.mp4"], t0)
        db.post("FORGE", "rendu Remotion terminé")
        cancel.checkpoint("avant le mux")
        tools.mux(offer_id)
        tools.require_fresh([out / "final.mp4"], t0)
        db.post("FORGE", "mux final.mp4 ok")
        metrics = tools.qc_metrics(offer_id)
        tools.require_fresh([out / "qc_metrics.json"], t0)
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
        verdict, error = None, None
        for attempt in (1, 2):
            try:
                verdict = validate_verdict(deepseek.vision(
                    "GROWTH", "qc_vision", frames, narration, prompt, validate=validate_verdict))
                break
            except ValueError as e:  # JSON introuvable ou notes hors schéma
                error = e
                db.post("GROWTH", f"QC vision invalide (essai {attempt}/2) : {str(e)[:160]}")
        if verdict is None:
            raise InvalidLLMOutput(f"QC vision invalide après 2 essais : {error}")
        total = sum(verdict[k] for k in AXES)
        humanite = verdict["humanite"]
        verdict["total_calcule"] = total
        verdict["ship_pass"] = total >= config.QC_SHIP_SCORE
        verdict["warm_pass"] = verdict["ship_pass"] and humanite >= config.QC_MIN_HUMANITE
        db.post("GROWTH", f"QC vision : {total}/35 · humanité {humanite}/5 · "
                          f"WARM {'PASS' if verdict['warm_pass'] else 'FAIL'}")
        db.decide("GROWTH", "qc_done", {"total": total, "humanite": humanite,
                                        "warm_pass": verdict["warm_pass"]})
        return verdict


class LEDGER:
    NAME = "LEDGER"

    @staticmethod
    def media_gate(metrics) -> list[tuple[str, str]]:
        """Contrôles ffmpeg bloquants : liste de (métrique, défaut). Vide si tout est conforme."""
        failures = []
        for key, rule in config.MEDIA_GATES.items():
            value = metrics.get(key)
            if value is None:
                failures.append((key, f"{key} non mesuré"))
            elif isinstance(rule, tuple):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    failures.append((key, f"{key}={value!r} illisible"))
                    continue
                if not rule[0] <= number <= rule[1]:
                    failures.append((key, f"{key} {number:g} hors de [{rule[0]:g}, {rule[1]:g}]"))
            elif value != rule:
                failures.append((key, f"{key} {value} au lieu de {rule}"))
        return failures

    @staticmethod
    def run(offer_id, metrics, growth):
        """Rubric /35 + contrôles ffmpeg + coût du cycle + go/no-go (calculés EN CODE, pas par le modèle)."""
        total = growth.get("total_calcule", 0)
        humanite = growth.get("humanite", 0)
        warm = growth.get("warm_pass", False)
        cost = _cycle_cost()
        failures = LEDGER.media_gate(metrics)
        budget_ok = cost <= config.CYCLE_BUDGET_USD
        go = bool(warm and budget_ok and not failures)
        fixes = []
        duration = metrics.get("duration_s")
        if any(k == "duration_s" for k, _ in failures) and isinstance(duration, (int, float)):
            low, high = config.MEDIA_GATES["duration_s"]
            fixes.append(f"Durée {duration:g} s hors de [{low:g}, {high:g}] s : "
                         f"{'raccourcir' if duration > high else 'allonger'} la narration (viser 20 à 28 s).")
        payload = {
            "offer_id": offer_id,
            "score": total, "humanite": humanite, "warm_pass": warm,
            "cost_usd": round(cost, 4), "budget_usd": config.CYCLE_BUDGET_USD, "budget_ok": budget_ok,
            "lufs": metrics.get("lufs_integrated"), "lra": metrics.get("lra_lu"),
            "satavg": metrics.get("satavg_mean"), "duration_s": duration,
            "media_ok": not failures, "blocking": [msg for _, msg in failures],
            "blocking_keys": [k for k, _ in failures], "fixes": fixes, "go": go,
        }
        db.record_metric(offer_id, total, humanite, "WARM_PASS" if warm else "WARM_FAIL", payload)
        db.decide("LEDGER", "ledger", payload)
        media = "média OK" if not failures else "média KO : " + " ; ".join(payload["blocking"])
        db.post("LEDGER", f"rubric {total}/35 · {media} · coût ${round(cost,4)} · go/no-go = {'GO' if go else 'NO-GO'}")
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
    def rule(ledger, iterations_left: int) -> dict:
        """Règle écrite d'ORBIT, appliquée en code (le banc du 16/09 : v4-pro ne la respectait pas)."""
        if not ledger.get("budget_ok", True):
            return {"decision": "stop", "message": "budget du cycle atteint"}
        if ledger.get("go"):
            return {"decision": "done", "message": f"{ledger.get('score')}/35, WARM_PASS, contrôles média conformes"}
        technical = [k for k in ledger.get("blocking_keys", []) if k not in config.MEDIA_FIXABLE_BY_SCRIPT]
        if technical:
            return {"decision": "stop", "message": "défaut technique non corrigeable par le script : "
                                                   + " ; ".join(ledger.get("blocking", []))}
        if iterations_left > 0:
            reasons = [] if ledger.get("warm_pass") else [f"{ledger.get('score')}/35 ou humanité {ledger.get('humanite')}/5 sous le seuil"]
            reasons += ledger.get("blocking", [])
            return {"decision": "iterate", "message": "itération : " + " ; ".join(reasons)}
        return {"decision": "stop", "message": "seuil non atteint et plus d'itération disponible"}

    @staticmethod
    def run(offer_id, ledger, iterations_left: int = 0):
        if config.ORBIT_DECISION == "llm":
            user = ORBIT.build_user(offer_id, ledger, db.human_messages(5))
            r = _pro("ORBIT", "arbitrage", ORBIT.SYS, user)
        else:
            r = ORBIT.rule(ledger, iterations_left)
        r["ledger"] = ledger
        db.post("ORBIT", f"arbitrage : {r.get('decision')} — {r.get('message')}")
        db.decide("ORBIT", "arbitrage", r)
        return r

