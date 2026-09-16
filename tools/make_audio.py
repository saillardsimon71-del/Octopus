"""Génère la VO (edge-tts multi-segment), les captions mot à mot, le bed + SFX, et le mix.

Usage :
    python tools/make_audio.py <job.json> <offer_id>

Produit (dans out/<offer_id>/audio/) : vo.wav, mix.wav, captions.json
Et (pour Remotion) : remotion/src/data/captions.ts, remotion/src/data/job.ts
Affiche DURATION_S=<x> en dernière ligne.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
GAP = 0.12  # silence de respiration entre segments (resserré pour le rythme)
TAIL = 1.0  # queue musicale après le dernier mot (le CTA reste à l'écran)
BED_LEVEL = 0.28
SFX_LEVEL = 0.45


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def wav_duration_s(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def mp3_to_wav(mp3, wav):
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
        "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(wav)])


def read_wav_mono(path):
    with wave.open(str(path), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def write_wav_stereo(path, left, right):
    inter = np.empty((len(left), 2), dtype=np.int16)
    inter[:, 0] = np.clip(left, -1, 1) * 32767
    inter[:, 1] = np.clip(right, -1, 1) * 32767
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(inter.tobytes())


async def synth(text, rate, voice, out_mp3):
    """Synthétise un segment et renvoie les words [{text,start,end}] en secondes."""
    import edge_tts
    c = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words, chunks = [], []
    async for x in c.stream():
        if x["type"] == "audio":
            chunks.append(x["data"])
        elif x["type"] == "WordBoundary":
            words.append({
                "text": x["text"],
                "start": x["offset"] / 1e7,
                "end": (x["offset"] + x["duration"]) / 1e7,
            })
    out_mp3.write_bytes(b"".join(chunks))
    return words


# --- synthèse numpy : bed + SFX ---
def _noise_swell(dur, sr):
    n = int(dur * sr)
    x = np.random.default_rng(7).standard_normal(n)
    k = max(1, int(sr * 0.004))
    lp = np.convolve(x, np.ones(k) / k, mode="same")
    k2 = max(1, int(sr * 0.02))
    hp = lp - np.convolve(lp, np.ones(k2) / k2, mode="same")
    t = np.linspace(0, 1, n)
    env = np.clip(np.minimum(t / 0.25, (1 - t) / 0.25), 0, 1) ** 1.5
    return hp * env * 0.6


def _pop(sr):
    n = int(0.07 * sr)
    t = np.linspace(0, 0.07, n, endpoint=False)
    return np.sin(2 * np.pi * 620 * t) * np.exp(-t * 55) * 0.7


def _chime(sr):
    n = int(0.9 * sr)
    t = np.linspace(0, 0.9, n, endpoint=False)
    env = np.exp(-t * 5)
    return (np.sin(2 * np.pi * 659.25 * t) * 0.5
            + np.sin(2 * np.pi * 987.77 * t) * 0.3) * env * 0.5


def _bed(dur, sr):
    n = int(dur * sr)
    t = np.linspace(0, dur, n, endpoint=False)
    freqs = [130.81, 196.00, 261.63, 329.63]  # C3 G3 C4 E4 (pad chaud)
    sig = np.zeros(n, dtype=np.float32)
    for i, f in enumerate(freqs):
        det = 1.0 + 0.0006 * (i - 1.5)
        sig += np.sin(2 * np.pi * f * det * t) / (i + 1.5)
        sig += np.sin(2 * np.pi * f * det * 0.5 * t) * 0.25 / (i + 1.5)
    sig /= sig.max() + 1e-9
    lfo = 0.1 + 0.9 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.08 * t))  # pulsation profonde -> LRA
    fade = np.clip(np.minimum(1, np.minimum(t / 1.0, (dur - t) / 1.5)), 0, 1)
    return (sig * lfo * fade * 0.9).astype(np.float32)


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: python tools/make_audio.py <job.json> <offer_id>")
    job_path = Path(sys.argv[1])
    offer = sys.argv[2]
    job = json.loads(job_path.read_text(encoding="utf-8"))
    voice = job["voix"]["nom"]

    out_dir = Path("out") / offer / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    segments = job["narration"]
    seg_wavs = []
    all_words = []
    offset = 0.0
    for i, seg in enumerate(segments):
        mp3 = out_dir / f"seg_{i}.mp3"
        wav = out_dir / f"seg_{i}.wav"
        words = asyncio.run(synth(seg["texte"], seg["rate"], voice, mp3))
        mp3_to_wav(mp3, wav)
        seg_wavs.append(wav)
        for w in words:
            all_words.append({
                "text": w["text"],
                "start": round(offset + w["start"], 3),
                "end": round(offset + w["end"], 3),
                "seg": i,
                "role": seg["role"],
            })
        offset += wav_duration_s(wav) + GAP

    # concat VO avec silences
    vo_parts = []
    for i, wav in enumerate(seg_wavs):
        vo_parts.append(read_wav_mono(wav))
        if i < len(seg_wavs) - 1:
            vo_parts.append(np.zeros(int(GAP * SR), dtype=np.float32))
    vo = np.concatenate(vo_parts)
    total = len(vo) / SR
    full = total + TAIL
    n = int(full * SR)
    if n > len(vo):
        vo = np.concatenate([vo, np.zeros(n - len(vo), dtype=np.float32)])
    else:
        vo = vo[:n]

    bed = _bed(full, SR) * BED_LEVEL

    # ducking : le bed s'efface sous la VO et gonfle dans les silences (monte le LRA)
    env = np.abs(vo)
    k = int(0.2 * SR)
    env = np.convolve(env, np.ones(k) / k, mode='same')
    env = np.clip(env / (np.percentile(env, 95) + 1e-9), 0, 1)
    duck = 1.0 - env
    bed = bed * duck

    sfx = np.zeros(len(vo), dtype=np.float32)
    for i, seg in enumerate(segments):
        if i == 0:
            continue
        seg_words = [w["start"] for w in all_words if w["seg"] == i]
        seg_start = min(seg_words) if seg_words else 0.0
        idx = int(seg_start * SR)
        sw = _noise_swell(0.45, SR)
        if idx + len(sw) <= len(sfx):
            sfx[idx:idx + len(sw)] += sw
        if seg["role"] == "soulagement":
            ch = _chime(SR)
            if idx + len(ch) <= len(sfx):
                sfx[idx:idx + len(ch)] += ch
        if seg["role"] == "cta":
            p = _pop(SR)
            if idx + len(p) <= len(sfx):
                sfx[idx:idx + len(p)] += p
    sfx *= SFX_LEVEL

    if n > len(sfx):
        sfx = np.concatenate([sfx, np.zeros(n - len(sfx), dtype=np.float32)])
    else:
        sfx = sfx[:n]
    bed = bed[:n]
    left = vo + bed + sfx
    right = vo + bed + sfx
    write_wav_stereo(out_dir / "mix.wav", left, right)
    write_wav_stereo(out_dir / "vo.wav", vo, vo)

    (out_dir / "captions.json").write_text(
        json.dumps({"duration_s": round(full, 3), "vo_end_s": round(total, 3), "words": all_words},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    rem_data = Path("remotion/src/data")
    rem_data.mkdir(parents=True, exist_ok=True)
    captions_ts = (
        "export type Caption = { text: string; start: number; end: number; seg: number; role: string };\n"
        f"export const DURATION_S = {round(full, 3)};\n"
        "export const CAPTIONS: Caption[] = " + json.dumps(all_words, ensure_ascii=False) + ";\n"
    )
    (rem_data / "captions.ts").write_text(captions_ts, encoding="utf-8")

    job_ts = (
        "export const JOB = " + json.dumps({
            "titre": job["titre"], "prix": job["prix"], "cta": job["cta"],
            "hook": job["hook"], "palette": job["palette"], "keywords": job["keywords"],
        }, ensure_ascii=False, indent=2) + " as const;\n"
    )
    (rem_data / "job.ts").write_text(job_ts, encoding="utf-8")

    print(f"OFFRE={offer}")
    print(f"DURATION_S={round(full, 3)}")
    print(f"WORDS={len(all_words)}")
    print(f"MIX={out_dir / 'mix.wav'}")


if __name__ == "__main__":
    main()

