"""Runtime général des agents : registre d'outils partagés + boucle ReAct + mémoire.

C'est ici que les agents deviennent « libres » : ils choisissent leurs actions
via le LLM (penser → agir → observer) parmi un registre d'outils, et apprennent
via la mémoire. Ajouter une capacité (prospection, etc.) = ajouter un OUTIL,
pas un nouvel agent ni un chantier.
"""
from __future__ import annotations

import contextvars
import json
import re
import time
import unicodedata
from datetime import date
from contextlib import contextmanager
from urllib.parse import urlparse

from octopus import journal, llm

from . import cancel, db, deepseek, web_guard

_ROLE: contextvars.ContextVar[str] = contextvars.ContextVar("podalux_role", default="RUNTIME")
_SEARCHES: contextvars.ContextVar[dict | None] = contextvars.ContextVar("podalux_searches", default=None)


@contextmanager
def _search_cache():
    """Recherches déjà faites dans l'exécution (un agent, ou toute une mission)."""
    if _SEARCHES.get() is not None:
        yield _SEARCHES.get()
        return
    token = _SEARCHES.set({})
    try:
        yield _SEARCHES.get()
    finally:
        _SEARCHES.reset(token)


def query_key(query: str) -> str:
    """Requêtes équivalentes : casse, accents, ordre des mots, pluriels et mots courts ignorés."""
    text = unicodedata.normalize("NFKD", str(query)).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted({w.rstrip("sx") for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2}))

MODEL = deepseek.config.MODEL_FLASH


# --- Outils partagés ---
def _search(args):
    from .search import effective_query, web_search
    query = str(args.get("query", ""))
    site = str(args.get("site") or "").strip() or None
    effective = effective_query(query, site)
    cache, key = _SEARCHES.get(), query_key(effective)
    if cache is not None and key in cache:  # 21 recherches quasi identiques dans un run du 16/09 (audit M2)
        previous = cache[key]
        return {"deja_cherche": True, "requete_precedente": previous["effective_query"],
                "note": "Recherche équivalente déjà faite : même résultat. Change d'angle, ouvre un lien "
                        "avec browse, ou conclus avec « final ».",
                "resultat": previous["result"][:600]}
    result = web_search(query, 6, site=site)
    if cache is not None:
        cache[key] = {
            "query": query,
            "site": site,
            "effective_query": effective,
            "result": result,
        }
    return result


def _search_result_urls(value) -> list[str]:
    """URLs HTTP(S) réellement présentes dans le résultat d'un search, ordre conservé."""
    parts = []

    def collect(item):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child)

    collect(value)
    urls = []
    for url in re.findall(r"https?://[^\s\]\[<>()\"']+", "\n".join(parts)):
        clean = url.rstrip(".,;:")
        if clean not in urls:
            urls.append(clean)
    return urls


_SEARCH_RELEVANCE_STOPWORDS = {
    "avec", "dans", "des", "les", "pour", "sur", "une", "un", "aux", "par", "qui",
    "que", "quoi", "plus", "moins", "france", "francais", "francaise", "francaises",
    "probleme", "problemes", "besoin", "besoins", "client", "clients", "economique",
    "economiques", "rapport", "source", "officiel", "officielle", "officielles",
}
_LOW_EVIDENCE_HOSTS = {
    "fr.wikipedia.org",
    "www.larousse.fr",
    "dictionnaire.lerobert.com",
    "www.le-dictionnaire.com",
    "www.soutien67.fr",
}
_EVIDENCE_HOST_SUFFIXES = (
    ".gouv.fr",
    ".gov",
    ".europa.eu",
)
_EVIDENCE_HOSTS = {
    "www.insee.fr",
    "insee.fr",
    "www.banque-france.fr",
    "banque-france.fr",
    "www.bpifrance.fr",
    "bpifrance.fr",
    "www.service-public.fr",
    "service-public.fr",
    "www.urssaf.fr",
    "urssaf.fr",
    "www.senat.fr",
    "senat.fr",
    "www.assemblee-nationale.fr",
    "assemblee-nationale.fr",
    "www.vie-publique.fr",
    "vie-publique.fr",
}


def _flatten_search_text(value) -> str:
    parts = []

    def collect(item):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child)

    collect(value)
    return "\n".join(parts)


def _search_result_candidates(value) -> list[dict]:
    """Parse le format texte de web_search en candidats URL + contexte compact."""
    text = _flatten_search_text(value)
    lines = text.splitlines()
    candidates = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped.startswith("- "):
            i += 1
            continue
        title = stripped[2:].strip()
        context = []
        url = ""
        j = i + 1
        while j < len(lines):
            nxt = lines[j].strip()
            if nxt.startswith("- "):
                break
            match = re.search(r"https?://[^\s\]\[<>()\"']+", nxt)
            if match and not url:
                url = match.group(0).rstrip(".,;:")
            elif nxt and not nxt.lower().startswith(("actualités", "encyclopédie", "pistes google")):
                context.append(nxt)
            j += 1
        if url and url not in {item["url"] for item in candidates}:
            candidates.append({"url": url, "title": title, "context": " ".join(context)})
        i = max(j, i + 1)
    if not candidates:
        return [{"url": url, "title": "", "context": ""} for url in _search_result_urls(value)]
    return candidates


def _norm_words(value: str) -> set[str]:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2}


def _search_relevance_tokens(query: str) -> set[str]:
    words = _norm_words(re.sub(r"\b(?:site|source):\S+", " ", str(query), flags=re.I))
    return {
        word for word in words
        if word not in _SEARCH_RELEVANCE_STOPWORDS and not re.fullmatch(r"20\d{2}", word)
    }


def _host_matches_site(host: str, site: str) -> bool:
    host = host.lower().removeprefix("www.")
    site = site.lower().removeprefix("www.").strip(".")
    return host == site or host.endswith("." + site)


def _select_search_browse_candidate(value, query: str, selector: str = "first") -> dict | None:
    """Choisit uniquement parmi les URLs effectivement retournées par SEARCH."""
    candidates = _search_result_candidates(value)
    if not candidates:
        return None
    if selector == "first":
        return {"url": candidates[0]["url"], "selector": "first", "rank": 1, "score": None}
    if selector != "evidence_relevance":
        raise ValueError(f"search_browse_selector inconnu : {selector}")

    required_sites = [
        site.rstrip(".,;:)")
        for site in re.findall(r"\bsite:([^\s]+)", str(query), flags=re.I)
    ]
    tokens = _search_relevance_tokens(query)
    scored = []
    for rank, candidate in enumerate(candidates, start=1):
        url = candidate["url"]
        host = urlparse(url).netloc.lower()
        title_words = _norm_words(candidate.get("title", ""))
        context_words = _norm_words(candidate.get("context", ""))
        title_overlap = len(tokens & title_words)
        context_overlap = len(tokens & context_words)
        score = title_overlap * 4 + context_overlap

        site_match = None
        if required_sites:
            site_match = any(_host_matches_site(host, site) for site in required_sites)
            score += 100 if site_match else -100

        low_evidence = host in _LOW_EVIDENCE_HOSTS
        if low_evidence:
            score -= 20
        official = host in _EVIDENCE_HOSTS or any(host.endswith(suffix) for suffix in _EVIDENCE_HOST_SUFFIXES)
        if official:
            score += 6

        scored.append({
            "url": url,
            "selector": selector,
            "rank": rank,
            "score": score,
            "title_overlap": title_overlap,
            "context_overlap": context_overlap,
            "official": official,
            "low_evidence": low_evidence,
            "site_match": site_match,
        })

    best = max(scored, key=lambda item: (item["score"], -item["rank"]))
    # Si un opérateur site: est demandé, ne viole jamais explicitement cette contrainte.
    if required_sites and not best["site_match"]:
        return None
    # Évite les dictionnaires/Wikipedia ou collisions lexicales sans signal de pertinence.
    if best["score"] <= 0:
        return None
    return best


_BROWSE_BLOCK_MARKERS = (
    "performing security verification",
    "verify you are not a bot",
    "security service to protect against malicious bots",
    "just a moment",
    "access denied",
    "403 error",
    "request blocked",
    "captcha",
    "chrome-error://",
)


def _browse_result_meta(value) -> dict | None:
    """Métadonnées compactes issues de l'objet de page structuré, jamais d'un JSON tronqué."""
    if not isinstance(value, dict):
        return None
    page = value.get("page") if isinstance(value.get("page"), dict) else value
    url = str(page.get("final_url") or value.get("url") or "").strip()
    text = str(page.get("main_text") or value.get("texte") or "")
    lowered = f"{url}\n{text}".lower()
    return {
        "url": url,
        "text_chars": int(page.get("text_chars") or len(text)),
        "blocked": bool(page.get("blocked")) or any(marker in lowered for marker in _BROWSE_BLOCK_MARKERS),
        "vision_error": bool(value.get("vision_error")),
        "extraction_method": str(page.get("extraction_method") or ""),
        "rendered": bool(page.get("rendered")),
        "http_status": page.get("http_status"),
        "error": page.get("error"),
    }


def _tool_result_view(tool: str, result, max_chars: int | None = None) -> str:
    """Vue courte destinée au prompt ; l'objet structuré source reste intact à côté."""
    if tool == "browse" and isinstance(result, dict):
        page = result.get("page") if isinstance(result.get("page"), dict) else result
        text = str(page.get("main_text") or result.get("texte") or "")
        limit = 6000 if max_chars is None else max(200, int(max_chars))
        payload = {
            "url": str(page.get("final_url") or result.get("url") or ""),
            "source": str(result.get("source") or ""),
            "title": str(page.get("title") or ""),
            "fetched_at": page.get("fetched_at"),
            "http_status": page.get("http_status"),
            "extraction_method": page.get("extraction_method"),
            "rendered": bool(page.get("rendered")),
            "blocked": bool(page.get("blocked")),
            "error": page.get("error"),
            "text_chars": int(page.get("text_chars") or len(text)),
            "texte": text[:limit],
        }
        return json.dumps(payload, ensure_ascii=False)

    raw = json.dumps(result, ensure_ascii=False)
    limit = max_chars if max_chars is not None else (6000 if tool == "search" else 1500)
    return raw[:int(limit)]


def _mission_prompt_results(results: list[dict]) -> list[dict]:
    """Projection bornée pour la synthèse : jamais les payloads structurés complets."""
    projected = []
    for subtask in results or []:
        steps = []
        for step in subtask.get("steps") or []:
            item = {
                "step": step.get("step"),
                "tool": step.get("tool"),
                "result": str(step.get("result") or ""),
            }
            if isinstance(step.get("args"), dict):
                item["args"] = dict(step["args"])
            if isinstance(step.get("browse_meta"), dict):
                item["browse_meta"] = dict(step["browse_meta"])
            if isinstance(step.get("result_urls"), list):
                item["result_urls"] = list(step["result_urls"])
            steps.append(item)
        projected.append({
            "role": subtask.get("role"),
            "task": subtask.get("task"),
            "final": subtask.get("final"),
            "steps": steps,
        })
    return projected


def _browser_request_allowed(url: str, state, *, account_context: bool) -> bool:
    """Isole les sous-requêtes publiques des vrais comptes connectés.

    Un navigateur public est éphémère et sans cookies : une pub/embed vers un domaine classé
    ACCOUNT ne doit ni être chargée dans ce contexte ni marquer toute la mission account_read.
    Le navigateur connecté conserve, lui, le garde-fou historique qui taint immédiatement la session.
    """
    if account_context:
        return web_guard.allowed(url, state)
    try:
        return web_guard.check(url, state) == web_guard.PUBLIC
    except web_guard.BrowseRefused:
        return False


def _browse(args):
    """Public : HTTP-first + extraction ; comptes : Chromium connecté, lecture seule."""
    from . import browser
    state = web_guard.current()
    url = str(args.get("url", ""))
    kind = web_guard.check(url, state)
    account = kind == web_guard.ACCOUNT

    if not account:
        record = browser.acquire_public_page(
            url,
            guard=lambda u: _browser_request_allowed(u, state, account_context=False),
        ).as_dict()
        final = str(record.get("final_url") or url)
        if (
            not record.get("blocked")
            and not record.get("error")
            and int(record.get("text_chars") or 0) >= browser.PUBLIC_MIN_TEXT_CHARS
        ):
            web_guard.record(final, web_guard.PUBLIC, state)
        return {
            "url": final,
            "source": web_guard.UNTRUSTED_NOTE,
            "texte": str(record.get("main_text") or ""),
            "page": record,
            "vision": None,
            "vision_error": None,
        }

    b = browser.new_browser(
        headless=False,
        account=True,
        guard=lambda u: _browser_request_allowed(u, state, account_context=True),
    )
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
        seen = b.see(agent=_ROLE.get())
        return {
            "url": final,
            "source": "compte connecté (lecture seule)",
            "texte": b.snapshot(6000),
            "vision": seen["description"],
            "vision_error": seen.get("vision_error"),
        }
    finally:
        b.stop()


def _render_offer(args):
    run = journal.current_run()
    if run is not None and run.business != DEFAULT_BUSINESS:
        # Les offres et le rendu (potentiellement payant, RunPod) appartiennent à Podalux.
        return {"refuse": True, "note": f"render_offer produit une offre Podalux : indisponible pour le business "
                                        f"{run.business}. Propose l'action à l'humain au lieu de l'exécuter."}
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


# --- Boucle économique (génériques, tout business) ---
def _run_business() -> str:
    run = journal.current_run()
    return run.business if run else DEFAULT_BUSINESS


def _economy_status(args):
    from octopus import economy
    return economy.status(_run_business())


def _seen_this_session(source: str) -> bool:
    """La source a-t-elle réellement été ouverte (browse) ou renvoyée par une recherche dans cette exécution ?"""
    if not source:
        return False
    if source in web_guard.current().visited:
        return True
    return any(source in str(entry.get("result", "")) for entry in (_SEARCHES.get() or {}).values())


def _ids(args, keys) -> dict:
    return {k: int(args[k]) for k in keys if args.get(k) not in (None, "")}


def _record_observation(args):
    """Observé seulement si la source a été consultée dans cette exécution ; sinon non vérifié (source gardée)."""
    from octopus import strategy
    source = str(args.get("source_ref") or "").strip()
    observed = _seen_this_session(source)
    fields = {k: args[k] for k in ("metric", "unit") if args.get(k) not in (None, "")}
    fields.update(_ids(args, ("experiment_id", "channel_id")))
    if args.get("value") not in (None, ""):
        fields["value"] = float(args["value"])
    evidence_id = strategy.create(
        "evidence", _run_business(), str(args.get("summary") or args.get("observation", ""))[:200],
        created_by=f"agent:{_ROLE.get()}", nature="observed" if observed else "unverified",
        source_type=str(args.get("source_type") or "agent"), source_ref=source or None,
        captured_at=time.time() if observed else None, observation=str(args.get("observation", "")), **fields)
    return {"evidence_id": evidence_id, "nature": "observed" if observed else "unverified",
            "note": None if observed else "source non consultée dans cette exécution : donnée non vérifiée"}


def _propose_experiment(args):
    """Crée (ou réutilise) objectif et hypothèse, puis une expérience mesurable en statut planned."""
    from octopus import strategy
    business, by = _run_business(), f"agent:{_ROLE.get()}"
    objective_id = args.get("objective_id") or strategy.create(
        "objective", business, str(args["objective"])[:200], created_by=by, statement=str(args["objective"]))
    hypothesis_id = args.get("hypothesis_id") or strategy.create(
        "hypothesis", business, str(args["hypothesis"])[:200], created_by=by, parent_id=int(objective_id),
        statement=str(args["hypothesis"]), stop_criterion=args.get("stop_criterion"))
    fields = {k: args[k] for k in ("metric", "budget_currency", "expected_result") if args.get(k)}
    fields.update(_ids(args, ("channel_id",)))
    for key in ("target_value", "stop_value", "budget_limit"):
        if args.get(key) not in (None, ""):
            fields[key] = float(args[key])
    if args.get("deadline_days"):
        fields["deadline_at"] = time.time() + float(args["deadline_days"]) * 86400
    experiment_id = strategy.create("experiment", business, str(args.get("summary") or args["action"])[:200],
                                    created_by=by, parent_id=int(hypothesis_id), action=str(args["action"]), **fields)
    return {"objective_id": int(objective_id), "hypothesis_id": int(hypothesis_id), "experiment_id": experiment_id,
            "status": "planned"}


def _start_experiment(args):
    from octopus import strategy
    strategy.transition("experiment", int(args["experiment_id"]), _run_business(), "running",
                        actor=f"agent:{_ROLE.get()}")
    return {"experiment_id": int(args["experiment_id"]), "status": "running"}


def _register_channel(args):
    from octopus import economy
    caps = args.get("capabilities") or []
    if isinstance(caps, str):
        caps = [c.strip() for c in caps.split(",")]
    source = str(args.get("source_ref") or "").strip()
    channel_id = economy.add_channel(_run_business(), str(args["kind"]), str(args["name"]),
                                     created_by=f"agent:{_ROLE.get()}", locator=args.get("locator"), capabilities=caps,
                                     nature="observed" if _seen_this_session(source) else "unverified",
                                     source_ref=source or None,
                                     notes=args.get("notes"))
    return {"channel_id": channel_id, "access": "none", "note": "accès à qualifier avant toute action"}


def _resources_status(args):
    """Inventaire reel : ce qui existe, ce qui repond, ce qui manque. Aucune consigne d'usage."""
    from octopus import resources
    rows = resources.list_resources(capability=args.get("capability"), state=args.get("state"))
    return {"overview": resources.overview(),
            "resources": [{k: r[k] for k in ("key", "kind", "label", "state", "access", "capabilities",
                                             "needs", "last_check_detail")} for r in rows[:40]]}


def _request_resource(args):
    """Frontiere humaine : demande la creation, la connexion ou l'autorisation d'une ressource."""
    from octopus import resources
    task_id = resources.request(str(args["key"]), str(args.get("need") or "create"), str(args["question"]),
                                created_by=f"agent:{_ROLE.get()}", business=_run_business(),
                                label=args.get("label"), kind=str(args.get("kind") or "autre"))
    return {"task_id": task_id, "status": "en attente de l'humain",
            "note": "la tache reprend seule apres la reponse : la sonde est repassee"}


def _open_business(args):
    """Ouvre une nouvelle activité : un business n'existe que par ses objets (objectif, grand livre...)."""
    import re as _re
    from octopus import strategy
    ascii_id = unicodedata.normalize("NFKD", str(args["business_id"])).encode("ascii", "ignore").decode()
    business_id = _re.sub(r"[^a-z0-9_]+", "_", ascii_id.strip().lower()).strip("_")[:64]
    if not business_id:
        raise ValueError("business_id vide")
    if any(r["business"] == business_id for r in strategy.portfolio()):
        return {"business_id": business_id, "status": "exists"}
    objective_id = strategy.create("objective", business_id, str(args.get("name") or business_id)[:200],
                                   created_by=f"agent:{_ROLE.get()}", statement=str(args["thesis"]),
                                   success_criteria="cash net observé positif")
    return {"business_id": business_id, "objective_id": objective_id, "status": "opened",
            "note": "business ouvert en brouillon ; ses missions tournent sous ce business_id"}


def _act_on_channel(args):
    """Action réelle sur un canal : exécutée seulement si l'humain a ouvert l'accès et qu'un exécuteur existe."""
    from octopus import actions
    payload = args.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload) if payload.strip().startswith("{") else {"text": payload}
    return actions.propose(_run_business(), int(args["channel_id"]), str(args["action"]), payload,
                           requested_by=f"agent:{_ROLE.get()}",
                           experiment_id=int(args["experiment_id"]) if args.get("experiment_id") else None,
                           spend_amount=float(args["spend_amount"]) if args.get("spend_amount") else None,
                           spend_currency=args.get("spend_currency"),
                           idempotency_key=args.get("idempotency_key"))


def _request_spend(args):
    """Demande d'autorisation : ne paie rien. Refusée sans enveloppe accordée par l'humain ou une politique."""
    from octopus import economy
    return economy.authorize_spend(_run_business(), float(args["amount"]), str(args["currency"]), str(args["purpose"]),
                                   requested_by=f"agent:{_ROLE.get()}",
                                   experiment_id=int(args["experiment_id"]) if args.get("experiment_id") else None)


TOOLS = {
    "search": {"desc": "recherche web (liens) ; site est un domaine optionnel réellement appliqué, ex. bpifrance.fr", "params": {"query": "str", "site": "str?"}, "fn": _search},
    "browse": {"desc": "ouvre une page dans TON Chrome réel (comptes Stripe/Reddit/X/Fiverr/YouTube connectés) et la décrit", "params": {"url": "str"}, "fn": _browse},
    "render_offer": {"desc": "produit la vidéo complète d'une offre", "params": {"offer_id": "str"}, "fn": _render_offer},
    "qc": {"desc": "métriques ffmpeg d'une offre", "params": {"offer_id": "str"}, "fn": _qc},
    "ask_human": {"desc": "demande confirmation/info à l'humain", "params": {"question": "str"}, "fn": _ask_human},
    "publish": {"desc": "plan de publication (dry-run)", "params": {"offer_id": "str"}, "fn": _publish},
    "send_message": {"desc": "prospection : envoie un message (dry-run)", "params": {"platform": "str", "recipient": "str", "text": "str"}, "fn": _send_message},
    "remember": {"desc": "mémorise un apprentissage", "params": {"agent": "str?", "key": "str", "value": "str"}, "fn": _remember},
    "recall": {"desc": "retrouve un apprentissage", "params": {"agent": "str?", "key": "str"}, "fn": _recall},
    "economy_status": {"desc": "état économique réel du business : cash observé par devise, coûts LLM calculés, enveloppes de dépense, canaux, expériences en cours et leur verdict", "params": {}, "fn": _economy_status},
    "record_observation": {"desc": "enregistre un fait constaté (avec source_ref consultable = observé, sinon non vérifié), éventuellement une valeur mesurée pour une expérience", "params": {"summary": "str", "observation": "str", "source_ref": "str?", "metric": "str?", "value": "float?", "unit": "str?", "experiment_id": "int?", "channel_id": "int?"}, "fn": _record_observation},
    "propose_experiment": {"desc": "propose une expérience mesurable (objectif/hypothèse créés si absents) ; metric peut être cash_net:DEVISE", "params": {"objective": "str|objective_id", "hypothesis": "str|hypothesis_id", "action": "str", "metric": "str", "target_value": "float", "stop_value": "float?", "deadline_days": "float?", "budget_limit": "float?", "budget_currency": "str?", "channel_id": "int?"}, "fn": _propose_experiment},
    "start_experiment": {"desc": "passe une expérience planned en running", "params": {"experiment_id": "int"}, "fn": _start_experiment},
    "register_channel": {"desc": "enregistre un canal économique découvert (site, marketplace, réseau, email, API, publicité...)", "params": {"kind": "str", "name": "str", "locator": "str?", "capabilities": "list", "source_ref": "str?", "notes": "str?"}, "fn": _register_channel},
    "act_on_channel": {"desc": "agit réellement sur un canal (publier, vendre, écrire...) ; bloqué et tracé si l'accès, l'exécuteur, l'idempotence ou la dépense manquent", "params": {"channel_id": "int", "action": "str", "payload": "dict", "experiment_id": "int?", "idempotency_key": "str?", "spend_amount": "float?", "spend_currency": "str?"}, "fn": _act_on_channel},
    "open_business": {"desc": "ouvre une nouvelle activité économique distincte (objectif initial en brouillon)", "params": {"business_id": "str", "name": "str", "thesis": "str"}, "fn": _open_business},
    "resources_status": {"desc": "inventaire des ressources reelles disponibles (comptes, argent, audiences, machines) avec leur etat constate, leur acces et ce qui manque", "params": {"capability": "str?", "state": "str?"}, "fn": _resources_status},
    "request_resource": {"desc": "demande a l'humain de creer, connecter ou autoriser une ressource manquante (login, oauth, 2fa, kyc, signature, validation bancaire) ; l'operation reprend seule apres la reponse", "params": {"key": "str", "need": "str", "question": "str", "label": "str?", "kind": "str?"}, "fn": _request_resource},
    "request_spend": {"desc": "demande l'autorisation de dépenser (ne paie rien) ; refusée hors enveloppe accordée", "params": {"amount": "float", "currency": "str", "purpose": "str", "experiment_id": "int?"}, "fn": _request_spend},
}


def tools_desc(allowed_tools: set[str] | None = None, *, legacy_search: bool = False) -> str:
    items = TOOLS.items() if allowed_tools is None else (
        (name, spec) for name, spec in TOOLS.items() if name in allowed_tools
    )
    lines = []
    for name, spec in items:
        if legacy_search and name == "search":
            lines.append("- search(query) : recherche web (liens)")
        else:
            lines.append(f"- {name}({', '.join(spec['params'])}) : {spec['desc']}")
    return "\n".join(lines)


def _matches_tool_type(value, token: str) -> bool:
    if token.endswith("_id"):
        token = "int"
    checks = {
        "str": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "list": lambda v: isinstance(v, list),
        "dict": lambda v: isinstance(v, dict),
        "bool": lambda v: isinstance(v, bool),
    }
    check = checks.get(token)
    return True if check is None else check(value)


def _validate_tool_args(tool: str, args) -> str | None:
    """Validation structurelle minimale des paramètres déclarés dans TOOLS."""
    if not isinstance(args, dict):
        return f"args doit être un objet, reçu {type(args).__name__}"
    for name, declared in TOOLS[tool]["params"].items():
        declared = str(declared)
        variants = declared.split("|")
        optional = all(v.endswith("?") for v in variants)
        clean = [v[:-1] if v.endswith("?") else v for v in variants]
        if name not in args or args[name] is None:
            if optional:
                continue
            return f"argument obligatoire manquant : {name}"
        value = args[name]
        if not any(_matches_tool_type(value, token) for token in clean):
            expected = "|".join(clean)
            return f"argument {name} : type attendu {expected}, reçu {type(value).__name__}"
    return None


def _normalize_allowed_tools(allowed_tools) -> set[str] | None:
    if allowed_tools is None:
        return None
    allowed = set(allowed_tools)
    unknown = sorted(allowed - set(TOOLS))
    if unknown:
        raise ValueError(f"outils inconnus dans allowed_tools : {', '.join(unknown)}")
    return allowed


ROLES = {
    "SOUT": "Recherche et veille : utilise search/browse pour trouver des infos utiles.",
    "CONVERT": "Monétisation : rédige offres, prix, CTA, contenu.",
    "FORGE": "Production : produit les vidéos (render_offer).",
    "GROWTH": "Qualité/distribution : juge (qc), publie (publish), prospecte (send_message).",
    "LEDGER": "Data/finance : mesure (qc), suit les coûts, mémorise (remember).",
    "ORBIT": "CEO : planifie, arbitre, décide.",
}


# Hors Podalux, les rôles ne présupposent ni vidéo ni produit : ils décrivent des fonctions économiques.
GENERIC_ROLES = {
    "SOUT": "Observation du monde réel : demandes, marchés, concurrents, canaux ; cite les sources consultées (record_observation).",
    "CONVERT": "Monétisation : ce qui peut être vendu, à qui, à quel prix, par quel canal ; propose des expériences mesurables.",
    "FORGE": "Production : fabrique ce que l'expérience exige (offre, contenu, produit, service, page, outil).",
    "GROWTH": "Distribution : agit sur les canaux ouverts (act_on_channel) et mesure les retours.",
    "LEDGER": "Mesure économique : cash observé, coûts, verdicts et apprentissages (economy_status).",
    "ORBIT": "Arbitrage : choisit, arrête ou étend les expériences selon le cash net observé.",
}


def _budget_exhausted() -> bool:
    """Budget du run OCTOPUS en cours (et de ses parents). Coupe-circuit : contrôle historique."""
    import octopus
    if not octopus.enabled():
        return db.total_cost() > deepseek.config.CYCLE_BUDGET_USD
    from octopus import journal
    run = journal.current_run()
    return bool(run and journal.budget_exhausted(run))


def _group() -> str:
    """Identité donnée à l'agent : Podalux par défaut, sinon le business du run en cours."""
    run = journal.current_run()
    if run is None or run.business == DEFAULT_BUSINESS:
        return "Podalux"
    return f"OCTOPUS (business {run.business})"


def _today_iso() -> str:
    return date.today().isoformat()


def _freshness_context(run) -> str:
    if run is None or run.business == DEFAULT_BUSINESS:
        return ""
    today = _today_iso()
    year = today[:4]
    return (
        f"DATE ACTUELLE : {today}. Pour une recherche présentée comme actuelle/récente, "
        f"privilégie {year} et les sources les plus récentes disponibles ; n'utilise pas une année antérieure "
        "comme substitut implicite du présent sauf si l'objectif demande explicitement une période historique.\n"
        "Pour cibler un domaine, utilise le paramètre site de search (ex. site=\"insee.fr\") "
        "plutôt que de supposer qu'un champ non déclaré sera appliqué.\n\n"
    )


def build_prompts(role: str, goal: str, conversational: bool = False,
                  allowed_tools: set[str] | None = None) -> tuple[str, str, str]:
    """(prompt système, premier message, libellé de fin) de la boucle ReAct."""
    run = journal.current_run()
    roles = GENERIC_ROLES if run is not None and run.business != DEFAULT_BUSINESS else ROLES
    role_desc = roles.get(role, "")
    group = _group()
    legacy_search = run is None or run.business == DEFAULT_BUSINESS
    tool_text = tools_desc(allowed_tools, legacy_search=legacy_search)
    freshness_context = _freshness_context(run)
    proof_rule = ""
    if run is not None and run.business != DEFAULT_BUSINESS:
        proof_rule = (
            "RÈGLE DE PREUVE : ne présente jamais comme observé, réel ou disponible un fait, un chiffre, "
            "un canal ou une ressource qui n'apparaît pas dans un résultat d'outil de cette exécution. "
            "Si l'information manque, écris qu'elle est inconnue ; une hypothèse ou une inférence doit rester explicitement telle.\n"
            "CONTRAT record_observation : experiment_id et channel_id sont des identifiants NUMÉRIQUES de base "
            "de données. S'ils sont inconnus, omets ces champs ; ne mets jamais un nom de rôle comme "
            "ORBIT, SOUT ou FORGE à leur place.\n\n"
        )
    if conversational:
        system = (
            f"Tu es l'agent {role} du groupe {group}. {role_desc} "
            f"Un humain t'a écrit. Réponds-lui DIRECTEMENT, en français, de façon utile "
            f"et conversationnelle. Tu peux utiliser un outil (search, browse, recall) "
            f"si besoin, mais ta priorité est de répondre à sa demande.\n\n"
            f"IMPORTANT : tu es connecté à tes comptes (Stripe, Reddit, X, Fiverr, YouTube…) "
            f"via l'outil `browse`, qui ouvre les pages dans TON Chrome réel. Pour vérifier "
            f"un accès, utilise `browse` sur la page concernée.\n\n"
            f"Outils disponibles :\n{tool_text}\n\n"
            f"{freshness_context}"
            "Réponds TOUJOURS en JSON : soit {\"tool\": \"<nom>\", \"args\": {...}} pour agir, "
            "soit {\"final\": \"<ta réponse à l'humain>\"}."
        )
        first_user = f"Message de l'humain : {goal}"
        done_label = "réponse"
    else:
        memory_hint = ""
        if allowed_tools is None or {"remember", "recall"} <= allowed_tools:
            memory_hint = "Utilise `remember` pour stocker tes apprentissages et `recall` pour les relire."
        system = (
            f"Tu es l'agent {role} du groupe {group}. {role_desc} "
            f"Poursuis l'objectif en utilisant "
            f"les outils disponibles. À chaque étape, choisis UNE action. "
            f"{memory_hint}\n\n"
            f"Outils disponibles :\n{tool_text}\n\n"
            f"{freshness_context}"
            f"{proof_rule}"
            "Réponds TOUJOURS en JSON : soit {\"tool\": \"<nom>\", \"args\": {...}} pour agir, "
            "soit {\"final\": \"<réponse>\"} quand l'objectif est atteint."
        )
        first_user = f"Objectif : {goal}"
        done_label = "objectif atteint"
    return system, first_user, done_label


DEFAULT_BUSINESS = "podalux"


def _business(business: str | None) -> str:
    """Business explicite, sinon celui du run englobant (tâche, mission), sinon l'activité historique."""
    if business:
        return business
    current = journal.current_run()
    return current.business if current else DEFAULT_BUSINESS


def run_agent(role: str, goal: str, max_steps: int = 10,
              conversational: bool = False, *, business: str | None = None,
              allowed_tools: set[str] | None = None,
              search_browse_lockstep: bool = False,
              search_browse_selector: str = "first") -> dict:
    """Un agent (rôle) poursuit un objectif librement via la boucle ReAct.

    `conversational=True` → l'agent répond à un message humain (pas un objectif).
    """
    allowed_tools = _normalize_allowed_tools(allowed_tools)
    if search_browse_selector not in {"first", "evidence_relevance"}:
        raise ValueError(f"search_browse_selector inconnu : {search_browse_selector}")
    with journal.run(_business(business), "agent", label=f"{role} : {goal}",
                     budget_usd=deepseek.config.CYCLE_BUDGET_USD):
        token = _ROLE.set(role)
        try:
            with cancel.scope(), web_guard.session(), _search_cache():
                return _run_agent(
                    role, goal, max_steps, conversational, allowed_tools,
                    search_browse_lockstep=search_browse_lockstep,
                    search_browse_selector=search_browse_selector,
                )
        finally:
            _ROLE.reset(token)


def _run_agent(role: str, goal: str, max_steps: int, conversational: bool,
               allowed_tools: set[str] | None = None, *,
               search_browse_lockstep: bool = False,
               search_browse_selector: str = "first") -> dict:
    system, first_user, done_label = build_prompts(role, goal, conversational, allowed_tools)
    context = [{"role": "system", "content": system},
               {"role": "user", "content": first_user}]
    steps = []
    last_sig = None
    repeat = 0
    lockstep_url = None
    lockstep_selection = None
    for i in range(max_steps):
        if cancel.requested():
            db.post(role, "arrêt demandé par l'humain — fin de l'agent")
            return {"role": role, "steps": steps, "final": "(arrêt demandé)"}
        # Garde-budget : on arrête l'agent si le budget du run est atteint.
        if _budget_exhausted():
            db.post(role, "budget dépassé — arrêt du run")
            return {"role": role, "steps": steps, "final": "(budget dépassé)"}
        forced_lockstep = lockstep_url is not None
        forced_selection = lockstep_selection if forced_lockstep else None
        if forced_lockstep:
            # Expérience opt-in : l'URL choisie provient littéralement du SEARCH précédent.
            r = {"tool": "browse", "args": {"url": lockstep_url}}
            lockstep_url = None
            lockstep_selection = None
        else:
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
        raw_args = r.get("args")
        args = {} if raw_args is None else raw_args
        # L'action choisie entre dans l'historique : sans elle, le modèle ne voyait que les résultats
        # et relançait les mêmes requêtes (audit M2).
        context.append({"role": "assistant", "content": json.dumps(r, ensure_ascii=False)[:600]})
        if tool not in TOOLS:
            context.append({"role": "user", "content": f"outil inconnu : {tool}. Disponibles : {list(TOOLS)}"})
            steps.append({"step": i + 1, "tool": tool, "result": "inconnu"})
            continue

        refusal = None
        if allowed_tools is not None and tool not in allowed_tools:
            refusal = f"outil {tool} interdit par la politique de cette mission"
        else:
            refusal = _validate_tool_args(tool, args)

        result = None
        if refusal is not None:
            result = {"refused": True, "tool": tool, "reason": refusal}
            result_str = _tool_result_view(tool, result)
            db.post(role, f"refus outil {tool} : {refusal}")
        else:
            try:
                result = TOOLS[tool]["fn"](args)
                result_str = _tool_result_view(tool, result)
            except cancel.Cancelled:
                db.post(role, "arrêt demandé par l'humain — fin de l'agent")
                return {"role": role, "steps": steps, "final": "(arrêt demandé)"}
            except Exception as e:
                result_str = f"erreur : {e}"
        if (
            search_browse_lockstep
            and refusal is None
            and result is not None
            and tool == "search"
            and (allowed_tools is None or "browse" in allowed_tools)
        ):
            choice = _select_search_browse_candidate(
                result,
                str(args.get("query") or ""),
                selector=search_browse_selector,
            )
            if choice is not None:
                lockstep_url = choice["url"]
                lockstep_selection = choice
                context.append({
                    "role": "user",
                    "content": (
                        "LOCKSTEP EXPÉRIMENTAL : avant tout nouveau search, ouvre avec browse une URL "
                        "réellement retournée par la recherche. "
                        f"Sélecteur={choice['selector']}, rang={choice['rank']}, URL imposée : {lockstep_url}"
                    ),
                })
            elif search_browse_selector == "evidence_relevance":
                context.append({
                    "role": "user",
                    "content": (
                        "LOCKSTEP EXPÉRIMENTAL : aucun résultat suffisamment pertinent n'a été sélectionné. "
                        "Ne browse pas une page hors sujet ; reformule la recherche ou conclus si le budget est épuisé."
                    ),
                })

        # garde anti-boucle : si même action + même résultat répétés, demander de changer
        sig = f"{tool}:{result_str}"
        repeat = repeat + 1 if sig == last_sig else 0
        last_sig = sig
        if repeat >= 2:
            recovery = "Change d'approche"
            if allowed_tools is None or "ask_human" in allowed_tools:
                recovery += ", demande à l'humain (ask_human)"
            recovery += ", ou réponds avec « final »."
            context.append({"role": "user", "content":
                            "Tu répètes la même action sans progrès (ex. CAPTCHA/échec). " + recovery})
            repeat = 0
        if refusal is None:
            db.post(role, f"action {tool} {json.dumps(args, ensure_ascii=False)[:90]}")
        context.append({"role": "user", "content": f"Résultat de {tool} : {result_str}"})
        # Le résultat structuré reste intact ; result n'est qu'une vue de prompt bornée.
        step_record = {"step": i + 1, "tool": tool, "result": result_str}
        if tool in {"search", "browse"}:
            step_record["args"] = dict(args)
            if result is not None:
                if tool == "browse" and isinstance(result, dict) and isinstance(result.get("page"), dict):
                    step_record["result_data"] = {
                        "url": result.get("url"),
                        "source": result.get("source"),
                        "page": result["page"],
                    }
                else:
                    step_record["result_data"] = result
        if tool == "search":
            step_record["result_urls"] = _search_result_urls(result)
        elif tool == "browse" and result is not None:
            browse_meta = _browse_result_meta(result)
            if browse_meta is not None:
                step_record["browse_meta"] = browse_meta
        if forced_lockstep:
            step_record["lockstep_forced"] = True
            if isinstance(forced_selection, dict):
                step_record["lockstep_selection"] = dict(forced_selection)
        steps.append(step_record)
    return {"role": role, "steps": steps, "final": "(max steps atteint)"}


def run_mission(goal: str, max_steps_per_agent: int = 8, *, business: str | None = None,
                allowed_tools: set[str] | None = None, profile: str | None = None,
                search_browse_lockstep: bool = False,
                search_browse_selector: str = "first") -> dict:
    """ORBIT planifie puis délègue aux rôles (multi-agents via le runtime).

    Le profil explicite est hérité par les runs agents imbriqués via le journal.
    """
    allowed_tools = _normalize_allowed_tools(allowed_tools)
    if search_browse_selector not in {"first", "evidence_relevance"}:
        raise ValueError(f"search_browse_selector inconnu : {search_browse_selector}")
    with journal.run(_business(business), "mission", label=goal, budget_usd=deepseek.config.CYCLE_BUDGET_USD,
                     profile=profile):
        with cancel.scope(), web_guard.session(), _search_cache():
            return _run_mission(
                goal, max_steps_per_agent, allowed_tools,
                search_browse_lockstep=search_browse_lockstep,
                search_browse_selector=search_browse_selector,
            )


MAX_PLAN_TASKS = 5

# Artefacts suffisamment petits pour circuler entre sous-agents sans recopier toute leur trace.
# Le but est la continuité de travail : une URL/source déjà trouvée doit rester exploitable même
# si l'agent amont termine sur son budget d'étapes avant d'avoir produit un final détaillé.
_HANDOFF_TOOLS = {"search", "browse", "record_observation", "economy_status", "resources_status"}


def _handoff_payload(result: dict) -> dict:
    artifacts = []
    for step in result.get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "")
        if tool not in _HANDOFF_TOOLS:
            continue
        data = step.get("result_data")
        artifacts.append({
            "step": step.get("step"),
            "tool": tool,
            "result": (
                _tool_result_view(tool, data, max_chars=1200)
                if data is not None
                else str(step.get("result") or "")[:1200]
            ),
        })
    return {
        "role": str(result.get("role") or ""),
        "task": str(result.get("task") or ""),
        "final": str(result.get("final") or ""),
        "artifacts": artifacts,
    }


def _run_mission(goal: str, max_steps_per_agent: int, allowed_tools: set[str] | None = None, *,
                 search_browse_lockstep: bool = False,
                 search_browse_selector: str = "first") -> dict:
    pro = deepseek.config.MODEL_PRO
    if cancel.requested():
        return {"plan": [], "results": [], "rapport": "(arrêt demandé)"}
    current = journal.current_run()
    planner_roles = GENERIC_ROLES if current is not None and current.business != DEFAULT_BUSINESS else ROLES
    role_catalog = "\n".join(f"- {name}: {desc}" for name, desc in planner_roles.items())
    planner_freshness = _freshness_context(current)
    plan_sys = (
        "Tu es ORBIT, l'orchestrateur de la mission. "
        f"{planner_freshness}"
        "Utilise les rôles comme des responsabilités spécialisées, pas comme des workers interchangeables.\n\n"
        f"Rôles disponibles et responsabilités :\n{role_catalog}\n\n"
        "Décompose l'objectif en 2 à 5 sous-tâches, chacune assignée à UN rôle dont la responsabilité "
        "correspond réellement au travail demandé. N'assigne pas une tâche à un rôle seulement pour l'occuper "
        "et ne duplique pas la même collecte chez plusieurs rôles sans nécessité. "
        "Un même rôle peut recevoir plusieurs sous-tâches distinctes si elles relèvent de sa spécialité ; "
        "ne force jamais la diversité des rôles. "
        f"Chaque sous-tâche doit être réalisable en au plus {max_steps_per_agent} étapes ; si plusieurs pistes "
        "indépendantes demandent chacune plusieurs actions, répartis-les au lieu de surcharger un seul agent. "
        "Si une sous-tâche aval dépend de découvertes d'une sous-tâche amont, rends cette dépendance explicite. "
        "Les artefacts utiles des étapes amont (recherches, pages ouvertes, observations et statuts) seront transmis "
        "automatiquement au sous-agent suivant : il doit les réutiliser avant de recommencer une collecte équivalente. "
        "Réponds en JSON : "
        '{"tasks":[{"role":"...","task":"..."}]}'
    )
    plan = deepseek.call_json("ORBIT", "planification", pro,
                              [{"role": "system", "content": plan_sys},
                               {"role": "user", "content": goal}],
                              reasoning="high")
    proposed = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
    # Le prompt demande 2 à 5 sous-tâches : au-delà, chaque sous-tâche coûte une boucle ReAct complète.
    tasks = [t for t in proposed if isinstance(t, dict)][:MAX_PLAN_TASKS]
    if len(proposed) > len(tasks):
        db.post("ORBIT", f"plan tronqué : {len(proposed)} sous-tâches proposées, {len(tasks)} gardées")
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
        # Contexte cumulatif structuré : le final seul est insuffisant quand l'agent amont
        # atteint max_steps. On transmet donc aussi ses artefacts de preuve utiles, de façon compacte.
        # La tâche stockée dans results reste l'ORIGINALE pour éviter une croissance récursive du prompt.
        if results:
            handoff = [_handoff_payload(r) for r in results]
            task = (
                f"{task}\n\nContexte structuré des sous-tâches précédentes :\n"
                "Réutilise d'abord les artefacts déjà collectés ; ne relance pas une recherche équivalente "
                "si une URL, une page ouverte ou une observation exploitable est déjà présente.\n"
                f"{json.dumps(handoff, ensure_ascii=False)}"
            )
        db.post(role, f"sous-tâche : {original[:80]}")
        r = run_agent(
            role,
            task,
            max_steps=max_steps_per_agent,
            allowed_tools=allowed_tools,
            search_browse_lockstep=search_browse_lockstep,
            search_browse_selector=search_browse_selector,
        )
        results.append({"role": role, "task": original,
                        "final": r.get("final"), "steps": r.get("steps")})

    if cancel.requested():
        db.post("ORBIT", "mission arrêtée par l'humain avant la synthèse")
        return {"plan": tasks, "results": results, "rapport": "(arrêt demandé)"}
    syn_sys = (
        "Tu es ORBIT. Synthétise les résultats des sous-tâches en un rapport final concis. "
        "Respecte aussi toutes les contraintes de l'objectif original : une synthèse ne doit pas réintroduire "
        "une recommandation, décision, action ou autre contenu que la mission interdisait. "
        "N'introduis aucun fait, chiffre, canal, ressource ou résultat absent des sous-tâches et de leurs résultats d'outils. "
        "Si un sous-agent affirme quelque chose sans preuve visible dans ses étapes, qualifie-le de non vérifié ou d'inférence, "
        "jamais de fait observé. Réponds en JSON : {\"rapport\":\"...\"}"
    )
    synthesis_input = {
        "objectif_original": goal,
        "resultats_sous_taches": _mission_prompt_results(results),
    }
    try:
        syn = deepseek.call_json("ORBIT", "synthese", pro,
                                 [{"role": "system", "content": syn_sys},
                                  {"role": "user", "content": json.dumps(synthesis_input, ensure_ascii=False)}],
                                 reasoning="high", max_tokens=4000)
    except llm.GatewayError as exc:
        # Le travail des sous-agents existe deja. Une panne de serialisation/synthese
        # ne doit pas jeter la mission ni fabriquer une nouvelle conclusion factuelle.
        rapport = (
            "Synthèse LLM indisponible. Les résultats bruts des sous-tâches sont conservés "
            "dans results ; aucune synthèse factuelle validée n'a été produite."
        )
        error = f"{type(exc).__name__}: {exc}"
        db.decide("ORBIT", "mission_done", {
            "rapport": rapport,
            "synthesis_status": "degraded",
            "synthesis_error": error[:1500],
        })
        db.post("ORBIT", f"mission terminée en mode dégradé : {error[:120]}")
        return {
            "plan": tasks,
            "results": results,
            "rapport": rapport,
            "synthesis_status": "degraded",
            "synthesis_error": error,
        }

    rapport = syn.get("rapport", "")
    db.decide("ORBIT", "mission_done", {"rapport": rapport, "synthesis_status": "validated"})
    db.post("ORBIT", f"mission terminée : {rapport[:80]}")
    return {"plan": tasks, "results": results, "rapport": rapport, "synthesis_status": "validated"}
