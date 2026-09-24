"""Recherche web : API (Brave/Tavily) si clé, sinon flux RSS / API keyless.

Le problème des moteurs classiques (DuckDuckGo, Bing HTML) est le blocage anti-bot.
On contourne avec des sources bot-friendly :
- Brave / Tavily : vraie recherche web (clé gratuite requise).
- Bing News RSS : actualités avec URL éditeur directe quand disponible.
- Bing Web RSS : recherche web keyless, utilisée avant les wrappers Google News.
- Google News RSS : piste titre/source uniquement quand aucune URL directe n'est trouvée.
- Wikipedia : encyclopédie (API MediaWiki, keyless).

`search_items` renvoie des résultats structurés (titre, URL, source, date, extrait) pour les
traitements qui doivent citer leurs sources ; `web_search` les met en texte pour les agents.
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
    if domain and domain not in site_constraints(q):
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


def search_items(query: str, max_results: int = 6, site: str | None = None) -> tuple[list[dict], list[str]]:
    """Recherche stable : API si disponible, sinon Bing Web puis News, avec filtre site local."""
    errors: list[str] = []
    q = effective_query(query, site)
    sites = site_constraints(q, site)

    def attempt(name, fn_name):
        try:
            items = globals()[fn_name](q, max_results) or []
            return _filter_sites(items, sites)
        except Exception as exc:
            errors.append(f"{name} : {type(exc).__name__} {str(exc)[:160]}")
            return []

    for name, fn_name, available in PROVIDERS:
        if available():
            items = attempt(name, fn_name)
            if items:
                return items[:max_results], errors

    # Le fallback keyless ne change plus de famille selon le premier RSS non vide :
    # Bing Web est toujours prioritaire, Bing News complète ensuite.
    web_items = attempt("Bing Web", "_bing_web_items")
    news_items = attempt("Bing News", "_bing_news_items")
    direct = _merge_unique(web_items, news_items, max_results=max_results)
    if len(direct) >= max_results or sites:
        return direct, errors

    # Wrappers Google/Wikipedia ne servent qu'à compléter une recherche non contrainte.
    hints = attempt("Google News", "_gnews_items")
    wiki = attempt("Wikipedia", "_wikipedia_items")
    return _merge_unique(direct, hints, wiki, max_results=max_results), errors


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


def web_search(query: str, max_results: int = 6, site: str | None = None) -> str:
    """Recherche web en texte ; requête effective et fournisseurs restent visibles pour le diagnostic."""
    q = effective_query(query, site)
    items, errors = search_items(query, max_results, site=site)
    text = format_items(items)
    prefix = f"Requête effective : {q}"
    text = prefix + ("\n\n" + text if text else "")
    if errors:
        text += "\n\nSources en erreur : " + " ; ".join(errors)
    return text
