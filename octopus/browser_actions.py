"""Executor navigateur borné pour les formulaires web.

Contrat V1:
- canal kind=browser_form avec capability browser_form_submit;
- HTTPS uniquement;
- cible limitée au même hôte (ou sous-domaine) que channel.locator;
- domaines financiers / sécurité refusés;
- champs texte seulement, sans secrets/OTP/cartes/IBAN;
- preuve explicite requise après soumission (texte observé sur la page).

Le droit d'agir reste accordé par economy.update_channel(..., actor="human", access="act").
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .strategy import StrategyError

CAPABILITY = "browser_form_submit"
MAX_FIELDS = 20
MAX_FIELD_CHARS = 10_000
MAX_TOTAL_CHARS = 25_000

_BLOCKED_HOST_SUFFIXES = (
    "stripe.com",
    "paypal.com",
    "wise.com",
    "revolut.com",
    "adyen.com",
    "checkout.com",
    "accounts.google.com",
    "myaccount.google.com",
    "gumroad.com",
)

_SENSITIVE = re.compile(
    r"(password|passcode|passwd|otp|2fa|two[-_ ]?factor|token|secret|api[-_ ]?key|"
    r"cvv|cvc|card|iban|routing|bank|payout|withdraw|transfer|billing|"
    r"delete|remove[-_ ]?account|close[-_ ]?account|security)",
    re.I,
)


def _host(url: str, field: str) -> str:
    parts = urlsplit(str(url or "").strip())
    if parts.scheme != "https":
        raise StrategyError(f"{field} doit utiliser HTTPS")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise StrategyError(f"{field} sans hôte")
    return host


def _host_is(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _same_site(base: str, target: str) -> bool:
    return target == base or target.endswith("." + base)


def _capabilities(channel: dict) -> set[str]:
    raw = channel.get("capabilities") or "[]"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StrategyError("capabilities du canal illisibles") from exc
    if not isinstance(raw, list):
        raise StrategyError("capabilities du canal invalides")
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def _validate_fields(payload: dict) -> list[tuple[str, str]]:
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields or len(fields) > MAX_FIELDS:
        raise StrategyError(f"fields doit contenir 1 à {MAX_FIELDS} champs")
    out: list[tuple[str, str]] = []
    total = 0
    for index, item in enumerate(fields, start=1):
        if not isinstance(item, dict):
            raise StrategyError(f"field #{index} doit être un objet")
        selector = str(item.get("selector") or "").strip()
        value = item.get("value")
        if not selector or not isinstance(value, str):
            raise StrategyError(f"field #{index}: selector et value texte requis")
        if len(value) > MAX_FIELD_CHARS:
            raise StrategyError(f"field #{index}: texte trop long")
        if _SENSITIVE.search(selector):
            raise StrategyError(f"field #{index}: champ sensible refusé")
        total += len(value)
        out.append((selector, value))
    if total > MAX_TOTAL_CHARS:
        raise StrategyError("volume de texte trop important")
    return out


def submit_form(channel: dict, payload: dict) -> dict:
    """Soumet un formulaire web et ne réussit que si une preuve externe est observée."""
    if CAPABILITY not in _capabilities(channel):
        raise StrategyError(f"capability {CAPABILITY!r} requise")

    locator = str(channel.get("locator") or "").strip()
    target_url = str(payload.get("url") or locator).strip()
    locator_host = _host(locator, "channel.locator")
    target_host = _host(target_url, "payload.url")

    if not _same_site(locator_host, target_host):
        raise StrategyError("payload.url doit rester sur le domaine déclaré du canal")
    if any(_host_is(target_host, suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        raise StrategyError("domaine financier ou de sécurité refusé par l'exécuteur générique")

    fields = _validate_fields(payload)
    submit_selector = str(payload.get("submit_selector") or "").strip()
    success_text = str(payload.get("success_text") or "").strip()
    if not submit_selector:
        raise StrategyError("submit_selector requis")
    if _SENSITIVE.search(submit_selector):
        raise StrategyError("action sensible refusée")
    if not success_text:
        raise StrategyError("success_text requis pour prouver la soumission")
    if len(success_text) > 300:
        raise StrategyError("success_text trop long")

    from agents import browser, web_guard

    with web_guard.session() as state:
        kind = web_guard.classify(target_url)
        account = kind == web_guard.ACCOUNT
        b = browser.new_browser(
            headless=not account,
            account=account,
            guard=lambda url: web_guard.allowed(url, state),
        )
        try:
            b.goto(target_url)
            opened_host = _host(b.url(), "URL ouverte")
            if not _same_site(locator_host, opened_host):
                raise StrategyError("redirection hors du domaine déclaré du canal")
            for selector, value in fields:
                b.type(selector, value)
            b.click(submit_selector)
            b.wait_for(success_text)
            final_url = b.url()
            final_host = _host(final_url, "URL finale")
            if not _same_site(locator_host, final_host):
                raise StrategyError("preuve finale hors du domaine déclaré du canal")
            return {
                "observation": f"Formulaire soumis; confirmation observée: {success_text}",
                "source_ref": final_url,
                "metric": payload.get("metric"),
                "value": payload.get("value"),
                "unit": payload.get("unit"),
            }
        finally:
            b.stop()
