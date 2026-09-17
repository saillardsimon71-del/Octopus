"""Chaîne FORGE autonome pour un exécuteur cloud (runner CI) : voix -> Remotion -> mux -> QC.

Usage : python tools/render_short.py <offer_id>
Lit jobs/<offer_id>.json, produit out/<offer_id>/final.mp4, qc_metrics.json et render_report.json
(durée de chaque étape). N'importe pas `agents` : aucune base, aucun LLM, aucun secret requis.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd, cwd=ROOT, log=None):
    started = time.time()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (proc.stdout or "") + (proc.stderr or "")
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(output, encoding="utf-8")
    if proc.returncode != 0:
        sys.exit(f"échec ({proc.returncode}) : {' '.join(map(str, cmd))}\n{output[-3000:]}")
    return output, round(time.time() - started, 2)


def require(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        sys.exit(f"artefact absent ou vide : {path}")


def main() -> None:
    offer = sys.argv[1]
    job = ROOT / "jobs" / f"{offer}.json"
    require(job)
    out = ROOT / "out" / offer
    logs = out / "logs"
    report = {"offer_id": offer, "tts_backend": os.environ.get("CHATTERBOX_URL", "local:4123"), "steps": {}}
    total = time.time()

    _, report["steps"]["audio_s"] = run([sys.executable, "tools/make_audio_chatterbox_full.py", str(job), offer,
                                         "vivienne-fr", "0.5", "0.5"], log=logs / "audio.log")
    for artifact in (out / "audio" / "mix.wav", ROOT / "remotion" / "src" / "data" / "captions.ts"):
        require(artifact)

    npx = "npx.cmd" if os.name == "nt" else "npx"
    _, report["steps"]["render_s"] = run([npx, "remotion", "render", "CashShort", f"../out/{offer}/video.mp4"],
                                         cwd=ROOT / "remotion", log=logs / "render.log")
    require(out / "video.mp4")

    probe, _ = run(["ffmpeg", "-hide_banner", "-i", str(out / "audio" / "mix.wav"), "-af", "ebur128", "-f", "null", "-"])
    lufs = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", probe)
    if not lufs:
        sys.exit("LUFS introuvable avant mux")
    gain = round(-14 - float(lufs[-1]), 2)
    _, report["steps"]["mux_s"] = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / "video.mp4"),
                                       "-i", str(out / "audio" / "mix.wav"), "-af", f"volume={gain}dB", "-c:v", "copy",
                                       "-c:a", "aac", "-b:a", "192k", "-shortest", str(out / "final.mp4")])
    require(out / "final.mp4")

    _, report["steps"]["qc_s"] = run([sys.executable, "tools/qc_metrics.py", str(out / "final.mp4"), str(out),
                                      "--label", offer], log=logs / "qc.log")
    report["qc"] = json.loads((out / "qc_metrics.json").read_text(encoding="utf-8"))
    report["total_s"] = round(time.time() - total, 2)
    report["final_mp4_bytes"] = (out / "final.mp4").stat().st_size
    (out / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
