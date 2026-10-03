"""Passerelle LLM OCTOPUS : un seul point d'entree pour tous les appels de modeles.

Pour chaque appel : choix du modele selon le profil de cout et la tache, verification des
capacites, de la confidentialite, de la disponibilite et des preuves du banc, controle des
budgets AVANT l'appel, journalisation de l'usage reel et du cout, justification du choix.

Profils (config/catalog.json) :
- legacy        : modele impose par le code existant (comportement historique) ;
- zero_cost     : local et quotas gratuits uniquement, aucun appel payant ;
- economical    : au plus deux routes gratuites (trois requetes), puis DeepSeek sous plafond ;
- low_cost      : local et gratuit d'abord, payant si aucune alternative validee ;
- quality_first : meilleur modele valide d'abord ;
- bench         : banc d'evaluation (modele impose, sans exigence de preuve).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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


class RequestLimitExceeded(BudgetExceeded):
    pass


class InvalidOutput(GatewayError):
    pass


class StructuredResponseError(ValueError):
    def __init__(self, message: str, result):
        super().__init__(message)
        self.result = result


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


def _repair_json_control_chars(text: str) -> str | None:
    match = _JSON_RE.search(text or "")
    if not match:
        return None
    repaired = []
    quoted = escaped = changed = False
    for char in match.group(0):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif quoted and ord(char) < 32:
            repaired.append(json.dumps(char)[1:-1])
            changed = True
            continue
        repaired.append(char)
    return text[:match.start()] + "".join(repaired) + text[match.end():] if changed else None


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


def _safe_error(exc: Exception, provider: dict) -> str:
    message = str(exc)
    key_name = provider.get("api_key_env")
    api_key = secret(key_name) if key_name else ""
    return (message.replace(api_key, "[redacted]") if api_key else message)[:160]


_health: dict[str, tuple[float, bool, str]] = {}
_rate_limit_cooldowns: dict[str, tuple[float, str]] = {}
_provider_cooldowns: dict[str, tuple[float, str]] = {}
_DEFAULT_RATE_LIMIT_COOLDOWN_S = 30.0
_DEFAULT_PROVIDER_COOLDOWN_S = 60.0


def _rate_limit_delay(exc: Exception) -> float | None:
    """Retourne le délai de cooldown si l'exception est un 429, sinon None.

    Retry-After numérique est respecté quand il est plus long que notre plancher
    local ; il ne peut jamais raccourcir cette protection. Le fallback court évite
    de retenter immédiatement une route déjà limitée sans transformer un incident
    transitoire en bannissement pour tout le run.
    """
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)

    message = str(exc).lower()
    is_rate_limit = status == 429 or (
        "429" in message and any(marker in message for marker in (
            "rate limit", "rate_limit", "rate-limit", "too many requests", "rate_limit_exceeded",
        ))
    )
    if not is_rate_limit:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            raw = json.dumps(body, ensure_ascii=False).lower()
            is_rate_limit = (
                '"code": 429' in raw
                or '"code":"429"' in raw
                or "rate_limit" in raw
                or "rate limit" in raw
            ) and "429" in raw
    if not is_rate_limit:
        return None

    headers = getattr(response, "headers", None) if response is not None else None
    if headers:
        retry_ms = headers.get("retry-after-ms")
        if retry_ms is not None:
            try:
                return max(_DEFAULT_RATE_LIMIT_COOLDOWN_S, float(retry_ms) / 1000.0)
            except (TypeError, ValueError):
                pass
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                return max(_DEFAULT_RATE_LIMIT_COOLDOWN_S, float(retry_after))
            except (TypeError, ValueError):
                try:
                    until = parsedate_to_datetime(retry_after)
                    return max(_DEFAULT_RATE_LIMIT_COOLDOWN_S,
                               (until - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
    return _DEFAULT_RATE_LIMIT_COOLDOWN_S


def _set_rate_limit_cooldown(model_id: str, exc: Exception) -> float | None:
    delay = _rate_limit_delay(exc)
    if delay is None:
        return None
    _rate_limit_cooldowns[model_id] = (
        time.monotonic() + delay,
        f"429 rate limit; cooldown {delay:g}s",
    )
    return delay


def _rate_limit_cooldown_reason(model_id: str) -> str | None:
    entry = _rate_limit_cooldowns.get(model_id)
    if entry is None:
        rows = journal.query("SELECT ts, status, justification FROM llm_calls WHERE model=? AND status IN ('error','ok') "
                             "AND ts>=? ORDER BY ts DESC LIMIT 1", (model_id, time.time() - 86400))
        if rows and rows[0]["status"] == "error":
            detail = json.loads(rows[0]["justification"] or "{}")
            delay = detail.get("rate_limit_cooldown_s")
            remaining = rows[0]["ts"] + delay - time.time() if isinstance(delay, (int, float)) else 0
            if remaining > 0:
                return f"429 rate limit; cooldown ({remaining:.1f}s restantes)"
        return None
    until, reason = entry
    remaining = until - time.monotonic()
    if remaining <= 0:
        _rate_limit_cooldowns.pop(model_id, None)
        return None
    return f"{reason} ({remaining:.1f}s restantes)"


def _is_provider_connection_error(exc: Exception) -> bool:
    """Vrai seulement pour une panne de connectivité partagée par le provider.

    Le run H3 #66 a observé des `APIConnectionError` répétés sur plusieurs modèles
    modèles partageant le même fournisseur. On reste volontairement conservateur :
    un read timeout, un 429, une erreur d'auth ou une sortie invalide ne prouvent
    pas que le provider entier est injoignable.
    """
    connection_types = {
        "APIConnectionError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "ConnectError",
        "ConnectTimeout",
        "gaierror",
    }
    current: BaseException | None = exc
    seen: set[int] = set()
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if type(current).__name__ in connection_types:
            return True
        current = current.__cause__ or current.__context__
    return False


def _set_provider_cooldown(provider_name: str, exc: Exception) -> float | None:
    """Met temporairement hors circuit un provider dont le transport est injoignable."""
    if not _is_provider_connection_error(exc):
        return None
    delay = _DEFAULT_PROVIDER_COOLDOWN_S
    _provider_cooldowns[provider_name] = (
        time.monotonic() + delay,
        f"provider injoignable ({type(exc).__name__}); cooldown {delay:g}s",
    )
    return delay


def _provider_cooldown_reason(provider_name: str) -> str | None:
    entry = _provider_cooldowns.get(provider_name)
    if entry is None:
        rows = journal.query("SELECT ts, status, justification FROM llm_calls WHERE provider=? "
                             "AND status IN ('error','ok','blocked') AND ts>=? ORDER BY ts DESC LIMIT 1",
                             (provider_name, time.time() - 86400))
        if rows and rows[0]["status"] in {"error", "blocked"}:
            detail = json.loads(rows[0]["justification"] or "{}")
            delay = (detail.get("provider_safety_cooldown_s") or detail.get("provider_rate_limit_cooldown_s")
                     or detail.get("provider_cooldown_s"))
            remaining = rows[0]["ts"] + delay - time.time() if isinstance(delay, (int, float)) else 0
            if remaining > 0:
                kind = ("route gratuite non attestée" if detail.get("provider_safety_cooldown_s") else
                        "quota 429" if detail.get("provider_rate_limit_cooldown_s") else "provider injoignable")
                return f"{provider_name} {kind}; cooldown ({remaining:.1f}s restantes)"
        return None
    until, reason = entry
    remaining = until - time.monotonic()
    if remaining <= 0:
        _provider_cooldowns.pop(provider_name, None)
        return None
    return f"{reason} ({remaining:.1f}s restantes)"


def provider_status(name: str, provider: dict) -> tuple[bool, str]:
    if name not in {"deepseek", "openrouter"}:
        return False, "fournisseur retire"
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
    structured_error = None
    try:
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
    except ValueError as exc:
        structured_error = str(exc)
    provider_cost = None
    if provider.get("api_key_env") == "OPENROUTER_API_KEY":
        provider_cost = _response_field(getattr(response, "usage", None), "cost")
    try:
        provider_cost_usd = float(provider_cost) if provider_cost is not None and not isinstance(provider_cost, bool) else None
    except (ValueError, TypeError):
        provider_cost_usd = None

    resolved_model = getattr(response, "model", None)
    resolved_provider = None
    if provider.get("api_key_env") == "OPENROUTER_API_KEY":
        resolved_provider = _response_field(response, "provider")
        metadata = _response_field(response, "openrouter_metadata") or {}
        endpoints = _response_field(metadata, "endpoints") or {}
        available = _response_field(endpoints, "available") or []
        selected = next((entry for entry in available if _response_field(entry, "selected")), None)
        if selected is not None:
            resolved_provider = _response_field(selected, "provider")
            resolved_model = _response_field(selected, "model") or resolved_model

    result = TransportResult(
        text=text,
        usage=_usage(getattr(response, "usage", None)),
        requested_model=request["model"],
        resolved_model=resolved_model,
        resolved_provider=resolved_provider,
        request_id=raw_response.request_id,
        provider_cost_usd=provider_cost_usd,
    )
    if structured_error is not None:
        raise StructuredResponseError(structured_error, result)
    return result


def _transport_result(value: tuple[str, Usage] | TransportResult, requested_model: str,
                      requested_provider: str) -> TransportResult:
    if isinstance(value, TransportResult):
        return value
    text, usage = value
    return TransportResult(
        text=text,
        usage=usage,
        requested_model=requested_model,
        resolved_model=requested_model,
        resolved_provider=requested_provider,
    )


def _opt_int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _response_field(value, name):
    if isinstance(value, dict):
        return value.get(name)
    found = getattr(value, name, None)
    return found if found is not None else (getattr(value, "model_extra", None) or {}).get(name)


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
    # Plus la source est locale/spécifique, plus elle a priorité :
    # appel explicite > run/mission courant > environnement global > défaut catalogue.
    return (explicit or (ctx.profile if ctx and ctx.profile else "")
            or os.environ.get("OCTOPUS_PROFILE", "").strip() or cat.default_profile)


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
    if model["provider"] not in {"openrouter", "deepseek"}:
        return "fournisseur retire"
    allowed = profile.get("allowed_cost_classes", list(catalog.COST_CLASSES))
    if model["cost_class"] not in allowed:
        return f"classe de cout {model['cost_class']} interdite par le profil {profile_name}"
    if model["provider"] == "openrouter" and (model["cost_class"] != "free_quota"
                                                or not model.get("free_verified")):
        return "OpenRouter exige une tarification gratuite verifiee"
    if model["provider"] == "openrouter" and not secret("OPENROUTER_API_KEY"):
        return "cle OPENROUTER_API_KEY absente"
    if (model["cost_class"] == "paid" and profile.get("allowed_paid_providers")
            and model["provider"] not in profile["allowed_paid_providers"]):
        return f"fournisseur payant {model['provider']} interdit par le profil {profile_name}"
    if task_def.get("privacy") == "sensitive" and model["cost_class"] != "local" and not profile.get("honor_pins"):
        return "tache sensible : fournisseur local uniquement"
    missing = needs - set(model.get("capabilities", []))
    if missing:
        return "capacites manquantes : " + ", ".join(sorted(missing))
    provider_cooldown = _provider_cooldown_reason(model["provider"])
    if provider_cooldown:
        return provider_cooldown
    cooldown = _rate_limit_cooldown_reason(model_id)
    if cooldown:
        return cooldown
    ok, why = provider_status(model["provider"], cat.provider(model["provider"]))
    if not ok:
        return why
    if task == "browser.react_step":
        proof = browser_quality(model)
        return None if proof["eligible"] else proof["reason"]
    evidence_required = profile.get("require_evidence") and model_id != task_def.get("baseline")
    if profile_name == "economical" and model["provider"] == "deepseek" and model["cost_class"] == "paid":
        evidence_required = False
    if evidence_required:
        identity = model.get("evidence_identity", model_id)
        proof_tasks = (task,) + (_BENCH_TASKS.get(task, ()) if profile_name == "economical" else ())
        proofs = [journal.evidence(name, identity, cat.evidence_rules()) for name in proof_tasks]
        if not any(proof["eligible"] for proof in proofs):
            return proofs[0]["reason"]
    return None


def _budget_block(ctx, cat: catalog.Catalog, business: str, estimate: float) -> str | None:
    if ctx is not None and ctx.llm_cost_observation_only:
        return None
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


def _structured_methods(model: dict, json_mode: bool, json_schema: dict | None,
                        tool_schemas: list[dict] | None) -> list[str | None]:
    """Ordre empirique des mecanismes de sortie structuree pour un modele.

    La capacite a produire du JSON ne signifie pas que le provider accepte
    response_format=json_object. Une methode "text" garde le contrat JSON dans
    le prompt mais n'ajoute aucune contrainte API.
    """
    if not json_mode and json_schema is None:
        return [None]

    declared = list(model.get("structured_methods", []))
    if declared:
        methods = declared
    elif json_schema is not None:
        schema_mode = model.get("json_schema_mode")
        if schema_mode == "tool_call":
            methods = ["tool_call", "text"]
        elif schema_mode == "json_object":
            methods = ["json_object", "text"]
        else:
            methods = ["json_schema", "text"]
    else:
        methods = ["json_object", "text"]

    allowed = {"json_schema", "json_object", "tool_call", "text"}
    methods = [m for m in methods if m in allowed]
    if json_schema is None:
        methods = [m for m in methods if m != "json_schema"]
    if "tool_call" in methods and (json_schema is None or not tool_schemas):
        methods = [m for m in methods if m != "tool_call"]
    if not methods:
        methods = ["text"]
    return methods


def _structured_method_error(exc: Exception) -> bool:
    """Vrai seulement si l'erreur pointe vers le mecanisme de sortie structuree.

    On ne traite pas tout HTTP 400 comme une incompatibilite de methode : certains
    providers reaffichent la requete (et donc le mot response_format) dans leur erreur.
    """
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    if (status is not None and status != 400) or isinstance(exc, TimeoutError) or _is_provider_connection_error(exc):
        return False

    parts: list[str] = []
    structured_signal = False

    def collect(value: Any) -> None:
        nonlocal structured_signal
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).lower()
                if lowered == "failed_generation":
                    structured_signal = True
                    parts.append("failed_generation")
                elif lowered in {"error", "code", "type", "reason", "message"}:
                    collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
        elif value is not None:
            parts.append(str(value).lower())

    collect(getattr(exc, "body", None))
    message = " ".join(parts) if parts else str(exc).lower()

    unrelated = (
        "invalid_api_key", "authentication", "unauthorized", "permission",
        "rate_limit", "context_length", "content_policy", "safety",
        "model_not_found", "invalid model",
    )
    if any(marker in message for marker in unrelated):
        return False

    if structured_signal:
        return True
    markers = (
        "failed to generate json",
        "json schema",
        "structured output",
        "unsupported response_format",
        "response_format unsupported",
        "response_format is not supported",
        "invalid response_format",
        "unknown response_format",
        "json_object is not supported",
        "json_object not supported",
        "unsupported json_object",
        "appel d'outil structuré",
        "outil structuré inconnu",
        "arguments absents pour l'outil structuré",
        "arguments objet attendus pour l'outil structuré",
        "arguments inattendus pour l'outil structuré",
        "arguments requis absents",
        "arguments json invalides",
        "type invalide pour l'argument",
        "appel structuré attendu",
        "arguments absents pour l'appel structuré",
    )
    return any(marker in message for marker in markers)


def _build_request(model: dict, messages: list[dict], max_tokens: int, json_mode: bool,
                   reasoning: str | None, json_schema: dict | None = None,
                   tool_schemas: list[dict] | None = None,
                   structured_method: str | None = None) -> dict:
    request: dict = {"model": model["api_model"], "messages": messages, "max_tokens": max_tokens}
    capabilities = model.get("capabilities", [])
    if reasoning and "reasoning_effort" in capabilities:
        request["reasoning_effort"] = reasoning
    for key, value in copy.deepcopy(model.get("params", {})).items():
        request.setdefault(key, value)
    if model.get("provider") == "openrouter":
        if reasoning and "reasoning" in capabilities:
            request.setdefault("extra_body", {}).setdefault("reasoning", {"effort": reasoning})
        request.setdefault("extra_body", {}).setdefault("provider", {})["max_price"] = {
            "prompt": 0, "completion": 0,
        }

    # Compatibilite des appels directs historiques a _build_request().
    # La gateway moderne passe toujours structured_method explicitement.
    if structured_method is None and (json_mode or json_schema is not None):
        if json_schema is not None:
            schema_mode = model.get("json_schema_mode")
            if schema_mode == "tool_call":
                structured_method = "tool_call"
            elif schema_mode == "json_object":
                structured_method = "json_object"
            else:
                structured_method = "json_schema"
        elif json_mode and "json" in capabilities:
            structured_method = "json_object"

    if structured_method == "tool_call":
        if json_schema is None or not tool_schemas:
            raise ValueError("schémas d'outils déclaratifs requis pour le mode tool_call")
        request["tools"] = copy.deepcopy(tool_schemas)
        request["tool_choice"] = "required"
    elif structured_method == "json_schema":
        if json_schema is None:
            raise ValueError("json_schema requis pour le mode json_schema")
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "octopus_response",
                "strict": True,
                "schema": copy.deepcopy(json_schema),
            },
        }
    elif structured_method == "json_object":
        request["response_format"] = {"type": "json_object"}
    elif structured_method in {None, "text"}:
        pass
    else:
        raise ValueError(f"methode structuree inconnue: {structured_method}")
    return request


def _justify(profile_name: str, task: str, model_id: str, model: dict, considered: list[dict],
             pinned: bool) -> dict:
    justification = {"profile": profile_name, "task": task, "chosen": model_id,
                     "cost_class": model["cost_class"], "considered": considered}
    if model["provider"] == "openrouter":
        justification.update({key: model[key] for key in
            ("evidence_identity", "canonical_slug", "source", "fetched_at")})
    if profile_name == "economical":
        justification["selection_reason"] = "qualité du dernier benchmark, succès et latence récents; gratuit qualifié puis DeepSeek"
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
    # zero_cost interdit tout coût. flash_fallback autorise uniquement ses modèles
    # explicitement "paid" (DeepSeek Flash dans les tâches agents) ; ses routes
    # free_quota doivent rester réellement gratuites.
    enforce_free = model["provider"] == "openrouter" or profile_name == "zero_cost" or (
        profile_name in {"flash_fallback", "economical"} and model["cost_class"] != "paid"
    )
    if not enforce_free:
        return None
    if result.provider_cost_usd not in {None, 0.0}:
        return f"route gratuite bloquée : coût résolu {result.provider_cost_usd:.6f} $"
    if model["provider"] == "openrouter":
        if isinstance(result.provider_cost_usd, bool) or result.provider_cost_usd != 0.0:
            return "route OpenRouter bloquée : coût provider non attesté à zéro"
        if result.resolved_model not in {model["api_model"], model.get("canonical_slug")} or not result.resolved_provider:
            return "route OpenRouter bloquée : modèle ou provider gratuit non résolu"
    return None


_BENCH_TASKS = {
    "agent.plan": ("octopus.plan",),
    "agent.react_step": ("octopus.json",),
    "agent.synthesize": ("octopus.synthesis",),
    "agent.decision": ("octopus.decision", "octopus.critique"),
    "web.summarize": ("octopus.extraction", "octopus.synthesis"),
    "web.inspect_page": ("octopus.extraction",),
}


def _rank_economical(task: str, candidates: list[str], cat: catalog.Catalog) -> list[str]:
    bench_tasks = _BENCH_TASKS.get(task, ("octopus.json",))
    marks = ",".join("?" for _ in bench_tasks)
    rows = journal.query(
        f"SELECT task, model, passed, latency_ms, bench_run_id FROM bench_results WHERE task IN ({marks}) AND ts>=? ORDER BY ts DESC",
        (*bench_tasks, time.time() - 14 * 86400),
    )
    bench: dict[str, list[int]] = {}
    bench_latency: dict[str, list[int]] = {}
    latest_run: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["task"], row["model"])
        if key not in latest_run:
            latest_run[key] = row["bench_run_id"]
        if row["bench_run_id"] == latest_run[key] and len(bench.setdefault(row["model"], [])) < 20:
            bench[row["model"]].append(row["passed"])
            if row["latency_ms"] is not None:
                bench_latency.setdefault(row["model"], []).append(row["latency_ms"])
    recent = journal.query("SELECT model, status, duration_ms FROM llm_calls WHERE task=? AND ts>=? "
                           "ORDER BY ts DESC LIMIT 100", (task, time.time() - 86400))
    health: dict[str, list] = {}
    for row in recent:
        health.setdefault(row["model"], []).append(row)

    def score(model_id: str) -> tuple:
        model = cat.model(model_id)
        if model is None or model["cost_class"] == "paid":
            return (1, 0)
        samples = bench.get(model.get("evidence_identity", model_id), [])
        quality = sum(samples) / len(samples) if samples else 0.5
        calls = health.get(model_id, [])[:10]
        reliability = sum(r["status"] == "ok" for r in calls) / len(calls) if calls else 0.5
        latency = sum((r["duration_ms"] or 0) for r in calls) / len(calls) if calls else 0
        benchmark_latency = statistics.median(bench_latency[model.get("evidence_identity", model_id)]) if bench_latency.get(model.get("evidence_identity", model_id)) else 0
        return (0, -(quality * 100 + reliability * 20
                     - min(latency / 1000, 30) - min(benchmark_latency / 1000, 10)))

    qualified = []
    for mid in candidates:
        model = cat.model(mid)
        samples = bench.get(model.get("evidence_identity", mid), []) if model else []
        if model and (model["cost_class"] == "paid" or len(samples) < 2 or sum(samples) / len(samples) >= .9):
            qualified.append(mid)
    return sorted(qualified, key=score)


# Multi-step fixture evidence is independent of JSON/API health. No baseline exemption.
BROWSER_BENCH_TASK = "browser.trajectory"
BROWSER_BENCH_VERSION = "browser-v1"
BROWSER_SCENARIOS = frozenset({"affordance", "dynamic_menu", "recovery", "ambiguous_dom",
    "sufficient_dom", "stale_refs", "invalid_args", "multi_screen", "language_layout", "vision"})


def browser_identity(model: dict, resolved_model: str | None = None) -> str | None:
    """Identity of the underlying model, never reputation for an unknown auto pool."""
    name = resolved_model or model.get("api_model", "")
    if model.get("provider") == "openrouter":
        if not model.get("free_verified") or not model.get("evidence_identity"):
            return None
        if resolved_model is not None and resolved_model not in {model["api_model"], model.get("canonical_slug")}:
            return None
        return "browser.model:" + model["evidence_identity"]
    if not name or any(token in name.lower() for token in ("auto", "best-free", "router")):
        return None
    if resolved_model == model.get('api_model') and model.get('browser_identity'):
        name = model['browser_identity']
    return "browser.model:" + name


def browser_quality(model: dict, *, resolved_model: str | None = None) -> dict:
    identity = browser_identity(model, resolved_model)
    if identity is None:
        return {"eligible": False, "quality": 0., "identity": None, "reason": "browser: identité fixe inconnue"}
    rows = journal.query("SELECT * FROM bench_results WHERE task=? AND model=? AND prompt_version=? "
                         "AND ts>=? ORDER BY ts DESC,id DESC",
                         (BROWSER_BENCH_TASK, identity, BROWSER_BENCH_VERSION, time.time() - 14 * 86400))
    if not rows:
        return {"eligible": False, "quality": 0., "identity": identity,
                "reason": "browser: qualification absente ; lancer browser benchmark sur fixtures locales"}
    # Last completed/attempted suite wins: a failed rerun cannot fall back to old glory.
    latest = rows[0]["bench_run_id"]
    attempted = [r for r in rows if r["bench_run_id"] == latest and r["item"] in BROWSER_SCENARIOS]
    infra_errors = {"RateLimitError", "NoEligibleModel", "APIConnectionError", "APITimeoutError", "TimeoutError"}
    samples = [r for r in attempted if not json.loads(r["checks"] or "{}").get("benchmark_incomplete")
               and r["error"] not in infra_errors]
    by_item = {item: [r for r in samples if r["item"] == item] for item in BROWSER_SCENARIOS}
    coverage = all(by_item.values())
    rates = {item: sum(r["passed"] for r in group) / len(group) if group else 0.
             for item, group in by_item.items()}
    measured = [rates[item] for item, group in by_item.items() if group]
    quality = sum(measured) / len(measured) if measured else None
    safe = all(not any(json.loads(r["checks"] or "{}").get(key, False)
                       for key in ("forbidden", "benchmark_incomplete")) for r in attempted)
    safe = safe and len(samples) == len(attempted)
    mandatory = all(rates[x] == 1. for x in ("vision", "ambiguous_dom", "recovery", "invalid_args", "stale_refs"))
    eligible = coverage and quality is not None and quality >= .9 and mandatory and safe
    return {"eligible": eligible, "quality": quality, "identity": identity, "samples": len(samples),
            "coverage": coverage, "bench_run_id": latest,
            "incomplete": len(samples) != len(attempted), "attempted": len(attempted),
            "reason": "browser: 10 scénarios, >=90%, recovery/refs/arguments/vision réussis" if eligible
                      else "browser: évaluation interrompue ; qualification non acquise" if len(samples) != len(attempted)
                      else "browser: seuil ou couverture insuffisant"}


def _rank_browser(candidates, cat, *, exclude_models=(), min_quality=0.):
    proof = {m: browser_quality(cat.model(m)) for m in candidates if cat.model(m)}
    qualified = [m for m in candidates if m not in exclude_models and m in proof
                 and proof[m]['eligible'] and proof[m]['quality'] > min_quality]
    def rank(mid):
        model = cat.model(mid)
        # Price wins only AFTER competence qualification; API reliability remains observable.
        paid = model["cost_class"] == "paid"
        price = sum(v for v in model.get("price", {}).values() if isinstance(v, (float, int)))
        recent = journal.query("SELECT status FROM llm_calls WHERE task='browser.react_step' AND model=? "
                               "ORDER BY id DESC LIMIT 10", (mid,))
        reliability = sum(r["status"] == "ok" for r in recent) / len(recent) if recent else 1.
        return (paid, price, -reliability, -proof[mid]["quality"])
    return sorted(qualified, key=rank)


def complete(task: str, messages: list[dict], *, agent: str = "", business: str | None = None,
             max_tokens: int = 1200, json_mode: bool = False, json_schema: dict | None = None,
             tool_schemas: list[dict] | None = None,
             reasoning: str | None = None,
             needs: tuple[str, ...] = (), pin_model: str | None = None, profile: str | None = None,
             validate: Callable[[str], Any] | None = None,
             exclude_models: tuple[str, ...] = (), browser_min_quality: float = 0.,
             before_request: Callable[[], None] | None = None) -> Completion:
    cat = catalog.load()
    ctx = journal.current_run()
    profile_name = _resolve_profile(cat, profile, ctx)
    prof = cat.profile(profile_name)
    task_def = cat.task(task)
    need = set(task_def.get("needs", [])) | set(needs) | ({"json"} if json_mode or json_schema is not None else set())
    if task in {'browser.bench_step', 'browser.react_step'} and json_schema is None:
        need.discard('json')
    business_name = business or (ctx.business if ctx else "")
    pinned = bool(pin_model and prof.get("honor_pins"))
    if pinned:
        candidates = [pin_model]
    else:
        candidates = list(task_def.get("candidates", {}).get(profile_name, [])) or ([pin_model] if pin_model else [])
    if profile_name == "economical" and task != "browser.react_step":
        candidates = _rank_economical(task, candidates, cat)
    prompt_chars, images = _prompt_size(messages)
    if images:
        need.add("vision")
        # Reuse this task's configured free alternatives, never widen its paid policy.
        # Economical text preferences may otherwise hide a compatible local/free route.
        if not pinned:
            free_vision = [m for m in task_def.get("candidates", {}).get("zero_cost", [])
                           if cat.model(m) and "vision" in cat.model(m).get("capabilities", [])
                           and cat.model(m)["cost_class"] != "paid"]
            candidates = list(dict.fromkeys([m for m in candidates if cat.model(m)
                and cat.model(m)["cost_class"] != "paid"] + free_vision +
                [m for m in candidates if not cat.model(m) or cat.model(m)["cost_class"] == "paid"]))
    if task == "browser.react_step":
        # Apply AFTER vision expansion: an unqualified multimodal route is still unqualified.
        candidates = _rank_browser(candidates, cat, exclude_models=exclude_models,
                                   min_quality=browser_min_quality)
        if not candidates:
            raise NoEligibleModel(task, profile_name, [{"model": m, "eligible": False,
                "reason": browser_quality(cat.model(m))["reason"] if cat.model(m) else "absent"}
                for m in task_def.get("candidates", {}).get(profile_name, [])])
    digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    considered: list[dict] = []
    last_error: Exception | None = None
    budget_block: str | None = None
    free_attempts = 0
    free_routes = 0
    json_repair_attempted = False
    free_limit = prof.get("max_free_attempts", float("inf"))
    free_request_limit = free_limit + 1 if profile_name == "economical" else free_limit
    oversized_providers = {
        row["provider"] for row in journal.query(
            "SELECT DISTINCT provider FROM llm_calls WHERE task=? AND business=? AND prompt_sha256=? "
            "AND status='request_too_large' AND ts>=?",
            (task, business_name, digest, time.time() - 86400),
        )
    }

    for candidate_attempt, model_id in enumerate(candidates, start=1):
        model = cat.model(model_id)
        if model is None:
            considered.append({"model": model_id, "eligible": False, "reason": "absent du catalogue"})
            continue
        if profile_name == "economical" and ctx is not None and model["cost_class"] == "paid":
            exhausted = next((f"budget du run #{run_id} atteint : {journal.subtree_cost(run_id):.4f} $ "
                              f"depenses >= {budget:.4f} $"
                              for run_id, budget in ctx.budgets
                              if journal.subtree_cost(run_id) >= budget), None)
            if exhausted:
                journal.record_llm_call({"ts": time.time(), "run_id": ctx.id, "root_run_id": ctx.root_id,
                                         "business": business_name, "agent": agent, "task": task,
                                         "profile": profile_name, "model": model_id,
                                         "provider": model["provider"], "cost_class": model["cost_class"],
                                         "attempt": candidate_attempt, "status": "blocked", "error": exhausted})
                raise BudgetExceeded(exhausted)
        is_free = model["cost_class"] != "paid"
        if is_free and (free_routes if profile_name == "economical" else free_attempts) >= free_limit:
            considered.append({"model": model_id, "eligible": False, "reason": "limite d'essais gratuits atteinte"})
            continue
        if model["provider"] in oversized_providers:
            considered.append({"model": model_id, "eligible": False,
                               "reason": "HTTP 413 déjà constaté pour ce contenu et ce fournisseur"})
            continue
        reason = _ineligibility(cat, profile_name, prof, task, task_def, model_id, model, need)
        if reason:
            considered.append({"model": model_id, "eligible": False, "reason": reason})
            continue
        provider = dict(cat.provider(model["provider"]))
        if profile_name in {"economical", "bench"}:
            provider["max_retries"] = 0
            if profile_name == "economical" and model["cost_class"] == "paid":
                provider["timeout_s"] = min(provider.get("timeout_s", 90), prof["paid_timeout_s"])
        methods = (['text'] if task in {'browser.bench_step', 'browser.react_step'} and json_schema is None
            and 'json' not in model.get('capabilities', []) else
            _structured_methods(model, json_mode, json_schema, tool_schemas))
        if profile_name == "economical":
            methods = methods[:2]

        route_attempted = False
        for method_index, structured_method in enumerate(methods):
            if is_free and free_attempts >= free_request_limit:
                considered.append({"model": model_id, "eligible": False, "reason": "limite d'essais gratuits atteinte"})
                break
            now = time.time()
            peak = pricing.is_peak(now, provider.get("peak_utc_weekdays"))
            base = {
                "ts": now, "run_id": ctx.id if ctx else None, "root_run_id": ctx.root_id if ctx else None,
                "business": business_name, "agent": agent, "task": task, "profile": profile_name,
                "model": model_id, "provider": model["provider"], "cost_class": model["cost_class"],
                "requested_model": model["api_model"],
                "attempt": candidate_attempt, "peak": int(peak), "prompt_sha256": digest, "prompt_chars": prompt_chars,
            }
            if profile_name == "economical" and ctx is not None and method_index and model["cost_class"] == "paid":
                exhausted = next((f"budget du run #{run_id} atteint : {journal.subtree_cost(run_id):.4f} $ "
                                  f"depenses >= {budget:.4f} $"
                                  for run_id, budget in ctx.budgets
                                  if journal.subtree_cost(run_id) >= budget), None)
                if exhausted:
                    journal.record_llm_call({**base, "status": "blocked", "error": exhausted})
                    raise BudgetExceeded(exhausted)
            estimate = pricing.estimate_max_cost(model.get("price"), prompt_chars + images * pricing.IMAGE_CHARS_ESTIMATE,
                                                 max_tokens, peak)
            block = _budget_block(ctx, cat, business_name, estimate)
            if block:
                reason_text = f"{block} [structured_method={structured_method or 'none'}]"
                considered.append({"model": model_id, "eligible": False, "reason": reason_text})
                justification = _justify(profile_name, task, model_id, model, considered, pinned)
                justification["structured_method"] = structured_method
                journal.record_llm_call({**base, "status": "blocked", "error": block, "justification": json.dumps(
                    justification, ensure_ascii=False)})
                budget_block = block
                last_error = BudgetExceeded(block)
                break

            route_reasoning = reasoning
            if route_reasoning is None and task in {"browser.bench_step", "browser.react_step"}:
                efforts = model.get("reasoning", {}).get("supported_efforts", ())
                if efforts is None or "low" in efforts:
                    route_reasoning = "low"
            request = _build_request(
                model, messages, max_tokens, json_mode, route_reasoning, json_schema, tool_schemas,
                structured_method=structured_method,
            )
            started = time.perf_counter()
            if before_request is not None:
                before_request()
            if is_free:
                free_attempts += 1
                if profile_name == "economical" and not route_attempted:
                    free_routes += 1
                    route_attempted = True
            try:
                result = _transport_result(_transport(provider, request), request["model"], model["provider"])
                text, usage = result.text, result.usage
                structured_error = None
            except StructuredResponseError as exc:
                result = exc.result
                text, usage = result.text, result.usage
                structured_error = str(exc)
            except Exception as exc:
                method_name = structured_method or "none"
                # Some providers echo the whole multimodal request in error bodies.
                # Neither journal nor a caller's recovery checkpoint may receive image bytes.
                safe_exc = RuntimeError(type(exc).__name__ + ': image request failed') if images else exc
                failure = f"echec [{method_name}] : {type(exc).__name__}: " + (
                    'image request failed' if images else _safe_error(exc, provider))
                response = getattr(exc, "response", None)
                too_large = (getattr(exc, "status_code", None) == 413
                             or getattr(response, "status_code", None) == 413)
                cooldown_delay = _set_rate_limit_cooldown(model_id, exc)
                provider_rate_limit_delay = None
                if cooldown_delay is not None and model["provider"] == "openrouter":
                    body = getattr(exc, "body", None)
                    source = ((body.get("metadata") or body.get("error", {}).get("metadata") or {}).get("limit_source")
                              if isinstance(body, dict) else None)
                    if source != "upstream_provider_shared_pool":
                        _provider_cooldowns["openrouter"] = (time.monotonic() + cooldown_delay,
                                                             f"quota OpenRouter 429; cooldown {cooldown_delay:g}s")
                        provider_rate_limit_delay = cooldown_delay
                provider_cooldown_delay = _set_provider_cooldown(model["provider"], exc)
                considered.append({"model": model_id, "eligible": True, "reason": failure})
                justification = _justify(profile_name, task, model_id, model, considered, pinned)
                justification["structured_method"] = structured_method
                if request.get("extra_body", {}).get("reasoning"):
                    justification["reasoning"] = request["extra_body"]["reasoning"]
                if cooldown_delay is not None:
                    justification["rate_limit_cooldown_s"] = cooldown_delay
                if provider_rate_limit_delay is not None:
                    justification["provider_rate_limit_cooldown_s"] = provider_rate_limit_delay
                if provider_cooldown_delay is not None:
                    justification["provider_cooldown_s"] = provider_cooldown_delay
                journal.record_llm_call({**base, "status": "request_too_large" if too_large else "error", "error": failure,
                                         "duration_ms": int((time.perf_counter() - started) * 1000),
                                         "justification": json.dumps(justification, ensure_ascii=False)})
                last_error = safe_exc
                if too_large:
                    oversized_providers.add(model["provider"])
                    break
                if (structured_method is not None and method_index + 1 < len(methods)
                        and _structured_method_error(exc)):
                    continue
                if profile_name == "economical" and model["cost_class"] == "paid":
                    raise NoEligibleModel(task, profile_name, considered, safe_exc) from None
                if prof.get("fallback") and candidate_attempt < len(candidates):
                    break
                if images:
                    raise safe_exc from None
                raise

            _rate_limit_cooldowns.pop(model_id, None)
            _provider_cooldowns.pop(model["provider"], None)
            duration_ms = int((time.perf_counter() - started) * 1000)
            observed_cost = result.provider_cost_usd
            if observed_cost is not None and (not math.isfinite(observed_cost) or observed_cost < 0):
                observed_cost = None
            cost = (observed_cost if observed_cost is not None
                    else pricing.call_cost(model.get("price"), usage, peak))
            data, status, error = None, "ok", _zero_cost_violation(profile_name, model, result)
            repaired = repair_attempted_here = False
            json_syntax_error = False
            if error is not None:
                status = "blocked"
            elif structured_error is not None:
                status, error = "invalid", f"ValueError: {structured_error}"[:300]
            elif validate is not None or (profile_name == "economical" and json_mode):
                try:
                    data = (validate or parse_json)(text)
                except Exception as exc:
                    status, error = "invalid", f"{type(exc).__name__}: {exc}"[:300]
                    json_syntax_error = isinstance(exc, json.JSONDecodeError)
                    if profile_name == "economical" and not json_repair_attempted and isinstance(exc, json.JSONDecodeError):
                        fixed = _repair_json_control_chars(text)
                        if fixed is not None:
                            json_repair_attempted = True
                            repair_attempted_here = True
                            try:
                                data = (validate or parse_json)(fixed)
                            except Exception as repair_exc:
                                error = f"{type(repair_exc).__name__}: {repair_exc}"[:300]
                                json_syntax_error = isinstance(repair_exc, json.JSONDecodeError)
                            else:
                                text, status, error, repaired = fixed, "ok", None, True
            if task == "browser.react_step" and status == "ok":
                expected = browser_identity(model)
                actual = browser_identity(model, result.resolved_model) if result.resolved_model else None
                if actual != expected or not browser_quality(model, resolved_model=result.resolved_model)["eligible"]:
                    status, error = "blocked", "browser: modèle résolu non qualifié ou identité différente"
            if images and status == 'invalid':
                error = 'sortie multimodale invalide'  # Validator/provider errors may echo image content.
            justification = _justify(profile_name, task, model_id, model,
                                     considered + [{"model": model_id, "eligible": True, "reason": "choisi"}], pinned)
            justification["structured_method"] = structured_method
            if request.get("extra_body", {}).get("reasoning"):
                justification["reasoning"] = request["extra_body"]["reasoning"]
            if task == "browser.react_step":
                justification["browser_qualification"] = browser_quality(model)
                justification["selection_reason"] = "compétence browser démontrée puis coût minimal ; identité résolue contrôlée"
            if isinstance(data, dict) and data.get('_protocol'):
                justification['protocol_status'] = data['_protocol']
            if repair_attempted_here:
                justification["json_repair_attempted"] = True
            if repaired:
                justification["json_repair"] = "control_chars"
            if structured_error is not None and model["cost_class"] == "paid" and cost == 0:
                cost = estimate
                justification["cost_estimated_due_missing_usage"] = True
            if status == "blocked" and model["provider"] == "openrouter":
                delay = 3600.0
                _provider_cooldowns["openrouter"] = (time.monotonic() + delay,
                                                     f"route gratuite non attestée; cooldown {delay:g}s")
                justification["provider_safety_cooldown_s"] = delay
            from agents import agent_browser
            call_id = journal.record_llm_call({
                **base, "status": status, "error": error, "prompt_tokens": usage.prompt_tokens,
                "cache_hit_tokens": usage.cache_hit_tokens, "cache_miss_tokens": usage.cache_miss_tokens,
                "completion_tokens": usage.completion_tokens, "reasoning_tokens": usage.reasoning_tokens,
                "cost_usd": cost, "duration_ms": duration_ms, "output_preview": (
                    '[multimodal response]' if images else agent_browser.redact(text[:300])),
                "resolved_model": result.resolved_model, "resolved_provider": result.resolved_provider,
                "request_id": result.request_id, "provider_cost_usd": observed_cost,
                "justification": json.dumps(justification, ensure_ascii=False),
            })
            if status == "blocked":
                considered.append({"model": model_id, "eligible": False, "reason": error})
                last_error = GatewayError(error)
                break
            if status == "invalid":
                method_name = structured_method or "none"
                considered.append({"model": model_id, "eligible": True,
                                   "reason": f"sortie invalide [{method_name}] : {error}"})
                last_error = InvalidOutput(f"{model_id} [{method_name}] : {error}")
                if (structured_method is not None and method_index + 1 < len(methods)
                        and (profile_name != "economical" or structured_error is not None or json_syntax_error)):
                    continue
                if prof.get("fallback") and candidate_attempt < len(candidates):
                    break
                raise last_error
            return Completion(text=text, model=model_id, provider=model["provider"], cost_usd=cost, usage=usage,
                              call_id=call_id, requested_model=result.requested_model,
                              resolved_model=result.resolved_model, resolved_provider=result.resolved_provider,
                              request_id=result.request_id, provider_cost_usd=result.provider_cost_usd,
                              data=data, justification=justification)

        if not prof.get("fallback"):
            if last_error is not None:
                raise last_error
            break

    if budget_block:
        detail = "; ".join(f"{c['model']} : {c['reason']}" for c in considered)
        raise BudgetExceeded(f"{budget_block} (candidats : {detail})")
    raise NoEligibleModel(task, profile_name, considered, last_error)

