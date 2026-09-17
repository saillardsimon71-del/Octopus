"""Chaine de fournisseurs de voix : ordre, repli, et absence de quota bloquant."""
from __future__ import annotations

import base64
import json

import pytest

from tools import tts_providers as tts


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("TTS_CHAIN", "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION", "AZURE_TTS_VOICE", "CF_ACCOUNT_ID",
                "CF_API_TOKEN", "CHATTERBOX_URL", "HF_TOKEN", "PIPER_VOICE"):
        monkeypatch.delenv(var, raising=False)


def test_chain_defaults_and_validation(monkeypatch):
    assert tts.chain() == ["azure", "cloudflare", "chatterbox", "piper"]
    monkeypatch.setenv("TTS_CHAIN", "piper, azure")
    assert tts.chain() == ["piper", "azure"]
    monkeypatch.setenv("TTS_CHAIN", "piper,inconnu")
    with pytest.raises(ValueError, match="inconnu"):
        tts.chain()


def test_unconfigured_providers_are_skipped_not_fatal(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "azure,cloudflare,piper")
    monkeypatch.setattr(tts, "piper", lambda text, **kw: b"RIFFfake")
    audio, used, failures = tts.synthesize("bonjour")
    assert audio == b"RIFFfake" and used == "piper"
    assert any("AZURE_SPEECH_KEY" in f for f in failures) and any("CF_ACCOUNT_ID" in f for f in failures)


def test_azure_sends_ssml_with_voice_and_key(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "cle")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "westeurope")
    monkeypatch.setenv("AZURE_TTS_VOICE", "fr-FR-DeniseNeural")
    seen = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"RIFFazure"

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
        seen["body"] = req.data.decode("utf-8")
        return Response()

    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    assert tts.azure("Bonjour & bienvenue") == b"RIFFazure"
    assert seen["url"] == "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["ocp-apim-subscription-key"] == "cle"
    assert "fr-FR-DeniseNeural" in seen["body"] and "&amp;" in seen["body"]
    assert "xml:lang='fr-FR'" in seen["body"]


def test_cloudflare_decodes_base64_audio(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "compte")
    monkeypatch.setenv("CF_API_TOKEN", "jeton")
    payload = json.dumps({"success": True, "result": {"audio": base64.b64encode(b"ID3audio").decode()}}).encode()
    monkeypatch.setattr(tts, "_post_json", lambda url, body, headers, timeout=120: payload)
    assert tts.cloudflare("bonjour") == b"ID3audio"


def test_cloudflare_error_is_skippable(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "compte")
    monkeypatch.setenv("CF_API_TOKEN", "jeton")
    monkeypatch.setattr(tts, "_post_json",
                        lambda *a, **k: json.dumps({"success": False, "errors": [{"message": "quota"}]}).encode())
    with pytest.raises(tts.TTSUnavailable, match="quota"):
        tts.cloudflare("bonjour")


def test_chain_reports_every_failure_when_all_fail(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "azure,piper")
    monkeypatch.setattr(tts, "piper", lambda text, **kw: (_ for _ in ()).throw(tts.TTSUnavailable("piper : absent")))
    with pytest.raises(tts.TTSUnavailable) as exc:
        tts.synthesize("bonjour")
    assert "azure" in str(exc.value) and "piper" in str(exc.value)


def test_chatterbox_hf_space_routes_to_space(monkeypatch):
    monkeypatch.setenv("CHATTERBOX_URL", "hf-space:owner/space")
    monkeypatch.setattr(tts, "_hf_space", lambda space, text, exagg, cfg: b"RIFFspace" if space == "owner/space" else b"")
    assert tts.chatterbox("bonjour") == b"RIFFspace"
