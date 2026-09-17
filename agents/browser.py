"""Navigateur Chromium (Playwright) pour les agents (Phase 5).

- Profil persistant (`agents/data/browser_profile`) : les connexions aux comptes
  (Stripe, Reddit, X, Fiverr, YouTube…) sont CONSERVÉES entre les sessions.
  → l'humain se connecte une fois dans la fenêtre, l'agent retrouve la session.
- Mode visible par défaut (headless=False) : l'humain regarde, interagit et se connecte.

⚠️ ToS : préférer les API officielles (YouTube Data API, Stripe API).
L'automatisation de logins est fragile et peut violer les CGU.
"""
from __future__ import annotations

import asyncio
import atexit
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import config, db, deepseek, web_guard


class BrowserTool:
    ACTIVE_RESOURCE_TYPES = {"fetch", "xhr", "websocket", "eventsource", "beacon"}

    def __init__(self, headless: bool = False, profile_dir: Path | None = None, persistent: bool = True,
                 guard=None):
        self.headless = headless
        self.guard = guard
        self.blocked: list[str] = []
        self.persistent = persistent
        self.profile_dir = profile_dir or (config.DATA_DIR / "browser_profile")
        if persistent:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False

    def start(self) -> "BrowserTool":
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        if self.persistent:
            self._context = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), headless=self.headless,
                viewport={"width": 1280, "height": 800}, service_workers="block")
        else:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800}, service_workers="block")
        if self.guard is not None:
            self._context.route("**/*", self._route)
            # Playwright demande un route_web_socket dédié pour intercepter réellement les
            # WebSockets ; la dépendance locale est >=1.55. On garde un fallback pour
            # d'anciens environnements importés manuellement.
            route_ws = getattr(self._context, "route_web_socket", None)
            if route_ws is not None:
                route_ws("**/*", self._route_websocket)
        self._page = self._context.new_page()
        self._started = True
        return self

    MAX_REDIRECTS = 10

    def _must_guard_request(self, request) -> bool:
        if request.is_navigation_request():
            return True
        try:
            return request.resource_type() in self.ACTIVE_RESOURCE_TYPES
        except Exception:
            return True

    def _route(self, route) -> None:
        request = route.request
        if not self._must_guard_request(request):
            self._continue(route)
            return
        try:
            allowed = self.guard(request.url)
        except Exception:
            allowed = False
        if not allowed:
            self._block(route, request.url)
            return
        if not request.is_navigation_request():
            self._continue(route)
            return

        # Suivi manuel des redirections : le premier saut est contrôlé puis chaque Location
        # est vérifiée avant d'être demandée. `route.fetch(url=...)` conserve le contexte réseau
        # Playwright, contrairement à un nouvel APIRequestContext indépendant.
        url, response = request.url, self._fetch(route, request.url, first=True)
        for _ in range(self.MAX_REDIRECTS):
            location = response.headers.get("location")
            if not (300 <= response.status < 400 and location):
                break
            target = urljoin(url, location)
            try:
                allowed = self.guard(target)
            except Exception:
                allowed = False
            if not allowed:
                self._block(route, target)
                return
            url, response = target, self._fetch(route, target, first=False)
        else:
            self._block(route, url)
            return
        self._fulfill(route, response)

    @staticmethod
    def _websocket_guard_url(url: str) -> str:
        """Convertit ws(s):// en http(s):// pour réutiliser le même garde d'URL."""
        parts = urlsplit(str(url).strip())
        scheme = {"ws": "http", "wss": "https"}.get(parts.scheme.lower())
        if scheme is None:
            raise ValueError(f"schéma WebSocket inattendu : {parts.scheme or '(aucun)'}")
        return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))

    def _route_websocket(self, websocket) -> None:
        original_url = websocket.url
        try:
            guard_url = self._websocket_guard_url(original_url)
            allowed = self.guard(guard_url)
        except Exception:
            allowed = False
        if not allowed:
            self.blocked.append(original_url)
            reason = "WebSocket bloqué par le garde-fou OCTOPUS"
            impl = getattr(websocket, "_impl_obj", None)
            if impl is None:
                websocket.close(code=1008, reason=reason)
            else:
                # Le handler tourne dans la boucle de Playwright : le close() synchrone y attendrait
                # sa propre boucle (blocage définitif). On planifie la fermeture sur cette boucle.
                asyncio.get_running_loop().create_task(impl.close(code=1008, reason=reason))
            return
        websocket.connect_to_server()

    def _block(self, route, url: str) -> None:
        self.blocked.append(url)
        route.abort("blockedbyclient")

    def _fetch(self, route, url: str, first: bool):
        # `url=` est supported by Playwright Route.fetch and keeps the redirect inside the
        # intercepted browser request rather than issuing a separate context request.
        return route.fetch(url=url, max_redirects=0)

    def _fulfill(self, route, response) -> None:
        route.fulfill(response=response)

    def _continue(self, route) -> None:
        route.continue_()

    def stop(self) -> None:
        for closable in (self._context, self._browser):
            try:
                if closable:
                    closable.close()
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

    def goto(self, url: str) -> str:
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        db.set_state("browser_url", self._page.url)
        return self._page.url

    def snapshot(self, max_chars: int = 4000) -> str:
        try:
            txt = self._page.inner_text("body", timeout=5000)
        except Exception:
            txt = ""
        return txt[:max_chars]

    def selector_text(self, selector: str, max_chars: int = 4000) -> str:
        try:
            txt = self._page.inner_text(selector, timeout=5000)
        except Exception:
            txt = ""
        return txt[:max_chars]

    def links(self, max_items: int = 40) -> list[str]:
        try:
            hrefs = self._page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href + ' | ' + (e.innerText||'').trim())")
        except Exception:
            return []
        return [h for h in hrefs if h][:max_items]

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

    def search(self, query: str, max_results: int = 6) -> str:
        from .search import web_search
        return web_search(query, max_results)

    def screenshot(self, path: Path | None = None) -> Path:
        path = path or (config.DATA_DIR / "screenshots" / f"shot_{int(time.time())}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(path))
        db.set_state("browser_shot", str(path))
        return path

    def see(self, prompt: str = "Décris cette page et ce qu'on peut y faire.", agent: str = "SOUT") -> dict:
        shot = self.screenshot()
        kind = web_guard.classify(self.url())
        task = "web.describe_page" if kind == web_guard.ACCOUNT else "web.inspect_page"
        try:
            txt = deepseek.vision_text(agent, task, [str(shot)], prompt)
        except Exception as exc:
            from octopus import llm
            if isinstance(exc, llm.NoEligibleModel):
                txt = self.snapshot(2000) or "Vision indisponible : aucun modèle vision éligible."
            else:
                raise
        return {"screenshot": str(shot), "description": txt, "vision_task": task, "page_kind": kind}

    def handoff(self, message: str, timeout_s: int = 300) -> bool:
        ans = db.ask_human("BROWSER", "browser_handoff", message, timeout_s=timeout_s)
        if ans is None:
            db.post("BROWSER", f"handoff expiré (timeout) : {message}", kind="handoff")
            return False
        db.post("BROWSER", f"handoff résolu : {ans[:120]}", kind="handoff")
        return ans.strip().lower() in ("ok", "o", "oui", "y", "yes", "continue", "valider", "c")


def new_browser(headless: bool = False, account: bool = False, guard=None) -> BrowserTool:
    return BrowserTool(headless=headless, persistent=account, guard=guard).start()


_shared: BrowserTool | None = None


def get_shared_browser() -> BrowserTool:
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
