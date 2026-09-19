"""Catalogue des fournisseurs, modeles, profils de cout et taches (config/catalog.json)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import paths

COST_CLASSES = ("local", "free_quota", "paid")
_cache: dict[str, tuple[float, "Catalog"]] = {}


class CatalogError(ValueError):
    pass


@dataclass
class Catalog:
    raw: dict
    path: Path

    @property
    def default_profile(self) -> str:
        selected = self.raw.get("default_profile", "legacy")
        if (selected == "legacy" and _omniroute_enabled()
                and not os.environ.get("OCTOPUS_PROFILE", "").strip()):
            return "zero_cost"
        return selected

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


def _omniroute_enabled() -> bool:
    return os.environ.get("OMNIROUTE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _overlay_omniroute(raw: dict) -> dict:
    """Ajoute un modèle virtuel OmniRoute sans modifier le catalogue Git.

    Le gateway est local au sens du déploiement (localhost), mais il requiert une clé et
    peut transmettre le prompt aux providers qu'il sélectionne. On le traite donc comme
    un provider HTTP authentifié pour la disponibilité du routage ; `doctor` vérifie réellement
    son endpoint `/models` avant un cycle.
    """
    if not _omniroute_enabled():
        return raw
    raw = json.loads(json.dumps(raw))
    provider_id = "omniroute"
    model_id = "omniroute/auto-free"
    model_name = os.environ.get("OMNIROUTE_MODEL", "auto/best-free").strip() or "auto/best-free"
    zero_cost_attestation = os.environ.get("OMNIROUTE_ZERO_COST_ATTESTATION", "").strip().lower()
    raw.setdefault("providers", {})[provider_id] = {
        "kind": "cloud",
        "base_url": os.environ.get("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/"),
        "api_key_env": "OMNIROUTE_API_KEY",
        "timeout_s": float(os.environ.get("OMNIROUTE_TIMEOUT_S", "120")),
        "max_retries": 0,
        "health_path": "/models",
        "health_timeout_s": 2.0,
        "data_policy": "Le gateway est local ; le contenu peut ensuite partir vers les providers connectés à OmniRoute. Respecter leurs conditions et quotas.",
        "sources": ["https://github.com/diegosouzapw/OmniRoute/wiki/Free-Tiers-Guide", "https://github.com/diegosouzapw/OmniRoute/wiki/API-Reference"],
    }
    raw.setdefault("models", {})[model_id] = {
        "provider": provider_id,
        "api_model": model_name,
        "cost_class": "free_quota",
        "capabilities": ["json", "vision", "tools", "reasoning_effort"],
        "zero_cost_attestation": zero_cost_attestation,
        "notes": "Modèle virtuel OmniRoute : auto/best-free. La disponibilité et le provider réel dépendent des connexions OmniRoute.",
    }

    free_defaults = {
        "podalux.select_offer": model_id,
        "podalux.write_job": model_id,
        "podalux.qc_vision": model_id,
        "podalux.arbitrate": model_id,
        "agent.react_step": model_id,
        "agent.plan": model_id,
        "agent.synthesize": model_id,
        "web.summarize": model_id,
        "web.inspect_page": model_id,
        "veille.brief": model_id,
    }
    for task_name, selected_model in free_defaults.items():
        task = raw.setdefault("tasks", {}).setdefault(task_name, {})
        candidates = task.setdefault("candidates", {})
        current = list(candidates.get("zero_cost", []))
        if selected_model not in current:
            candidates["zero_cost"] = [selected_model, *current]
        current_low = list(candidates.get("low_cost", []))
        if selected_model not in current_low:
            candidates["low_cost"] = [selected_model, *current_low]
        if raw.get("profiles", {}).get("zero_cost", {}).get("fallback"):
            task.setdefault("omniroute_bootstrap_baseline", {})["zero_cost"] = selected_model
    return raw


def load(path: Path | None = None) -> Catalog:
    p = Path(path) if path else paths.catalog_path()
    mtime = p.stat().st_mtime
    cache_key = (f"{p}|omni={_omniroute_enabled()}|model={os.environ.get('OMNIROUTE_MODEL','')}|"
                 f"base={os.environ.get('OMNIROUTE_BASE_URL','')}|"
                 f"zero_cost={os.environ.get('OMNIROUTE_ZERO_COST_ATTESTATION','')}")
    cached = _cache.get(cache_key)
    if cached and cached[0] == mtime:
        return cached[1]
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw = _overlay_omniroute(raw)
    validate(raw)
    cat = Catalog(raw=raw, path=p)
    _cache[cache_key] = (mtime, cat)
    return cat


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
