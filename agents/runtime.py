"""Runtime général des agents : registre d'outils partagés + boucle ReAct + mémoire.

C'est ici que les agents deviennent « libres » : ils choisissent leurs actions
via le LLM (penser → agir → observer) parmi un registre d'outils, et apprennent
via la mémoire. Ajouter une capacité (prospection, etc.) = ajouter un OUTIL,
pas un nouvel agent ni un chantier.
"""
from __future__ import annotations

import contextvars
import copy
import json
import math
import re
import time
import unicodedata
from datetime import date
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from octopus import browser_workspace, journal, llm

from . import cancel, config, db, deepseek, web_guard
from .tool_registry import ToolRegistry, technical_refusal

_ROLE: contextvars.ContextVar[str] = contextvars.ContextVar("podalux_role", default="RUNTIME")
_SEARCHES: contextvars.ContextVar[dict | None] = contextvars.ContextVar("podalux_searches", default=None)
_SEARCH_PURPOSE: contextvars.ContextVar[str] = contextvars.ContextVar("podalux_search_purpose", default="")

# Extraits plus courts quand la recherche est résumée pour un autre sous-agent.
_SEARCH_HANDOFF_SNIPPET_CHARS = 60


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


@contextmanager
def search_purpose(purpose: str):
    """Intention de recherche de l'exécution : elle isole aussi le cache par mode."""
    token = _SEARCH_PURPOSE.set(purpose)
    try:
        yield
    finally:
        _SEARCH_PURPOSE.reset(token)


def query_key(query: str) -> str:
    """Requêtes équivalentes : casse, accents, ordre des mots, pluriels et mots courts ignorés."""
    text = unicodedata.normalize("NFKD", str(query)).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted({w.rstrip("sx") for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2}))

MODEL = deepseek.config.MODEL_FLASH


# --- Outils partagés ---
def _search_cache_key(effective: str, purpose: str) -> str:
    """Cache isolé par intention : une recherche business ne réutilise pas un cache général."""
    return f"{purpose}|{query_key(effective)}"


def _search(args):
    """SEARCH reste structuré : l'enveloppe du provider descend jusqu'au selector.

    Le texte n'est produit qu'ensuite, comme vue bornée pour le LLM. Aucun chemin machine
    ne repasse par STRUCTURE -> TEXTE -> REGEX -> STRUCTURE.
    """
    from .search import SEARCH_PURPOSE_GENERAL, effective_query, render_envelope, search_envelope
    query = str(args.get("query", ""))
    site = str(args.get("site") or "").strip() or None
    purpose = _SEARCH_PURPOSE.get() or SEARCH_PURPOSE_GENERAL
    effective = effective_query(query, site)
    cache, key = _SEARCHES.get(), _search_cache_key(effective, purpose)
    if cache is not None and key in cache:  # 21 recherches quasi identiques dans un run du 16/09 (audit M2)
        previous = cache[key]
        cached = previous["envelope"]
        return {"deja_cherche": True, "requete_precedente": previous["effective_query"],
                "note": "Recherche équivalente déjà faite : même résultat. Change d'angle, ouvre un lien "
                        "avec browse, ou conclus avec « final ».",
                "query": cached["query"], "effective_query": cached["effective_query"],
                "purpose": cached["purpose"], "errors": list(cached["errors"]),
                "items": copy.deepcopy(cached["items"]),
                "resultat": render_envelope(cached, max_items=2, max_snippet_chars=160)}
    # Le cache garde la donnée structurée COMPLÈTE ; le consommateur reçoit une copie.
    envelope = search_envelope(query, 6, site=site, purpose=purpose)
    if cache is not None:
        cache[key] = {
            "query": query,
            "site": site,
            "effective_query": effective,
            "purpose": purpose,
            "envelope": copy.deepcopy(envelope),
        }
    return copy.deepcopy(envelope)


def _structured_search_items(value) -> list[dict] | None:
    """Items natifs d'une recherche structurée, ou None pour une valeur d'ancien format.

    Reconnaître l'enveloppe permet de ne jamais extraire une URL d'un extrait, d'un titre
    ou d'un message d'erreur : seuls les items du provider sont des résultats.
    """
    if not isinstance(value, dict):
        return None
    items = value.get("items")
    if not isinstance(items, list):
        return None
    return [item for item in items
            if isinstance(item, dict) and str(item.get("url") or "").strip()]


def _search_result_urls(value) -> list[str]:
    """URLs réellement retournées par SEARCH, ordre conservé.

    Enveloppe structurée : lecture directe des items. Ancien format texte : parsing
    historique conservé pour la compatibilité des traces et des consommateurs hérités.
    """
    structured = _structured_search_items(value)
    if structured is not None:
        urls = []
        for item in structured:
            url = str(item.get("url") or "").strip()
            if url and url not in urls:
                urls.append(url)
        return urls
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


# Hôtes qui ne servent jamais la page de l'éditeur : une acquisition là-bas récupère une
# redirection, pas du contenu. C'est une propriété de l'outil browse, pas un jugement métier.
_WRAPPER_HOSTS = {
    "www.bing.com",
    "bing.com",
    "news.google.com",
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
    """Candidats URL + contexte : items natifs de l'enveloppe, texte seulement en repli."""
    structured = _structured_search_items(value)
    if structured is not None:
        return [{"url": str(item.get("url") or "").strip(),
                 "title": str(item.get("title") or ""),
                 "context": str(item.get("snippet") or "")}
                for item in structured]
    return _search_result_candidates_from_text(value)


def _search_result_candidates_from_text(value) -> list[dict]:
    """Parsing historique du format texte : repli pour les valeurs non structurées.

    Réservé aux anciennes traces et aux consommateurs hérités. Le parcours natif ne
    doit jamais en dépendre (cf. tests/test_search_structured.py).
    """
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


def _query_tokens(query: str) -> set[str]:
    """Mots discriminants d'une requête, opérateur `site:` retiré.

    Aucune liste de vocabulaire métier : juger si un titre ou un extrait est pertinent
    appartient au LLM, pas à un dictionnaire codé dans OCTOPUS.
    """
    return _norm_words(re.sub(r"(?i)\bsite:\S+", " ", str(query)))


def _host_matches_site(host: str, site: str) -> bool:
    host = host.lower().removeprefix("www.")
    site = site.lower().removeprefix("www.").strip(".")
    return host == site or host.endswith("." + site)


def _select_search_browse_candidate(value, query: str, selector: str = "first") -> dict | None:
    """Choisit une URL à ouvrir, uniquement parmi celles effectivement retournées par SEARCH.

    Responsabilité minimale (contrat d'outil, pas jugement métier) :
      * `first` : le premier résultat, tel quel ;
      * sinon le meilleur résultat ADMISSIBLE — URL http(S) du provider, qui n'est pas un
        hôte de redirection, et qui respecte une contrainte `site:` explicite.

    Un plancher de pertinence lexicale générale évite de forcer l'ouverture d'une page ne
    partageant qu'un seul mot avec la requête (collisions « appel »/Apple, « mission »/film).
    EXPERIMENTAL / BUDGET PROTECTION : ce plancher est temporaire, il protège le budget de
    browse du lockstep expérimental ; ce n'est pas un invariant architectural et rien de
    plus ne doit être construit autour.

    Le sélecteur ne décide PAS si une page est une bonne opportunité d'affaires. Il voit
    title/snippet/URL ; juger leur utilité appartient au LLM.
    """
    candidates = _search_result_candidates(value)
    if not candidates:
        return None
    if selector == "first":
        return {"url": candidates[0]["url"], "selector": "first", "rank": 1, "score": None}
    if selector not in {"evidence_relevance", "business_signal_relevance"}:
        raise ValueError(f"search_browse_selector inconnu : {selector}")

    required_sites = [
        site.rstrip(".,;:)")
        for site in re.findall(r"\bsite:([^\s]+)", str(query), flags=re.I)
    ]
    tokens = _query_tokens(query)
    scored = []
    for rank, candidate in enumerate(candidates, start=1):
        url = candidate["url"]
        host = urlparse(url).netloc.lower()
        if host in _WRAPPER_HOSTS:
            continue
        site_match = None
        if required_sites:
            site_match = any(_host_matches_site(host, site) for site in required_sites)
            if not site_match:
                continue
        title_words = _norm_words(candidate.get("title", ""))
        context_words = _norm_words(candidate.get("context", ""))
        scored.append({
            "url": url,
            "selector": selector,
            "rank": rank,
            "score": len(tokens & title_words) * 4 + len(tokens & context_words),
            "title_overlap": len(tokens & title_words),
            "context_overlap": len(tokens & context_words),
            "distinct_overlap": len(tokens & (title_words | context_words)),
            "site_match": site_match,
        })

    # EXPERIMENTAL / BUDGET PROTECTION (temporaire, pas un invariant) : un seul mot partagé
    # ne suffit pas à forcer une ouverture. Aucune liste lexicale métier n'intervient ici.
    scored = [item for item in scored if item["score"] > 0 and item["distinct_overlap"] >= 2]
    return max(scored, key=lambda item: (item["score"], -item["rank"]), default=None)


_BROWSE_BLOCK_MARKERS = (
    "performing security verification",
    "verify you are not a bot",
    "security service to protect against malicious bots",
    "just a moment",
    "verification successful. waiting",
    "access denied",
    "403 error",
    "request blocked",
    "captcha",
    "chrome-error://",
    "err_blocked_by_client",
    "blocked by chromium",
    "bloqué par chromium",
    "bloquée par chromium",
    "you've been blocked",
)


def _browse_result_meta(value) -> dict | None:
    """Métadonnées compactes issues de l'objet de page structuré, jamais d'un JSON tronqué."""
    if not isinstance(value, dict):
        return None
    from . import browser
    page = value.get("page") if isinstance(value.get("page"), dict) else value
    url = str(page.get("final_url") or value.get("url") or "").strip()
    text = str(page.get("main_text") or value.get("texte") or "")
    lowered = f"{url}\n{text}".lower()
    markers = getattr(browser, "PUBLIC_BLOCK_MARKERS", _BROWSE_BLOCK_MARKERS)
    meta = {
        "url": url,
        "text_chars": int(page.get("text_chars") or len(text)),
        "blocked": bool(page.get("blocked")) or any(marker in lowered for marker in markers),
        "vision_error": bool(value.get("vision_error")),
        "extraction_method": str(page.get("extraction_method") or ""),
        "rendered": bool(page.get("rendered")),
        "http_status": page.get("http_status"),
        "error": page.get("error"),
    }
    meta["usable"] = browser.is_public_text_acquisition(page)
    return meta


def _tool_result_view(tool: str, result, max_chars: int | None = None) -> str:
    """Vue courte destinée au prompt ; l'objet structuré source reste intact à côté."""
    if tool.startswith("browser_") and isinstance(result, dict):
        limit = 6000 if max_chars is None else max(200, int(max_chars))
        head = {k: v for k, v in result.items() if k != "snapshot"}
        text = json.dumps(head, ensure_ascii=False, default=str)[:2000]
        snapshot = str(result.get("snapshot") or "")
        return text + (f"\nPAGE (refs @eN) :\n{snapshot[:limit]}" if snapshot else "")
    if tool == "browse" and isinstance(result, dict):
        page = result.get("page") if isinstance(result.get("page"), dict) else result
        text = str(page.get("main_text") or result.get("texte") or "")
        limit = 6000 if max_chars is None else max(200, int(max_chars))
        title = str(page.get("title") or "").strip()
        # Body extraction can begin with several screens of site navigation.
        # Keep a literal bounded window around a late heading, without changing
        # the full acquisition used by the evidence gate.
        heading = text.find(title) if title else -1
        start = max(0, heading - min(200, limit // 4)) if heading >= limit // 2 else 0
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
            "text_start_char": start,
            "text_truncated": start > 0 or len(text) > limit,
            "texte": text[start:start + limit],
        }
        return json.dumps(payload, ensure_ascii=False)

    if tool == "search":
        view = _search_view(result, max_chars=max_chars)
        if view is not None:
            return view

    raw = json.dumps(result, ensure_ascii=False)
    limit = max_chars if max_chars is not None else (6000 if tool == "search" else 1500)
    return raw[:int(limit)]


def _search_view(result, max_chars: int | None = None) -> str | None:
    """Vue texte bornée d'une recherche structurée ; None si la valeur n'est pas structurée.

    La structure reste la source ; cette vue ne sert qu'au prompt. Les URL ne sont pas
    raccourcies : seuls les extraits sont bornés.
    """
    items = _structured_search_items(result)
    if items is None:
        return None
    from .search import render_envelope
    view = render_envelope(result, max_snippet_chars=(
        None if max_chars is None else _SEARCH_HANDOFF_SNIPPET_CHARS))
    if isinstance(result, dict) and result.get("deja_cherche"):
        # Le signal de cache doit rester lisible par le LLM dans la vue texte.
        previous = str(result.get("requete_precedente") or "").strip()
        view = "[deja_cherche] " + previous + "\n\n" + view
    if max_chars is not None and len(view) > max_chars:
        view = view[:max_chars]  # filet : les URL des premiers items restent lisibles
    return view


def _mission_prompt_results(results: list[dict]) -> list[dict]:
    """Projection bornée pour la synthèse : jamais les payloads structurés complets."""
    run = journal.current_run()
    if run is not None and run.profile == "economical":
        return [_handoff_payload(result) for result in results or []]
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


def _browser_request_allowed(url: str, state, *, account_context: bool,
                             anonymous_account_domains=()) -> bool:
    """Alias historique : la règle vit dans `web_guard.request_allowed` (partagée avec le
    proxy de garde de l'espace de travail navigateur)."""
    return web_guard.request_allowed(url, state, account_context=account_context,
                                     anonymous_account_domains=anonymous_account_domains)


def _account_read_succeeded(browser_instance, final_url: str) -> bool:
    """Deterministic check: the account navigation returned real content, not a block/error page.

    A failed account read must NOT commit mission-level taint.  The markers are the same
    already used for public acquisition so there is a single shared vocabulary.
    """
    from . import browser as _browser_mod
    if getattr(browser_instance, "blocked", None):
        return False
    try:
        page_text = browser_instance.snapshot(6000)
    except Exception:
        page_text = ""
    if not page_text.strip():
        # Empty or whitespace-only page: no account content was exposed.
        # Do not commit mission taint.
        return False
    try:
        html_sample = browser_instance.html()[:4000]
    except Exception:
        html_sample = ""
    return not _browser_mod._contains_block_marker(final_url, page_text, html_sample)


def _browse(args):
    """Public : HTTP-first + extraction ; comptes : Chromium connecté, lecture seule."""
    from . import browser
    state = web_guard.current()
    url = str(args.get("url", ""))
    kind = web_guard.check(url, state)
    account = kind == web_guard.ACCOUNT
    anonymous_account_domains: tuple = ()
    if account and not state.account_read and not browser.profile_has_cookies(url):
        # Domaine capable d'héberger un compte, mais le profil connecté ne détient aucun
        # cookie pour cette origine : le serveur ne peut rattacher la requête à un compte.
        # L'acquisition est anonyme par construction (HTTP sans session ou contexte
        # éphémère) et ne doit pas tainter la mission comme la lecture d'un compte.
        account = False
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        anonymous_account_domains = tuple(
            d for d in config.ACCOUNT_DOMAINS if host == d or host.endswith("." + d))

    if not account:
        record = browser.acquire_public_page(
            url,
            guard=lambda u: _browser_request_allowed(
                u, state, account_context=False,
                anonymous_account_domains=anonymous_account_domains),
        ).as_dict()
        final = str(record.get("final_url") or url)
        if browser.is_public_text_acquisition(record):
            web_guard.record(final, web_guard.PUBLIC, state)
        return {
            "url": final,
            "source": web_guard.UNTRUSTED_NOTE,
            "texte": str(record.get("main_text") or ""),
            "page": record,
            "vision": None,
            "vision_error": None,
        }

    # --- Account path: temporary state transaction ---
    # The guard must taint a *temporary* state during navigation so that anti-exfiltration
    # holds within the account browser (public subrequests are refused).  The mission state
    # is only committed after the navigation is verified successful.  A blocked/error page
    # therefore never taints the mission, and subsequent public navigations remain allowed.
    #
    # The temporary state inherits the mission's current account_read flag so that a
    # previously successful account read still blocks public subrequests in later navigations.
    temp_state = web_guard.BrowseState(account_read=state.account_read)

    b = browser.new_browser(
        headless=False,
        account=True,
        guard=lambda u: _browser_request_allowed(u, temp_state, account_context=True),
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

        if not _account_read_succeeded(b, final):
            # Navigation completed but the page is a block/error page.
            # Discard temporary taint: mission state stays clean.
            page_text = b.snapshot(6000)
            return {
                "url": final,
                "source": "compte connecté (lecture seule)",
                "texte": page_text,
                "vision": None,
                "vision_error": "page de compte bloquée ou en erreur",
                "page": {
                    "final_url": final,
                    "main_text": page_text,
                    "text_chars": len(page_text),
                    "blocked": True,
                    "error": "account_page_blocked_or_error",
                    "extraction_method": "",
                    "rendered": True,
                    "http_status": None,
                    "content_type": "text/html",
                    "title": "",
                    "raw_chars": 0,
                    "truncated": False,
                    "requested_url": url,
                    "fetched_at": "",
                },
            }

        # Successful account read: commit temporary taint to mission state.
        # web_guard.record sets account_read=True for ACCOUNT-kind final URLs,
        # preserving the historical anti-exfiltration property.
        web_guard.record(final, final_kind, state)

        seen = b.see(agent=_ROLE.get())
        page_text = b.snapshot(6000)
        return {
            "url": final,
            "source": "compte connecté (lecture seule)",
            "texte": page_text,
            "vision": seen["description"],
            "vision_error": seen.get("vision_error"),
        }
    finally:
        b.stop()


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
    return any(source in str(entry.get("envelope", "")) for entry in (_SEARCHES.get() or {}).values())


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


TOOLS = ToolRegistry({
    "search": {"desc": "recherche web (liens) ; site est un domaine optionnel réellement appliqué, ex. bpifrance.fr", "params": {"query": "str", "site": "str?"}, "fn": _search},
    "browse": {"desc": "ouvre une page dans TON Chrome réel (comptes Stripe/Reddit/X/Fiverr/YouTube connectés) et la décrit", "params": {"url": "str"}, "fn": _browse},
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
})


def _browser_tool(method: str, **fixed):
    def fn(args: dict):
        from octopus import browser_workspace
        return browser_workspace.call(method, **{**fixed, **{k: v for k, v in args.items() if v is not None}})
    return fn


# Espace de travail navigateur (backend Hermes agent-browser + Chromium, politiques OCTOPUS) :
# observer -> décider -> agir -> vérifier. Les refs @eN viennent du dernier snapshot.
_BROWSER_ACT = ("exige un canal actif avec accès act accordé par l'humain pour ce site "
                "(channel_id optionnel si un seul) ; ")
def _agnes_probe(args):
    from octopus import agnes as _agnes
    base = str(args.get("base_url") or _agnes.DEFAULT_URL)
    try:
        result = _agnes.probe(base)
        return {"ok": True, "service": result["service"], "expected_pin": result["expected_pin"],
                "base_url": base, "note": "Agnes service reachable on loopback, key is in Agnes process only"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "base_url": base,
                "note": "Agnes indisponible : optionnel, ne bloque pas les autres activités"}


def _agnes_submit(args):
    from octopus import agnes_production as _prod
    business = _run_business()
    prompt = str(args.get("prompt") or "").strip()
    idem = str(args.get("idempotency_key") or "").strip()
    if not prompt:
        raise ValueError("prompt required (1..5000 chars)")
    if not idem:
        raise ValueError("idempotency_key required for crash-resume and idempotence")
    base = str(args.get("base_url") or "http://127.0.0.1:8765")
    # Enforce prompt length here too for tool-level feedback
    if len(prompt) > 5000:
        raise ValueError("prompt too long (max 5000)")
    try:
        gen = _prod.request_generation(
            business=business, prompt=prompt, idempotency_key=idem,
            task_id=_current_task_id(), base_url=base,
        )
        return {"generation_id": gen["id"], "agnes_task_id": gen["agnes_task_id"],
                "status": gen["status"], "source_ref": gen.get("source_ref"),
                "note": "Agnes task ID preserved immediately; tracking can resume after crash"}
    except Exception as exc:
        # Never leak secrets; return sanitized error
        return {"ok": False, "error": str(exc)[:500],
                "note": "En cas de résultat incertain, ne pas déclencher aveuglément nouvelle génération; reconcile"}


def _current_task_id():
    try:
        from octopus import journal
        run = journal.current_run()
        if run is None:
            return None
        rows = journal.query("SELECT id FROM tasks WHERE run_id=? ORDER BY id DESC LIMIT 1", (run.root_id,))
        return int(rows[0]["id"]) if rows else None
    except Exception:
        return None


def _agnes_status(args):
    from octopus import agnes as _agnes, agnes_production as _prod
    base = str(args.get("base_url") or _agnes.DEFAULT_URL)
    gen_id = args.get("generation_id")
    agnes_task_id = args.get("agnes_task_id")
    if gen_id is not None:
        gen = _prod.get_generation(int(gen_id))
        if not gen:
            raise ValueError(f"generation #{gen_id} not found")
        agnes_task_id = gen["agnes_task_id"]
    if not agnes_task_id:
        raise ValueError("agnes_task_id or generation_id required")
    try:
        st = _agnes.status_full(str(agnes_task_id), base_url=base)
        # Also get local record if exists
        local = _prod.get_by_agnes_id(str(agnes_task_id))
        return {"agnes_task_id": agnes_task_id, "status": st["status"], "task_type": st.get("task_type"),
                "source_ref": st["source_ref"], "local": {"id": local["id"], "status": local["status"]} if local else None,
                "note": "Polling with backoff; avoid excessive calls"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500], "agnes_task_id": agnes_task_id}


def _agnes_download(args):
    from octopus import agnes_production as _prod
    business = _run_business()
    gen_id = args.get("generation_id")
    if gen_id is None:
        raise ValueError("generation_id required")
    base = str(args.get("base_url") or "http://127.0.0.1:8765")
    try:
        gen = _prod.retrieve_and_verify(int(gen_id), base_url=base, task_id=_current_task_id())
        return {"generation_id": gen["id"], "agnes_task_id": gen["agnes_task_id"],
                "path": gen["output_path"], "sha256": gen["sha256"], "bytes": gen["file_size"],
                "verified": True, "evidence_id": gen.get("evidence_id"),
                "note": "MP4 verified physically (header, size, SHA-256), not just HTTP completed"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500], "generation_id": int(gen_id),
                "note": "Vérification réelle du MP4 échouée: fichier incomplet ou corrompu possible"}


def _agnes_stop(args):
    from octopus import agnes_production as _prod
    gen_id = args.get("generation_id")
    if gen_id is None:
        raise ValueError("generation_id required")
    base = str(args.get("base_url") or "http://127.0.0.1:8765")
    try:
        gen = _prod.stop_generation(int(gen_id), base_url=base)
        return {"generation_id": gen["id"], "status": gen["status"], "agnes_task_id": gen["agnes_task_id"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


def _agnes_list(args):
    from octopus import agnes_production as _prod
    business = _run_business()
    limit = int(args.get("limit", 10))
    gens = _prod.list_generations(business, limit=limit)
    return {"business": business, "count": len(gens),
            "generations": [{k: g[k] for k in ("id", "agnes_task_id", "status", "prompt", "output_path", "sha256", "created_at")} for g in gens]}


def _agnes_generate_full(args):
    """Full cycle: submit, poll, download, verify, persist proofs."""
    from octopus import agnes_production as _prod
    business = _run_business()
    prompt = str(args.get("prompt") or "").strip()
    idem = str(args.get("idempotency_key") or "").strip()
    if not prompt or not idem:
        raise ValueError("prompt and idempotency_key required")
    base = str(args.get("base_url") or "http://127.0.0.1:8765")
    poll_timeout = float(args.get("poll_timeout_s", 1800))
    try:
        gen = _prod.full_production_cycle(
            business=business, prompt=prompt, idempotency_key=idem,
            task_id=_current_task_id(), base_url=base, poll_timeout_s=poll_timeout,
        )
        return {"generation_id": gen["id"], "agnes_task_id": gen["agnes_task_id"],
                "status": gen["status"], "path": gen["output_path"], "sha256": gen["sha256"],
                "bytes": gen["file_size"], "verified": True, "evidence_id": gen.get("evidence_id"),
                "note": "Full cycle done: MP4 verified, proofs persisted, mission can continue autonomously"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500],
                "note": "Full cycle failed or rate limited; will retry with backoff, no blind new generation"}


TOOLS.update({
    "browser_navigate": {"desc": "espace de travail navigateur persistant de la tâche : ouvre une URL et renvoie "
                         "l'arbre de la page avec des refs @eN (cookies/étapes conservés entre appels)",
                         "params": {"url": "str"}, "fn": _browser_tool("navigate")},
    "browser_snapshot": {"desc": "observe la page courante (refs @eN à jour) ; full=true pour l'arbre complet",
                         "params": {"full": "bool?"}, "fn": _browser_tool("snapshot")},
    "browser_click": {"desc": "clique l'élément @eN. Un lien simple = navigation libre ; un bouton/validation "
                      "= action à effet : " + _BROWSER_ACT + "expect = court texte qui n'apparaîtra qu'après "
                      "l'effet (vérification réelle, reprise sans double envoi)",
                      "params": {"ref": "str", "expect": "str?", "channel_id": "int?"}, "fn": _browser_tool("click")},
    "browser_type": {"desc": "remplit le champ @eN (efface puis saisit) ; " + _BROWSER_ACT +
                     "jamais de mot de passe, secret ni moyen de paiement",
                     "params": {"ref": "str", "text": "str", "channel_id": "int?"}, "fn": _browser_tool("type")},
    "browser_select": {"desc": "choisit une option (libellé ou valeur) dans la liste @eN ; " + _BROWSER_ACT,
                       "params": {"ref": "str", "value": "str", "channel_id": "int?"}, "fn": _browser_tool("select")},
    "browser_check": {"desc": "coche la case @eN ; " + _BROWSER_ACT,
                      "params": {"ref": "str", "channel_id": "int?"}, "fn": _browser_tool("check")},
    "browser_press": {"desc": "appuie sur une touche (Tab, Escape, flèches : libres ; Enter et autres = action "
                      "à effet : " + _BROWSER_ACT + "expect comme browser_click)",
                      "params": {"key": "str", "expect": "str?", "channel_id": "int?"}, "fn": _browser_tool("press")},
    "browser_scroll": {"desc": "fait défiler la page (up/down/left/right) puis l'observe",
                       "params": {"direction": "str?"}, "fn": _browser_tool("scroll")},
    "browser_back": {"desc": "revient à la page précédente", "params": {}, "fn": _browser_tool("back")},
    "browser_verify": {"desc": "constate sur la page réelle qu'un texte est visible ; avec une action à effet non "
                       "vérifiée ou ambiguë (action_id), la marque vérifiée si ce texte n'était pas là avant elle",
                       "params": {"text": "str", "action_id": "int?"}, "fn": _browser_tool("verify")},
    "browser_download": {"desc": "télécharge le fichier du lien @eN dans l'espace de la tâche (filename sans chemin)",
                         "params": {"ref": "str", "filename": "str"}, "fn": _browser_tool("download")},
    "browser_upload": {"desc": "joint au champ fichier @eN un fichier préparé dans la boîte d'envoi du business ; "
                       + _BROWSER_ACT, "params": {"ref": "str", "filename": "str", "channel_id": "int?"},
                       "fn": _browser_tool("upload")},
    "agnes_probe": {"desc": "Vérifie que le service Agnes local (http://127.0.0.1:8765) répond ; optionnel, ne bloque pas",
                    "params": {"base_url": "str?"}, "fn": _agnes_probe},
    "agnes_submit_video": {"desc": "Demande génération vidéo Agnes (t2v) ; conserve immédiatement ID tâche Agnes pour reprise après crash ; idempotency_key requis",
                           "params": {"prompt": "str", "idempotency_key": "str", "base_url": "str?"}, "fn": _agnes_submit},
    "agnes_status": {"desc": "Suit statut génération Agnes sans appels excessifs (backoff) ; génération_id ou agnes_task_id",
                     "params": {"generation_id": "int?", "agnes_task_id": "str?", "base_url": "str?"}, "fn": _agnes_status},
    "agnes_download_video": {"desc": "Récupère MP4 Agnes, vérifie réellement (header, taille, SHA-256) ; preuve persistée",
                             "params": {"generation_id": "int", "base_url": "str?"}, "fn": _agnes_download},
    "agnes_stop_video": {"desc": "Demande arrêt génération Agnes en cours", "params": {"generation_id": "int", "base_url": "str?"}, "fn": _agnes_stop},
    "agnes_list_generations": {"desc": "Liste générations vidéo Agnes du business", "params": {"limit": "int?"}, "fn": _agnes_list},
    "agnes_generate_video_full": {"desc": "Cycle complet Agnes : soumission, suivi progression, récupération MP4, vérification livrable, persistance preuves, continuation autonome",
                                  "params": {"prompt": "str", "idempotency_key": "str", "base_url": "str?", "poll_timeout_s": "float?"}, "fn": _agnes_generate_full},
})
BROWSER_TOOLS = frozenset(name for name in TOOLS if name.startswith("browser_"))


# Compatibility names reference the single registry, not parallel implementations.
tools_desc = TOOLS.describe
_validate_tool_args = TOOLS.validate
_normalize_allowed_tools = TOOLS.normalize_allowed


ROLES = {
    "SOUT": "Recherche et veille : utilise search/browse pour trouver des infos utiles.",
    "CONVERT": "Monétisation : rédige offres, prix, CTA, contenu.",
    "FORGE": "Production : prépare les livrables de l'offre.",
    "GROWTH": "Qualité/distribution : prépare la publication (publish) et la prospection (send_message).",
    "LEDGER": "Data/finance : suit les coûts (economy_status), mémorise (remember).",
    "ORBIT": "CEO : planifie, arbitre, décide.",
}


# Hors Podalux, les rôles ne présupposent ni vidéo ni produit : ils décrivent des fonctions économiques.
GENERIC_ROLES = {
    "SOUT": "Observation du monde réel : demandes explicites, douleurs répétées, dépenses/budgets, alternatives payantes et canaux ; cite les sources consultées (record_observation).",
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
    """La date est un FAIT connu du système, pas une consigne d'écriture de requête.

    L'ancienne formulation (« privilégie {année} ») dictait au LLM comment rédiger une
    recherche et contredisait le contrat business (« n'ajoute pas l'année par défaut ») —
    run #63 : `2026 freelance request data migration budget cloud`. La date reste fournie ;
    décider si et comment s'en servir appartient au raisonnement.
    """
    if run is None or run.business == DEFAULT_BUSINESS:
        return ""
    return f"DATE ACTUELLE : {_today_iso()}.\n\n"


BUSINESS_SIGNAL_TYPES = {
    "explicit_request",
    "manual_work",
    "procurement",
    "job_demand",
    "complaint",
    "regulatory_deadline",
    "paid_alternative",
    "review_gap",
}
_BUSINESS_SIGNAL_REQUIRED = (
    "signal_type",
    "buyer",
    "pain",
    "money_signal",
    "evidence_url",
    "evidence_summary",
    "test_channel",
    "test_offer",
    "next_test",
)
_BUSINESS_SIGNAL_QUOTES = ("buyer_evidence", "pain_evidence", "money_evidence", "summary_evidence")
_BUSINESS_SIGNAL_UNKNOWN = {
    "", "unknown", "inconnu", "inconnue", "non connu", "non connue", "none", "n/a", "na",
}


def _business_signal_contract(target: int) -> str:
    """Objectif et critères de RÉSULTAT d'une mission business signal.

    Ce contrat décrit ce qu'OCTOPUS cherche, pas comment chercher. Aucune procédure de
    requête (nombre de termes, année, `site:`, guillemets, séquence de reformulation) :
    un LLM moderne sait déjà chercher, et lui dicter une méthode l'empêche de s'adapter.
    """
    target = max(1, int(target))
    return (
        "MODE BUSINESS SIGNAL — objectif : identifier des opportunités économiques TESTABLES, "
        "pas produire une étude générale.\n"
        f"Seuil minimal visé : {target} signaux qualifiés. N'invente jamais un signal pour "
        "atteindre ce seuil : mieux vaut rester en dessous avec des preuves solides que le "
        "dépasser avec des banalités. Le seuil est un minimum de mission, pas un quota par piste.\n"
        "Un signal n'est qualifié que si TOUT est présent :\n"
        "1) un acheteur/segment identifiable ;\n"
        "2) une douleur, tâche manuelle, obligation ou demande concrète ;\n"
        "3) une source réellement ouverte pendant cette mission ;\n"
        "4) un signal monétaire ou d'urgence (prix payé, alternative payante, recrutement, "
        "appel d'offres, dépense existante, échéance réglementaire créant du travail) ;\n"
        "5) un canal réaliste pour atteindre ce type d'acheteur ;\n"
        "6) une offre minimale et un prochain test faisable rapidement.\n"
        "Pour buyer, pain, money_signal et evidence_summary, fournis respectivement buyer_evidence, "
        "pain_evidence, money_evidence et summary_evidence : extraits littéraux de 8 à 600 caractères "
        "du texte acquis de la même page, jamais inventés. Ces citations prouvent leur présence, "
        "pas la justesse de l'interprétation : revue humaine nécessaire.\n"
        "Sources à PRIORISER : demandes explicites de prestataire ou d'outil, missions freelance, "
        "offres d'emploi révélant un travail coûteux, appels d'offres, forums où le problème est "
        "décrit, avis négatifs, comparatifs et prix de solutions payantes, obligations "
        "réglementaires qui créent une tâche concrète.\n"
        "À REJETER : définitions, statistiques macro seules, actualité générale, taille de marché, "
        "homepage de société, tendance sectorielle sans acheteur ni dépense, problème social large "
        "sans action achetable identifiable.\n"
        "Cherche librement des preuves concrètes, adapte ton approche aux résultats, et ouvre les "
        "sources importantes avec browse avant de conclure. Distingue ce que tu observes de ce que "
        "tu infères ; n'invente aucune preuve.\n"
        "Ne construis rien et ne recommande pas encore un business : collecte et qualifie des signaux.\n"
    )


def _business_signal_task_context(target: int, role: str) -> str:
    """Contrat de mission + rappel des champs obligatoires, pour une sous-tâche."""
    role = str(role or "").upper()
    base = (
        _business_signal_contract(target)
        + "\nPour chaque candidat retenu, conserve précisément buyer, pain, money_signal, "
          "evidence_url, evidence_summary, buyer_evidence, pain_evidence, money_evidence, "
          "summary_evidence, test_channel, test_offer et next_test. Si un champ manque, "
          "le candidat n'est pas qualifié."
    )
    if role == "SOUT":
        return base + ("\nTON RÔLE ICI : découverte. Ouvre les pages prometteuses et conserve "
                       "des preuves concrètes.")
    return base + (
        f"\nTON RÔLE ICI ({role or 'AVAL'}) : exploitation des preuves amont. Réutilise d'abord "
        "les artefacts transmis ; ne recherche que ce qui manque réellement."
    )

_TRACKING_QUERY_KEYS = {
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid",
}


def _canonical_evidence_url(url: str) -> str:
    """Normalisation minimale pour comparer URL demandée, redirigée et URL citée par le LLM."""
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if not parts.hostname or parts.username or parts.password:
        return ""
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        query.append((key, value))
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query, doseq=True), ""))


def _evidence_text(text: str) -> str:
    """Comparaison littérale seulement : Unicode, casse et espaces, pas de synonymes."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _verified_browse_pages(results: list[dict]) -> list[dict]:
    """Payloads d'acquisition du runtime, jamais des URLs demandées seules ou du texte LLM.

    Chaque capture reste séparée : ne pas assembler des citations provenant de pages
    ou de versions différentes. Les aliases ne valent que pour cette acquisition réussie.
    """
    from . import browser
    pages = []
    for subtask in results or []:
        for step in subtask.get("steps") or []:
            if step.get("tool") != "browse":
                continue
            meta, data = step.get("browse_meta"), step.get("result_data")
            if not isinstance(meta, dict) or not isinstance(data, dict) or data.get("refused"):
                continue
            page = data.get("page")
            if not isinstance(page, dict):
                continue
            if (
                not browser.is_public_text_acquisition(page)
                or not browser.is_public_text_acquisition_meta(meta)
            ):
                continue
            text = page.get("main_text")
            if not isinstance(text, str) or len(_evidence_text(text)) < 100:
                continue
            requested = _canonical_evidence_url((step.get("args") or {}).get("url", ""))
            final = _canonical_evidence_url(page.get("final_url", ""))
            if (not requested or not final
                    or requested != _canonical_evidence_url(page.get("requested_url", ""))
                    or final != _canonical_evidence_url(meta.get("url", ""))):
                continue
            if any(marker in text.lower() for marker in browser.PUBLIC_BLOCK_MARKERS):
                continue
            pages.append({"urls": {requested, final}, "text": _evidence_text(text),
                          "final_url": page["final_url"], "fetched_at": page["fetched_at"]})
    return pages


def _verified_browse_urls(results: list[dict]) -> set[str]:
    return {url for page in _verified_browse_pages(results) for url in page["urls"]}


def _qualify_business_signals(raw_signals, results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Gate structurel : acquisition + citations littérales, pas d'entailment sémantique."""
    pages = _verified_browse_pages(results)
    accepted, rejected = [], []
    seen = set()
    if not isinstance(raw_signals, list):
        return [], [{"signal": raw_signals, "reasons": ["signals_not_list"]}]
    for raw in raw_signals:
        if not isinstance(raw, dict):
            rejected.append({"signal": raw, "reasons": ["signal_not_object"]})
            continue
        item = {key: raw[key].strip() if isinstance(raw.get(key), str) else ""
                for key in (*_BUSINESS_SIGNAL_REQUIRED, *_BUSINESS_SIGNAL_QUOTES)}
        reasons = []
        if item["signal_type"] not in BUSINESS_SIGNAL_TYPES:
            reasons.append("unsupported_signal_type")
        for key in _BUSINESS_SIGNAL_REQUIRED[1:]:
            if item[key].lower() in _BUSINESS_SIGNAL_UNKNOWN:
                reasons.append(f"missing_{key}")
        canonical_evidence_url = _canonical_evidence_url(item["evidence_url"])
        sources = [page for page in pages if canonical_evidence_url in page["urls"]]
        if not canonical_evidence_url or not sources:
            reasons.append("evidence_url_not_opened")
        quotes = {field: _evidence_text(item[field]) for field in _BUSINESS_SIGNAL_QUOTES}
        for field, quote in quotes.items():
            if not 8 <= len(quote) <= 600 or quote in _BUSINESS_SIGNAL_UNKNOWN:
                reasons.append(f"invalid_{field}")
            elif not any(quote in page["text"] for page in sources):
                reasons.append(f"{field}_not_in_source")
        matching = next((page for page in sources if all(
            quote and quote in page["text"] for quote in quotes.values())), None)
        if sources and matching is None:
            reasons.append("quotes_not_in_same_acquisition")
        key = (
            item["buyer"].lower(),
            item["pain"].lower(),
            _canonical_evidence_url(matching["final_url"]) if matching else canonical_evidence_url,
        )
        if key in seen:
            reasons.append("duplicate_signal")
        if reasons:
            rejected.append({"signal": raw, "reasons": sorted(set(reasons))})
            continue
        seen.add(key)
        item["action_fields_nature"] = "inferred"
        # Provenance calculée depuis l'outil, non depuis des champs proposés par le LLM.
        item["evidence_acquisition"] = {"final_url": matching["final_url"],
                                        "fetched_at": matching["fetched_at"]}
        accepted.append(item)
    return accepted, rejected


# --- Revue d'actionnabilité : couche de MESURE posée après le gate #94 ---
#
# Le gate #94 prouve la structure (acquisition réelle + citations littérales), pas
# l'actionnabilité économique actuelle. La revue ci-dessous mesure ce second aspect,
# signal par signal, sans jamais filtrer, corriger ni compléter les signaux.

_BUSINESS_SIGNAL_REVIEW_CLASSES = (
    "actionable_now",
    "market_evidence",
    "historical_or_closed",
    "unsupported",
    "uncertain",
)
# Borne de coût de la mesure : les citations complètes voyagent déjà dans le signal.
_BUSINESS_SIGNAL_REVIEW_TEXT_CHARS = 8000


def _validate_business_signal_review(data: dict) -> dict:
    """Sortie structurée du reviewer : exactement une classification connue + justification.

    Toute sortie hors de ce contrat est rejetée en amont (InvalidOutput côté passerelle) ;
    cette normalisation garantit aussi qu'aucun champ supplémentaire n'est stocké.
    """
    if not isinstance(data, dict):
        raise ValueError("revue_non_objet")
    classification = str(data.get("classification") or "").strip()
    if classification not in _BUSINESS_SIGNAL_REVIEW_CLASSES:
        raise ValueError("classification_inconnue")
    justification = str(data.get("justification") or "").strip()
    if not justification:
        raise ValueError("justification_manquante")
    return {"classification": classification, "justification": justification[:600]}


def _business_signal_review_messages(signal: dict, acquisition_text: str) -> list[dict]:
    """Prompt du reviewer : uniquement le signal proposé et le texte de son acquisition.

    Le reviewer ne reçoit ni l'objectif de mission, ni le rapport de synthèse, ni les
    autres signaux : sa lecture est indépendante de l'appel de synthèse qui a rédigé
    le signal. La consigne reste conceptuelle (pas de règles métier codées).
    """
    system = (
        "Voici un signal économique proposé et sa preuve source.\n"
        "Évalue indépendamment si cette preuve démontre une opportunité économique "
        "actuellement testable.\n"
        "Ne complète aucune information manquante et ne corrige pas le signal.\n"
        "Réponds en JSON avec exactement UNE classification parmi : "
        '"actionable_now", "market_evidence", "historical_or_closed", "unsupported", '
        '"uncertain" ; et une justification courte. '
        'Forme : {"classification": "...", "justification": "..."}'
    )
    payload = {
        "signal_propose": signal,
        "preuve_source": acquisition_text[:_BUSINESS_SIGNAL_REVIEW_TEXT_CHARS],
    }
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def _review_business_signals(business_signals: list[dict], results: list[dict]) -> list[dict]:
    """Revue LLM indépendante de chaque signal structurellement valide (mesure seule).

    Une entrée par signal, quoi qu'il arrive. Une panne du reviewer (passerelle, sortie
    invalide) dégrade uniquement la revue concernée : la mission, le gate #94 et le
    comptage structurel restent intacts, et aucune classification n'est inventée.
    """
    pages = _verified_browse_pages(results)
    by_acquisition = {(p["final_url"], p["fetched_at"]): p for p in pages}
    reviews = []
    for index, signal in enumerate(business_signals or []):
        acquisition = signal.get("evidence_acquisition") or {}
        # Présence garantie par le gate sur les mêmes results ; sinon bug, à ne pas masquer.
        page = by_acquisition[(acquisition.get("final_url"), acquisition.get("fetched_at"))]
        entry = {"signal_index": index, "evidence_url": signal.get("evidence_url", "")}
        messages = _business_signal_review_messages(signal, page["text"])
        try:
            verdict = deepseek.call_json(
                "REVUE", "revue_signal", deepseek.config.MODEL_FLASH, messages,
                max_tokens=1000, validate=_validate_business_signal_review)
        except llm.GatewayError as exc:
            error = f"{type(exc).__name__}: {exc}"
            db.post("REVUE", f"revue d'actionnabilité dégradée : {error[:120]}")
            entry.update({"status": "degraded", "classification": None, "justification": None,
                          "error": error[:1500]})
        else:
            entry.update({"status": "reviewed", "classification": verdict["classification"],
                          "justification": verdict["justification"]})
        reviews.append(entry)
    return reviews


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
              search_browse_selector: str = "first", resume_steps: list | None = None,
              checkpoint=None) -> dict:
    """Un agent (rôle) poursuit un objectif librement via la boucle ReAct.

    `conversational=True` → l'agent répond à un message humain (pas un objectif).
    """
    allowed_tools = _normalize_allowed_tools(allowed_tools)
    if search_browse_selector not in {"first", "evidence_relevance", "business_signal_relevance"}:
        raise ValueError(f"search_browse_selector inconnu : {search_browse_selector}")
    with journal.run(_business(business), "agent", label=f"{role} : {goal}",
                     budget_usd=None if journal.current_run() and journal.current_run().budgets
                     else deepseek.config.CYCLE_BUDGET_USD):
        token = _ROLE.set(role)
        try:
            with cancel.scope(), web_guard.session(), browser_workspace.mission_scope(), _search_cache():
                return _run_agent(
                    role, goal, max_steps, conversational, allowed_tools,
                    search_browse_lockstep=search_browse_lockstep,
                    search_browse_selector=search_browse_selector,
                    resume_steps=resume_steps, checkpoint=checkpoint,
                )
        finally:
            _ROLE.reset(token)


def _react_prompt_context(context: list[dict], steps: list[dict]) -> list[dict]:
    if len(context) <= 10:
        return context
    older = []
    for step in steps[:-2]:
        if step.get("tool") in {"search", "browse", "record_observation", "browser_navigate", "browser_snapshot"}:
            older.append({"tool": step.get("tool"), "result": str(step.get("result") or "")[:200],
                          "urls": (step.get("result_urls") or [])[:3]})
    summary = [{"role": "user", "content": "Observations antérieures : " +
                json.dumps(older[-8:], ensure_ascii=False)}] if older else []
    return context[:2] + summary + context[-4:]


def _run_agent(role: str, goal: str, max_steps: int, conversational: bool,
               allowed_tools: set[str] | None = None, *,
               search_browse_lockstep: bool = False,
               search_browse_selector: str = "first", resume_steps: list | None = None,
               checkpoint=None) -> dict:
    system, first_user, done_label = build_prompts(role, goal, conversational, allowed_tools)
    context = [{"role": "system", "content": system},
               {"role": "user", "content": first_user}]
    steps = list(resume_steps or [])
    for step in steps:
        context.append({"role": "assistant", "content": json.dumps(
            {"tool": step.get("tool"), "args": step.get("args") or {}}, ensure_ascii=False)[:600]})
        context.append({"role": "user", "content": "Observation déjà acquise, ne pas répéter : " +
                        str(step.get("result") or "")})
    last_sig = None
    repeat = 0
    lockstep_url = None
    lockstep_selection = None
    for i in range(len(steps), max_steps):
        if cancel.requested():
            status = "timeout" if cancel.timed_out() else "cancelled"
            db.post(role, "durée maximale atteinte" if status == "timeout" else "arrêt demandé par l'humain")
            return {"role": role, "steps": steps, "final": "(timeout)" if status == "timeout" else "(arrêt demandé)", "execution_status": status}
        # Garde-budget : on arrête l'agent si le budget du run est atteint.
        if _budget_exhausted():
            db.post(role, "budget dépassé — arrêt du run")
            return {"role": role, "steps": steps, "final": "(budget dépassé)", "execution_status": "budget_exceeded"}
        forced_lockstep = lockstep_url is not None
        forced_selection = lockstep_selection if forced_lockstep else None
        if forced_lockstep:
            # Expérience opt-in : l'URL choisie provient littéralement du SEARCH précédent.
            r = {"tool": "browse", "args": {"url": lockstep_url}}
            lockstep_url = None
            lockstep_selection = None
        else:
            try:
                economical = journal.current_run() and journal.current_run().profile == "economical"
                prompt_context = _react_prompt_context(context, steps) if economical else context + [
                    {"role": "user", "content": "Choisis ta prochaine action (JSON)."}]
                r = deepseek.call_json(role, "action", MODEL, prompt_context,
                    max_tokens=500 if economical else 2000,
                    validate=(_validate_action_contract if journal.current_run()
                              and journal.current_run().profile == "economical" else None))
            except llm.GatewayError as exc:
                error = f"{type(exc).__name__}: {exc}"[:1500]
                return {"role": role, "steps": steps, "final": "(LLM indisponible)",
                        "execution_status": "llm_unavailable", "execution_error": error}
        if "final" in r:
            db.post(role, f"{done_label} : {str(r['final'])[:120]}")
            return {"role": role, "steps": steps, "final": r["final"], "execution_status": "completed"}
        tool = r.get("tool")
        raw_args = r.get("args")
        args = {} if raw_args is None else raw_args
        # L'action choisie entre dans l'historique : sans elle, le modèle ne voyait que les résultats
        # et relançait les mêmes requêtes (audit M2).
        context.append({"role": "assistant", "content": json.dumps(r, ensure_ascii=False)[:600]})
        if tool not in TOOLS:
            context.append({"role": "user", "content": f"outil inconnu : {tool}. Disponibles : {list(TOOLS)}"})
            steps.append({"step": i + 1, "tool": tool, "result": "inconnu"})
            if checkpoint:
                checkpoint(steps)
            continue

        refusal, result = None, None
        try:
            refusal, result = TOOLS.dispatch(tool, args, allowed_tools)
            if refusal is not None:
                result = {"refused": True, "tool": tool, "reason": refusal}
                db.post(role, f"refus outil {tool} : {refusal}")
            result_str = _tool_result_view(tool, result)
        except cancel.Cancelled:
            status = "timeout" if cancel.timed_out() else "cancelled"
            db.post(role, "durée maximale atteinte" if status == "timeout" else "arrêt demandé par l'humain")
            return {"role": role, "steps": steps, "final": "(timeout)" if status == "timeout" else "(arrêt demandé)", "execution_status": status}
        except Exception as e:
            result_str = f"erreur : {str(e)[:2048]}"
        if (
            search_browse_lockstep
            and refusal is None
            and result is not None
            and tool == "search"
            and (allowed_tools is None or "browse" in allowed_tools)
        ):
            # EXPERIMENTAL CONTROL — pas un principe architectural. Le lockstep force
            # l'acquisition d'une URL réellement retournée par SEARCH (jamais inventée) afin
            # de mesurer une chaîne de preuve. Il n'impose PAS une séquence cognitive : hors
            # lockstep, l'agent reste libre d'ouvrir n'importe quelle URL quand il le juge bon.
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
            elif search_browse_selector in {"evidence_relevance", "business_signal_relevance"}:
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
        if checkpoint:
            step_record["args"] = args
        if tool in {"search", "browse"} or str(tool).startswith("browser_"):
            if tool in {"search", "browse"}:
                step_record["args"] = dict(args) if isinstance(args, dict) else args
            if result is not None:
                if tool == "browse" and isinstance(result, dict) and isinstance(result.get("page"), dict):
                    step_record["result_data"] = {
                        "url": result.get("url"),
                        "source": result.get("source"),
                        "page": result["page"],
                    }
                else:
                    step_record["result_data"] = result
        if refusal is not None:
            step_record["result_data"] = result
        if isinstance(result, dict) and result.get("refused") and technical_refusal(str(result.get("reason") or "")):
            step_record["failure_class"] = "technical"
            context.append({"role": "user", "content":
                            "Erreur technique ou source invalide. Corrige les arguments ou abandonne cette source "
                            "et utilise une autre source publique. Cela n'exige aucune permission humaine."})
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
        if checkpoint:
            checkpoint(steps)
    return {"role": role, "steps": steps, "final": "(max steps atteint)", "execution_status": "step_limit"}


def run_mission(goal: str, max_steps_per_agent: int = 8, *, business: str | None = None,
                allowed_tools: set[str] | None = None, profile: str | None = None,
                search_browse_lockstep: bool = False,
                search_browse_selector: str = "first",
                business_signal_focus: bool = False,
                business_signal_target: int = 3, max_duration_s: float = 900,
                determination: bool = False, resume: dict | None = None, checkpoint=None) -> dict:
    """ORBIT planifie puis délègue aux rôles (multi-agents via le runtime).

    Le profil explicite est hérité par les runs agents imbriqués via le journal.
    """
    allowed_tools = _normalize_allowed_tools(allowed_tools)
    if not math.isfinite(max_duration_s) or max_duration_s <= 0:
        raise ValueError("max_duration_s doit être une durée finie strictement positive")
    if search_browse_selector not in {"first", "evidence_relevance", "business_signal_relevance"}:
        raise ValueError(f"search_browse_selector inconnu : {search_browse_selector}")
    # A cold mission has no inherited business objective. Existing runs and
    # explicitly selected legacy businesses retain their scope.
    mission_business = business or (journal.current_run().business if journal.current_run() else "octopus")
    with journal.run(mission_business, "mission", label=goal,
                     budget_usd=None if journal.current_run() and journal.current_run().budgets
                     else deepseek.config.CYCLE_BUDGET_USD,
                     profile=profile):
        with cancel.scope(max_duration_s=max_duration_s), web_guard.session(), \
                browser_workspace.mission_scope(), _search_cache():
            return _run_mission(
                goal, max_steps_per_agent, allowed_tools,
                search_browse_lockstep=search_browse_lockstep,
                search_browse_selector=search_browse_selector,
                business_signal_focus=business_signal_focus,
                business_signal_target=max(1, int(business_signal_target)),
                determination=determination,
                resume=resume, checkpoint=checkpoint,
            )


def _validate_plan_contract(data: dict) -> dict:
    proposed = data.get("tasks")
    if not isinstance(proposed, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("role"), str)
            or not isinstance(item.get("task"), str) or not item["task"].strip()
            for item in proposed):
        raise ValueError("planification : liste de tâches structurées requise")
    return data


def _validate_synthesis_contract(data: dict, determination: bool) -> dict:
    if not isinstance(data.get("rapport"), str) or not data["rapport"].strip():
        raise ValueError("synthèse : rapport absent ou vide")
    if determination:
        choice = data.get("determination")
        if (not isinstance(choice, dict) or choice.get("action") not in {"continue", "pause", "request_permission"}
                or not all(isinstance(choice.get(key), str) for key in ("reason", "next_goal", "permission"))
                or not choice["reason"].strip()
                or (choice["action"] == "request_permission" and not choice["permission"].strip())):
            raise ValueError("synthèse : détermination structurée invalide")
    return data


def _validate_action_contract(data: dict) -> dict:
    if isinstance(data.get("final"), str) and data["final"].strip():
        return data
    if data.get("tool") in TOOLS and isinstance(data.get("args", {}), dict):
        return data
    raise ValueError("action : final ou outil et arguments valides requis")


MAX_PLAN_TASKS = 5

# Artefacts suffisamment petits pour circuler entre sous-agents sans recopier toute leur trace.
# Le but est la continuité de travail : une URL/source déjà trouvée doit rester exploitable même
# si l'agent amont termine sur son budget d'étapes avant d'avoir produit un final détaillé.
_HANDOFF_TOOLS = {"search", "browse", "record_observation", "economy_status", "resources_status",
                  "browser_navigate", "browser_snapshot", "browser_verify", "browser_download"}


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
        "artifacts": artifacts[-8:],
        "error": str(result.get("execution_error") or "")[:300],
    }


def _mission_unavailable(tasks: list[dict], results: list[dict], status: str, error: str) -> dict:
    """An execution failure is not a model-authored conclusion or strategy evidence."""
    output = {"plan": tasks, "results": results,
              "rapport": "Mission interrompue ; résultats bruts conservés, aucune synthèse validée.",
              "execution_status": status, "synthesis_status": "degraded",
              "synthesis_error": str(error)[:1500]}
    db.decide("ORBIT", "mission_done", {k: v for k, v in output.items() if k not in {"plan", "results"}})
    return output


def _run_mission(goal: str, max_steps_per_agent: int, allowed_tools: set[str] | None = None, *,
                 search_browse_lockstep: bool = False,
                 search_browse_selector: str = "first",
                 business_signal_focus: bool = False,
                 business_signal_target: int = 3, determination: bool = False,
                 resume: dict | None = None, checkpoint=None) -> dict:
    from .search import SEARCH_PURPOSE_BUSINESS, SEARCH_PURPOSE_GENERAL
    pro = deepseek.config.MODEL_PRO
    if cancel.requested():
        return _mission_unavailable([], [], "timeout" if cancel.timed_out() else "cancelled",
                                    "Durée maximale atteinte" if cancel.timed_out() else "Arrêt demandé")
    current = journal.current_run()
    economical = current is not None and current.profile == "economical"
    planner_roles = GENERIC_ROLES if current is not None and current.business != DEFAULT_BUSINESS else ROLES
    role_catalog = "\n".join(f"- {name}: {desc}" for name, desc in planner_roles.items())
    planner_freshness = _freshness_context(current)
    business_signal_context = _business_signal_contract(business_signal_target) if business_signal_focus else ""
    plan_sys = (
        "Tu es ORBIT, l'orchestrateur de la mission. "
        f"{planner_freshness}"
        f"{business_signal_context}"
        "Utilise les rôles comme des responsabilités spécialisées, pas comme des workers interchangeables.\n\n"
        f"Rôles disponibles et responsabilités :\n{role_catalog}\n\n"
        "Décompose l'objectif en quelques sous-tâches, chacune assignée à UN rôle dont la responsabilité "
        "correspond réellement au travail demandé. N'assigne pas une tâche à un rôle seulement pour l'occuper "
        "et ne duplique pas la même collecte chez plusieurs rôles sans nécessité. "
        "Un même rôle peut recevoir plusieurs sous-tâches distinctes si elles relèvent de sa spécialité ; "
        "ne force jamais la diversité des rôles. "
        f"Chaque sous-tâche doit être réalisable en au plus {max_steps_per_agent} étapes ; si plusieurs pistes "
        "indépendantes demandent chacune plusieurs actions, répartis-les au lieu de surcharger un seul agent. "
        "N'ajoute une tâche aval (offre, production, diffusion) que si un artefact amont exploitable existe "
        "ou va exister : une mission sans preuve ne doit pas passer à la construction. "
        "Les artefacts utiles des étapes amont (recherches, pages ouvertes, observations et statuts) seront transmis "
        "automatiquement au sous-agent suivant : il doit les réutiliser avant de recommencer une collecte équivalente. "
        "Réponds en JSON : "
        '{"tasks":[{"role":"...","task":"..."}]}'
    )
    if resume and resume.get("plan"):
        plan = _validate_plan_contract({"tasks": resume["plan"]})
    else:
        try:
            plan = deepseek.call_json("ORBIT", "planification", pro,
                                  [{"role": "system", "content": plan_sys},
                                   {"role": "user", "content": goal}],
                                  reasoning="high", max_tokens=700 if economical else 2000,
                                  validate=_validate_plan_contract if economical else None)
        except llm.GatewayError as exc:
            return _mission_unavailable([], [], "llm_unavailable", f"{type(exc).__name__}: {exc}")
    proposed = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
    # Garde-fou budgétaire, pas une consigne du prompt : au-delà de 5, chaque
    # sous-tâche coûte une boucle ReAct complète de plus.
    tasks = [t for t in proposed if isinstance(t, dict)][:MAX_PLAN_TASKS]
    if len(proposed) > len(tasks):
        db.post("ORBIT", f"plan tronqué : {len(proposed)} sous-tâches proposées, {len(tasks)} gardées")
    if cancel.requested():
        status = "timeout" if cancel.timed_out() else "cancelled"
        return _mission_unavailable(tasks, [], status, "Durée maximale atteinte" if status == "timeout" else "Arrêt demandé")
    # Replan unique de granularité (H3 #68/#69) : une mission multi-signaux concentrée dans
    # une seule sous-tâche plafonne chaque voie de découverte au budget d'étapes d'un agent.
    # Le feedback ne porte que sur la faisabilité du plan par rapport au budget ; il ne
    # prescrit aucun rôle, aucune source, aucune requête ni aucune stratégie. Le second
    # plan est accepté tel quel : pas de boucle de retry, pas de minimum de sous-tâches.
    if not resume and business_signal_focus and business_signal_target > 1 and len(tasks) == 1:
        db.post("ORBIT", "mission multi-signaux concentrée en une seule sous-tâche : replan unique")
        feedback = (
            f"Le plan précédent concentre une mission visant plusieurs signaux dans une seule boucle "
            f"limitée à {max_steps_per_agent} étapes.\n"
            "Décompose les voies de découverte indépendantes en plusieurs sous-tâches autonomes afin que "
            "chacune puisse utiliser son propre budget d'étapes.\n"
            "Le même rôle, notamment SOUT, peut être utilisé plusieurs fois ; ne force pas la diversité des rôles.\n"
            "Ne prescris pas de sources, de requêtes, de segments ou de stratégie, et n'exige pas une "
            "sous-tâche par signal : tu restes libre de déterminer la bonne décomposition."
        )
        try:
            plan = deepseek.call_json("ORBIT", "planification", pro,
                                      [{"role": "system", "content": plan_sys},
                                       {"role": "user", "content": goal},
                                       {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)},
                                       {"role": "user", "content": feedback}],
                                      reasoning="high", max_tokens=700 if economical else 2000,
                                      validate=_validate_plan_contract if economical else None)
        except llm.GatewayError as exc:
            return _mission_unavailable(tasks, [], "llm_unavailable", f"{type(exc).__name__}: {exc}")
        proposed = plan.get("tasks") if isinstance(plan.get("tasks"), list) else []
        tasks = [t for t in proposed if isinstance(t, dict)][:MAX_PLAN_TASKS]
        if len(proposed) > len(tasks):
            db.post("ORBIT", f"plan tronqué : {len(proposed)} sous-tâches proposées, {len(tasks)} gardées")
    db.post("ORBIT", f"mission : {goal[:70]} → {len(tasks)} sous-tâches")

    results = list((resume or {}).get("results") or [])
    partial_steps = list((resume or {}).get("agent_steps") or [])

    def save_progress(agent_steps=None):
        if checkpoint:
            checkpoint({"plan": tasks, "results": results, "agent_steps": agent_steps or [],
                        "collect_complete": len(results) == len(tasks)})

    save_progress(partial_steps)
    for t in tasks[len(results):]:
        if cancel.requested():
            status = "timeout" if cancel.timed_out() else "cancelled"
            db.post("ORBIT", "mission interrompue : " + status)
            return _mission_unavailable(tasks, results, status, "Durée maximale atteinte" if status == "timeout" else "Arrêt demandé")
        role = t.get("role", "ORBIT")
        if role not in ROLES:
            role = "ORBIT"
        task = t.get("task", "")
        original = task
        if business_signal_focus:
            task = f"{task}\n\n{_business_signal_task_context(business_signal_target, role)}"
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
        effective_selector = (
            "business_signal_relevance"
            if business_signal_focus and search_browse_selector == "evidence_relevance"
            else search_browse_selector
        )
        # L'intention de recherche se propage aux outils du sous-agent : elle choisit la
        # politique de providers et isole le cache. Pas de nouveau paramètre d'outil.
        purpose = (SEARCH_PURPOSE_BUSINESS if business_signal_focus else SEARCH_PURPOSE_GENERAL)
        with search_purpose(purpose):
            recovery = {"resume_steps": partial_steps, "checkpoint": save_progress} if checkpoint else {}
            r = run_agent(
                role,
                task,
                max_steps=max_steps_per_agent,
                allowed_tools=allowed_tools,
                search_browse_lockstep=search_browse_lockstep,
                search_browse_selector=effective_selector,
                **recovery,
            )
        subresult = {"role": role, "task": original,
                     "final": r.get("final"), "steps": r.get("steps"),
                     "execution_status": r.get("execution_status", "unknown"),
                     "execution_error": r.get("execution_error")}
        if r.get("execution_status") in {"cancelled", "timeout", "budget_exceeded", "llm_unavailable"}:
            save_progress(r.get("steps"))
            return _mission_unavailable(tasks, results + [subresult], r["execution_status"],
                                        r.get("execution_error") or r.get("final"))
        results.append(subresult)
        partial_steps = []
        save_progress()

    if not results or all(not r.get("steps") and r.get("execution_status") != "completed"
                          for r in results):
        return _mission_unavailable(tasks, results, "no_work", "Aucun travail exécuté")

    if cancel.requested():
        status = "timeout" if cancel.timed_out() else "cancelled"
        db.post("ORBIT", "mission interrompue avant la synthèse : " + status)
        return _mission_unavailable(tasks, results, status, "Durée maximale atteinte" if status == "timeout" else "Arrêt demandé")
    signal_schema = ""
    if business_signal_focus:
        signal_schema = (
            "\nMODE BUSINESS SIGNAL : ne retiens dans business_signals QUE les candidats satisfaisant "
            "tous les critères du contrat ci-dessus. Chaque evidence_url doit être une URL effectivement "
            "acquise dans les étapes. Fournis buyer_evidence, pain_evidence, money_evidence et "
            "summary_evidence : citations exactes de 8 à 600 caractères du texte de cette même "
            "acquisition. Pour chaque signal, choisis exactement UNE acquisition réellement ouverte : "
            "evidence_url identifie cette acquisition et les quatre champs *_evidence proviennent tous "
            "de son texte. Chaque champ *_evidence est UNE SEULE sous-chaîne continue, copiée mot pour "
            "mot : ne concatène jamais plusieurs fragments, n'insère jamais « ... » ni « … » pour les "
            "relier, ne paraphrase pas. Si un même document a été acquis sous plusieurs représentations "
            "(par ex. page HTML et PDF), n'en utilise qu'une seule, sans les mélanger ; si le PDF contient "
            "les quatre preuves, evidence_url est l'URL exacte du PDF acquis et les quatre citations "
            "viennent du PDF. Les champs analytiques (buyer, pain, money_signal, evidence_summary, "
            "test_channel, test_offer, next_test) restent rédigés selon les règles ci-dessus. "
            "Le contrôle est littéral, non sémantique ; revue humaine nécessaire.\n"
            "Réponds en JSON avec exactement la forme : "
            '{\"rapport\":\"...\",\"business_signals\":[{'
            '\"signal_type\":\"explicit_request|manual_work|procurement|job_demand|complaint|regulatory_deadline|paid_alternative|review_gap\",'
            '\"buyer\":\"...\",\"pain\":\"...\",\"money_signal\":\"...\",'
            '\"evidence_url\":\"https://...\",\"evidence_summary\":\"...\",'
            '\"buyer_evidence\":\"...\",\"pain_evidence\":\"...\",'
            '\"money_evidence\":\"...\",\"summary_evidence\":\"...\",'
            '\"test_channel\":\"...\",\"test_offer\":\"...\",\"next_test\":\"...\"}]}'
        )
    syn_sys = (
        "Tu es ORBIT. Synthétise les résultats des sous-tâches en un rapport final concis. "
        "Respecte aussi toutes les contraintes de l'objectif original : une synthèse ne doit pas réintroduire "
        "une recommandation, décision, action ou autre contenu que la mission interdisait. "
        "N'introduis aucun fait, chiffre, canal, ressource ou résultat absent des sous-tâches et de leurs résultats d'outils. "
        "Si un sous-agent affirme quelque chose sans preuve visible dans ses étapes, qualifie-le de non vérifié ou d'inférence, "
        "jamais de fait observé. "
        # Source unique des critères : le contrat n'est pas recopié dans le schéma de sortie.
        + (f"\n{_business_signal_contract(business_signal_target)}\n" if business_signal_focus else "")
        + signal_schema
        + (" Réponds en JSON : {\"rapport\":\"...\"}" if not business_signal_focus else "")
    )
    if determination:
        syn_sys += (
            '\nAjoute "determination":{"action":"continue|pause|request_permission",'
            '"reason":"raison liée aux observations", "next_goal":"prochaine recherche précise ou vide",'
            '"permission":"permission manquante ou vide"}. '
            "Décide de la suite à partir des résultats réellement acquis. Pause si rien n'est justifié. "
            "Demande une permission si une action nécessaire dépasse les limites. "
            "Un rapport n'est pas une preuve et une décision n'accorde aucune permission."
        )
    synthesis_input = {
        "objectif_original": goal,
        "resultats_sous_taches": _mission_prompt_results(results),
    }
    try:
        syn = deepseek.call_json("ORBIT", "determination" if determination else "synthese", pro,
                                 [{"role": "system", "content": syn_sys},
                                  {"role": "user", "content": json.dumps(synthesis_input, ensure_ascii=False)}],
                                 reasoning="high", max_tokens=1600 if economical else 4000,
                                 validate=(lambda data: _validate_synthesis_contract(data, determination))
                                 if economical else None)
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
            "execution_status": "synthesis_unavailable",
            "synthesis_status": "degraded",
            "synthesis_error": error,
        }

    if cancel.requested():
        status = "timeout" if cancel.timed_out() else "cancelled"
        return _mission_unavailable(tasks, results, status, "Durée maximale atteinte" if status == "timeout" else "Arrêt demandé")
    rapport = syn.get("rapport", "")
    if not isinstance(rapport, str) or not rapport.strip():
        return _mission_unavailable(tasks, results, "invalid_synthesis", "Rapport absent ou vide")
    business_signals, rejected_signals = ([], [])
    business_signal_reviews = []
    if business_signal_focus:
        business_signals, rejected_signals = _qualify_business_signals(
            syn.get("business_signals"),
            results,
        )
        if cancel.requested():
            status = "timeout" if cancel.timed_out() else "cancelled"
            return _mission_unavailable(tasks, results, status, "Durée maximale atteinte" if status == "timeout" else "Arrêt demandé")
        # Couche de MESURE après #94 : la revue ne filtre ni ne modifie les signaux ;
        # elle ajoute une lecture indépendante de leur actionnabilité actuelle.
        business_signal_reviews = _review_business_signals(business_signals, results)
    if cancel.requested():
        status = "timeout" if cancel.timed_out() else "cancelled"
        return _mission_unavailable(tasks, results, status, "Durée maximale atteinte" if status == "timeout" else "Arrêt demandé")
    actionable_business_signal_count = sum(
        1 for review in business_signal_reviews
        if review.get("classification") == "actionable_now"
    )
    if not business_signal_reviews:
        business_signal_review_status = "no_signals"
    elif any(review.get("status") == "degraded" for review in business_signal_reviews):
        business_signal_review_status = "degraded"
    else:
        business_signal_review_status = "reviewed"
    decision_payload = {"rapport": rapport, "synthesis_status": "validated"}
    if business_signal_focus:
        decision_payload["business_signal_count"] = len(business_signals)
        decision_payload["business_signal_rejected"] = len(rejected_signals)
        # Mesure séparée du gate structurel : qualified_business_signal_count reste la
        # métrique #94 ; seule la classification actionable_now alimente ce compteur.
        decision_payload["actionable_business_signal_count"] = actionable_business_signal_count
        decision_payload["business_signal_review_status"] = business_signal_review_status
    db.decide("ORBIT", "mission_done", decision_payload)
    db.post("ORBIT", f"mission terminée : {rapport[:80]}")
    output = {"plan": tasks, "results": results, "rapport": rapport, "synthesis_status": "validated",
              "execution_status": ("completed" if all(r.get("execution_status") == "completed"
                                                       for r in results) else "incomplete")}
    if determination:
        choice = syn.get("determination")
        if (not isinstance(choice, dict) or choice.get("action") not in {"continue", "pause", "request_permission"}
                or not all(isinstance(choice.get(k), str) for k in ("reason", "next_goal", "permission"))
                or not choice["reason"].strip()
                or (choice["action"] == "request_permission" and not choice["permission"].strip())):
            choice = {"action": "pause", "reason": "Décision structurée absente ou invalide.",
                      "next_goal": "", "permission": ""}
        output["determination"] = {k: v[:3000] for k, v in choice.items()
                                   if k in {"action", "reason", "next_goal", "permission"}}
    if business_signal_focus:
        output["business_signals"] = business_signals
        output["business_signal_rejections"] = rejected_signals
        output["business_signal_reviews"] = business_signal_reviews
        output["actionable_business_signal_count"] = actionable_business_signal_count
        output["business_signal_review_status"] = business_signal_review_status
    return output
