"""Client DeepSeek du groupe : texte + vision, avec logging de coût systématique.

- Routage : deepseek-flash (courant) / deepseek-v4-pro (arbitrage, reasoning high).
- Chaque appel est loggé (agent, tâche, modèle, tokens, coût) dans SQLite.
"""
from __future__ import annotations

import json
import re

from openai import OpenAI

from . import config, db

_cli: OpenAI | None = None


def _client() -> OpenAI:
    global _cli
    if _cli is None:
        _cli = OpenAI(api_key=config.api_key(), base_url=config.BASE_URL)
    return _cli


def call(agent: str, task: str, model: str, messages: list[dict],
         max_tokens: int = 1200, reasoning: str | None = None,
         json_mode: bool = False) -> str:
    """Appel texte. Retourne le contenu, logge le coût."""
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
    r = _client().chat.completions.create(**kw)
    content = (r.choices[0].message.content or "").strip()
    u = r.usage
    db.log_cost(agent, task, model,
                u.prompt_tokens, u.completion_tokens)
    return content


def call_json(agent: str, task: str, model: str, messages: list[dict],
              max_tokens: int = 2000, reasoning: str | None = None) -> dict:
    """Appel texte, parse un objet JSON (tolérant aux balises)."""
    raw = call(agent, task, model, messages, max_tokens=max_tokens,
               reasoning=reasoning, json_mode=True)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"JSON introuvable dans la réponse de {agent}: {raw[:300]}")
    return json.loads(m.group(0))


def _vision_raw(frames: list[str], prompt: str, max_tokens: int) -> str:
    import base64
    from pathlib import Path
    content: list[dict] = [{"type": "text", "text": prompt}]
    for f in frames:
        b = base64.b64encode(Path(f).read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "low"}})
    r = _client().chat.completions.create(
        model=config.MODEL_FLASH,
        messages=[{"role": "user", "content": content}],
        max_tokens=max_tokens,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return (r.choices[0].message.content or "").strip(), r.usage


def vision(agent: str, task: str, frames: list[str], narration: str,
           prompt: str) -> dict:
    """QC vision (JSON) : deepseek-flash uniquement."""
    raw, u = _vision_raw(frames, prompt, 3000)
    db.log_cost(agent, task, config.MODEL_FLASH,
                u.prompt_tokens, u.completion_tokens)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"JSON introuvable dans la réponse vision: {raw[:300]}")
    return json.loads(m.group(0))


def vision_text(agent: str, task: str, frames: list[str],
                prompt: str, max_tokens: int = 1500) -> str:
    """Vision en texte libre (description de page) : deepseek-flash."""
    raw, u = _vision_raw(frames, prompt, max_tokens)
    db.log_cost(agent, task, config.MODEL_FLASH,
                u.prompt_tokens, u.completion_tokens)
    return raw
