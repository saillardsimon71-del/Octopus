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

__all__ = ["BudgetExceeded", "Completion", "GatewayError", "InvalidOutput", "NoEligibleModel", "TransportResult",
           "Usage", "complete", "legacy_task", "parse_json"]


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
    requested_model: str | None = None
    resolved_model: str | None = None
    resolved_provider: str | None = None
    request_id: str | None = None
    provider_cost_usd: float | None = None
    data: Any = None
    justification: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TransportResult:
    text: str
    usage: Usage
    requested_model: str
    resolved_model: str | None = None
    resolved_provider: str | None = None
    request_id: str | None = None
    provider_cost_usd: float | None = None


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

_transport_override: Callable[[dict, dict], tuple[str, Usage] | TransportResult] | None = None
_clients: dict[tuple, Any] = {}


def _matches_json_type(value: Any, expected: str | list[str]) -> bool:
    types = [expected] if isinstance(expected, str) else expected
    checks = {
        "null": value is None,
        "string": isinstance(value, str),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }
    return any(checks.get(kind, False) for kind in types)


def _declarative_tool_text(request: dict, message: Any) -> str:
    declared = {
        tool["function"]["name"]: tool["function"]
        for tool in request.get("tools", [])
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict)
    }
    tool_calls = getattr(message, "tool_calls", None) or []
    if len(tool_calls) != 1:
        raise ValueError("un appel d'outil structuré est requis")
    call = tool_calls[0].function
    if call.name not in declared:
        raise ValueError(f"outil structuré inconnu: {call.name}")
    if not isinstance(call.arguments, str) or not call.arguments.strip():
        raise ValueError(f"arguments absents pour l'outil structuré: {call.name}")
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments JSON invalides pour l'outil structuré: {call.name}") from exc
    if not isinstance(arguments, dict):
        raise ValueError(f"arguments objet attendus pour l'outil structuré: {call.name}")

    parameters = declared[call.name].get("parameters", {})
    properties = parameters.get("properties", {})
    unexpected = sorted(set(arguments) - set(properties))
    if parameters.get("additionalProperties") is False and unexpected:
        raise ValueError(f"arguments inattendus pour l'outil structuré {call.name}: {unexpected}")
    missing = sorted(set(parameters.get("required", [])) - set(arguments))
    if missing:
        raise ValueError(f"arguments requis absents pour l'outil structuré {call.name}: {missing}")
    for name, value in arguments.items():
        expected = properties.get(name, {}).get("type")
        if expected is not None and not _matches_json_type(value, expected):
            raise ValueError(f"type invalide pour l'argument {name} de l'outil structuré {call.name}")
    return json.dumps({"action": call.name, **arguments}, ensure_ascii=False, separators=(",", ":"))


def _transport(provider: dict, request: dict) -> tuple[str, Usage] | TransportResult:
    if _transport_override is not None:
        return _transport_override(provider, request)
    from openai import OpenAI

    key = (provider["base_url"], provider.get("api_key_env"), provider.get("timeout_s"), provider.get("max_retries"))
    client = _clients.get(key)
    if client is None:
        api_key = secret(provider["api_key_env"]) if provider.get("api_key_env") else ""
        kwargs: dict = {"api_key": api_key or "local", "base_url": provider["base_url"]}
        if provider.get("timeout_s") is not None:
            kwargs["timeout"] = provider["timeout_s"]
        if provider.get("max_retries") is not None:
            kwargs["max_retries"] = provider["max_retries"]
        client = _clients[key] = OpenAI(**kwargs)
    raw_response = client.chat.completions.with_raw_response.create(**request)
    response = raw_response.parse()
    headers = raw_response.headers
    message = response.choices[0].message
    text = (message.content or "").strip()
    tool_choice = request.get("tool_choice")
    if tool_choice == "required":
        text = _declarative_tool_text(request, message)
    elif isinstance(tool_choice, dict):
        expected_tool = tool_choice.get("function", {}).get("name")
        tool_calls = getattr(message, "tool_calls", None) or []
        if expected_tool and (len(tool_calls) != 1 or tool_calls[0].function.name != expected_tool):
            raise ValueError(f"appel structuré attendu: {expected_tool}")
        arguments = tool_calls[0].function.arguments if expected_tool else None
        if expected_tool and (not isinstance(arguments, str) or not arguments.strip()):
            raise ValueError(f"arguments absents pour l'appel structuré: {expected_tool}")
        if expected_tool:
            text = arguments.strip()
    provider_cost = headers.get("x-omniroute-response-cost")
    try:
        provider_cost_usd = float(provider_cost) if provider_cost is not None else None
    except ValueError:
        provider_cost_usd = None

    litellm_model = headers.get("x-litellm-model-name")
    resolved_model = (
        headers.get("x-omniroute-model")
        or litellm_model
        or getattr(response, "model", None)
    )
    resolved_provider = headers.get("x-omniroute-provider")
    if resolved_provider is None and litellm_model and "/" in litellm_model:
        resolved_provider = litellm_model.split("/", 1)[0]

    return TransportResult(
        text=text,
        usage=_usage(getattr(response, "usage", None)),
        requested_model=request["model"],
        resolved_model=resolved_model,
        resolved_provider=resolved_provider,
        request_id=raw_response.request_id,
        provider_cost_usd=provider_cost_usd,
    )


def _transport_result(value: tuple[str, Usage] | TransportResult, requested_model: str,
                      requested_provider: str) -> TransportResult:
    if isinstance(value, TransportResult):
        return value
    text, usage = value
    resolved = requested_provider != "omniroute"
    return TransportResult(
        text=text,
        usage=usage,
        requested_model=requested_model,
        resolved_model=requested_model if resolved else None,
        resolved_provider=requested_provider if resolved else None,
    )


def _opt_int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _usage(u) -> Usage:
    prompt_details = getattr(u, "prompt_tokens_details", None)
    completion_details = getattr(u, "completion_tokens_details", None)
    hit = _opt_int(getattr(u, "prompt_cache_hit_tokens", None))
    if hit is None:
        hit = _opt_int(getattr(prompt_details, "cached_tokens", None))
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
    if (profile_name == "zero_cost" and model["provider"] == "omniroute"
            and model.get("zero_cost_attestation") != "free_only"):
        return "pool OmniRoute : attestation free_only absente"
    ok, why = provider_status(model["provider"], cat.provider(model["provider"]))
    if not ok:
        return why
    evidence_required = profile.get("require_evidence") and model_id != task_def.get("baseline")
    # OmniRoute auto/free est un routeur virtuel : son éligibilité est déterminée par le
    # health-check du gateway et par son propre pool free. On ne fabrique pas de bench local
    # fictif pour autoriser le premier démarrage.
    if evidence_required and model["provider"] == "omniroute" and profile_name in {"zero_cost", "low_cost"}:
        evidence_required = False
    if evidence_required:
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
        spent = journal.spent_today(None)
        if spent + estimate > daily:
            return f"plafond journalier atteint : {spent:.4f} $ + {estimate:.4f} $ estimes > {daily:.2f} $"
    if business:
        from . import businesses
        try:
            declared = businesses.get(business)
        except Exception:
            declared = None
        cap = declared.budget_daily_usd if declared else None
        if cap is not None:
            spent = journal.spent_today(business)
            if spent + estimate > cap:
                return (f"plafond journalier de l'activite {business} atteint : {spent:.4f} $ + {estimate:.4f} $ "
                        f"> {cap:.2f} $")
    return None


def _build_request(model: dict, messages: list[dict], max_tokens: int, json_mode: bool,
                   reasoning: str | None, json_schema: dict | None = None,
                   tool_schemas: list[dict] | None = None) -> dict:
    request: dict = {"model": model["api_model"], "messages": messages, "max_tokens": max_tokens}
    capabilities = model.get("capabilities", [])
    if reasoning and "reasoning_effort" in capabilities:
        request["reasoning_effort"] = reasoning
    for key, value in copy.deepcopy(model.get("params", {})).items():
        request.setdefault(key, value)
    if json_schema is not None and "json" in capabilities:
        schema_mode = model.get("json_schema_mode")
        if schema_mode == "tool_call":
            if not tool_schemas:
                raise ValueError("schémas d'outils déclaratifs requis pour le mode tool_call")
            request["tools"] = copy.deepcopy(tool_schemas)
            request["tool_choice"] = "required"
        elif schema_mode == "json_object":
            request["response_format"] = {"type": "json_object"}
        else:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "octopus_response",
                    "strict": True,
                    "schema": copy.deepcopy(json_schema),
                },
            }
    elif json_mode and "json" in capabilities:
        request["response_format"] = {"type": "json_object"}
    return request


def _justify(profile_name: str, task: str, model_id: str, model: dict, considered: list[dict],
             pinned: bool) -> dict:
    justification = {"profile": profile_name, "task": task, "chosen": model_id,
                     "cost_class": model["cost_class"], "considered": considered}
    if profile_name == "zero_cost" and model["provider"] == "omniroute":
        justification["zero_cost_attestation"] = model.get("zero_cost_attestation")
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


def _zero_cost_violation(profile_name: str, model: dict, result: TransportResult) -> str | None:
    if profile_name != "zero_cost":
        return None
    if result.provider_cost_usd not in {None, 0.0}:
        return f"route zero_cost bloquée : coût résolu {result.provider_cost_usd:.6f} $"
    if model["provider"] == "omniroute" and not (result.resolved_model and result.resolved_provider):
        return "route zero_cost bloquée : identité résolue absente"
    return None


def complete(task: str, messages: list[dict], *, agent: str = "", business: str | None = None,
             max_tokens: int = 1200, json_mode: bool = False, json_schema: dict | None = None,
             tool_schemas: list[dict] | None = None,
             reasoning: str | None = None,
             needs: tuple[str, ...] = (), pin_model: str | None = None, profile: str | None = None,
             validate: Callable[[str], Any] | None = None) -> Completion:
    cat = catalog.load()
    ctx = journal.current_run()
    profile_name = _resolve_profile(cat, profile, ctx)
    prof = cat.profile(profile_name)
    task_def = cat.task(task)
    need = set(task_def.get("needs", [])) | set(needs) | ({"json"} if json_mode or json_schema is not None else set())
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
            "requested_model": model["api_model"],
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
                continue
            raise BudgetExceeded(block)

        request = _build_request(
            model, messages, max_tokens, json_mode, reasoning, json_schema, tool_schemas,
        )
        started = time.perf_counter()
        try:
            result = _transport_result(_transport(provider, request), request["model"], model["provider"])
            text, usage = result.text, result.usage
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
        cost = (result.provider_cost_usd if result.provider_cost_usd is not None
                else pricing.call_cost(model.get("price"), usage, peak))
        data, status, error = None, "ok", _zero_cost_violation(profile_name, model, result)
        if error is not None:
            status = "blocked"
        elif validate is not None:
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
            "resolved_model": result.resolved_model, "resolved_provider": result.resolved_provider,
            "request_id": result.request_id, "provider_cost_usd": result.provider_cost_usd,
            "justification": json.dumps(justification, ensure_ascii=False),
        })
        if status == "blocked":
            considered.append({"model": model_id, "eligible": False, "reason": error})
            last_error = GatewayError(error)
            if prof.get("fallback") and attempt < len(candidates):
                continue
            break
        if status == "invalid":
            considered.append({"model": model_id, "eligible": True, "reason": f"sortie invalide : {error}"})
            last_error = InvalidOutput(f"{model_id} : {error}")
            if prof.get("fallback") and attempt < len(candidates):
                continue
            raise last_error
        return Completion(text=text, model=model_id, provider=model["provider"], cost_usd=cost, usage=usage,
                          call_id=call_id, requested_model=result.requested_model,
                          resolved_model=result.resolved_model, resolved_provider=result.resolved_provider,
                          request_id=result.request_id, provider_cost_usd=result.provider_cost_usd,
                          data=data, justification=justification)

    if budget_block:
        detail = "; ".join(f"{c['model']} : {c['reason']}" for c in considered)
        raise BudgetExceeded(f"{budget_block} (candidats : {detail})")
    raise NoEligibleModel(task, profile_name, considered, last_error)
