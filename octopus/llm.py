"""Passerelle LLM OCTOPUS : un seul point d'entree pour tous les appels de modeles.

Pour chaque appel : choix du modele selon le profil de cout et la tache, verification des
capacites, de la confidentialite, de la disponibilite et des preuves du banc, controle des
budgets AVANT l'appel, journalisation de l'usage reel et du cout, justification du choix.

Profils (config/catalog.json) :
- legacy        : modele impose par le code existant (comportement historique) ;
- zero_cost     : local et quotas gratuits uniquement, aucun appel payant ;
- low_cost      : local et gratuit d'abord, payant si aucune alternative validee ;
- quality_first : meilleur modele valide d'abord ;
- bench         : banc d'evaluation (modele impose, sans exigence de preuve).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from . import catalog, journal, pricing
from .pricing import Usage

__all__ = ["BudgetExceeded", "Completion", "GatewayError", "InvalidOutput", "NoEligibleModel", "Usage",
           "complete", "legacy_task", "parse_json"]


class GatewayError(RuntimeError):
    pass


class BudgetExceeded(GatewayError):
    pass


class InvalidOutput(GatewayError):
    pass


class NoEligibleModel(GatewayError):
    def __init__(self, task: str, profile: str, considered: list[dict], last_error: Exception | None = None):
        self.task, self.profile, self.considered, self.last_error = task, profile, considered, last_error
        detail = "; ".join(f"{c['model']} : {c['reason']}" for c in considered) or "aucun candidat configure"
        super().__init__(f"aucun modele utilisable pour {task} (profil {profile}) : {detail}")


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    cost_usd: float
    usage: Usage
    call_id: int
    data: Any = None
    justification: dict = field(default_factory=dict)


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_json(text: str) -> dict:
    """Extrait l'objet JSON d'une reponse (tolere du texte autour, comme le code historique)."""
    match = _JSON_RE.search(text or "")
    if not match:
        raise ValueError("objet JSON introuvable")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("la reponse n'est pas un objet JSON")
    return data


def legacy_task(agent: str, task: str) -> str:
    return catalog.load().legacy_task(agent, task)


# --- secrets et disponibilite des fournisseurs ----------------------------------------

def secret(name: str) -> str:
    """Variable d'environnement, sinon variable utilisateur Windows (registre). Jamais dans le code."""
    value = os.environ.get(name, "").strip()
    if value or sys.platform != "win32":
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return (winreg.QueryValueEx(key, name)[0] or "").strip()
    except OSError:
        return ""


_health: dict[str, tuple[float, bool, str]] = {}


def provider_status(name: str, provider: dict) -> tuple[bool, str]:
    if provider.get("kind") == "local":
        cached = _health.get(name)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1], cached[2]
        url = provider["base_url"].rstrip("/") + provider.get("health_path", "/models")
        try:
            with urllib.request.urlopen(url, timeout=provider.get("health_timeout_s", 1.5)) as resp:
                ok, reason = 200 <= resp.status < 300, f"serveur local HTTP {resp.status}"
        except Exception as exc:
            ok, reason = False, f"serveur local injoignable ({type(exc).__name__})"
        _health[name] = (time.monotonic(), ok, reason)
        return ok, reason
    env = provider.get("api_key_env")
    if env and not secret(env):
        return False, f"cle {env} absente"
    return True, "disponible"


# --- transport (remplacable dans les tests) ----------------------------------------------

_transport_override: Callable[[dict, dict], tuple[str, Usage]] | None = None
_clients: dict[tuple, Any] = {}


def _transport(provider: dict, request: dict) -> tuple[str, Usage]:
    if _transport_override is not None:
        return _transport_override(provider, request)
    from openai import OpenAI

    key = (provider["base_url"], provider.get("api_key_env"), provider.get("timeout_s"), provider.get("max_retries"))
    client = _clients.get(key)
    if client is None:
        kwargs: dict = {"api_key": (secret(provider["api_key_env"]) if provider.get("api_key_env") else "") or "local",
                        "base_url": provider["base_url"]}
        if provider.get("timeout_s") is not None:
            kwargs["timeout"] = provider["timeout_s"]
        if provider.get("max_retries") is not None:
            kwargs["max_retries"] = provider["max_retries"]
        client = _clients[key] = OpenAI(**kwargs)
    response = client.chat.completions.create(**request)
    text = (response.choices[0].message.content or "").strip()
    return text, _usage(getattr(response, "usage", None))


def _opt_int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _usage(u) -> Usage:
    prompt_details = getattr(u, "prompt_tokens_details", None)
    completion_details = getattr(u, "completion_tokens_details", None)
    hit = _opt_int(getattr(u, "prompt_cache_hit_tokens", None))  # DeepSeek
    if hit is None:
        hit = _opt_int(getattr(prompt_details, "cached_tokens", None))  # format OpenAI
    return Usage(
        prompt_tokens=_opt_int(getattr(u, "prompt_tokens", None)) or 0,
        completion_tokens=_opt_int(getattr(u, "completion_tokens", None)) or 0,
        cache_hit_tokens=hit,
        cache_miss_tokens=_opt_int(getattr(u, "prompt_cache_miss_tokens", None)),
        reasoning_tokens=_opt_int(getattr(completion_details, "reasoning_tokens", None)),
    )


# --- routage ---------------------------------------------------------------------------

def _resolve_profile(cat: catalog.Catalog, explicit: str | None, ctx) -> str:
    return (explicit or os.environ.get("OCTOPUS_PROFILE", "").strip()
            or (ctx.profile if ctx and ctx.profile else "") or cat.default_profile)


def _prompt_size(messages: list[dict]) -> tuple[int, int]:
    chars, images = 0, 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    chars += len(part.get("text", ""))
                else:
                    images += 1
    return chars, images


def _ineligibility(cat, profile_name: str, profile: dict, task: str, task_def: dict, model_id: str,
                   model: dict, needs: set[str]) -> str | None:
    allowed = profile.get("allowed_cost_classes", list(catalog.COST_CLASSES))
    if model["cost_class"] not in allowed:
        return f"classe de cout {model['cost_class']} interdite par le profil {profile_name}"
    if task_def.get("privacy") == "sensitive" and model["cost_class"] != "local" and not profile.get("honor_pins"):
        return "tache sensible : fournisseur local uniquement"
    missing = needs - set(model.get("capabilities", []))
    if missing:
        return "capacites manquantes : " + ", ".join(sorted(missing))
    ok, why = provider_status(model["provider"], cat.provider(model["provider"]))
    if not ok:
        return why
    if profile.get("require_evidence") and model_id != task_def.get("baseline"):
        proof = journal.evidence(task, model_id, cat.evidence_rules())
        if not proof["eligible"]:
            return proof["reason"]
    return None


def _budget_block(ctx, cat: catalog.Catalog, business: str, estimate: float) -> str | None:
    if estimate <= 0:
        return None
    if ctx is not None:
        for run_id, budget in ctx.budgets:
            spent = journal.subtree_cost(run_id)
            if spent + estimate > budget:
                return (f"budget du run #{run_id} atteint : {spent:.4f} $ depenses + {estimate:.4f} $ estimes "
                        f"> {budget:.4f} $")
    daily = cat.daily_budget_usd()
    if daily is not None:
        spent = journal.spent_today(business or None)
        if spent + estimate > daily:
            return f"plafond journalier atteint : {spent:.4f} $ + {estimate:.4f} $ estimes > {daily:.2f} $"
    return None


def _build_request(model: dict, messages: list[dict], max_tokens: int, json_mode: bool,
                   reasoning: str | None) -> dict:
    request: dict = {"model": model["api_model"], "messages": messages, "max_tokens": max_tokens}
    capabilities = model.get("capabilities", [])
    if reasoning and "reasoning_effort" in capabilities:
        request["reasoning_effort"] = reasoning
    for key, value in copy.deepcopy(model.get("params", {})).items():
        request.setdefault(key, value)
    if json_mode and "json" in capabilities:
        request["response_format"] = {"type": "json_object"}
    return request


def _justify(profile_name: str, task: str, model_id: str, model: dict, considered: list[dict],
             pinned: bool) -> dict:
    justification = {"profile": profile_name, "task": task, "chosen": model_id,
                     "cost_class": model["cost_class"], "considered": considered}
    if model["cost_class"] != "paid":
        return justification
    others = [c for c in considered if c["model"] != model_id]
    if pinned:
        justification["paid_reason"] = "legacy_pin"
        justification["explanation"] = "modele impose par le code existant ; aucune alternative evaluee"
    elif not others:
        justification["paid_reason"] = "no_alternative_configured"
        justification["explanation"] = "aucune alternative locale ou gratuite configuree pour cette tache"
    elif any(c["reason"].startswith(("echec", "sortie invalide")) for c in others):
        justification["paid_reason"] = "fallback_after_failure"
        justification["explanation"] = "repli apres echec : " + "; ".join(f"{c['model']} ({c['reason']})" for c in others)
    else:
        justification["paid_reason"] = "alternatives_ineligible"
        justification["explanation"] = "alternatives ecartees : " + "; ".join(f"{c['model']} ({c['reason']})" for c in others)
    return justification


def complete(task: str, messages: list[dict], *, agent: str = "", business: str | None = None,
             max_tokens: int = 1200, json_mode: bool = False, reasoning: str | None = None,
             needs: tuple[str, ...] = (), pin_model: str | None = None, profile: str | None = None,
             validate: Callable[[str], Any] | None = None) -> Completion:
    cat = catalog.load()
    ctx = journal.current_run()
    profile_name = _resolve_profile(cat, profile, ctx)
    prof = cat.profile(profile_name)
    task_def = cat.task(task)
    need = set(task_def.get("needs", [])) | set(needs) | ({"json"} if json_mode else set())
    business_name = business or (ctx.business if ctx else "")
    pinned = bool(pin_model and prof.get("honor_pins"))
    if pinned:
        candidates = [pin_model]
    else:
        candidates = list(task_def.get("candidates", {}).get(profile_name, [])) or ([pin_model] if pin_model else [])
    prompt_chars, images = _prompt_size(messages)
    digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    considered: list[dict] = []
    last_error: Exception | None = None
    budget_block: str | None = None

    for attempt, model_id in enumerate(candidates, start=1):
        model = cat.model(model_id)
        if model is None:
            considered.append({"model": model_id, "eligible": False, "reason": "absent du catalogue"})
            continue
        reason = _ineligibility(cat, profile_name, prof, task, task_def, model_id, model, need)
        if reason:
            considered.append({"model": model_id, "eligible": False, "reason": reason})
            continue
        provider = cat.provider(model["provider"])
        now = time.time()
        peak = pricing.is_peak(now, provider.get("peak_utc_weekdays"))
        base = {
            "ts": now, "run_id": ctx.id if ctx else None, "root_run_id": ctx.root_id if ctx else None,
            "business": business_name, "agent": agent, "task": task, "profile": profile_name,
            "model": model_id, "provider": model["provider"], "cost_class": model["cost_class"],
            "attempt": attempt, "peak": int(peak), "prompt_sha256": digest, "prompt_chars": prompt_chars,
        }
        estimate = pricing.estimate_max_cost(model.get("price"), prompt_chars + images * pricing.IMAGE_CHARS_ESTIMATE,
                                             max_tokens, peak)
        block = _budget_block(ctx, cat, business_name, estimate)
        if block:
            considered.append({"model": model_id, "eligible": False, "reason": block})
            journal.record_llm_call({**base, "status": "blocked", "error": block, "justification": json.dumps(
                _justify(profile_name, task, model_id, model, considered, pinned), ensure_ascii=False)})
            budget_block = block
            if prof.get("fallback") and attempt < len(candidates):
                continue  # un candidat suivant moins cher (local, quota gratuit) peut encore passer
            raise BudgetExceeded(block)

        request = _build_request(model, messages, max_tokens, json_mode, reasoning)
        started = time.perf_counter()
        try:
            text, usage = _transport(provider, request)
        except Exception as exc:
            failure = f"echec : {type(exc).__name__}: {str(exc)[:160]}"
            considered.append({"model": model_id, "eligible": True, "reason": failure})
            journal.record_llm_call({**base, "status": "error", "error": failure,
                                     "duration_ms": int((time.perf_counter() - started) * 1000),
                                     "justification": json.dumps(_justify(profile_name, task, model_id, model,
                                                                          considered, pinned), ensure_ascii=False)})
            last_error = exc
            if prof.get("fallback") and attempt < len(candidates):
                continue
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        cost = pricing.call_cost(model.get("price"), usage, peak)
        data, status, error = None, "ok", None
        if validate is not None:
            try:
                data = validate(text)
            except Exception as exc:
                status, error = "invalid", f"{type(exc).__name__}: {exc}"[:300]
        justification = _justify(profile_name, task, model_id, model,
                                 considered + [{"model": model_id, "eligible": True, "reason": "choisi"}], pinned)
        call_id = journal.record_llm_call({
            **base, "status": status, "error": error, "prompt_tokens": usage.prompt_tokens,
            "cache_hit_tokens": usage.cache_hit_tokens, "cache_miss_tokens": usage.cache_miss_tokens,
            "completion_tokens": usage.completion_tokens, "reasoning_tokens": usage.reasoning_tokens,
            "cost_usd": cost, "duration_ms": duration_ms, "output_preview": text[:300],
            "justification": json.dumps(justification, ensure_ascii=False),
        })
        if status == "invalid":
            considered.append({"model": model_id, "eligible": True, "reason": f"sortie invalide : {error}"})
            last_error = InvalidOutput(f"{model_id} : {error}")
            if prof.get("fallback") and attempt < len(candidates):
                continue
            raise last_error
        return Completion(text=text, model=model_id, provider=model["provider"], cost_usd=cost, usage=usage,
                          call_id=call_id, data=data, justification=justification)

    if budget_block:
        detail = "; ".join(f"{c['model']} : {c['reason']}" for c in considered)
        raise BudgetExceeded(f"{budget_block} (candidats : {detail})")
    raise NoEligibleModel(task, profile_name, considered, last_error)
