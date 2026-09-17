"""Runtime général des agents : registre d'outils partagés + boucle ReAct + mémoire.

C'est ici que les agents deviennent « libres » : ils choisissent leurs actions
via le LLM (penser → agir → observer) parmi un registre d'outils, et apprennent
via la mémoire. Ajouter une capacité (prospection, etc.) = ajouter un OUTIL,
pas un nouvel agent ni un chantier.
"""
from __future__ import annotations

import contextvars
import json

from octopus.journal import with_run

from . import cancel, db, deepseek, web_guard

_ROLE: contextvars.ContextVar[str] = contextvars.ContextVar("podalux_role", default="RUNTIME")

MODEL = deepseek.config.MODEL_FLASH


# --- Outils partagés ---
def _search(args):
    from .search import web_search
    return web_search(args["query"], 6)


def _browse(args):
    """Web public : contexte éphémère sans cookies. Comptes : profil connecté, visible, lecture seule."""
    from . import browser
    state = web_guard.current()
    url = str(args.get("url", ""))
    kind = web_guard.check(url, state)
    account = kind == web_guard.ACCOUNT
    b = browser.new_browser(headless=not account, account=account, guard=lambda u: web_guard.allowed(u, state))
    try:
        try:
            b.goto(url)
        except Exception as e:
            if b.blocked:
                raise web_guard.BrowseRefused(f"redirection refusée vers {b.blocked[-1][:120]}") from e
            raise
        final = b.url()
        final_kind = web_guard.classify(final)
        web_guard.record(final, final_kind, state)
        source = "compte connecté (lecture seule)" if final_kind == web_guard.ACCOUNT else web_guard.UNTRUSTED_NOTE
        return {"url": final, "source": source, "texte": b.snapshot()[:1500],
                "vision": b.see(agent=_ROLE.get())["description"]}
    finally:
        b.stop()


def _render_offer(args):
    from .cycle import run_cycle
    r = run_cycle(offer_id=args["offer_id"], max_iterations=1)
    return {"score": r["ledger"].get("score"), "decision": r["orbit"].get("decision"),
            "iterations": r["iterations"]}


def _qc(args):
    from . import tools
    return tools.qc_metrics(args["offer_id"])


def _ask_human(args):
    # En mode autonome (CLI), pas d'humain présent → on ne bloque pas 5 min.
    ans = db.ask_human("RUNTIME", "agent_question", args["question"], timeout_s=5)
    if ans is None:
        return "question posée à l'humain (aucune réponse immédiate en mode autonome)"
    return f"réponse humaine : {ans}"


def _publish(args):
    from .publish import publish
    return publish(args["offer_id"], dry_run=True)


def _send_message(args):
    """Prospection : envoi d'un message (dry-run — rien ne sort sans validation)."""
    plan = {"platform": args["platform"], "recipient": args["recipient"],
            "text": args["text"]}
    db.decide("GROWTH", "send_message_plan", plan)
    db.post("GROWTH", f"prospection (dry-run) : {args['platform']} → {args['recipient']}")
    return {"dry_run": True, "plan": plan, "note": "message non envoyé (dry-run)"}


def _remember(args):
    # L'agent qui écrit est celui qui tourne, pas celui qu'il nomme (audit, risque 18).
    role = _ROLE.get()
    db.remember(role, args["key"], args["value"])
    return f"mémorisé pour {role} sous la clé {db.norm_key(args['key'])}"


def _recall(args):
    agent = args.get("agent") or _ROLE.get()
    value = db.recall(agent, args["key"])
    if value is not None:
        return value
    keys = [r["key"] for r in db.memory_keys(agent, limit=30)]
    return {"trouve": False, "agent": str(agent).upper(), "cles_connues": keys}


TOOLS = {
    "search": {"desc": "recherche web (liens)", "params": {"query": "str"}, "fn": _search},
    "browse": {"desc": "ouvre une page dans TON Chrome réel (comptes Stripe/Reddit/X/Fiverr/YouTube connectés) et la décrit", "params": {"url": "str"}, "fn": _browse},
    "render_offer": {"desc": "produit la vidéo complète d'une offre", "params": {"offer_id": "str"}, "fn": _render_offer},
    "qc": {"desc": "métriques ffmpeg d'une offre", "params": {"offer_id": "str"}, "fn": _qc},
    "ask_human": {"desc": "demande confirmation/info à l'humain", "params": {"question": "str"}, "fn": _ask_human},
    "publish": {"desc": "plan de publication (dry-run)", "params": {"offer_id": "str"}, "fn": _publish},
    "send_message": {"desc": "prospection : envoie un message (dry-run)", "params": {"platform": "str", "recipient": "str", "text": "str"}, "fn": _send_message},
    "remember": {"desc": "mémorise un apprentissage", "params": {"agent": "str", "key": "str", "value": "str"}, "fn": _remember},
    "recall": {"desc": "retrouve un apprentissage", "params": {"agent": "str", "key": "str"}, "fn": _recall},
}


def tools_desc() -> str:
    return "\n".join(
        f"- {name}({', '.join(spec['params'])}) : {spec['desc']}"
        for name, spec in TOOLS.items()
    )


ROLES = {
    "SOUT": "Recherche et veille : utilise search/browse pour trouver des infos utiles.",
    "CONVERT": "Monétisation : rédige offres, prix, CTA, contenu.",
    "FORGE": "Production : produit les vidéos (render_offer).",
    "GROWTH": "Qualité/distribution : juge (qc), publie (publish), prospecte (send_message).",
    "LEDGER": "Data/finance : mesure (qc), suit les coûts, mémorise (remember).",
    "ORBIT": "CEO : planifie, arbitre, décide.",
}


def _budget_exhausted() -> bool:
    """Budget du run OCTOPUS en cours (et de ses parents). Coupe-circuit : contrôle historique."""
    import octopus
    if not octopus.enabled():
        return db.total_cost() > deepseek.config.CYCLE_BUDGET_USD
    from octopus import journal
    run = journal.current_run()
    return bool(run and journal.budget_exhausted(run))


def build_prompts(role: str, goal: str, conversational: bool = False) -> tuple[str, str, str]:
    """(prompt système, premier message, libellé de fin) de la boucle ReAct."""
    role_desc = ROLES.get(role, "")
    if conversational:
        system = (
            f"Tu es l'agent {role} du groupe Podalux. {role_desc} "
            f"Un humain t'a écrit. Réponds-lui DIRECTEMENT, en français, de façon utile "
            f"et conversationnelle. Tu peux utiliser un outil (search, browse, recall) "
            f"si besoin, mais ta priorité est de répondre à sa demande.\n\n"
            f"IMPORTANT : tu es connecté à tes comptes (Stripe, Reddit, X, Fiverr, YouTube…) "
            f"via l'outil `browse`, qui ouvre les pages dans TON Chrome réel. Pour vérifier "
            f"un accès, utilise `browse` sur la page concernée.\n\n"
            f"Outils disponibles :\n{tools_desc()}\n\n"
            "Réponds TOUJOURS en JSON : soit {\"tool\": \"<nom>\", \"args\": {...}} pour agir, "
            "soit {\"final\": \"<ta réponse à l'humain>\"}."
        )
        first_user = f"Message de l'humain : {goal}"
        done_label = "réponse"
    else:
        system = (
            f"Tu es l'agent {role} du groupe Podalux. {role_desc} "
            f"Poursuis l'objectif en utilisant "
            f"les outils disponibles. À chaque étape, choisis UNE action. "
            f"Utilise `remember` pour stocker tes apprentissages et `recall` pour les relire.\n\n"
            f"Outils disponibles :\n{tools_desc()}\n\n"
            "Réponds TOUJOURS en JSON : soit {\"tool\": \"<nom>\", \"args\": {...}} pour agir, "
            "soit {\"final\": \"<réponse>\"} quand l'objectif est atteint."
        )
        first_user = f"Objectif : {goal}"
        done_label = "objectif atteint"
    return system, first_user, done_label


@with_run("podalux", "agent", budget_usd=deepseek.config.CYCLE_BUDGET_USD)
def run_agent(role: str, goal: str, max_steps: int = 10,
              conversational: bool = False) -> dict:
    """Un agent (rôle) poursuit un objectif librement via la boucle ReAct.

    `conversational=True` → l'agent répond à un message humain (pas un objectif).
    """
    token = _ROLE.set(role)
    try:
        with cancel.scope(), web_guard.session():
            return _run_agent(role, goal, max_steps, conversational)
    finally:
        _ROLE.reset(token)


def _run_agent(role: str, goal: str, max_steps: int, conversational: bool) -> dict:
    system, first_user, done_label = build_prompts(role, goal, conversational)
    context = [{"role": "system", "content": system},
               {"role": "user", "content": first_user}]
    steps = []
    last_sig = None
    repeat = 0
    for i in range(max_steps):
        if cancel.requested():
            db.post(role, "arrêt demandé par l'humain — fin de l'agent")
            return {"role": role, "steps": steps, "final": "(arrêt demandé)"}
        # Garde-budget : on arrête l'agent si le budget du run est atteint.
        if _budget_exhausted():
            db.post(role, "budget dépassé — arrêt du run")
            return {"role": role, "steps": steps, "final": "(budget dépassé)"}
        try:
            r = deepseek.call_json(role, "action", MODEL, context + [
                {"role": "user", "content": "Choisis ta prochaine action (JSON)."}])
        except Exception as e:
            steps.append({"step": i + 1, "tool": "erreur", "result": str(e)})
            break
        if "final" in r:
            db.post(role, f"{done_label} : {str(r['final'])[:120]}")
            return {"role": role, "steps": steps, "final": r["final"]}
        tool = r.get("tool")
        args = r.get("args") or {}
        if tool not in TOOLS:
            context.append({"role": "user", "content": f"outil inconnu : {tool}. Disponibles : {list(TOOLS)}"})
            steps.append({"step": i + 1, "tool": tool, "result": "inconnu"})
            continue
        try:
            result = TOOLS[tool]["fn"](args)
            result_str = json.dumps(result, ensure_ascii=False)[:1500]
        except cancel.Cancelled:
            db.post(role, "arrêt demandé par l'humain — fin de l'agent")
            return {"role": role, "steps": steps, "final": "(arrêt demandé)"}
        except Exception as e:
            result_str = f"erreur : {e}"
        # garde anti-boucle : si même action + même résultat répétés, demander de changer
        sig = f"{tool}:{result_str}"
        repeat = repeat + 1 if sig == last_sig else 0
        last_sig = sig
        if repeat >= 2:
            context.append({"role": "user", "content":
                            "Tu répètes la même action sans progrès (ex. CAPTCHA/échec). "
                            "Change d'approche, demande à l'humain (ask_human), ou réponds avec « final »."})
            repeat = 0
        db.post(role, f"action {tool} {json.dumps(args, ensure_ascii=False)[:90]}")
        context.append({"role": "user", "content": f"Résultat de {tool} : {result_str}"})
        steps.append({"step": i + 1, "tool": tool, "result": result_str[:200]})
    return {"role": role, "steps": steps, "final": "(max steps atteint)"}


@with_run("podalux", "mission", budget_usd=deepseek.config.CYCLE_BUDGET_USD)
def run_mission(goal: str, max_steps_per_agent: int = 8) -> dict:
    """ORBIT planifie puis délègue aux rôles (multi-agents via le runtime)."""
    with cancel.scope(), web_guard.session():
        return _run_mission(goal, max_steps_per_agent)


def _run_mission(goal: str, max_steps_per_agent: int) -> dict:
    pro = deepseek.config.MODEL_PRO
    if cancel.requested():
        return {"plan": [], "results": [], "rapport": "(arrêt demandé)"}
    plan_sys = (
        "Tu es ORBIT, le CEO. Décompose l'objectif en 2 à 5 sous-tâches, chacune assignée "
        f"à UN rôle parmi {list(ROLES)}. Réponds en JSON : "
        '{"tasks":[{"role":"...","task":"..."}]}'
    )
    plan = deepseek.call_json("ORBIT", "planification", pro,
                              [{"role": "system", "content": plan_sys},
                               {"role": "user", "content": goal}],
                              reasoning="high")
    tasks = plan.get("tasks", [])
    db.post("ORBIT", f"mission : {goal[:70]} → {len(tasks)} sous-tâches")

    results = []
    for t in tasks:
        if cancel.requested():
            db.post("ORBIT", "mission arrêtée par l'humain")
            return {"plan": tasks, "results": results, "rapport": "(arrêt demandé)"}
        role = t.get("role", "ORBIT")
        if role not in ROLES:
            role = "ORBIT"
        task = t.get("task", "")
        original = task
        # Contexte cumulatif : chaque sous-tâche reçoit les résultats des précédentes.
        # On stocke la tâche ORIGINALE (sans le contexte) pour éviter l'explosion du prompt.
        if results:
            prev = "\n".join(f"- [{r['role']}] {r['task']} → {r['final']}" for r in results)
            task = f"{task}\n\nContexte des sous-tâches précédentes :\n{prev}"
        db.post(role, f"sous-tâche : {original[:80]}")
        r = run_agent(role, task, max_steps=max_steps_per_agent)
        results.append({"role": role, "task": original,
                        "final": r.get("final"), "steps": r.get("steps")})

    if cancel.requested():
        db.post("ORBIT", "mission arrêtée par l'humain avant la synthèse")
        return {"plan": tasks, "results": results, "rapport": "(arrêt demandé)"}
    syn_sys = ("Tu es ORBIT. Synthétise les résultats des sous-tâches en un rapport final "
               "concis. Réponds en JSON : {\"rapport\":\"...\"}")
    syn = deepseek.call_json("ORBIT", "synthese", pro,
                             [{"role": "system", "content": syn_sys},
                              {"role": "user", "content": json.dumps(results, ensure_ascii=False)}],
                             reasoning="high", max_tokens=4000)
    rapport = syn.get("rapport", "")
    db.decide("ORBIT", "mission_done", {"rapport": rapport})
    db.post("ORBIT", f"mission terminée : {rapport[:80]}")
    return {"plan": tasks, "results": results, "rapport": rapport}
