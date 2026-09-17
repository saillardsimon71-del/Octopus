"""Registre léger des business/workspaces du cockpit.

La source de vérité métier reste les fichiers jobs/ et la base Octopus. Ce registre ne stocke
que de la métadonnée d'interface : nom, description et regroupement d'offres.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .. import config, db

STATE_KEY = "active_business"
STORE_PATH = config.DATA_DIR / "workspaces.json"
DEFAULT_BUSINESS_ID = "all"


@dataclass(slots=True)
class Business:
    id: str
    name: str
    description: str = ""
    offers: list[str] = field(default_factory=list)

    def label(self) -> str:
        return self.name or self.id.replace("_", " ").title()


class WorkspaceRegistry:
    """Charge et persiste les contextes business sans dépendre d'un nouveau service."""

    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = Path(path)
        self._businesses: dict[str, Business] = {}
        self.reload()

    def reload(self) -> None:
        self._businesses = self._load_file()
        if not self._businesses:
            self._businesses = self._derive_from_jobs()
            if self._businesses:
                self._save_file()

    def all(self) -> list[Business]:
        return sorted(self._businesses.values(), key=lambda b: (b.id == DEFAULT_BUSINESS_ID, b.label().lower()))

    def get(self, business_id: str | None) -> Business | None:
        if not business_id:
            return None
        return self._businesses.get(str(business_id))

    def current(self) -> Business | None:
        selected = db.get_state(STATE_KEY)
        if selected and selected != DEFAULT_BUSINESS_ID:
            found = self.get(selected)
            if found:
                return found
        return None

    def set_current(self, business_id: str) -> None:
        if business_id != DEFAULT_BUSINESS_ID and business_id not in self._businesses:
            raise KeyError(business_id)
        db.set_state(STATE_KEY, business_id)

    def offers_for(self, business_id: str | None) -> list[str]:
        if not business_id or business_id == DEFAULT_BUSINESS_ID:
            return self._all_offers()
        business = self.get(business_id)
        return list(business.offers) if business else []

    def upsert(self, business_id: str, name: str, description: str = "", offers: Iterable[str] = ()) -> Business:
        business_id = _normalize_id(business_id)
        if business_id == DEFAULT_BUSINESS_ID:
            raise ValueError("Le business 'all' est réservé au sélecteur global")
        offer_list = sorted({str(o).strip() for o in offers if str(o).strip()})
        business = Business(business_id, name.strip() or business_id.replace("_", " ").title(), description.strip(), offer_list)
        self._businesses[business.id] = business
        self._save_file()
        return business

    def ensure_offer(self, business_id: str, offer_id: str) -> None:
        business = self.get(business_id)
        if not business:
            business = self.upsert(business_id, business_id.replace("_", " ").title(), offers=[offer_id])
            return
        if offer_id not in business.offers:
            business.offers.append(offer_id)
            business.offers.sort()
            self._save_file()

    def _all_offers(self) -> list[str]:
        if not config.JOBS_DIR.exists():
            return list(config.CATALOG_OFFERS)
        found = {p.stem for p in config.JOBS_DIR.glob("*.json")}
        found.update(config.CATALOG_OFFERS)
        return sorted(found)

    def _derive_from_jobs(self) -> dict[str, Business]:
        grouped: dict[str, list[str]] = {}
        for offer_id in self._all_offers():
            business_id = offer_id.split("_", 1)[0].strip().lower() or "general"
            grouped.setdefault(business_id, []).append(offer_id)
        return {
            key: Business(key, key.replace("_", " ").title(), "Contexte dérivé automatiquement des offres.", sorted(values))
            for key, values in grouped.items()
        }

    def _load_file(self) -> dict[str, Business]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        items = payload.get("businesses", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return {}
        result: dict[str, Business] = {}
        for raw in items:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            try:
                business = Business(
                    _normalize_id(str(raw["id"])),
                    str(raw.get("name") or raw["id"]).strip(),
                    str(raw.get("description") or "").strip(),
                    sorted({str(o).strip() for o in raw.get("offers", []) if str(o).strip()}),
                )
            except (TypeError, ValueError):
                continue
            if business.id != DEFAULT_BUSINESS_ID:
                result[business.id] = business
        return result

    def _save_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "businesses": [asdict(b) for b in self.all()]}
        fd, tmp_name = tempfile.mkstemp(prefix="workspaces-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            Path(tmp_name).replace(self.path)
        finally:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _normalize_id(value: str) -> str:
    value = value.strip().lower().replace(" ", "_")
    value = re.sub(r"[^a-z0-9_-]+", "", value)
    value = re.sub(r"_+", "_", value).strip("_-")
    if not value:
        raise ValueError("identifiant business vide")
    return value[:64]
