"""Client LLM du groupe : texte + vision, avec logging de cout systematique.

Depuis OCTOPUS M1, chaque appel passe par la passerelle `octopus.llm` : journal
(data/octopus.db), cout a la grille officielle, budget par run et profils de cout.
Signatures historiques conservées. Le profil par défaut des agents Podalux est maintenant
`zero_cost` lorsque OmniRoute est actif, afin d'éviter les appels DeepSeek payants après
épuisement du quota ; définir explicitement `OCTOPUS_PROFILE=legacy` pour restaurer le
routage historique.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Callable

import octopus

from . import config, db

_MODEL_IDS = {config.MODEL_FLASH: "deepseek/flash", config.MODEL_PRO: "deepseek/v4-pro"}
_cli = None


def _default_profile() -> str:
    explicit = os.environ.get("OCTOPUS_PROFILE", "").strip()
    if explicit:
        return explicit
    omni_enabled = os.environ.get("OMNIROUTE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    return "zero_cost" if omni_enabled else "legacy"


def _client():
    """Client direct historique (utilise seulement avec OCTOPUS=off)."""
    global _cli
    if _cli is None:
        from openai import OpenAI
        _cli = OpenAI(api_key=config.api_key(), base_url=config.BASE_URL)
    return _cli


def _legacy_request(model: str, messages: list[dict], max_tokens: int, reasoning: str | None,
                    json_mode: bool) -> dict:
    kw: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if model == config.MODEL_PRO:
        if reasoning:
            kw["reasoning_effort"] = reasoning
        kw["extra_body"] = {"thinking": {"type": "enabled"}}
    else:
        kw["extra_body"] = {"thinking": {"type": "disabled"}}
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    return kw


def _complete(agent: str, task: str, model: str, messages: list[dict], max_tokens: int,
              reasoning: str | None = None, json_mode: bool = False, needs: tuple[str, ...] = (),
              validate: Callable[[str], Any] | None = None) -> Any:
    if not octopus.enabled():
        r = _client().chat.completions.create(**_legacy_request(model, messages, max_tokens, reasoning, json_mode))
        content = (r.choices[0].message.content or "").strip()
        db.log_cost(agent, task, model, r.usage.prompt_tokens, r.usage.completion_tokens)
        return validate(content) if validate is not None else content
    from octopus import journal, llm
    run = journal.current_run()  # coûts rattachés au business du run (mission, tâche), pas toujours à Podalux
    c = llm.complete(llm.legacy_task(agent, task), messages, agent=agent, business=run.business if run else "podalux",
                     max_tokens=max_tokens, json_mode=json_mode, reasoning=reasoning, needs=needs,
                     pin_model=_MODEL_IDS.get(model, model), profile=_default_profile(), validate=validate)
    used = model if c.model == _MODEL_IDS.get(model) else c.model
    db.log_cost(agent, task, used, c.usage.prompt_tokens, c.usage.completion_tokens, cost_usd=c.cost_usd)
    return c.data if validate is not None else c.text


def _json_validator(validate: Callable[[dict], Any] | None = None) -> Callable[[str], dict]:
    from octopus import llm

    def parse_and_validate(text: str) -> dict:
        data = llm.parse_json(text)
        if validate is None:
            return data
        validated = validate(data)
        return data if validated is None else validated

    return parse_and_validate


def call(agent: str, task: str, model: str, messages: list[dict],
         max_tokens: int = 1200, reasoning: str | None = None,
         json_mode: bool = False) -> str:
    """Appel texte. Retourne le contenu, logge le coût."""
    return _complete(agent, task, model, messages, max_tokens, reasoning, json_mode)


def call_json(agent: str, task: str, model: str, messages: list[dict],
              max_tokens: int = 2000, reasoning: str | None = None,
              validate: Callable[[dict], Any] | None = None) -> dict:
    """Appel texte, parse un objet JSON (tolérant aux balises)."""
    return _complete(agent, task, model, messages, max_tokens, reasoning, json_mode=True,
                     validate=_json_validator(validate))


def build_vision_messages(frames: list[str], prompt: str) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for f in frames:
        b = base64.b64encode(Path(f).read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "low"}})
    return [{"role": "user", "content": content}]


def vision(agent: str, task: str, frames: list[str], narration: str,
           prompt: str, validate: Callable[[dict], Any] | None = None) -> dict:
    """QC vision (JSON) via the configured zero-cost / legacy route."""
    return _complete(agent, task, config.MODEL_FLASH, build_vision_messages(frames, prompt), 3000,
                     needs=("vision",), validate=_json_validator(validate))


def vision_text(agent: str, task: str, frames: list[str],
                prompt: str, max_tokens: int = 1500) -> str:
    """Vision en texte libre (description de page)."""
    return _complete(agent, task, config.MODEL_FLASH, build_vision_messages(frames, prompt), max_tokens,
                     needs=("vision",))
