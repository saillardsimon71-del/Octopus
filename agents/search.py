"""Recherche web : API (Brave/Tavily) si clé, sinon Google News RSS + Wikipedia (keyless).

Le problème des moteurs classiques (DuckDuckGo, Bing HTML) est le blocage anti-bot.
On contourne avec des sources bot-friendly :
- Brave / Tavily : vraie recherche web (clé gratuite requise).
- Google News RSS : résultats d'actualité (keyless, XML).
- Wikipedia : encyclopédie (keyless, scraping du navigateur).
"""
from __future__ import annotations

import re

import requests

from . import config

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}


def _strip_html(s: str) -> str:
    return (re.sub(r"<[^>]+>", " ", s)
            .replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
            .replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
            .strip())


def _brave(query: str, max_results: int) -> str:
    r = requests.get("https://api.search.brave.com/res/v1/web/search",
                     params={"q": query, "count": max_results},
                     headers={"X-Subscription-Token": config.BRAVE_API_KEY,
                              "Accept": "application/json", **UA}, timeout=15)
    r.raise_for_status()
    rows = []
    for w in r.json().get("web", {}).get("results", []):
        rows.append(f"- {w.get('title', '')}\n  {w.get('url', '')}\n  {w.get('description', '')}")
    return "\n".join(rows)


def _tavily(query: str, max_results: int) -> str:
    r = requests.post("https://api.tavily.com/search",
                      json={"query": query, "max_results": max_results},
                      headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                      timeout=15)
    r.raise_for_status()
    rows = []
    for res in r.json().get("results", []):
        rows.append(f"- {res.get('title', '')}\n  {res.get('url', '')}\n  {res.get('content', '')}")
    return "\n".join(rows)


def _gnews(query: str, max_results: int) -> str:
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": query, "hl": "fr", "gl": "FR", "ceid": "FR:fr"},
                     headers=UA, timeout=15)
    r.raise_for_status()
    items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
    rows = []
    for it in items[:max_results]:
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        s = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        title = _strip_html(t.group(1)) if t else ""
        src = _strip_html(s.group(1)) if s else ""
        date = _strip_html(d.group(1)) if d else ""
        rows.append(f"- {title} ({src}, {date})")
    return "\n".join(rows)


def _wikipedia(query: str, max_results: int) -> str:
    """Recherche Wikipedia via l'API MediaWiki (keyless, JSON, pas de navigateur)."""
    r = requests.get("https://fr.wikipedia.org/w/api.php",
                     params={"action": "query", "list": "search", "srsearch": query,
                             "srlimit": max_results, "srprop": "snippet",
                             "format": "json", "utf8": 1},
                     headers=UA, timeout=15)
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])
    if not hits:
        return ""
    rows = []
    for h in hits:
        title = h.get("title", "")
        snippet = _strip_html(h.get("snippet", ""))
        rows.append(f"- {title}\n  {snippet}")
    return "\n".join(rows)


def web_search(query: str, max_results: int = 6) -> str:
    """Recherche web : Brave/Tavily si clé, sinon Google News + Wikipedia."""
    if config.BRAVE_API_KEY:
        try:
            out = _brave(query, max_results)
            if out.strip():
                return out
        except Exception:
            pass
    if config.TAVILY_API_KEY:
        try:
            out = _tavily(query, max_results)
            if out.strip():
                return out
        except Exception:
            pass
    parts: list[str] = []
    try:
        gn = _gnews(query, max_results)
        if gn.strip():
            parts.append("Actualités (Google News) :\n" + gn)
    except Exception:
        pass
    try:
        wk = _wikipedia(query, max_results)
        if wk.strip():
            parts.append("Encyclopédie (Wikipedia) :\n" + wk)
    except Exception:
        pass
    return "\n\n".join(parts) if parts else "(aucun résultat de recherche)"