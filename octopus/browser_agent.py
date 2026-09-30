"""Supervised, resumable browser capability for OCTOPUS.

This module deliberately contains no planner or LLM.  A caller supplies a small
policy which turns bounded observations into actions; the capability executes,
verifies and journals each step.  Chromium/Playwright remains the web backend.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from agents import browser, web_guard
from . import tasks


@dataclass(frozen=True)
class BrowserObservation:
    url: str
    title: str
    text: str
    accessibility: str
    screenshot: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title, "text": self.text,
                "accessibility": self.accessibility, "screenshot": self.screenshot}


class BrowserCapability:
    """Observe/act/verify loop with persistent profile and task-step checkpoints."""
    def __init__(self, *, task_id: int | None = None, account: bool = False,
                 headless: bool = True, guard=None):
        self.task_id = task_id
        self.account = account
        self._browser = browser.new_browser(
            headless=headless if not account else False, account=account,
            guard=guard or (lambda url: web_guard.allowed(url, web_guard.BrowseState())))

    def close(self) -> None:
        self._browser.stop()

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def observe(self, *, screenshot: bool = False, max_chars: int = 6000) -> BrowserObservation:
        page = self._browser._page
        try: title = page.title()[:300]
        except Exception: title = ""
        try: accessibility = page.locator("body").aria_snapshot(timeout=3000)[:6000]
        except Exception: accessibility = ""
        shot = str(self._browser.screenshot()) if screenshot else None
        return BrowserObservation(self._browser.url(), title,
                                  self._browser.snapshot(max_chars), accessibility, shot)

    def goto(self, url: str) -> BrowserObservation:
        self._browser.goto(url)
        return self.observe()

    def act(self, action: dict[str, Any]) -> None:
        kind = action.get("kind")
        if kind == "click": self._browser.click(str(action["selector"]))
        elif kind in {"type", "fill"}: self._browser.type(str(action["selector"]), str(action["text"]))
        elif kind == "select": self._browser._page.select_option(str(action["selector"]), str(action["value"]))
        elif kind == "scroll": self._browser._page.mouse.wheel(0, int(action.get("pixels", 600)))
        elif kind == "press": self._browser._page.press(str(action["selector"]), str(action["key"]))
        elif kind == "upload": self._browser._page.set_input_files(str(action["selector"]), str(action["path"]))
        elif kind == "goto": self._browser.goto(str(action["url"]))
        else: raise ValueError(f"action navigateur inconnue: {kind!r}")

    def verify(self, check: dict[str, Any]) -> bool:
        if "text" in check: return str(check["text"]) in self._browser.snapshot(12000)
        if "selector" in check: return self._browser._page.locator(str(check["selector"])).count() > 0
        if "url_contains" in check: return str(check["url_contains"]) in self._browser.url()
        return False

    def checkpoint(self, key: str, value: Any) -> None:
        if self.task_id is not None: tasks.save_step(self.task_id, key, value)

    def run(self, objective: str, policy: Callable[[BrowserObservation], dict[str, Any] | None],
            *, max_steps: int = 20) -> dict[str, Any]:
        """Run until policy returns None. Completed actions survive interruption."""
        start = tasks.step_value(self.task_id, "browser.completed", []) if self.task_id else []
        completed = list(start)
        if completed and completed[-1].get("url"):
            self._browser.goto(completed[-1]["url"])
        for index in range(len(completed), max_steps):
            obs = self.observe()
            decision = policy(obs)
            if decision is None: break
            self.act(decision["action"])
            check = decision.get("verify")
            if check and not self.verify(check):
                raise RuntimeError(f"vérification navigateur échouée à l'étape {index}")
            record = {"index": index, "action": {k: v for k, v in decision["action"].items() if k != "text"},
                      "verified": bool(check), "url": self._browser.url(), "ts": time.time()}
            completed.append(record)
            self.checkpoint("browser.completed", completed)
            self.checkpoint("browser.objective", objective)
        return {"objective": objective, "steps": completed, "observation": self.observe().as_dict()}
