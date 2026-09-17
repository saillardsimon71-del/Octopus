"""Fournisseurs de voix, essayes dans l'ordre : le premier qui repond sert le segment.

Chaine par defaut (TTS_CHAIN) : azure,cloudflare,chatterbox,piper
- azure      : Azure Speech, palier gratuit F0 (500 000 caracteres/mois, voix neuronales).
               AZURE_SPEECH_KEY + AZURE_SPEECH_REGION, voix AZURE_TTS_VOICE.
- cloudflare : Workers AI MeloTTS (10 000 neurones/jour). CF_ACCOUNT_ID + CF_API_TOKEN.
- chatterbox : serveur local OpenAI-compatible, ou Space Hugging Face (CHATTERBOX_URL=hf-space:...).
- piper      : synthese locale CPU (MIT), sans compte ni quota : c'est le plancher qui ne tombe jamais.

Chaque fournisseur leve TTSUnavailable s'il n'est pas configure ou s'il echoue ; la chaine passe
au suivant. Si tous echouent, TTSUnavailable finale avec le detail de chaque tentative.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CHAIN = "azure,cloudflare,chatterbox,piper"
HF_PREFIX = "hf-space:"
PIPER_DEFAULT_VOICE = "fr_FR-siwis-medium"


class TTSUnavailable(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def azure(text: str, **_) -> bytes:
    key, region = _env("AZURE_SPEECH_KEY"), _env("AZURE_SPEECH_REGION")
    if not key or not region:
        raise TTSUnavailable("azure : AZURE_SPEECH_KEY/AZURE_SPEECH_REGION absents")
    voice = _env("AZURE_TTS_VOICE") or "fr-FR-VivienneMultilingualNeural"
    lang = voice.rsplit("-", 1)[0] if voice.count("-") >= 2 else "fr-FR"
    ssml = (f"<speak version='1.0' xml:lang='{lang}'><voice name='{voice}'>"
            f"<prosody rate='{_env('AZURE_TTS_RATE') or '0%'}'>{_escape(text)}</prosody></voice></speak>")
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm", "User-Agent": "octopus"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except (urllib.error.URLError, OSError) as exc:
        raise TTSUnavailable(f"azure : {type(exc).__name__}: {str(exc)[:160]}") from exc


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cloudflare(text: str, **_) -> bytes:
    account, token = _env("CF_ACCOUNT_ID"), _env("CF_API_TOKEN")
    if not account or not token:
        raise TTSUnavailable("cloudflare : CF_ACCOUNT_ID/CF_API_TOKEN absents")
    model = _env("CF_TTS_MODEL") or "@cf/myshell-ai/melotts"
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    payload = {"prompt": text, "lang": _env("CF_TTS_LANG") or "fr"}
    try:
        raw = _post_json(url, payload, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    except (urllib.error.URLError, OSError) as exc:
        raise TTSUnavailable(f"cloudflare : {type(exc).__name__}: {str(exc)[:160]}") from exc
    if raw[:4] in (b"RIFF", b"ID3\x03", b"\xff\xfb\x00\x00"):  # audio brut
        return raw
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise TTSUnavailable(f"cloudflare : reponse illisible ({raw[:80]!r})") from exc
    audio = (data.get("result") or {}).get("audio")
    if not data.get("success") or not audio:
        raise TTSUnavailable(f"cloudflare : {str(data.get('errors') or data)[:160]}")
    return base64.b64decode(audio)


def chatterbox(text: str, *, voice: str = "vivienne-fr", exaggeration: float = 0.5,
               cfg_weight: float = 0.5, **_) -> bytes:
    base = _env("CHATTERBOX_URL") or "http://127.0.0.1:4123/v1/audio/speech"
    if base.startswith(HF_PREFIX):
        return _hf_space(base[len(HF_PREFIX):], text, exaggeration, cfg_weight)
    payload = {"model": "chatterbox", "input": text, "voice": voice, "response_format": "wav",
               "exaggeration": exaggeration, "cfg_weight": cfg_weight, "temperature": 0.7}
    try:
        return _post_json(base, payload, {"Content-Type": "application/json"}, timeout=600)
    except (urllib.error.URLError, OSError) as exc:
        raise TTSUnavailable(f"chatterbox : {type(exc).__name__}: {str(exc)[:160]}") from exc


def _hf_space(space: str, text: str, exaggeration: float, cfg_weight: float) -> bytes:
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise TTSUnavailable("hf-space : gradio_client absent (pip install gradio_client)") from exc
    ref = _env("CHATTERBOX_REF_AUDIO") or (
        "https://github.com/gradio-app/gradio/raw/main/test/test_files/audio_sample.wav")
    try:
        out = Client(space, verbose=False, hf_token=_env("HF_TOKEN") or None).predict(
            text[:300], _env("CHATTERBOX_LANG") or "fr", handle_file(ref), exaggeration, 0.8, 0,
            cfg_weight, api_name="/generate_tts_audio")
        return Path(out).read_bytes()
    except Exception as exc:  # quota ZeroGPU, Space endormi, API changee
        raise TTSUnavailable(f"hf-space : {type(exc).__name__}: {str(exc)[:160]}") from exc


def piper(text: str, **_) -> bytes:
    voice = _env("PIPER_VOICE") or PIPER_DEFAULT_VOICE
    data_dir = Path(_env("PIPER_DATA_DIR") or (Path.home() / ".cache" / "piper"))
    model = data_dir / f"{voice}.onnx"
    if not model.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        dl = subprocess.run([sys.executable, "-m", "piper.download_voices", voice, "--download-dir", str(data_dir)],
                            capture_output=True, text=True)
        if dl.returncode != 0 or not model.exists():
            raise TTSUnavailable(f"piper : telechargement de {voice} impossible ({dl.stderr[-160:].strip()})")
    out = data_dir / "_octopus_seg.wav"
    proc = subprocess.run([sys.executable, "-m", "piper", "-m", str(model), "-f", str(out),
                           "--length-scale", _env("PIPER_LENGTH_SCALE") or "1.0", "--", text],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise TTSUnavailable(f"piper : synthese echouee ({proc.stderr[-160:].strip()})")
    return out.read_bytes()


PROVIDERS = ("azure", "cloudflare", "chatterbox", "piper")


def chain() -> list[str]:
    names = [n.strip() for n in (_env("TTS_CHAIN") or DEFAULT_CHAIN).split(",") if n.strip()]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise ValueError(f"TTS_CHAIN : fournisseur inconnu {unknown} (connus : {', '.join(PROVIDERS)})")
    return names


def synthesize(text: str, **kwargs) -> tuple[bytes, str, list[str]]:
    """(audio, fournisseur_utilise, echecs). Leve TTSUnavailable si toute la chaine echoue."""
    failures = []
    for name in chain():
        try:
            audio = globals()[name](text, **kwargs)  # resolution tardive : testable par monkeypatch
        except TTSUnavailable as exc:
            failures.append(str(exc))
            continue
        if audio:
            return audio, name, failures
        failures.append(f"{name} : reponse vide")
    raise TTSUnavailable("aucun fournisseur de voix disponible : " + " | ".join(failures))
