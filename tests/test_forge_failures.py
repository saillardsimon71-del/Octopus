"""Etape 1 de l'audit : echecs bruyants dans FORGE (C1) et verrou de production (C2)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agents import agents as ag
from agents import config, cycle, db, tools

PY = sys.executable


def forge_messages() -> list[str]:
    return [m["content"] for m in db.recent_messages(100) if m["from_agent"] == "FORGE"]


# --- sous-processus -------------------------------------------------------------------------

def test_non_zero_exit_raises_with_stderr_tail(tmp_path):
    log = tmp_path / "logs" / "audio.log"
    with pytest.raises(tools.StepError, match="(?s)code 3 : .*Chatterbox 500"):
        tools._run_checked([PY, "-c", "import sys; print('seg 0'); sys.stderr.write('Chatterbox 500'); sys.exit(3)"],
                           str(tmp_path), log=log)
    text = log.read_text(encoding="utf-8")
    assert "# code 3" in text and "seg 0" in text and "Chatterbox 500" in text


def test_timeout_raises(tmp_path):
    started = time.time()
    with pytest.raises(tools.StepError, match="timeout 1 s"):
        tools._run_checked([PY, "-c", "import time; time.sleep(30)"], str(tmp_path), timeout=1)
    assert time.time() - started < 15


def test_missing_binary_raises(tmp_path):
    with pytest.raises(tools.StepError, match="lancement impossible"):
        tools._run_checked([str(tmp_path / "absent-binary")], str(tmp_path))


def test_run_shell_keeps_whitelist_and_now_checks_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SHELL_WHITELIST", config.SHELL_WHITELIST + [Path(PY).stem.lower()])
    assert "ok" in tools.run_shell([PY, "-c", "print('ok')"], cwd=str(tmp_path))
    with pytest.raises(tools.StepError):  # sonde 1 de l'audit : renvoyait 'echec\n' sans erreur
        tools.run_shell([PY, "-c", "import sys; print('echec'); sys.exit(3)"], cwd=str(tmp_path))
    with pytest.raises(ValueError, match="hors liste blanche"):
        tools.run_shell(["notarealcmd", "x"])


# --- artefacts ------------------------------------------------------------------------------

def test_require_fresh(tmp_path):
    t0 = time.time()
    fresh = tmp_path / "fresh.mp4"
    fresh.write_bytes(b"x")
    tools.require_fresh([fresh], t0)
    with pytest.raises(tools.StepError, match="absent ou périmé"):
        tools.require_fresh([tmp_path / "missing.mp4"], t0)
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(tools.StepError):
        tools.require_fresh([empty], t0)
    stale = tmp_path / "stale.mp4"
    stale.write_bytes(b"x")
    os.utime(stale, (t0 - 3600, t0 - 3600))  # fichier d'un run precedent
    with pytest.raises(tools.StepError, match="stale.mp4"):
        tools.require_fresh([stale], t0)


def _fake_step(*paths_fn):
    def step(*args, **kwargs):
        for fn in paths_fn:
            path = fn()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        return "ok"
    return step


def _artefacts(root: Path, offer: str) -> dict:
    out = root / "out" / offer
    rem = root / "remotion" / "src" / "data"
    return {"audio": [lambda: out / "audio" / "mix.wav", lambda: rem / "captions.ts", lambda: rem / "job.ts"],
            "render": [lambda: out / "video.mp4"], "mux": [lambda: out / "final.mp4"],
            "qc": [lambda: out / "qc_metrics.json"]}


def test_forge_stops_at_first_step_without_artefact(monkeypatch, isolated):
    called = []
    monkeypatch.setattr(tools, "make_audio", lambda *a: "Chatterbox arrete, rien produit")
    monkeypatch.setattr(tools, "remotion_render", lambda *a: called.append("render"))
    with pytest.raises(tools.StepError, match="mix.wav"):
        ag.FORGE.run("cash_devis_cgv01", {})
    assert called == []
    assert forge_messages() == ["démarrage du rendu de cash_devis_cgv01 (@FORGE)"]


def test_forge_refuses_stale_render_from_previous_run(monkeypatch, isolated):
    a = _artefacts(isolated, "o")
    for fn in a["render"]:  # video.mp4 d'un run precedent
        fn().parent.mkdir(parents=True, exist_ok=True)
        fn().write_bytes(b"old")
        os.utime(fn(), (time.time() - 3600,) * 2)
    monkeypatch.setattr(tools, "make_audio", _fake_step(*a["audio"]))
    monkeypatch.setattr(tools, "remotion_render", lambda *args: "rendu en echec silencieux")
    with pytest.raises(tools.StepError, match="video.mp4"):
        ag.FORGE.run("o", {})
    assert "rendu Remotion terminé" not in forge_messages()


def test_forge_nominal(monkeypatch, isolated):
    a = _artefacts(isolated, "o")
    monkeypatch.setattr(tools, "make_audio", _fake_step(*a["audio"]))
    monkeypatch.setattr(tools, "remotion_render", _fake_step(*a["render"]))
    monkeypatch.setattr(tools, "mux", _fake_step(*a["mux"]))

    def qc(offer_id):
        _fake_step(*a["qc"])()
        return {"lufs_integrated": -14.0, "lra_lu": 5.0, "satavg_mean": 40.0}

    monkeypatch.setattr(tools, "qc_metrics", qc)
    assert ag.FORGE.run("o", {})["lufs_integrated"] == -14.0
    assert len(forge_messages()) == 5


# --- verrou ---------------------------------------------------------------------------------

def test_run_lock_semantics():
    assert db.acquire_run_lock("a") is True
    assert db.acquire_run_lock("b") is False
    assert db.acquire_run_lock("a") is True  # renouvellement
    assert db.run_lock_holder() == "a"
    db.release_run_lock("b")  # un autre proprietaire ne libere pas
    assert db.run_lock_holder() == "a"
    db.release_run_lock("a")
    assert db.run_lock_holder() is None and db.acquire_run_lock("b") is True


def test_expired_lock_is_taken_over():
    assert db.acquire_run_lock("processus-tue", ttl_s=-1)
    assert db.run_lock_holder() is None
    assert db.acquire_run_lock("nouveau") is True


def test_corrupted_lock_value_is_treated_as_expired():
    db.set_state("run_lock", "sans-echeance")
    assert db.acquire_run_lock("a") is True


def test_second_cycle_is_refused_without_side_effects(monkeypatch):
    monkeypatch.setattr(ag.SOUT, "run", lambda already: pytest.fail("SOUT ne doit pas etre appele"))
    assert db.acquire_run_lock("cycle-en-cours")
    db.request_stop()
    with pytest.raises(cycle.CycleBusy):
        cycle.run_cycle("cash_devis_cgv01")
    assert db.stop_requested() is True  # l'arret demande sur le cycle en cours est conserve
    assert db.current_run() is None  # aucun run cree
    assert any("cycle refusé" in m["content"] for m in db.recent_messages(10))
    assert db.run_lock_holder() == "cycle-en-cours"


def test_lock_is_released_when_the_cycle_fails(monkeypatch):
    def boom(already):
        assert db.run_lock_holder() is not None
        raise RuntimeError("SOUT en panne")

    monkeypatch.setattr(ag.SOUT, "run", boom)
    with pytest.raises(RuntimeError, match="SOUT en panne"):
        cycle.run_cycle()
    assert db.run_lock_holder() is None
