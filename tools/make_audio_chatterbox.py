"""Génère la narration complète via Chatterbox (serveur local 4123) et mesure LRA + durée.

Usage : python tools/make_audio_chatterbox.py <job.json> <offer_id> [voice] [exaggeration] [cfg_weight]
"""
import json
import re
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path

import numpy as np

SR = 44100
GAP = 0.12
BASE = "http://127.0.0.1:4123/v1/audio/speech"


def tts(text, voice, exaggeration, cfg_weight):
    payload = json.dumps({
        "model": "chatterbox",
        "input": text,
        "voice": voice,
        "response_format": "wav",
        "exaggeration": exaggeration,
        "cfg_weight": cfg_weight,
        "temperature": 0.7,
    }).encode("utf-8")
    req = urllib.request.Request(BASE, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return d.astype(np.float32) / 32768.0


def write_wav(path, x):
    inter = np.empty((len(x), 2), dtype=np.int16)
    inter[:, 0] = np.clip(x, -1, 1) * 32767
    inter[:, 1] = inter[:, 0]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(inter.tobytes())


def measure(wav):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    lra = re.findall(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", out)
    lufs = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", out)
    return (float(lra[-1]) if lra else None, float(lufs[-1]) if lufs else None)


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    offer = sys.argv[2]
    voice = sys.argv[3] if len(sys.argv) > 3 else "vivienne-fr"
    exagg = float(sys.argv[4]) if len(sys.argv) > 4 else 0.6
    cfg = float(sys.argv[5]) if len(sys.argv) > 5 else 0.4
    out_dir = Path("out") / offer / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    for i, seg in enumerate(job["narration"]):
        raw = tts(seg["texte"], voice, exagg, cfg)
        raw_wav = out_dir / f"cb_seg_raw_{i}.wav"
        raw_wav.write_bytes(raw)
        seg_wav = out_dir / f"cb_seg_{i}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_wav),
                        "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(seg_wav)])
        parts.append(read_wav(seg_wav))
        if i < len(job["narration"]) - 1:
            parts.append(np.zeros(int(GAP * SR), dtype=np.float32))
        print(f"seg {i} ok ({len(raw)//1024} Ko)")
    vo = np.concatenate(parts)
    vo_wav = out_dir / "vo_chatterbox.wav"
    write_wav(vo_wav, vo)
    lra, lufs = measure(vo_wav)
    print(f"DURATION_S={round(len(vo)/SR, 2)}  LRA={lra}  LUFS={lufs}")
    print(f"WAV={vo_wav}")


if __name__ == "__main__":
    main()
