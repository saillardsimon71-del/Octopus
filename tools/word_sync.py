"""Calage des sous-titres sur la voix reellement synthetisee (faster-whisper, CPU).

Sans ce module, chaque mot recoit une duree proportionnelle a son nombre de caracteres : correct en
moyenne, faux des que la voix accelere ou marque une pause. Ici, chaque segment audio est transcrit
avec horodatage au mot, puis les mots du script sont alignes sur cette transcription (difflib) :
les mots reconnus prennent le temps mesure, les autres sont interpoles entre deux ancres.

Optionnel : si `faster-whisper` n'est pas installe ou si PODALUX_WORD_SYNC=0, l'appelant garde
sa repartition proportionnelle. Aucun compte, aucun reseau apres le premier telechargement du modele.
"""
from __future__ import annotations

import difflib
import os
import re
import unicodedata

MODEL_SIZE = os.environ.get("PODALUX_WHISPER_MODEL", "tiny")
_model = None


def enabled() -> bool:
    if os.environ.get("PODALUX_WORD_SYNC", "1").strip().lower() in {"0", "false", "no"}:
        return False
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def _norm(token: str) -> str:
    plain = unicodedata.normalize("NFKD", token).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", plain)


def transcribe(wav_path: str) -> list[tuple[str, float, float]]:
    """(mot, debut, fin) mesures dans le fichier, en secondes depuis son debut."""
    segments, _ = _load().transcribe(wav_path, language=os.environ.get("CHATTERBOX_LANG", "fr"),
                                     word_timestamps=True, vad_filter=False)
    return [(w.word.strip(), float(w.start), float(w.end))
            for seg in segments for w in (seg.words or []) if w.word.strip()]


def align(tokens: list[str], heard: list[tuple[str, float, float]], start: float, end: float) -> list[tuple[float, float]]:
    """Fenetres (debut, fin) de chaque mot du script, dans le repere global du montage."""
    span = max(0.05, end - start)
    if not tokens:
        return []
    if not heard:
        return _proportional(tokens, start, end)
    a, b = [_norm(t) for t in tokens], [_norm(h[0]) for h in heard]
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    anchors: dict[int, tuple[float, float]] = {}
    heard_span = max(1e-6, heard[-1][2] - heard[0][1])
    for i, j, size in matcher.get_matching_blocks():
        for k in range(size):
            h_start, h_end = heard[j + k][1], heard[j + k][2]
            # remise a l'echelle : la transcription couvre le segment, pas le montage complet
            anchors[i + k] = (start + (h_start - heard[0][1]) / heard_span * span,
                              start + (h_end - heard[0][1]) / heard_span * span)
    if not anchors:
        return _proportional(tokens, start, end)
    return _interpolate(tokens, anchors, start, end)


def _proportional(tokens: list[str], start: float, end: float) -> list[tuple[float, float]]:
    weights = [max(1, len(t.rstrip(".,;:!?"))) for t in tokens]
    total = sum(weights)
    out, t = [], start
    for w in weights:
        d = (w / total) * (end - start)
        out.append((t, t + d))
        t += d
    return out


def _interpolate(tokens, anchors, start, end):
    """Les mots non reconnus se repartissent entre les deux mots reconnus qui les encadrent."""
    known = sorted(anchors)
    out: list[tuple[float, float]] = [(0.0, 0.0)] * len(tokens)
    for i in known:
        out[i] = anchors[i]
    gaps = [(-1, known[0])] + list(zip(known, known[1:])) + [(known[-1], len(tokens))]
    for left, right in gaps:
        hole = list(range(left + 1, right))
        if not hole:
            continue
        t0 = start if left < 0 else anchors[left][1]
        t1 = end if right >= len(tokens) else anchors[right][0]
        if t1 <= t0:
            t1 = t0 + 0.01 * len(hole)
        for n, i in enumerate(hole):
            d = (t1 - t0) / len(hole)
            out[i] = (t0 + n * d, t0 + (n + 1) * d)
    return [(round(max(start, s), 3), round(min(end, max(s + 0.05, e)), 3)) for s, e in out]
