"""Navigateur Chromium (Playwright) pour les agents (Phase 5).

- Profil persistant (`agents/data/browser_profile`) : les connexions aux comptes
  (Stripe, Reddit, X, Fiverr, YouTube…) sont CONSERVÉES entre les sessions.
  → l'humain se connecte une fois dans la fenêtre, l'agent retrouve la session.
- Mode visible par défaut (headless=False) : l'humain regarde, interagit et se connecte.

⚠️ ToS : préférer les API officielles (YouTube Data API, Stripe API).
L'automatisation de logins est fragile et peut violer les CGU.
"""
from __future__ import annotations

import atexit
import time
from pathlib import Path

from . import config, db, deepseek


class BrowserTool:
    def __init__(self, headless: bool = False, profile_dir: Path | None = None):
        self.headless = headless
        self.profile_dir = profile_dir or (config.DATA_DIR / "browser_profile")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = None
        self._context = None
        self._page = None
        self._started = False

    # --- cycle de vie ---

    def start(self) -> "BrowserTool":
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
            args=["--no-sandbox"],
        )
        self._page = self._context.new_page()
        self._started = True
        return self

    def stop(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # --- navigation / lecture ---
    def goto(self, url: str) -> str:
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        db.set_state("browser_url", self._page.url)
        return self._page.url

    def snapshot(self, max_chars: int = 4000) -> str:
        """Texte visible de la page (pour que l'agent « lise »)."""
        try:
            txt = self._page.inner_text("body", timeout=5000)
        except Exception:
            txt = ""
        return txt[:max_chars]

    def selector_text(self, selector: str, max_chars: int = 4000) -> str:
        """Texte d'un conteneur précis (ex. résultats de recherche)."""
        try:
            txt = self._page.inner_text(selector, timeout=5000)
        except Exception:
            txt = ""
        return txt[:max_chars]

    def links(self, max_items: int = 40) -> list[str]:
        """Liens de la page (pour la navigation décisionnelle)."""
        try:
            hrefs = self._page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href + ' | ' + (e.innerText||'').trim())")
        except Exception:
            return []
        return [h for h in hrefs if h][:max_items]

    # --- interaction ---
    def click(self, selector: str) -> None:
        self._page.click(selector, timeout=15000)

    def type(self, selector: str, text: str) -> None:
        self._page.fill(selector, text, timeout=15000)

    def wait_for(self, text: str, timeout: int = 15000) -> None:
        self._page.get_by_text(text).first.wait_for(timeout=timeout)

    def wait_for_selector(self, selector: str, timeout: int = 15000) -> None:
        self._page.wait_for_selector(selector, timeout=timeout)

    def url(self) -> str:
        return self._page.url

    # --- recherche web (veille) ---
    def search(self, query: str, max_results: int = 6) -> str:
        """Recherche web multi-sources → texte lisible des résultats.

        Délègue à `agents.search.web_search` (Brave/Tavily/Google News/Wikipedia).
        Gardé pour compatibilité avec le code existant.
        """
        from .search import web_search
        return web_search(query, max_results)

    # --- vision ---
    def screenshot(self, path: Path | None = None) -> Path:
        path = path or (config.DATA_DIR / "screenshots" / f"shot_{int(time.time())}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(path))
        db.set_state("browser_shot", str(path))
        return path

    def see(self, prompt: str = "Décris cette page et ce qu'on peut y faire.", agent: str = "SOUT") -> dict:
        """Capture + vision : l'agent « voit » la page."""
        shot = self.screenshot()
        txt = deepseek.vision_text(agent, "voir_page", [str(shot)], prompt)
        return {"screenshot": str(shot), "description": txt}

    # --- passation humaine (login, 2FA, captcha, confirmation) ---
    def handoff(self, message: str, timeout_s: int = 300) -> bool:
        """Demande à l'humain (GUI ou terminal) via le mécanisme ask/answer."""
        ans = db.ask_human("BROWSER", "browser_handoff", message, timeout_s=timeout_s)
        if ans is None:
            db.post("BROWSER", f"handoff expiré (timeout) : {message}", kind="handoff")
            return False
        db.post("BROWSER", f"handoff résolu : {ans[:120]}", kind="handoff")
        return ans.strip().lower() in ("ok", "o", "oui", "y", "yes", "continue", "valider", "c")


def new_browser(headless: bool = False) -> BrowserTool:
    return BrowserTool(headless=headless).start()


_shared: BrowserTool | None = None


def get_shared_browser() -> BrowserTool:
    """Navigateur partagé/persistant : un seul Chromium (visible) réutilisé.

    Les connexions aux comptes sont conservées dans le profil persistant, donc
    l'humain ne se connecte qu'une fois. Fermé proprement à la sortie (atexit).
    """
    global _shared
    if _shared is None or not _shared._started:
        _shared = BrowserTool(headless=False).start()
    return _shared


def _cleanup_shared() -> None:
    global _shared
    if _shared is not None:
        try:
            _shared.stop()
        except Exception:
            pass
        _shared = None


atexit.register(_cleanup_shared)
