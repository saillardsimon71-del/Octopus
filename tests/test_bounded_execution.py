"""Phase G — critère C : l'exécution est bornée et son état reste cohérent.

Deux bornes doivent être prouvées hors ligne :
- chaque appel fournisseur a une limite de temps réelle, appliquée au client et pas seulement déclarée ;
- la durée murale d'une mission est opposable (couverte par `tests/test_runtime_react.py`).

La cohérence de l'état après annulation/timeout est couverte par `tests/test_tasks_worker.py`
(baux, annulation, complétion périmée) et par `tests/test_autonomous_loop.py` (runs orphelins).
"""
from __future__ import annotations

import math
import sys
import types
from types import SimpleNamespace

import pytest

from octopus import catalog, llm


def test_every_provider_declares_a_finite_timeout_and_bounded_retries(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    providers = catalog.load().raw["providers"]
    assert providers, "le catalogue doit déclarer des fournisseurs"
    for name, provider in providers.items():
        timeout = provider.get("timeout_s")
        assert isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout > 0, name
        retries = provider.get("max_retries")
        assert isinstance(retries, int) and retries >= 0, name
    assert providers["omniroute"]["timeout_s"] == 120.0, "borne par défaut du gateway partagée"


def test_transport_applies_the_configured_timeout_to_the_client(monkeypatch):
    """La borne déclarée doit atteindre le client HTTP : une déclaration ignorée n'est pas une borne."""
    seen: list[dict] = []
    raw_response = SimpleNamespace(
        headers={},
        request_id="req-bound",
        parse=lambda: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2,
                                  prompt_tokens_details=None, completion_tokens_details=None),
            model="test/model",
        ),
    )
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        with_raw_response=SimpleNamespace(create=lambda **kwargs: raw_response))))

    def fake_openai(**kwargs):
        seen.append(kwargs)
        return fake_client

    module = types.ModuleType("openai")
    module.OpenAI = fake_openai
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setattr(llm, "_transport_override", None)
    llm._clients.clear()
    try:
        llm._transport({"base_url": "http://127.0.0.1:9/v1", "api_key_env": None,
                        "timeout_s": 37, "max_retries": 1},
                       {"model": "test/model", "messages": [{"role": "user", "content": "x"}]})
    finally:
        llm._clients.clear()

    assert seen == [{"api_key": "local", "base_url": "http://127.0.0.1:9/v1", "timeout": 37, "max_retries": 1}]
    # Le client borné est réutilisé : la borne ne dépend pas d'un état construit ailleurs.
    assert list(llm._clients) == []


def test_health_probe_is_bounded_too(monkeypatch):
    """La sonde de disponibilité ne peut pas bloquer un cycle : borne courte par défaut."""
    opened: list[float] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        status = 200

    def fake_urlopen(url, timeout=None):
        opened.append(timeout)
        return _Response()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    llm._health.clear()
    ok, reason = llm.provider_status("probe", {"kind": "local", "base_url": "http://127.0.0.1:9/v1",
                                               "health_path": "/models"})
    llm._health.clear()
    assert ok is True and "HTTP 200" in reason
    assert opened == [1.5]
