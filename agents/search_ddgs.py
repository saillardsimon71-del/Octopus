"""Search-only adapter derived from Hermes plugins/web/ddgs at
59004a62356f3a4697ab0fe8ad5086d2b405e2a6. See THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SEARCH_TIMEOUT_S = 30
WEB_BACKENDS = "google,brave,duckduckgo,mojeek,startpage,yahoo"


def available():
    return importlib.util.find_spec("ddgs") is not None


def _child_env():
    # An anonymous search child needs transport/OS settings, never provider secrets.
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "DDGS_PROXY",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def search_items(query, limit):
    from . import cancel

    cancel.checkpoint("SEARCH")
    request = json.dumps({"query": query, "limit": max(1, min(int(limit), 20))})
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=_child_env(), text=True, encoding="utf-8", **flags,
    )
    try:
        deadline = time.monotonic() + SEARCH_TIMEOUT_S
        while True:
            cancel.checkpoint("SEARCH")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"DDGS search timed out after {SEARCH_TIMEOUT_S}s")
            try:
                raw, _ = proc.communicate(request, timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                # communicate preserves its buffers; input must only be supplied once.
                request = None
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                pipe.close()
    envelope = json.loads(raw)
    if not isinstance(envelope, dict) or not envelope.get("ok") or proc.returncode:
        raise RuntimeError("DDGS search failed" if not isinstance(envelope, dict)
                           else str(envelope.get("error") or "DDGS worker failed")[:160])
    items = envelope.get("results")
    if not isinstance(items, list):
        raise RuntimeError("DDGS worker returned non-list results")
    return items[:max(1, min(int(limit), 20))]


def _run_search(query, limit):
    from ddgs import DDGS

    # DDGS auto also selects encyclopedias. SEARCH needs Web discovery here.
    with DDGS(timeout=10) as client:
        hits = client.text(query, max_results=limit, backend=WEB_BACKENDS)
        return [{"provider": "ddgs", "title": str(hit.get("title") or ""),
                 "url": str(hit.get("href") or hit.get("url") or ""),
                 "source": "", "date": "", "snippet": str(hit.get("body") or "")}
                for hit in hits[:limit]]


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        results = _run_search(str(payload["query"]), max(1, min(int(payload["limit"]), 20)))
        json.dump({"ok": True, "results": results}, sys.stdout)
    except Exception as exc:
        json.dump({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}, sys.stdout)
        sys.exit(1)
