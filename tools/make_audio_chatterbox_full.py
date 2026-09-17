"""Pipeline audio complet : VO (chaine de fournisseurs) + word-timestamps + bed/SFX/ducking.

Usage : python tools/make_audio_chatterbox_full.py <job.json> <offer_id> [voice] [exaggeration] [cfg_weight]

La voix vient de `tools/tts_providers.py` : `TTS_CHAIN` (defaut azure,cloudflare,chatterbox,piper)
essaie les fournisseurs dans l'ordre et retient le premier qui repond. Aucun quota ne peut donc
arreter le pipeline : `piper` synthetise en local sur CPU, sans compte. Le fournisseur reellement
utilise par segment est ecrit dans out/<offer>/audio/tts_report.json.
"""
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import tts_providers  # noqa: E402

SR = 44100
GAP = 0.12
TAIL = 1.5
BED_LEVEL = 0.28
SFX_LEVEL = 0.45


def tts(text, voice, exaggeration, cfg_weight):
    audio, used, failures = tts_providers.synthesize(
        text, voice=voice, exaggeration=exaggeration, cfg_weight=cfg_weight)
    for failure in failures:
        print(f"tts: {failure}")
    return audio, used


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return d.astype(np.float32) / 32768.0


def write_wav_stereo(path, left, right):
    inter = np.empty((len(left), 2), dtype=np.int16)
    inter[:, 0] = np.clip(left, -1, 1) * 32767
    inter[:, 1] = np.clip(right, -1, 1) * 32767
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(inter.tobytes())


def wav_dur(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


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
    return (np.sin(2 * np.pi * 659.25 * t) * 0.5 + np.sin(2 * np.pi * 987.77 * t) * 0.3) * env * 0.5


def _bed(dur, sr):
    n = int(dur * sr)
    t = np.linspace(0, dur, n, endpoint=False)
    freqs = [130.81, 196.00, 261.63, 329.63]
    sig = np.zeros(n, dtype=np.float32)
    for i, f in enumerate(freqs):
        det = 1.0 + 0.0006 * (i - 1.5)
        sig += np.sin(2 * np.pi * f * det * t) / (i + 1.5)
        sig += np.sin(2 * np.pi * f * det * 0.5 * t) * 0.25 / (i + 1.5)
    sig /= sig.max() + 1e-9
    lfo = 0.1 + 0.9 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.08 * t))
    fade = np.clip(np.minimum(1, np.minimum(t / 1.0, (dur - t) / 1.5)), 0, 1)
    return (sig * lfo * fade * 0.9).astype(np.float32)


def render_job_ts(job):
    return (
        "export const JOB = " + json.dumps({
            "titre": job["titre"], "prix": job["prix"], "cta": job["cta"],
            "hook": job["hook"], "palette": job["palette"], "keywords": job["keywords"],
            "visuel": job.get("visuel"),
        }, ensure_ascii=False, indent=2) + " as const;\n"
    )


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    offer = sys.argv[2]
    voice = sys.argv[3] if len(sys.argv) > 3 else "vivienne-fr"
    exagg = float(sys.argv[4]) if len(sys.argv) > 4 else 0.6
    cfg = float(sys.argv[5]) if len(sys.argv) > 5 else 0.4

    out_dir = Path("out") / offer / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    segments = job["narration"]
    seg_wavs, seg_durs, used_providers = [], [], []
    for i, seg in enumerate(segments):
        raw, used = tts(seg["texte"], voice, exagg, cfg)
        used_providers.append(used)
        raw_wav = out_dir / f"cb_seg_raw_{i}.wav"
        raw_wav.write_bytes(raw)
        seg_wav = out_dir / f"cb_seg_{i}.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_wav),
                        "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(seg_wav)], check=True)
        seg_wavs.append(seg_wav)
        seg_durs.append(wav_dur(seg_wav))
        print(f"seg {i} ok duree={round(seg_durs[-1], 2)}s voix={used}")

    (out_dir / "tts_report.json").write_text(json.dumps(
        {"chain": tts_providers.chain(), "segments": used_providers}, ensure_ascii=False, indent=1), encoding="utf-8")

    seg_starts, acc = [], 0.0
    for d in seg_durs:
        seg_starts.append(acc)
        acc += d + GAP

    parts = []
    for i, wav in enumerate(seg_wavs):
        parts.append(read_wav(wav))
        if i < len(seg_wavs) - 1:
            parts.append(np.zeros(int(GAP * SR), dtype=np.float32))
    vo = np.concatenate(parts)
    vo_total = len(vo) / SR

    words = []
    for i, seg in enumerate(segments):
        seg_start, seg_end = seg_starts[i], seg_starts[i] + seg_durs[i]
        toks = seg["texte"].split()
        weights = [max(1, len(tok.rstrip('.,;:!?'))) for tok in toks]
        total_w = sum(weights)
        t = seg_start
        for j, tok in enumerate(toks):
            dur = (weights[j] / total_w) * (seg_end - seg_start)
            words.append({"text": tok, "start": round(t, 3), "end": round(t + dur, 3),
                          "seg": i, "role": seg["role"]})
            t += dur
    print(f"mots (source distribues): {len(words)}")

    full = vo_total + TAIL
    n = int(full * SR)
    vo = np.concatenate([vo, np.zeros(max(0, n - len(vo)), dtype=np.float32)])[:n]

    bed = _bed(full, SR) * BED_LEVEL
    env = np.abs(vo)
    k = int(0.2 * SR)
    env = np.convolve(env, np.ones(k) / k, mode="same")
    env = np.clip(env / (np.percentile(env, 95) + 1e-9), 0, 1)
    bed *= 1.0 - env

    sfx = np.zeros(n, dtype=np.float32)
    for i, seg in enumerate(segments):
        if i == 0:
            continue
        idx = int(seg_starts[i] * SR)
        sw = _noise_swell(0.45, SR)
        if idx + len(sw) <= n:
            sfx[idx:idx + len(sw)] += sw
        if seg["role"] == "soulagement":
            ch = _chime(SR)
            if idx + len(ch) <= n:
                sfx[idx:idx + len(ch)] += ch
        if seg["role"] == "cta":
            p = _pop(SR)
            if idx + len(p) <= n:
                sfx[idx:idx + len(p)] += p
    sfx *= SFX_LEVEL

    write_wav_stereo(out_dir / "mix.wav", vo + bed + sfx, vo + bed + sfx)
    write_wav_stereo(out_dir / "vo.wav", vo, vo)
    (out_dir / "captions.json").write_text(
        json.dumps({"duration_s": round(full, 3), "vo_end_s": round(vo_total, 3), "words": words},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    rem_data = Path("remotion/src/data")
    rem_data.mkdir(parents=True, exist_ok=True)
    captions_ts = (
        "export type Caption = { text: string; start: number; end: number; seg: number; role: string };\n"
        f"export const DURATION_S = {round(full, 3)};\n"
        "export const CAPTIONS: Caption[] = " + json.dumps(words, ensure_ascii=False) + ";\n"
    )
    (rem_data / "captions.ts").write_text(captions_ts, encoding="utf-8")
    (rem_data / "job.ts").write_text(render_job_ts(job), encoding="utf-8")

    print(f"DURATION_S={round(full, 3)}  VO={round(vo_total, 3)}  WORDS={len(words)}")
    print(f"MIX={out_dir / 'mix.wav'}")


if __name__ == "__main__":
    main()
