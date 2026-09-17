"""Cout des appels a partir de l'usage renvoye par le fournisseur (tokens en cache inclus)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# Borne haute pour estimer une image avant l'appel (mesure DeepSeek du 16/09 : ~200 tokens/frame "low").
IMAGE_CHARS_ESTIMATE = 2400


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    reasoning_tokens: int | None = None


def is_peak(ts: float, windows: list[str] | None) -> bool:
    """Vrai si l'instant tombe dans une fenetre "HH:MM-HH:MM" UTC, du lundi au vendredi."""
    if not windows:
        return False
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    if d.weekday() >= 5:
        return False
    minutes = d.hour * 60 + d.minute
    for window in windows:
        start, end = window.split("-")
        h1, m1 = (int(x) for x in start.split(":"))
        h2, m2 = (int(x) for x in end.split(":"))
        if h1 * 60 + m1 <= minutes < h2 * 60 + m2:
            return True
    return False


def call_cost(price: dict | None, usage: Usage, peak: bool) -> float:
    if not price:
        return 0.0
    hit = usage.cache_hit_tokens or 0
    if usage.cache_miss_tokens is not None:
        miss = usage.cache_miss_tokens
    else:
        miss = max(usage.prompt_tokens - hit, 0)
    cost = (hit * price.get("input_cache_hit", price.get("input_cache_miss", 0.0))
            + miss * price.get("input_cache_miss", 0.0)
            + usage.completion_tokens * price.get("output", 0.0)) / 1_000_000
    return cost * (price.get("peak_multiplier", 1.0) if peak else 1.0)


def estimate_max_cost(price: dict | None, prompt_chars: int, max_tokens: int, peak: bool) -> float:
    """Borne haute avant l'appel : 3 caracteres par token, aucun cache, sortie maximale."""
    if not price:
        return 0.0
    usage = Usage(prompt_tokens=prompt_chars // 3 + 1, completion_tokens=max_tokens, cache_hit_tokens=0)
    return call_cost(price, usage, peak)
