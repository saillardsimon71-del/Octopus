"""Client LLM du groupe : texte + vision, avec logging de cout systematique.

Depuis OCTOPUS M1, chaque appel passe par la passerelle `octopus.llm` : journal
(data/octopus.db), cout a la grille officielle (heures pleines, tokens en cache), budget
par run et profils de cout (OCTOPUS_PROFILE=legacy|zero_cost|low_cost|quality_first).
Signatures inchangees. Coupe-circuit : OCTOPUS=off retablit l'appel direct historique.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import octopus

from . import config, db

# Modeles historiques -> identifiants du catalogue OCTOPUS
_MODEL_IDS = {config.MODEL_FLASH: "deepseek/flash", config.MODEL_PRO: "deepseek/v4-pro"}

_cli = None


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
        # pro : thinking ACTIVÉ + reasoning high (arbitrages)
        if reasoning:
            kw["reasoning_effort"] = reasoning
        kw["extra_body"] = {"thinking": {"type": "enabled"}}
    else:
        # flash : thinking DÉSACTIVÉ sinon reasoning_tokens mange tout max_tokens
        kw["extra_body"] = {"thinking": {"type": "disabled"}}
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    return kw


def _complete(agent: str, task: str, model: str, messages: list[dict], max_tokens: int,
              reasoning: str | None = None, json_mode: bool = False, needs: tuple[str, ...] = ()) -> str:
    if not octopus.enabled():
        r = _client().chat.completions.create(**_legacy_request(model, messages, max_tokens, reasoning, json_mode))
        content = (r.choices[0].message.content or "").strip()
        db.log_cost(agent, task, model, r.usage.prompt_tokens, r.usage.completion_tokens)
        return content
    from octopus import llm
    c = llm.complete(llm.legacy_task(agent, task), messages, agent=agent, business="podalux",
                     max_tokens=max_tokens, json_mode=json_mode, reasoning=reasoning, needs=needs,
                     pin_model=_MODEL_IDS.get(model, model))
    used = model if c.model == _MODEL_IDS.get(model) else c.model
    db.log_cost(agent, task, used, c.usage.prompt_tokens, c.usage.completion_tokens, cost_usd=c.cost_usd)
    return c.text


def call(agent: str, task: str, model: str, messages: list[dict],
         max_tokens: int = 1200, reasoning: str | None = None,
         json_mode: bool = False) -> str:
    """Appel texte. Retourne le contenu, logge le coût."""
    return _complete(agent, task, model, messages, max_tokens, reasoning, json_mode)


def call_json(agent: str, task: str, model: str, messages: list[dict],
              max_tokens: int = 2000, reasoning: str | None = None) -> dict:
    """Appel texte, parse un objet JSON (tolérant aux balises)."""
    raw = call(agent, task, model, messages, max_tokens=max_tokens,
               reasoning=reasoning, json_mode=True)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"JSON introuvable dans la réponse de {agent}: {raw[:300]}")
    return json.loads(m.group(0))


def build_vision_messages(frames: list[str], prompt: str) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for f in frames:
        b = base64.b64encode(Path(f).read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "low"}})
    return [{"role": "user", "content": content}]


def vision(agent: str, task: str, frames: list[str], narration: str,
           prompt: str) -> dict:
    """QC vision (JSON) : deepseek-flash uniquement."""
    raw = _complete(agent, task, config.MODEL_FLASH, build_vision_messages(frames, prompt), 3000,
                    needs=("vision",))
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"JSON introuvable dans la réponse vision: {raw[:300]}")
    return json.loads(m.group(0))


def vision_text(agent: str, task: str, frames: list[str],
                prompt: str, max_tokens: int = 1500) -> str:
    """Vision en texte libre (description de page) : deepseek-flash."""
    return _complete(agent, task, config.MODEL_FLASH, build_vision_messages(frames, prompt), max_tokens,
                     needs=("vision",))
