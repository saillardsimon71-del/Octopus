"""Recherche web : API (Brave/Tavily) si clé, sinon flux RSS / API keyless.

Le problème des moteurs classiques (DuckDuckGo, Bing HTML) est le blocage anti-bot.
On contourne avec des sources bot-friendly :
- Brave / Tavily : vraie recherche web (clé gratuite requise).
- Bing Web RSS : recherche web keyless, priorité stable du fallback sans clé.
- Bing News RSS : actualités avec URL éditeur directe, utilisées en complément.
- Google News RSS : piste titre/source uniquement quand aucune URL directe n'est trouvée.
- Wikipedia : encyclopédie (API MediaWiki, keyless).

`search_envelope` renvoie la sortie MACHINE : une enveloppe structurée (requête, intention,
items à six champs, erreurs de provider). `search_items` n'en expose que les items ; `web_search`
et `render_envelope` produisent des VUES texte bornées pour les agents et l'humain.

Règle de la frontière : STRUCTURE -> TEXTE est une vue, jamais un aller-retour. Le chemin
machine ne refait pas STRUCTURE -> TEXTE -> REGEX -> STRUCTURE.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, quote, urlparse

import requests

from . import config

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
# API Wikimedia : User-Agent identifiable exigé par sa politique (audit M9). Ajouter un contact via
# PODALUX_WIKI_USER_AGENT, ex. "Podalux/0.2 (contact: vous@exemple.fr)".
WIKI_UA = {"User-Agent": os.environ.get("PODALUX_WIKI_USER_AGENT", "").strip()
           or "Podalux/0.2 (agent de veille personnel; python-requests)"}
SEARCH_COST_CLASS_ENV = {
    "brave": "OCTOPUS_SEARCH_BRAVE_COST_CLASS",
    "tavily": "OCTOPUS_SEARCH_TAVILY_COST_CLASS",
}

# --- Frontière SEARCH ---
# Une recherche produit UNE enveloppe structurée. Le texte n'est qu'une vue bornée
# de cette enveloppe, destinée au LLM et à l'humain : aucun chemin machine ne doit
# faire STRUCTURE -> TEXTE -> REGEX -> STRUCTURE.
SEARCH_PURPOSE_GENERAL = "general"
SEARCH_PURPOSE_BUSINESS = "business_signal"
SEARCH_PURPOSES = (SEARCH_PURPOSE_GENERAL, SEARCH_PURPOSE_BUSINESS)

# Deux politiques de complément keyless : `general` garde l'historique (Bing Web, Bing News,
# puis Google News/Wikipédia sur une recherche non contrainte), `business_signal` s'arrête
# après Bing Web. Une encyclopédie ou une actualité générale complète le bruit, pas un
# signal d'affaires.

# Bornes des VUES texte. La structure, elle, garde l'extrait complet.
VIEW_SNIPPET_CHARS = 300


def normalize_site(site: str | None) -> str:
    """Normalise une contrainte de domaine sans accepter de chemin ni opérateur arbitraire."""
    raw = str(site or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("site:"):
        raw = raw[5:].strip()
    if "://" in raw:
        raw = urlparse(raw).netloc.lower()
    raw = raw.split("/", 1)[0].strip().strip(".")
    if raw.startswith("www."):
        raw = raw[4:]
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,63}", raw):
        raise ValueError(f"site invalide : {site}")
    return raw


def site_constraints(query: str, site: str | None = None) -> list[str]:
    explicit = normalize_site(site)
    if explicit:
        return [explicit]
    found = []
    for raw in re.findall(r"(?i)\bsite:([^\s()]+)", str(query or "")):
        try:
            domain = normalize_site(raw.rstrip(".,;:"))
        except ValueError:
            continue
        if domain and domain not in found:
            found.append(domain)
    return found


def effective_query(query: str, site: str | None = None) -> str:
    q = re.sub(r"\s+", " ", str(query or "")).strip()
    domain = normalize_site(site)
    if domain:
        # Le champ structuré site est autoritaire : évite "site:a site:b" si le LLM
        # a laissé un ancien opérateur dans query.
        q = re.sub(r"(?i)\bsite:[^\s()]+", " ", q)
        q = re.sub(r"\s+", " ", q).strip()
        q = f"{q} site:{domain}".strip()
    return q


def _url_matches_sites(url: str, sites: list[str]) -> bool:
    if not sites:
        return True
    host = urlparse(str(url or "")).netloc.lower().removeprefix("www.")
    return any(host == site or host.endswith("." + site) for site in sites)


def _filter_sites(items: list[dict], sites: list[str]) -> list[dict]:
    if not sites:
        return items
    return [item for item in items if _url_matches_sites(item.get("url", ""), sites)]


def _merge_unique(*groups: list[dict], max_results: int) -> list[dict]:
    out = []
    seen = set()
    for group in groups:
        for item in group:
            url = str(item.get("url") or "").strip()
            key = url or (str(item.get("provider") or ""), str(item.get("title") or ""))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= max_results:
                return out
    return out


def _require_free_quota(provider: str) -> None:
    env_name = SEARCH_COST_CLASS_ENV[provider]
    cost_class = os.environ.get(env_name, "").strip().lower()
    if cost_class == "paid":
        raise RuntimeError(f"{provider} : cost class paid bloquée sans allowance")
    if cost_class != "free_quota":
        raise RuntimeError(f"{provider} : cost class non déclarée ({env_name}=free_quota requis)")


def _strip_html(s: str) -> str:
    return (re.sub(r"<[^>]+>", " ", s)
            .replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
            .replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
            .strip())


def _item(provider: str, title: str, url: str = "", source: str = "", date: str = "", snippet: str = "") -> dict:
    return {"provider": provider, "title": title.strip(), "url": url.strip(), "source": source.strip(),
            "date": date.strip(), "snippet": snippet.strip()}


def _clean_text(value) -> str:
    """Texte compact : espaces et sauts de ligne repliés, aucune perte de mot."""
    return re.sub(r"\s+", " ", str(value or "").replace("\r", " ")).strip()


def _clean_url(value) -> str:
    """URL réellement navigable, ou vide. Les identifiants embarqués ne sont jamais conservés."""
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    if parsed.username or parsed.password:
        return ""
    if any(char.isspace() or ord(char) < 0x20 for char in url):
        return ""  # artefact de parsing, pas une URL navigable
    return url


def coerce_item(raw) -> dict | None:
    """Item normalisé à six champs, ou None si le provider livre un objet inexploitable.

    Un item sans URL navigable n'est pas un résultat : il ne doit pas devenir une piste
    de browse reconstruite à partir d'un extrait ou d'un message d'erreur.
    """
    if not isinstance(raw, dict):
        return None
    url = _clean_url(raw.get("url"))
    if not url:
        return None
    return {
        "title": _clean_text(raw.get("title")),
        "url": url,
        "source": _clean_text(raw.get("source")),
        "date": _clean_text(raw.get("date")),
        "snippet": str(raw.get("snippet") or "").strip(),
        "provider": _clean_text(raw.get("provider")),
    }


def _brave_items(query: str, max_results: int) -> list[dict]:
    _require_free_quota("brave")
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": max_results},
                     headers={"X-Subscription-Token": config.BRAVE_API_KEY,
                              "Accept": "application/json", **UA}, timeout=15)
    r.raise_for_status()
    return [_item("brave", w.get("title", ""), w.get("url", ""), snippet=_strip_html(w.get("description", "")))
            for w in r.json().get("web", {}).get("results", [])][:max_results]


def _tavily_items(query: str, max_results: int) -> list[dict]:
    _require_free_quota("tavily")
    r = requests.post("https://api.tavily.com/search",
                      json={"query": query, "max_results": max_results},
                      headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                      timeout=15)
    r.raise_for_status()
    return [_item("tavily", res.get("title", ""), res.get("url", ""), snippet=res.get("content", ""))
            for res in r.json().get("results", [])][:max_results]


def _bing_direct_url(url: str) -> str:
    """Extrait l'URL éditeur quand Bing RSS renvoie un wrapper /news/apiclick.aspx."""
    parsed = urlparse(url)
    if parsed.netloc.lower() in {"www.bing.com", "bing.com"} and parsed.path == "/news/apiclick.aspx":
        return dict(parse_qsl(parsed.query)).get("url", "")
    return url


def _bing_news_items(query: str, max_results: int) -> list[dict]:
    r = requests.get(
        "https://www.bing.com/news/search",
        params={"q": query, "format": "RSS", "mkt": "fr-FR", "setlang": "fr"},
        headers=UA,
        timeout=15,
    )
    r.raise_for_status()
    items = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:max_results]:
        def tag(name, pattern=None):
            m = re.search(pattern or rf"<{name}>(.*?)</{name}>", it, re.S)
            return _strip_html(m.group(1)) if m else ""

        url = _bing_direct_url(tag("link"))
        if not url:
            continue
        items.append(
            _item(
                "bing_news",
                tag("title"),
                url,
                tag("News:Source"),
                tag("pubDate"),
                tag("description"),
            )
        )
    return items


def _bing_web_items(query: str, max_results: int) -> list[dict]:
    """Recherche web Bing RSS ; ne conserve que des URL externes directement navigables."""
    r = requests.get(
        "https://www.bing.com/search",
        params={"q": query, "format": "RSS", "mkt": "fr-FR", "setlang": "fr"},
        headers=UA,
        timeout=15,
    )
    r.raise_for_status()
    items = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:max_results]:
        def tag(name, pattern=None):
            m = re.search(pattern or rf"<{name}>(.*?)</{name}>", it, re.S)
            return _strip_html(m.group(1)) if m else ""

        url = _bing_direct_url(tag("link"))
        host = urlparse(url).netloc.lower()
        if not url or host in {"bing.com", "www.bing.com", "news.google.com"}:
            continue
        items.append(_item("bing_web", tag("title"), url, "Bing Web", tag("pubDate"), tag("description")))
    return items


def _gnews_items(query: str, max_results: int) -> list[dict]:
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": query, "hl": "fr", "gl": "FR", "ceid": "FR:fr"},
                     headers=UA, timeout=15)
    r.raise_for_status()
    items = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:max_results]:
        def tag(name, pattern=None):
            m = re.search(pattern or rf"<{name}>(.*?)</{name}>", it, re.S)
            return _strip_html(m.group(1)) if m else ""
        items.append(_item("google_news", tag("title"), tag("link"), tag("source", r"<source[^>]*>(.*?)</source>"),
                           tag("pubDate")))
    return items


def _wikipedia_items(query: str, max_results: int) -> list[dict]:
    r = requests.get("https://fr.wikipedia.org/w/api.php",
                     params={"action": "query", "list": "search", "srsearch": query,
                             "srlimit": max_results, "srprop": "snippet",
                             "format": "json", "utf8": 1},
                     headers=WIKI_UA, timeout=15)
    r.raise_for_status()
    return [_item("wikipedia", h.get("title", ""), "https://fr.wikipedia.org/wiki/" + quote(h.get("title", "").replace(" ", "_")),
                  "Wikipédia", snippet=_strip_html(h.get("snippet", "")))
            for h in r.json().get("query", {}).get("search", [])]


PROVIDERS = (("Brave", "_brave_items", lambda: bool(config.BRAVE_API_KEY)),
             ("Tavily", "_tavily_items", lambda: bool(config.TAVILY_API_KEY)))


def _envelope(query, effective, purpose: str, items: list[dict], errors: list[str]) -> dict:
    """Sortie machine unique de SEARCH : six champs par item, erreurs séparées des résultats."""
    return {
        "query": str(query or ""),
        "effective_query": effective,
        "purpose": purpose,
        "items": list(items),
        "errors": list(errors),
    }


def search_envelope(query: str, max_results: int = 6, site: str | None = None,
                    purpose: str = SEARCH_PURPOSE_GENERAL) -> dict:
    """Recherche structurée : items validés + erreurs de provider, jamais un bloc de texte."""
    if purpose not in SEARCH_PURPOSES:
        raise ValueError(f"purpose de recherche inconnu : {purpose}")
    errors: list[str] = []
    q = effective_query(query, site)
    sites = site_constraints(q, site)

    def attempt(name, fn_name):
        try:
            raw_items = globals()[fn_name](q, max_results) or []
        except Exception as exc:
            errors.append(f"{name} : {type(exc).__name__} {str(exc)[:160]}")
            return []
        kept = [coerce_item(raw) for raw in raw_items]
        return _filter_sites([item for item in kept if item is not None], sites)

    for name, fn_name, available in PROVIDERS:
        if available():
            items = attempt(name, fn_name)
            if items:
                return _envelope(query, q, purpose, _merge_unique(items, max_results=max_results), errors)

    if purpose == SEARCH_PURPOSE_BUSINESS:
        # Politique business : Bing Web keyless puis arrêt. Pas de complément
        # encyclopédique ni d'actualité générale : ce ne sont pas des signaux d'affaires.
        web = attempt("Bing Web", "_bing_web_items")
        return _envelope(query, q, purpose, _merge_unique(web, max_results=max_results), errors)

    # Le fallback keyless ne change plus de famille selon le premier RSS non vide :
    # Bing Web est toujours prioritaire, Bing News complète ensuite.
    web_items = attempt("Bing Web", "_bing_web_items")
    news_items = attempt("Bing News", "_bing_news_items")
    direct = _merge_unique(web_items, news_items, max_results=max_results)
    if len(direct) >= max_results or sites:
        return _envelope(query, q, purpose, direct, errors)

    # Wrappers Google/Wikipedia ne servent qu'à compléter une recherche non contrainte.
    hints = attempt("Google News", "_gnews_items")
    wiki = attempt("Wikipedia", "_wikipedia_items")
    return _envelope(query, q, purpose, _merge_unique(direct, hints, wiki, max_results=max_results), errors)


def search_items(query: str, max_results: int = 6, site: str | None = None,
                 purpose: str = SEARCH_PURPOSE_GENERAL) -> tuple[list[dict], list[str]]:
    """Recherche stable : API si disponible, sinon Bing Web puis News, avec filtre site local."""
    envelope = search_envelope(query, max_results=max_results, site=site, purpose=purpose)
    return envelope["items"], envelope["errors"]


def format_items(items: list[dict]) -> str:
    providers = []
    for item in items:
        provider = str(item.get("provider") or "")
        if provider and provider not in providers:
            providers.append(provider)
    news = [i for i in items if i["provider"] == "bing_news"]
    google_news = [i for i in items if i["provider"] == "google_news"]
    wiki = [i for i in items if i["provider"] == "wikipedia"]
    web = [i for i in items if i["provider"] not in ("bing_news", "google_news", "wikipedia")]
    parts = []
    if providers:
        parts.append("Fournisseurs de recherche : " + ", ".join(providers))
    if web:
        parts.append("\n".join(f"- {i['title']}\n  {i['url']}\n  {i['snippet']}" for i in web))
    if news:
        parts.append(
            "Actualités — URL éditeur directe :\n"
            + "\n".join(
                f"- {i['title']} ({i['source']}, {i['date']})\n  {i['url']}"
                for i in news
            )
        )
    if google_news:
        parts.append(
            "Pistes Google News — wrapper non exploitable par browse ; reformuler la recherche avec le titre/source :\n"
            + "\n".join(
                f"- {i['title']} ({i['source']}, {i['date']})"
                for i in google_news
            )
        )
    if wiki:
        parts.append(
            "Encyclopédie (Wikipedia) :\n"
            + "\n".join(
                f"- {i['title']}\n  {i['url']}\n  {i['snippet']}"
                for i in wiki
            )
        )
    return "\n\n".join(parts)


def _envelope_items(envelope) -> list[dict]:
    if not isinstance(envelope, dict):
        return []
    items = envelope.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items
            if isinstance(item, dict) and str(item.get("url") or "").strip()]


def render_items(items, *, max_items: int | None = None,
                 max_snippet_chars: int | None = None) -> str:
    """Vue texte d'items déjà structurés : extraits bornés, URL intacte."""
    limit = VIEW_SNIPPET_CHARS if max_snippet_chars is None else max_snippet_chars
    kept = items if max_items is None else list(items)[:max_items]
    blocks = []
    for item in kept:
        if not isinstance(item, dict):
            continue
        lines = [f"- {item.get('title') or '(sans titre)'}", f"  {item.get('url')}"]
        meta = " ; ".join(str(item.get(field) or "") for field in ("source", "date", "provider")
                          if item.get(field))
        if meta:
            lines.append(f"  {meta}")
        snippet = _clean_text(item.get("snippet"))[:limit]
        if snippet:
            lines.append(f"  {snippet}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def render_envelope(envelope, *, max_items: int | None = None,
                    max_snippet_chars: int | None = None) -> str:
    """Vue texte bornée d'une enveloppe structurée : LLM et humain, jamais une source à reparsing.

    Zéro résultat et erreur provider sont deux phrases distinctes : une panne n'est pas
    un marché vide, et l'inverse n'est pas vrai non plus.
    """
    if not isinstance(envelope, dict):
        return ""
    items = _envelope_items(envelope)
    parts = [f"Requête effective : {envelope.get('effective_query') or envelope.get('query') or ''}"]
    providers = []
    for item in items:
        provider = str(item.get("provider") or "")
        if provider and provider not in providers:
            providers.append(provider)
    if providers:
        parts.append("Fournisseurs de recherche : " + ", ".join(providers))
    body = render_items(items, max_items=max_items, max_snippet_chars=max_snippet_chars)
    if body:
        parts.append(body)
    else:
        parts.append("Aucun résultat exploitable.")
    errors = envelope.get("errors")
    if isinstance(errors, str):
        errors = [errors] if errors.strip() else []
    elif not isinstance(errors, (list, tuple)):
        errors = []
    if errors:
        parts.append("Sources en erreur : " + " ; ".join(str(err) for err in errors))
    return "\n\n".join(parts)


def web_search(query: str, max_results: int = 6, site: str | None = None) -> str:
    """Recherche web en texte ; requête effective et fournisseurs restent visibles pour le diagnostic."""
    q = effective_query(query, site)
    envelope = search_envelope(query, max_results=max_results, site=site)
    text = format_items(envelope["items"])
    prefix = f"Requête effective : {q}"
    text = prefix + ("\n\n" + text if text else "")
    if envelope["errors"]:
        text += "\n\nSources en erreur : " + " ; ".join(envelope["errors"])
    return text
