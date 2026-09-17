"""QC ffmpeg en une passe : mêmes valeurs que les mesures historiques passe par passe."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from conftest import PROJECT

sys.path.insert(0, str(PROJECT / "tools"))
import qc_metrics as q  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg absent")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    root = tmp_path_factory.mktemp("qc")
    freeze = root / "freeze.mp4"  # mire animée, 3 s d'image fixe (freeze), nouvelle mire (coupes), son
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=180x320:rate=30:duration=2",
            "-f", "lavfi", "-i", "color=c=red:size=180x320:rate=30:duration=2",
            "-f", "lavfi", "-i", "testsrc=size=180x320:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(freeze))
    silent = root / "silent.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=180x320:rate=30:duration=2", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(silent))
    return {"freeze": str(freeze), "silent": str(silent)}


def _reference(video):
    lufs, lra = q.lufs_lra(video)
    sat_mean, sat_max = q.satavg(video)
    return {"lufs": lufs, "lra": lra, "sat_mean": sat_mean, "sat_max": sat_max, "cuts": q.cuts(video),
            "freezes": q.freezes(video)}


@pytest.mark.parametrize("name", ["freeze", "silent"])
def test_single_pass_matches_per_metric_passes(videos, name):
    single = q.stream_metrics(videos[name])
    assert single == _reference(videos[name])
    if name == "freeze":
        assert single["freezes"] == 1 and single["cuts"] >= 1 and single["lufs"] is not None
    else:
        assert single["lufs"] is None and single["lra"] is None


def test_cli_still_writes_the_same_contract(videos, tmp_path):
    subprocess.run([sys.executable, str(PROJECT / "tools" / "qc_metrics.py"), videos["freeze"], str(tmp_path),
                    "--label", "t"], check=True, capture_output=True)
    data = json.loads((tmp_path / "qc_metrics.json").read_text(encoding="utf-8"))
    assert {"lufs_integrated", "lra_lu", "satavg_mean", "satavg_max", "cuts_scene025", "freezes_gt1_2s",
            "ken_burns_motion", "frames"} <= set(data)
    assert data["freezes_gt1_2s"] == 1 and len(data["frames"]) == 6
