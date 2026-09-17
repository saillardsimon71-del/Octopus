"""Génération vidéo WanGP : vrai pont (octopus/media/wangp_bridge.py) contre un faux WanGP au même contrat."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from octopus import tasks, worker
from octopus.media import handlers, library, wangp

FAKE_APP = Path(__file__).parent / "fixtures" / "fake_wangp"
QUIET = dict(log=lambda s: None)


@pytest.fixture
def fake_wangp(monkeypatch):
    monkeypatch.setenv("OCTOPUS_WANGP_ROOT", str(FAKE_APP))
    monkeypatch.setenv("OCTOPUS_WANGP_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_WANGP_MODE", "ok")
    monkeypatch.setattr(wangp, "running_instances", lambda: [])
    worker.load_handlers(["octopus.media.handlers"])
    return monkeypatch


def run_task(**inp) -> dict:
    worker.enqueue("studio", "media.video_generate", {"prompt": "Un chat astronaute danse sur la Lune", **inp})
    return worker.run_one("w", kinds=["media.video_generate"], **QUIET)


# --- découverte ---------------------------------------------------------------------------------

def test_discovery_through_pinokio_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    app = tmp_path / "pinokio" / "api" / "wan.git" / "app"
    (app / "shared").mkdir(parents=True)
    (app / "wgp.py").write_text("")
    (app / "shared" / "api.py").write_text("")
    (app / "venv" / "Scripts").mkdir(parents=True)
    (app / "venv" / "Scripts" / "python.exe").write_text("")
    (app / "venv" / "bin").mkdir(parents=True)
    (app / "venv" / "bin" / "python").write_text("")
    (home / ".pinokio").mkdir(parents=True)
    (home / ".pinokio" / "config.json").write_text(json.dumps({"home": str(tmp_path / "pinokio")}))
    monkeypatch.delenv("OCTOPUS_WANGP_ROOT", raising=False)
    monkeypatch.delenv("OCTOPUS_WANGP_PYTHON", raising=False)
    monkeypatch.setattr(Path, "home", lambda: home)
    install = wangp.discover()
    assert install.ok, install.problems
    assert install.app_dir == app and install.pinokio_home == tmp_path / "pinokio"


def test_discovery_explains_missing_pieces(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOPUS_WANGP_ROOT", str(tmp_path))
    monkeypatch.delenv("OCTOPUS_WANGP_PYTHON", raising=False)
    problems = " | ".join(wangp.discover().problems)
    assert "wgp.py absent" in problems and "shared/api.py absent" in problems and "Python de WanGP absent" in problems


@pytest.mark.skipif(sys.platform == "win32", reason="lecture de /proc")
def test_running_webui_is_detected(tmp_path):
    fake = tmp_path / "wgp.py"
    fake.write_text("import time; time.sleep(20)")
    proc = subprocess.Popen([sys.executable, str(fake), "--multiple-images"])
    try:
        time.sleep(0.5)
        assert any(i["pid"] == proc.pid for i in wangp.running_instances())
    finally:
        proc.kill()


def test_partial_event_lines_are_kept_for_later(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"kind": "a"}\n{"kind": "b"}\n{"kind": "c", "par')
    events, offset = wangp.read_new_events(path, 0)
    assert [e["kind"] for e in events] == ["a", "b"]
    with path.open("ab") as fh:
        fh.write(b'tiel": 1}\n')
    events, _ = wangp.read_new_events(path, offset)
    assert events == [{"kind": "c", "partiel": 1}]


# --- réglages -----------------------------------------------------------------------------------

def test_build_settings_merges_defaults_and_request(fake_wangp, isolated):
    wangp.probe()  # vrai pont, faux WanGP : met les réglages par défaut en cache
    settings = handlers.build_settings("minimax_h3_fl2va", "prompt", settings={"num_inference_steps": 8},
                                       duration_s=5, resolution="480x832", seed=7)
    assert settings == {"model_type": "minimax_h3_fl2va", "prompt": "prompt", "resolution": "480x832",
                        "num_inference_steps": 8, "video_length": "5s", "seed": 7}
    with pytest.raises(ValueError, match="résolution invalide"):
        handlers.build_settings("m", "p", resolution="grand")


def test_probe_reports_models_and_hardware(fake_wangp, isolated):
    data = wangp.probe(families="minimax_h3")
    assert data["ok"] and data["wangp_version"] == "fake-1.0"
    assert [m["model_type"] for m in data["models"]] == ["minimax_h3_fl2va", "minimax_h3_ref2va_pruned"]
    assert data["hardware"]["python"] and "cpu_count" in data["hardware"]
    assert handlers.default_model() == "minimax_h3_fl2va"  # premier modèle H3 disponible


# --- génération ---------------------------------------------------------------------------------

def test_generates_variants_with_distinct_seeds(fake_wangp, isolated):
    result = run_task(variants=2, seed=100, duration_s=5, resolution="480x832")
    assert result["status"] == "done", result["error"]
    videos = result["output"]["videos"]
    assert len(videos) == 2 and all(Path(v["path"]).exists() for v in videos)
    records = library.by_task(result["id"])
    assert [r["status"] for r in records] == ["done", "done"] and [r["progress"] for r in records] == [100, 100]
    assert [r["settings"]["seed"] for r in records] == [100, 101]
    assert records[0]["output_path"].endswith("un-chat-astronaute-danse-sur-la-lune-v1.mp4")
    if shutil.which("ffprobe"):
        assert (records[0]["width"], records[0]["height"]) == (480, 832) and records[0]["duration_s"] == pytest.approx(1, abs=0.1)
    workdir = library.media_root() / f"task-{result['id']:06d}"
    assert (workdir / "job.json").exists() and (workdir / "bridge.log").exists()
    kinds = {json.loads(l)["kind"] for l in (workdir / "events.jsonl").read_text(encoding="utf-8").splitlines()}
    assert {"bridge_started", "session_ready", "progress", "bridge_finished"} <= kinds


def test_partial_failure_keeps_the_good_variant(fake_wangp, isolated):
    fake_wangp.setenv("FAKE_WANGP_MODE", "fail_second")
    result = run_task(variants=3)
    assert result["status"] == "done" and result["output"]["failed"] == 1
    statuses = [r["status"] for r in library.by_task(result["id"])]
    assert statuses == ["done", "failed", "done"]
    assert "CUDA out of memory" in library.by_task(result["id"])[1]["error"]


def test_total_failure_fails_the_attempt(fake_wangp, isolated):
    fake_wangp.setenv("FAKE_WANGP_MODE", "fail_all")
    result = run_task()
    assert result["status"] == "queued" and "aucune vidéo produite" in result["error"]  # 2 tentatives
    assert library.by_task(result["id"])[0]["status"] == "failed"


def test_bridge_crash_is_reported(fake_wangp, isolated):
    fake_wangp.setenv("FAKE_WANGP_MODE", "crash")
    result = run_task()
    assert "pont arrêté sans résultat (code 9)" in result["error"] or "aucune vidéo" in result["error"]


def test_webui_already_running_blocks_generation(fake_wangp, isolated):
    fake_wangp.setattr(wangp, "running_instances", lambda: [{"pid": 4242, "cmdline": "python wgp.py"}])
    result = run_task()
    assert "WanGP est déjà lancé (PID 4242" in result["error"] and library.recent() == []


def test_cancel_stops_the_bridge_cleanly(fake_wangp, isolated):
    fake_wangp.setenv("FAKE_WANGP_MODE", "slow")
    task_id = worker.enqueue("studio", "media.video_generate", {"prompt": "long"})

    def cancel_when_running():
        for _ in range(100):
            records = library.by_task(task_id)
            if records and records[0]["progress"] > 0:
                tasks.cancel(task_id)
                return
            time.sleep(0.1)

    threading.Thread(target=cancel_when_running, daemon=True).start()
    started = time.time()
    result = worker.run_one("w", kinds=["media.video_generate"], **QUIET)
    assert result["status"] == "cancelled" and time.time() - started < 30
    assert library.by_task(task_id)[0]["status"] == "cancelled"


def test_timeout_kills_a_stuck_bridge(fake_wangp, isolated, tmp_path):
    fake_wangp.setenv("FAKE_WANGP_MODE", "slow")
    install = wangp.discover()
    settings = handlers.build_settings("minimax_h3_fl2va", "x")
    started = time.time()
    result = wangp.run_bridge(install, [settings], tmp_path / "run", timeout_s=1, cancel_grace_s=30, poll_s=0.2)
    assert result["stop_reason"].startswith("délai dépassé") and time.time() - started < 25
    assert result["success"] is False


def test_cli_list_show_and_reuse(fake_wangp, isolated, capsys):
    from octopus.__main__ import main
    first = run_task(seed=5)
    gen_id = first["output"]["generations"][0]
    assert main(["video", "list"]) == 0 and "Un chat astronaute" in capsys.readouterr().out
    assert main(["video", "show", str(gen_id)]) == 0 and '"status": "done"' in capsys.readouterr().out
    assert main(["video", "reuse", str(gen_id), "--variants", "2", "--wait"]) == 0
    children = [g for g in library.recent() if g["parent_id"] == gen_id]
    assert len(children) == 2 and all(c["status"] == "done" for c in children)
    assert all("seed" not in c["settings"] or c["settings"]["seed"] != 5 for c in children)
