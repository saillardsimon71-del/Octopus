"""Bench 3 voix FR edge-tts sur la même narration : durée + LRA + wav écoutable.

Usage : python tools/bench_voices.py <job.json> <offer_id>
Produit out/<offer_id>/bench/vo_<nom>.wav + bench.json
"""
import asyncio
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
GAP = 0.12
VOICES = [
    ("vivienne", "fr-FR-VivienneNeural"),
    ("henri", "fr-FR-HenriNeural"),
    ("denise", "fr-FR-DeniseNeural"),
]


async def synth(text, rate, voice, out_mp3):
    import edge_tts
    c = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    chunks = []
    async for x in c.stream():
        if x["type"] == "audio":
            chunks.append(x["data"])
    out_mp3.write_bytes(b"".join(chunks))


def mp3_to_wav(mp3, wav):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                    "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(wav)])


def read_mono(path):
    with wave.open(str(path), "rb") as w:
        d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return d.astype(np.float32) / 32768.0


def write_stereo(path, left):
    inter = np.empty((len(left), 2), dtype=np.int16)
    inter[:, 0] = np.clip(left, -1, 1) * 32767
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
    out_dir = Path("out") / offer / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    segments = job["narration"]
    results = []
    for name, vid in VOICES:
        parts = []
        for i, seg in enumerate(segments):
            mp3 = out_dir / f"_{name}_{i}.mp3"
            wav = out_dir / f"_{name}_{i}.wav"
            asyncio.run(synth(seg["texte"], seg["rate"], vid, mp3))
            mp3_to_wav(mp3, wav)
            parts.append(read_mono(wav))
            if i < len(segments) - 1:
                parts.append(np.zeros(int(GAP * SR), dtype=np.float32))
        vo = np.concatenate(parts)
        vo_wav = out_dir / f"vo_{name}.wav"
        write_stereo(vo_wav, vo)
        lra, lufs = measure(vo_wav)
        dur = len(vo) / SR
        results.append({"voice": vid, "short": name, "duration_s": round(dur, 2), "lra": lra, "lufs": lufs})
        print(f"{name:10s} ({vid}): duree={round(dur, 2)}s  LRA={lra}  LUFS={lufs}")
    (out_dir / "bench.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ecrit:", out_dir / "bench.json")


if __name__ == "__main__":
    main()
