"""Regression: default handler discovery loads orbit.mission without a Podalux declaration."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(script, *, env, timeout=30):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        cwd=ROOT,
    )
    return result


def _base_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "OCTOPUS_HOME": str(home),
        "OCTOPUS_DB": str(tmp_path / "octopus.db"),
        "PYTHONPATH": str(ROOT),
    }
    for var in ("OCTOPUS_HANDLERS", "PODALUX_ROOT"):
        env.pop(var, None)
    return env, home


def test_default_empty_home_loads_orbit_mission(tmp_path):
    env, home = _base_env(tmp_path)

    script = '''
        import sys
        sys.path.insert(0, {pythonpath!r})
        from octopus import worker
        worker.HANDLERS.clear()
        loaded = worker.load_handlers()
        for k in sorted(loaded.keys()):
            print(k)
    '''.format(pythonpath=str(ROOT))

    result = _run(script, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    handlers = result.stdout.strip().splitlines()
    assert "orbit.mission" in handlers
    assert "development.task" not in handlers
    assert "podalux.video_cycle" not in handlers
    assert "media.video_generate" not in handlers


def test_non_podalux_declaration_default_loads_orbit_mission(tmp_path):
    env, home = _base_env(tmp_path)
    (home / "businesses" / "demo").mkdir(parents=True)
    (home / "businesses" / "demo" / "business.toml").write_text(
        'id = "demo"\nhandlers = ["demo_pkg.handlers"]\n', encoding="utf-8"
    )
    (home / "demo_pkg").mkdir()
    (home / "demo_pkg" / "__init__.py").write_text("", encoding="utf-8")
    (home / "demo_pkg" / "handlers.py").write_text(
        "from octopus.worker import handler\n\n@handler('demo.ping')\ndef ping(ctx):\n    return {'pong': True}\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = os.pathsep.join([str(home), str(ROOT)])

    script = '''
        import sys
        sys.path.insert(0, {pythonpath!r})
        from octopus import worker
        worker.HANDLERS.clear()
        loaded = worker.load_handlers()
        for k in sorted(loaded.keys()):
            print(k)
    '''.format(pythonpath=str(ROOT))

    result = _run(script, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    handlers = result.stdout.strip().splitlines()
    assert "orbit.mission" in handlers
    assert "demo.ping" in handlers


def test_builtin_only_explicit_selection_excludes_orbit_mission(tmp_path):
    env, home = _base_env(tmp_path)

    script = '''
        import sys
        sys.path.insert(0, {pythonpath!r})
        from octopus import worker
        worker.HANDLERS.clear()
        loaded = worker.load_handlers(["octopus.builtin_handlers"])
        for k in sorted(loaded.keys()):
            print(k)
    '''.format(pythonpath=str(ROOT))

    result = _run(script, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    handlers = result.stdout.strip().splitlines()
    assert "orbit.mission" not in handlers


def test_builtin_only_env_override_excludes_orbit_mission(tmp_path):
    env, home = _base_env(tmp_path)
    env["OCTOPUS_HANDLERS"] = "octopus.builtin_handlers"

    script = '''
        import sys
        sys.path.insert(0, {pythonpath!r})
        from octopus import worker
        worker.HANDLERS.clear()
        loaded = worker.load_handlers()
        for k in sorted(loaded.keys()):
            print(k)
    '''.format(pythonpath=str(ROOT))

    result = _run(script, env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    handlers = result.stdout.strip().splitlines()
    assert "orbit.mission" not in handlers
