import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agents import cancel, search_ddgs


def worker(monkeypatch, tmp_path, source):
    script = tmp_path / "search_worker.py"
    script.write_text(source, encoding="utf-8")
    monkeypatch.setattr(search_ddgs, "__file__", str(script))
    processes = []
    real_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(search_ddgs.subprocess, "Popen", spawn)
    return processes


def test_worker_preserves_request_and_never_inherits_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("EXA_API_KEY", "must-not-leak")
    monkeypatch.setenv("ARBITRARY_CUSTOM_SECRET", "must-not-leak")
    monkeypatch.setenv("PYTHONPATH", "must-not-inherit")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    processes = worker(monkeypatch, tmp_path, '''
import json, os, sys
request = json.load(sys.stdin)
assert request == {"query": "été site:example.org", "limit": 2}
assert not any(k in os.environ for k in ("EXA_API_KEY", "ARBITRARY_CUSTOM_SECRET", "PYTHONPATH"))
assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:9"
json.dump({"ok": True, "results": [{"title": "été"}]}, sys.stdout)
''')
    assert search_ddgs.search_items("été site:example.org", 2) == [{"title": "été"}]
    assert processes[0].poll() == 0


@pytest.mark.parametrize("raw", ["", "not-json", "[]", '{"ok":true,"results":{}}',
                                     '{"ok":false,"error":"429 throttled"}'])
def test_worker_bad_response_is_error_not_empty_result(monkeypatch, tmp_path, raw):
    worker(monkeypatch, tmp_path, f"print({raw!r})")
    with pytest.raises((ValueError, RuntimeError)):
        search_ddgs.search_items("test", 2)


def test_timeout_reaps_native_worker(monkeypatch, tmp_path):
    # PyDLL holds the child's GIL, reproducing the reason Hermes isolates DDGS.
    native = ("ctypes.PyDLL('kernel32').Sleep(30000)" if sys.platform == "win32"
              else "ctypes.PyDLL(None).sleep(30)")
    processes = worker(monkeypatch, tmp_path, "import ctypes\n" + native)
    monkeypatch.setattr(search_ddgs, "SEARCH_TIMEOUT_S", 0.2)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        search_ddgs.search_items("test", 2)
    assert time.monotonic() - start < 4
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed and processes[0].stdin.closed


def test_human_cancel_reaps_worker(monkeypatch, tmp_path):
    processes = worker(monkeypatch, tmp_path, "import time\ntime.sleep(30)")
    calls = 0

    def checkpoint(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise cancel.Cancelled("stop")

    monkeypatch.setattr(cancel, "checkpoint", checkpoint)
    with pytest.raises(cancel.Cancelled):
        search_ddgs.search_items("test", 2)
    assert processes[0].poll() is not None


def test_hermes_normalization_uses_web_engines_including_google(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 10}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, **kwargs):
            assert query == "test"
            engines = kwargs["backend"].split(",")
            assert "google" in engines and "duckduckgo" in engines
            assert "wikipedia" not in engines and "grokipedia" not in engines
            assert kwargs["max_results"] == 1
            return [{"title": "Title", "href": "https://example.org", "body": "Text"}, {}]

    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=Client))
    assert search_ddgs._run_search("test", 1) == [
        {"title": "Title", "url": "https://example.org", "snippet": "Text",
         "provider": "ddgs", "source": "", "date": ""}]


def test_availability_is_local_only(monkeypatch):
    monkeypatch.setattr(search_ddgs.importlib.util, "find_spec", lambda name: None)
    assert not search_ddgs.available()
    monkeypatch.setattr(search_ddgs.importlib.util, "find_spec", lambda name: object())
    assert search_ddgs.available()
