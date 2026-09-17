"""Catalogue des fournisseurs, modeles, profils de cout et taches (config/catalog.json)."""
from __future__ import annotations

import json
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
        return self.raw.get("default_profile", "legacy")

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


def load(path: Path | None = None) -> Catalog:
    p = Path(path) if path else paths.catalog_path()
    mtime = p.stat().st_mtime
    cached = _cache.get(str(p))
    if cached and cached[0] == mtime:
        return cached[1]
    raw = json.loads(p.read_text(encoding="utf-8"))
    validate(raw)
    cat = Catalog(raw=raw, path=p)
    _cache[str(p)] = (mtime, cat)
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
