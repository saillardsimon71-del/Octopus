"""QC métrique ffmpeg — mesure objective d'un mp4, sans les yeux (filtre primaire).

Usage :
    python tools/qc_metrics.py <video.mp4> <out_dir> [--label <name>]

Produit :
    <out_dir>/qc_metrics.json   (métriques objectives)
    <out_dir>/frames/frame-0..5.jpg   (6 frames extraites pour le QC vision)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def ffprobe(video):
    out = sh(["ffprobe", "-v", "error",
              "-show_entries", "format=duration:stream=width,height,r_frame_rate,nb_frames",
              "-of", "json", video])
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def duration_s(video):
    info = ffprobe(video)
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def stream_info(video):
    info = ffprobe(video)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("width")), None)
    if not v:
        return (0, 0, 0.0, 0)
    w, h = v.get("width", 0), v.get("height", 0)
    fps = 0.0
    rfr = v.get("r_frame_rate", "0/1")
    try:
        num, den = rfr.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    nb = int(v.get("nb_frames", 0) or 0)
    return (w, h, fps, nb)


def lufs_lra(video):
    """Loudness intégrée (LUFS) et plage dynamique (LRA) via ebur128."""
    out = sh(["ffmpeg", "-hide_banner", "-i", video,
              "-af", "ebur128", "-f", "null", "-"])
    lufs, lra = None, None
    for ln in out.splitlines():
        m = re.search(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", ln)
        if m:
            lufs = float(m.group(1))
        m = re.search(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", ln)
        if m:
            lra = float(m.group(1))
    return lufs, lra


def satavg(video):
    """Saturation moyenne (SATAVG) : moyenne et max sur des frames échantillonnées."""
    out = sh(["ffmpeg", "-hide_banner", "-i", video,
              "-vf", "select='not(mod(n,30))',signalstats,metadata=print:key=lavfi.signalstats.SATAVG",
              "-an", "-f", "null", "-"])
    vals = [float(m) for m in re.findall(r"SATAVG=(\d+(?:\.\d+)?)", out)]
    if not vals:
        return None, None
    return round(sum(vals) / len(vals), 2), round(max(vals), 2)


def cuts(video):
    out = sh(["ffmpeg", "-hide_banner", "-i", video,
              "-vf", "select='gt(scene,0.25)',showinfo",
              "-f", "null", "-"])
    return len(re.findall(r"pts_time:", out))


def freezes(video):
    out = sh(["ffmpeg", "-hide_banner", "-i", video,
              "-vf", "freezedetect=n=-60dB:d=1.2",
              "-an", "-f", "null", "-"])
    return len(re.findall(r"freeze_start:", out))


def extract_frames(video, out_dir, n=6):
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    d = duration_s(video)
    paths = []
    for i in range(n):
        t = round(d * (i + 0.5) / n, 2) if d else 0.0
        p = frames_dir / f"frame-{i}.jpg"
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t),
            "-i", video, "-frames:v", "1", "-q:v", "3", str(p)])
        paths.append(p)
    return paths


def ken_burns_motion(video):
    """Score de mouvement inter-frames (Ken Burns).

    Compare des frames consécutives en échelle de gris. 0 = figé.
    Un zoom continu 1.0 -> 1.22 donne un score faible mais strictement positif.
    """
    try:
        from PIL import Image, ImageChops
        import io
        import statistics
    except ImportError:
        return None

    d = duration_s(video)
    if not d:
        return None
    imgs = []
    for i in range(8):
        t = round(d * (i + 0.5) / 8, 2)
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t),
             "-i", video, "-frames:v", "1", "-vf", "scale=160:-2,format=gray",
             "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True)
        if r.returncode != 0 or not r.stdout:
            continue
        imgs.append(Image.open(io.BytesIO(r.stdout)).convert("L"))
    if len(imgs) < 2:
        return None
    diffs = []
    for a, b in zip(imgs, imgs[1:]):
        diff = ImageChops.difference(a, b)
        hist = diff.histogram()
        total = sum(hist)
        if total == 0:
            diffs.append(0.0)
            continue
        mean = sum(i * c for i, c in enumerate(hist)) / total
        diffs.append(mean)
    return round(statistics.mean(diffs), 4)


def main():
    argv = sys.argv[1:]
    if len(argv) < 2:
        sys.exit("Usage: python tools/qc_metrics.py <video.mp4> <out_dir> [--label name]")

    video = argv[0]
    out_dir = Path(argv[1])
    label = None
    if "--label" in argv:
        label = argv[argv.index("--label") + 1]

    out_dir.mkdir(parents=True, exist_ok=True)
    if not Path(video).exists():
        sys.exit(f"Video introuvable : {video}")

    d = duration_s(video)
    w, h, fps, nb = stream_info(video)
    lufs, lra = lufs_lra(video)
    sat_mean, sat_max = satavg(video)
    n_cuts = cuts(video)
    n_freeze = freezes(video)
    motion = ken_burns_motion(video)

    frames = extract_frames(video, out_dir)

    result = {
        "label": label or Path(video).name,
        "video": str(video),
        "duration_s": round(d, 2),
        "resolution": f"{w}x{h}",
        "fps": round(fps, 2),
        "nb_frames": nb,
        "lufs_integrated": lufs,
        "lra_lu": lra,
        "satavg_mean": sat_mean,
        "satavg_max": sat_max,
        "cuts_scene025": n_cuts,
        "freezes_gt1_2s": n_freeze,
        "ken_burns_motion": motion,
        "targets": {"lufs": -14.0, "lra": 5.0, "satavg": 25.0},
        "frames": [str(p) for p in frames],
    }

    out_json = out_dir / "qc_metrics.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
