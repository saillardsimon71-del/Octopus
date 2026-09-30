"""Frontières partagées conservées : sous-processus, artefacts et verrou."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agents import config, db, tools

PY = sys.executable


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
