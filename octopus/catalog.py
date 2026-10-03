"""Catalogue des fournisseurs, modeles, profils de cout et taches (config/catalog.json)."""
from __future__ import annotations

import json
import os
import copy
import re
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import paths

COST_CLASSES = ("local", "free_quota", "paid")
STRUCTURED_METHODS = {"json_schema", "json_object", "tool_call", "text"}
_cache = {}
_refresh_failures = {}
OPENROUTER_SOURCE = "https://openrouter.ai/api/v1/models"
CATALOG_TTL_S = 6 * 3600
CATALOG_MAX_AGE_S = 24 * 3600
REFRESH_BACKOFF_S = 300
MAX_METADATA_BYTES = 8 * 1024 * 1024


class CatalogError(ValueError):
    pass


@dataclass
class Catalog:
    raw: dict
    path: Path

    @property
    def default_profile(self) -> str:
        return self.raw.get("default_profile", "zero_cost")

    def provider(self, name: str) -> dict:
        return self.raw["providers"][name]

    def model(self, model_id: str) -> dict | None:
        return self.raw["models"].get(model_id)

    def profile(self, name: str) -> dict:
        try:
            return self.raw["profiles"][name]
        except KeyError:
            raise CatalogError(f"profil inconnu : {name} (disponibles : {', '.join(self.raw['profiles'])})") from None

    def task(self, name: str) -> dict:
        return self.raw.get("tasks", {}).get(name, {})

    def legacy_task(self, agent: str, task: str) -> str:
        mapping = self.raw.get("legacy_tasks", {})
        return mapping.get(f"{agent}/{task}") or mapping.get(f"*/{task}") or f"legacy.{agent.lower()}.{task}"

    def evidence_rules(self) -> dict:
        return {"min_samples": 5, "min_pass_rate": 0.9, "max_age_days": 60, **self.raw.get("evidence", {})}

    def daily_budget_usd(self) -> float | None:
        return self.raw.get("budgets", {}).get("daily_usd")


def _zero_price(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    try:
        number = Decimal(str(value))
        return number.is_finite() and number == 0
    except InvalidOperation:
        return False


def normalize_openrouter(payload, fetched_at):
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or len(entries) > 4096:
        raise CatalogError("OpenRouter: liste de modeles invalide")
    models, duplicates, recognized = {}, set(), 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug, canonical = entry.get("id"), entry.get("canonical_slug")
        if (not isinstance(slug, str) or len(slug) > 256
                or not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+", slug)
                or slug.startswith("openrouter/") or not isinstance(canonical, str)
                or not canonical or len(canonical) > 256):
            continue
        prices = entry.get("pricing")
        if (not isinstance(prices, dict) or not {"prompt", "completion"} <= prices.keys()
                or not all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in prices.values())):
            continue
        try:
            numbers = [Decimal(str(v)) for v in prices.values()]
        except InvalidOperation:
            continue
        if not all(v.is_finite() and v >= 0 for v in numbers):
            continue
        architecture = entry.get("architecture")
        if not isinstance(architecture, dict):
            continue
        inputs, outputs = architecture.get("input_modalities"), architecture.get("output_modalities")
        parameters = entry.get("supported_parameters")
        context = entry.get("context_length")
        if (not isinstance(inputs, list) or "text" not in inputs or not all(isinstance(v, str) for v in inputs)
                or not isinstance(outputs, list) or "text" not in outputs
                or not all(isinstance(v, str) for v in outputs)
                or not isinstance(parameters, list) or not all(isinstance(v, str) for v in parameters)
                or isinstance(context, bool) or not isinstance(context, int) or context <= 0):
            continue
        recognized += 1
        if not all(_zero_price(v) for v in prices.values()):
            continue
        caps, methods = [], []
        if "image" in inputs:
            caps.append("vision")
        if "tools" in parameters:
            caps.append("tools")
            methods.append("tool_call")
        if "structured_outputs" in parameters:
            methods.append("json_schema")
        if "response_format" in parameters:
            methods.append("json_object")
        if methods:
            caps.append("json")
        if "reasoning" in parameters or "include_reasoning" in parameters:
            caps.append("reasoning")
        methods.append("text")
        mid = "openrouter/" + slug
        model = {"provider": "openrouter", "api_model": slug, "canonical_slug": canonical,
            "name": entry.get("name") if isinstance(entry.get("name"), str) else slug,
            "cost_class": "free_quota", "free_verified": True,
            "pricing": {k: str(v) for k, v in prices.items()}, "context_length": context,
            "input_modalities": sorted(set(inputs)), "output_modalities": sorted(set(outputs)),
            "supported_parameters": sorted(set(parameters)), "capabilities": caps,
            "structured_methods": methods, "source": OPENROUTER_SOURCE, "fetched_at": fetched_at,
            "evidence_identity": mid + "@" + canonical}
        if mid in models and model != models[mid]:
            duplicates.add(mid)
        models[mid] = model
    if entries and not recognized:
        raise CatalogError("OpenRouter: aucune tarification exploitable")
    return {mid: models[mid] for mid in sorted(models) if mid not in duplicates}


def _fetch_openrouter(key):
    request = urllib.request.Request(OPENROUTER_SOURCE, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(MAX_METADATA_BYTES + 1)
    if len(body) > MAX_METADATA_BYTES:
        raise CatalogError("OpenRouter: metadonnees trop volumineuses")
    return json.loads(body)


def _read_discovery(path):
    if not path.exists() or path.stat().st_size > MAX_METADATA_BYTES:
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        fetched = saved["fetched_at"]
        if (saved.get("version") != 1 or saved.get("source") != OPENROUTER_SOURCE
                or isinstance(fetched, bool) or not isinstance(fetched, (int, float))
                or not 0 <= time.time() - fetched <= CATALOG_MAX_AGE_S):
            return None
        # Revalidate cached fields; never trust a stored free flag or capability list.
        entries = [{"id": m["api_model"], "canonical_slug": m["canonical_slug"],
            "name": m["name"], "pricing": m["pricing"], "context_length": m["context_length"],
            "architecture": {"input_modalities": m["input_modalities"], "output_modalities": m["output_modalities"]},
            "supported_parameters": m["supported_parameters"]} for m in saved["models"].values()]
        return {"fetched_at": fetched, "models": normalize_openrouter({"data": entries}, fetched)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _write_discovery(path, saved):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(saved, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def discover_openrouter(refresh=False):
    from .llm import secret
    path = paths.data_dir() / "openrouter-models.json"
    saved = _read_discovery(path)
    now = time.time()
    age = now - saved["fetched_at"] if saved else None
    failure = _refresh_failures.get(str(path))
    error = failure[1] if not refresh and failure and now - failure[0] < REFRESH_BACKOFF_S else None
    should_refresh = refresh or not saved or age >= CATALOG_TTL_S
    if should_refresh and not error:
        key = secret("OPENROUTER_API_KEY")
        if not key:
            error = "cle OPENROUTER_API_KEY absente"
        else:
            try:
                fetched = time.time()
                models = normalize_openrouter(_fetch_openrouter(key), fetched)
                candidate = {"version": 1, "source": OPENROUTER_SOURCE, "fetched_at": fetched, "models": models}
                _write_discovery(path, candidate)
                saved, age = candidate, time.time() - fetched
                _refresh_failures.pop(str(path), None)
            except Exception as exc:
                # Exception messages and bodies can echo Authorization. Expose only type/status.
                status = getattr(exc, "code", None)
                error = "OpenRouter refresh: " + type(exc).__name__ + (f" HTTP {status}" if isinstance(status, int) else "")
        if error:
            _refresh_failures[str(path)] = (now, error)
    state = "unavailable" if saved is None else "fresh" if age < CATALOG_TTL_S else "stale"
    return (saved["models"] if saved else {}), {"source": OPENROUTER_SOURCE, "state": state,
        "age_s": round(age) if age is not None else None, "fetched_at": saved["fetched_at"] if saved else None,
        "count": len(saved["models"]) if saved else 0, "refresh_error": error,
        "ttl_s": CATALOG_TTL_S, "max_age_s": CATALOG_MAX_AGE_S}


def load(path: Path | None = None, *, refresh=False) -> Catalog:
    p = Path(path) if path else paths.catalog_path()
    stamp = p.stat().st_mtime_ns
    cached = _cache.get(str(p))
    if cached is None or cached[0] != stamp:
        cached = (stamp, json.loads(p.read_text(encoding="utf-8")))
        _cache[str(p)] = cached
    raw = copy.deepcopy(cached[1])
    raw["providers"] = {k: v for k, v in raw["providers"].items() if k in {"deepseek", "openrouter"}}
    raw["models"] = {k: v for k, v in raw["models"].items() if v.get("provider") == "deepseek"}
    discovered, state = discover_openrouter(refresh)
    raw["models"].update(discovered)
    raw["openrouter_catalog"] = state
    raw.setdefault("tasks", {}).setdefault("browser.react_step", {
        "needs": ["json"], "privacy": "internal", "browser_qualification": "browser-v1",
        "baseline": "deepseek/flash", "candidates": {}})
    for name, task in raw.get("tasks", {}).items():
        if task.get("baseline") not in raw["models"]:
            task.pop("baseline", None)
        needs = set(task.get("needs", []))
        free = [mid for mid, model in discovered.items() if needs <= set(model["capabilities"])]
        for profile, policy in raw["profiles"].items():
            previous = task.setdefault("candidates", {}).get(profile, [])
            paid = [mid for mid in previous if mid in raw["models"] and raw["models"][mid]["provider"] == "deepseek"]
            if profile in {"economical", "flash_fallback"} and task.get("baseline"):
                paid = ["deepseek/flash" if name in {"agent.plan", "agent.synthesize"} else task["baseline"]]
            if name == "browser.react_step":
                paid = [mid for mid, model in raw["models"].items() if model["provider"] == "deepseek" and needs <= set(model["capabilities"])]
            if task.get("privacy") == "sensitive":
                selected = []
            else:
                selected = paid + free if profile == "quality_first" else free + paid
            task["candidates"][profile] = [mid for mid in selected
                if raw["models"][mid]["cost_class"] in policy.get("allowed_cost_classes", COST_CLASSES)]
    validate(raw)
    return Catalog(raw=raw, path=p)


def validate(raw: dict) -> None:
    for key in ("providers", "models", "profiles"):
        if key not in raw:
            raise CatalogError(f"section manquante : {key}")
    for model_id, model in raw["models"].items():
        if model.get("provider") not in raw["providers"]:
            raise CatalogError(f"{model_id} : fournisseur inconnu {model.get('provider')!r}")
        if model.get("cost_class") not in COST_CLASSES:
            raise CatalogError(f"{model_id} : classe de cout invalide {model.get('cost_class')!r}")
        if model["cost_class"] == "paid" and not model.get("price"):
            raise CatalogError(f"{model_id} : modele payant sans prix")
        methods = model.get("structured_methods")
        if methods is not None:
            if not isinstance(methods, list) or not methods:
                raise CatalogError(f"{model_id} : structured_methods invalide")
            unknown = [method for method in methods if method not in STRUCTURED_METHODS]
            if unknown:
                raise CatalogError(f"{model_id} : methode structuree inconnue {unknown[0]!r}")
    for name, task in raw.get("tasks", {}).items():
        baseline = task.get("baseline")
        if baseline and baseline not in raw["models"]:
            raise CatalogError(f"tache {name} : modele de reference inconnu {baseline!r}")
        for profile, candidates in task.get("candidates", {}).items():
            if profile not in raw["profiles"]:
                raise CatalogError(f"tache {name} : profil inconnu {profile!r}")
            for model_id in candidates:
                if model_id not in raw["models"]:
                    raise CatalogError(f"tache {name} : modele inconnu {model_id!r}")
    if raw.get("default_profile", "legacy") not in raw["profiles"]:
        raise CatalogError("profil par defaut inconnu")
