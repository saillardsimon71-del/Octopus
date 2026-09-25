"""Tests hors-ligne : aucun appel reseau, aucun fichier du projet reel touche.

- PODALUX_ROOT pointe vers un dossier temporaire AVANT l'import de `agents` ;
- chaque test a son journal OCTOPUS, sa base Podalux et son dossier jobs/ ;
- le transport LLM est remplace par `FakeTransport` : un appel non prevu echoue.
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="podalux-tests-"))
os.environ["PODALUX_ROOT"] = str(_SESSION_ROOT)
os.environ["OCTOPUS_HOME"] = str(_SESSION_ROOT)
os.environ["OCTOPUS_DB"] = str(_SESSION_ROOT / "octopus-import.db")
for var in ("OCTOPUS", "OCTOPUS_ALLOW_LEGACY_DIRECT", "OCTOPUS_ALLOW_LEGACY_RUNPOD", "OCTOPUS_PROFILE",
            "OCTOPUS_CATALOG", "OMNIROUTE_ENABLED", "OMNIROUTE_MODEL", "OMNIROUTE_BASE_URL",
            "OMNIROUTE_API_KEY", "OMNIROUTE_TIMEOUT_S", "OMNIROUTE_ZERO_COST_ATTESTATION"):
    os.environ.pop(var, None)
# Les tests historiques ciblent le catalogue existant et n'impliquent aucun gateway réseau.
os.environ["OCTOPUS_PROFILE"] = "legacy"
os.environ["OMNIROUTE_ENABLED"] = "0"
sys.path.insert(0, str(PROJECT))

from agents import config, db  # noqa: E402
from octopus import llm, pricing  # noqa: E402
from octopus.pricing import Usage  # noqa: E402

REAL_IS_PEAK = pricing.is_peak  # les tests de la passerelle figent les heures creuses

assert config.PROJECT_ROOT == _SESSION_ROOT, "les tests ne doivent jamais viser le projet reel"


class _NoRegistry:
    """Remplace `winreg` : aucune variable utilisateur Windows n'est lisible pendant les tests."""

    HKEY_CURRENT_USER = None

    @staticmethod
    def OpenKey(*_args, **_kwargs):
        raise OSError("registre Windows neutralise pendant les tests")


class FakeTransport:
    """Remplace l'appel HTTP : enregistre les requetes, repond via `handler`."""

    def __init__(self):
        self.calls: list[tuple[dict, dict]] = []
        self.handler = None

    def __call__(self, provider: dict, request: dict):
        self.calls.append((provider, copy.deepcopy(request)))
        if self.handler is None:
            raise AssertionError(f"appel LLM non prevu : {request.get('model')}")
        out = self.handler(provider, request)
        if isinstance(out, BaseException):
            raise out
        return out

    @property
    def models(self) -> list[str]:
        return [request["model"] for _, request in self.calls]

    def reply(self, text: str = '{"ok": true}', **usage):
        usage = {"prompt_tokens": 100, "completion_tokens": 20, "cache_hit_tokens": 0, **usage}
        self.handler = lambda provider, request: (text, Usage(**usage))
        return self


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "agents" / "data").mkdir(parents=True)
    monkeypatch.setenv("OCTOPUS_HOME", str(root))
    monkeypatch.setenv("OCTOPUS_DB", str(tmp_path / "octopus.db"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    for var in ("OCTOPUS", "OCTOPUS_ALLOW_LEGACY_DIRECT", "OCTOPUS_ALLOW_LEGACY_RUNPOD", "OCTOPUS_PROFILE",
                "OCTOPUS_CATALOG", "OMNIROUTE_ENABLED", "OMNIROUTE_MODEL", "OMNIROUTE_BASE_URL",
                "OMNIROUTE_API_KEY", "OMNIROUTE_TIMEOUT_S", "OMNIROUTE_ZERO_COST_ATTESTATION",
                "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OCTOPUS_PROFILE", "legacy")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    # Rendu local simule par defaut : un test ne doit jamais viser RunPod sans le demander.
    monkeypatch.setenv("PODALUX_VIDEO_RENDERER", "local")
    # Les secrets de l'utilisateur (registre Windows) ne doivent jamais fuir dans les tests.
    if sys.platform == "win32":
        monkeypatch.setitem(sys.modules, "winreg", _NoRegistry())
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "DATA_DIR", root / "agents" / "data")
    monkeypatch.setattr(config, "DB_PATH", root / "agents" / "data" / "podalux.db")
    monkeypatch.setattr(config, "JOBS_DIR", root / "jobs")
    monkeypatch.setattr(pricing, "is_peak", lambda ts, windows: False)
    llm._health.clear()
    llm._rate_limit_cooldowns.clear()
    llm._provider_cooldowns.clear()
    llm._clients.clear()
    fake = FakeTransport()
    monkeypatch.setattr(llm, "_transport_override", fake)
    db.init_db()
    yield root


@pytest.fixture
def transport(monkeypatch) -> FakeTransport:
    return llm._transport_override


@pytest.fixture
def providers_up(monkeypatch):
    """Tous les fournisseurs disponibles (serveurs locaux et cles simulees)."""
    down: set[str] = set()
    monkeypatch.setattr(llm, "provider_status",
                        lambda name, provider: (False, "coupe par le test") if name in down else (True, "disponible"))
    return down
