"""Pipeline audio complet : VO (chaine de fournisseurs) + word-timestamps + bed/SFX/ducking.

Usage : python tools/make_audio_chatterbox_full.py <job.json> <offer_id> [voice] [exaggeration] [cfg_weight]

La voix vient de `tools/tts_providers.py` : `TTS_CHAIN` (defaut azure,cloudflare,chatterbox,piper)
essaie les fournisseurs dans l'ordre et retient le premier qui repond. Aucun quota ne peut donc
arreter le pipeline : `piper` synthetise en local sur CPU, sans compte. Le fournisseur reellement
utilise par segment est ecrit dans out/<offer>/audio/tts_report.json.
"""
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import tts_providers, word_sync  # noqa: E402

SR = 44100
# Debit par role : une narration qui garde le meme tempo du debut a la fin sonne robotique.
ROLE_SPEED = {"hook": 1.05, "douleur": 0.97, "preuve": 1.0, "soulagement": 1.0, "cta": 1.06}
# Traitement de la voix (ffmpeg, deja requis) : coupe des graves, presence, compression douce.
VOICE_FILTERS = ("highpass=f=85,equalizer=f=260:t=q:w=1.2:g=-2,equalizer=f=4200:t=q:w=1.4:g=3,"
                 "acompressor=threshold=-18dB:ratio=3:attack=5:release=140:makeup=2,alimiter=limit=0.95")
GAP = 0.12
TAIL = 1.5
MIN_DURATION_S = float(os.environ.get("PODALUX_MIN_DURATION_S", "20.5"))  # 20 a 35 s : la zone ou la retention tient
MAX_GAP = 0.6
BED_LEVEL = 0.28
SFX_LEVEL = 0.45


def tts(text, voice, exaggeration, cfg_weight, speed=1.0):
    audio, used, failures = tts_providers.synthesize(
        text, voice=voice, exaggeration=exaggeration, cfg_weight=cfg_weight, speed=speed)
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


def _voice_chain(vo, out_dir):
    """Voix travaillee par ffmpeg : graves coupes, presence, compression. Si ffmpeg echoue, brut."""
    raw_path, done_path = out_dir / "vo_raw.wav", out_dir / "vo_chain.wav"
    write_wav_stereo(raw_path, vo, vo)
    proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw_path),
                           "-af", VOICE_FILTERS, "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le",
                           str(done_path)], capture_output=True, text=True)
    if proc.returncode != 0 or not done_path.exists():
        print(f"voix: traitement ignore ({proc.stderr[-120:].strip()})")
        return vo
    treated = read_wav(done_path)
    if len(treated) < len(vo):
        treated = np.concatenate([treated, np.zeros(len(vo) - len(treated), dtype=np.float32)])
    return treated[:len(vo)]


def render_job_ts(job):
    return (
        "export const JOB = " + json.dumps({
            "titre": job["titre"], "prix": job["prix"], "cta": job["cta"],
            "hook": job["hook"], "palette": job["palette"], "keywords": job["keywords"],
            "visuel": job.get("visuel"),
        }, ensure_ascii=False, indent=2) + " as const;\n"
    )


def _fit_gap(seg_durs):
    """Respiration entre segments, allongee si la narration est trop courte pour la borne QC (18 s).

    Allonger les silences est sans risque visuel : l'animation continue, donc aucune image figee.
    """
    voice = sum(seg_durs)
    holes = max(1, len(seg_durs) - 1)
    needed = MIN_DURATION_S - TAIL - voice
    gap = min(MAX_GAP, max(GAP, needed / holes)) if needed > GAP * holes else GAP
    if gap > GAP:
        print(f"duree courte ({voice + TAIL + GAP * holes:.1f}s) : respiration portee a {gap:.2f}s")
    return round(gap, 3)


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
        raw, used = tts(seg["texte"], voice, exagg, cfg, ROLE_SPEED.get(seg["role"], 1.0))
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

    gap = _fit_gap(seg_durs)
    seg_starts, acc = [], 0.0
    for d in seg_durs:
        seg_starts.append(acc)
        acc += d + gap

    parts = []
    for i, wav in enumerate(seg_wavs):
        parts.append(read_wav(wav))
        if i < len(seg_wavs) - 1:
            parts.append(np.zeros(int(gap * SR), dtype=np.float32))
    vo = np.concatenate(parts)
    vo_total = len(vo) / SR

    synced = word_sync.enabled()
    words, shifts = [], []
    for i, seg in enumerate(segments):
        seg_start, seg_end = seg_starts[i], seg_starts[i] + seg_durs[i]
        toks = seg["texte"].split()
        spread = word_sync._proportional(toks, seg_start, seg_end)
        spans = spread
        if synced:
            try:
                heard = word_sync.transcribe(str(seg_wavs[i]))
                spans = word_sync.align(toks, heard, seg_start, seg_end)
                shifts += [abs(a[0] - b[0]) for a, b in zip(spans, spread)]
            except Exception as exc:  # modele absent, audio illisible : la repartition reste valable
                print(f"calage mots: segment {i} non cale ({type(exc).__name__}: {str(exc)[:80]})")
                spans = spread
        for tok, (start, end) in zip(toks, spans):
            words.append({"text": tok, "start": round(start, 3), "end": round(end, 3),
                          "seg": i, "role": seg["role"]})
    source = "cales sur la voix" if synced else "repartis au prorata"
    print(f"mots ({source}): {len(words)}" +
          (f" ; decalage moyen {sum(shifts) / len(shifts):.3f}s, max {max(shifts):.3f}s" if shifts else ""))

    full = vo_total + TAIL
    n = int(full * SR)
    vo = np.concatenate([vo, np.zeros(max(0, n - len(vo)), dtype=np.float32)])[:n]

    vo = _voice_chain(vo, out_dir)

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

    shift = int(0.012 * SR)  # 12 ms : elargissement stereo du lit, voix centree
    bed_r = np.concatenate([np.zeros(shift, dtype=np.float32), bed])[:len(bed)]
    write_wav_stereo(out_dir / "mix.wav", vo + bed + sfx, vo + bed_r + sfx)
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
