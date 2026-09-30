"""Backend Hermes (agent-browser) et proxy de garde, hors navigateur, sans réseau externe."""
from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import stat
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agents import agent_browser, config, web_guard

PROJECT = Path(__file__).resolve().parent.parent


# --- couche de commande adaptée de Hermes ---------------------------------------------------------

def test_shim_safe_args_only_rewrites_cmd_shims():
    assert agent_browser.shim_safe_args("/bin/agent-browser", "fill", ["@e1", "a\nb"]) == ("fill", ["@e1", "a\nb"], None)
    cmd, args, stdin = agent_browser.shim_safe_args("C:/npm/agent-browser.cmd", "fill", ["@e1", "50%\nOK"])
    assert (cmd, args) == ("batch", []) and json.loads(stdin) == [["fill", "@e1", "50%\nOK"]]
    cmd, args, stdin = agent_browser.shim_safe_args("x.CMD", "eval", ["1+1"])
    assert cmd == "eval" and args[0] == "--base64" and stdin is None


def test_output_interpretation_is_strict():
    assert agent_browser.interpret_output("close", "", "", 0)["success"] is True
    assert agent_browser.interpret_output("click", "", "", 0)["success"] is False
    assert agent_browser.interpret_output("click", "", "boom", 1)["error"] == "boom"
    assert agent_browser.interpret_output("click", "pas du json", "", 0)["success"] is False
    ok = agent_browser.interpret_output("open", '{"success":true,"data":{"url":"u"}}', "", 0)
    assert ok["data"]["url"] == "u"
    assert agent_browser.unwrap_batch_result([{"success": True, "result": {"a": 1}}], "fill")["data"] == {"a": 1}
    assert agent_browser.unwrap_batch_result([{}, {}], "fill")["success"] is False


def test_child_env_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://evil:1")
    monkeypatch.setenv("AGENT_BROWSER_ALLOWED_DOMAINS", "*")
    monkeypatch.setenv("AGENT_BROWSER_ARGS", "--proxy-server=direct://")
    env = agent_browser.child_env("/tmp/sock")
    assert "DEEPSEEK_API_KEY" not in env and "HTTPS_PROXY" not in env
    assert "AGENT_BROWSER_ALLOWED_DOMAINS" not in env and "AGENT_BROWSER_ARGS" not in env
    assert env["AGENT_BROWSER_SOCKET_DIR"] == "/tmp/sock" and env.get("PATH") == os.environ.get("PATH")


def test_binary_discovery_never_installs(monkeypatch, tmp_path):
    monkeypatch.delenv("OCTOPUS_AGENT_BROWSER", raising=False)
    monkeypatch.setattr(agent_browser.shutil, "which", lambda name: None)
    assert agent_browser.find_binary() is None
    local = config.DATA_DIR / "bin" / agent_browser.binary_name()
    local.parent.mkdir(parents=True)
    local.write_bytes(b"")
    assert agent_browser.find_binary() == str(local)
    monkeypatch.setenv("OCTOPUS_AGENT_BROWSER", str(tmp_path / "absent"))
    assert agent_browser.find_binary() is None
    monkeypatch.setenv("OCTOPUS_CHROMIUM", str(tmp_path / "absent"))
    with pytest.raises(agent_browser.BackendUnavailable, match="install_agent_browser"):
        agent_browser.require_backend()


def test_platform_names():
    assert agent_browser.binary_name("win32-x64") == "agent-browser-win32-x64.exe"
    assert agent_browser.binary_name("linux-arm64") == "agent-browser-linux-arm64"


def _fake_backend(monkeypatch, tmp_path, script: str) -> Path:
    binary = tmp_path / "agent-browser"
    binary.write_text("#!" + sys.executable + "\n" + script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    chromium = tmp_path / "chromium"
    chromium.write_bytes(b"")
    monkeypatch.setenv("OCTOPUS_AGENT_BROWSER", str(binary))
    monkeypatch.setenv("OCTOPUS_CHROMIUM", str(chromium))
    monkeypatch.setenv("OCTOPUS_BROWSER_ARGS", "--no-sandbox")
    return binary


@pytest.mark.skipif(os.name == "nt", reason="faux binaire POSIX (shebang)")
def test_session_argv_forces_the_guard_proxy_and_never_retries(monkeypatch, tmp_path):
    log = tmp_path / "argv.jsonl"
    _fake_backend(monkeypatch, tmp_path, f"""
import json, sys, os
open({str(log)!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({{"success": True, "data": {{"url": "http://x/", "sock": os.environ.get("AGENT_BROWSER_SOCKET_DIR")}}}}))
""")
    session = agent_browser.Session("oct-t1", proxy_url="http://127.0.0.1:5555", download_dir=tmp_path)
    result = session.run("open", ["http://x/"])
    assert result["success"] and result["data"]["sock"].endswith("oct-t1")
    argv = json.loads(log.read_text().splitlines()[0])
    launch = argv[argv.index("--args") + 1].split(",")
    assert "--proxy-server=http://127.0.0.1:5555" in launch and "--proxy-bypass-list=<-loopback>" in launch
    assert "--disable-quic" in launch and "--no-sandbox" in launch
    assert argv[argv.index("--session") + 1] == "oct-t1" and "--headed" not in argv
    assert argv[-3:] == ["--json", "open", "http://x/"]
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.skipif(os.name == "nt", reason="faux binaire POSIX (shebang)")
def test_session_timeout_and_abort_are_indeterminate(monkeypatch, tmp_path):
    _fake_backend(monkeypatch, tmp_path, "import time\ntime.sleep(30)\n")
    session = agent_browser.Session("oct-t2", proxy_url="http://127.0.0.1:1")
    started = time.monotonic()
    timed = session.run("click", ["@e1"], timeout=0.5)
    assert timed["indeterminate"] is True and time.monotonic() - started < 10
    aborted = session.run("click", ["@e1"], timeout=20, should_abort=lambda: True)
    assert aborted["indeterminate"] is True and "interrompue" in aborted["error"]


def test_snapshot_view_helpers():
    raw = '- list\n  - ListMarker "• "\n  - StaticText " · "\n  - StaticText "DV-1 enregistrée"'
    assert agent_browser.prune_snapshot(raw) == '- list\n  - StaticText "DV-1 enregistrée"'
    long = "\n".join(f'- button "b{i}" [ref=e{i}]' for i in range(500))
    cut = agent_browser.truncate_snapshot(long, 1000)
    assert len(cut) <= 1000 and cut.endswith("browser_snapshot(full=true)]") and "[ref=e0]" in cut
    assert agent_browser.redact("token sk-abcdefghijklmnop1234 ok") == "token sk-a…[masqué] ok"
    assert agent_browser.contains_secret("https://x/?k=ghp_abcdefghijklmnop%31234")


def test_install_script_verifies_the_pinned_archive(monkeypatch, tmp_path):
    sys.path.insert(0, str(PROJECT / "scripts"))
    import install_agent_browser
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w:gz") as archive:
        data = b"binary"
        info = tarfile.TarInfo(f"package/bin/{agent_browser.binary_name('win32-x64')}")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    tgz = tmp_path / "ab.tgz"
    tgz.write_bytes(blob.getvalue())
    assert install_agent_browser.main(["--tarball", str(tgz), "--dest", str(tmp_path / "bin")]) == 2
    monkeypatch.setattr(agent_browser, "AGENT_BROWSER_SHA256", hashlib.sha256(blob.getvalue()).hexdigest())
    assert install_agent_browser.main(["--tarball", str(tgz), "--dest", str(tmp_path / "bin"),
                                       "--target", "win32-x64"]) == 0
    assert (tmp_path / "bin" / "agent-browser-win32-x64.exe").read_bytes() == b"binary"


# --- proxy de garde ---------------------------------------------------------------------------------

@pytest.fixture
def upstream():
    class Page(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            body = f"ok {self.path} host={self.headers.get('Host')} pc={self.headers.get('Proxy-Connection')}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def echo():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=lambda c=conn: _echo(c), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    yield sock.getsockname()[1]
    sock.close()


def _echo(conn):
    try:
        while data := conn.recv(4096):
            conn.sendall(data)
    except OSError:
        pass


def _request(proxy: web_guard.GuardProxy, raw: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", int(proxy.url.rsplit(":", 1)[1])), timeout=5) as sock:
        sock.sendall(raw)
        chunks = b""
        while chunk := sock.recv(4096):
            chunks += chunk
        return chunks


def test_proxy_relays_allowed_http_and_strips_proxy_headers(upstream):
    origin = f"http://127.0.0.1:{upstream}"
    with web_guard.GuardProxy(lambda url: url.startswith(origin),
                              allow_private=lambda host, port: port == upstream) as proxy:
        raw = (f"GET {origin}/page?q=1 HTTP/1.1\r\nHost: 127.0.0.1:{upstream}\r\n"
               "Proxy-Connection: keep-alive\r\n\r\n").encode()
        reply = _request(proxy, raw)
    assert b"200" in reply.split(b"\r\n")[0] and b"ok /page?q=1" in reply and b"pc=None" in reply


def test_proxy_refuses_before_any_connection(upstream):
    proxy = web_guard.GuardProxy(lambda url: False)
    connects = []
    proxy._connect = lambda host, port: connects.append((host, port))
    with proxy:
        reply = _request(proxy, f"GET http://127.0.0.1:{upstream}/ HTTP/1.1\r\n\r\n".encode())
        tunnel = _request(proxy, b"CONNECT exfil.example:443 HTTP/1.1\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 403") and tunnel.startswith(b"HTTP/1.1 403") and connects == []
    assert proxy.blocked == [f"http://127.0.0.1:{upstream}/", "https://exfil.example/"]


def test_proxy_never_dials_private_addresses_unless_declared(upstream):
    proxy = web_guard.GuardProxy(lambda url: True)
    with pytest.raises(web_guard.BrowseRefused):
        proxy._connect("127.0.0.1", upstream)
    with proxy:
        reply = _request(proxy, f"GET http://127.0.0.1:{upstream}/ HTTP/1.1\r\n\r\n".encode())
    assert reply.startswith(b"HTTP/1.1 502")


def test_account_read_cuts_existing_public_tunnels(echo):
    state = {"tainted": False}

    def guard(url):
        if "account.test" in url:
            state["tainted"] = True
            return True
        return not state["tainted"]

    proxy = web_guard.GuardProxy(guard)
    proxy._connect = lambda host, port: socket.create_connection(("127.0.0.1", echo))
    with proxy:
        port = int(proxy.url.rsplit(":", 1)[1])
        public = socket.create_connection(("127.0.0.1", port), timeout=5)
        public.sendall(b"CONNECT public.test:443 HTTP/1.1\r\n\r\n")
        assert public.recv(1024).startswith(b"HTTP/1.1 200")
        public.sendall(b"ping")
        assert public.recv(1024) == b"ping"
        account = socket.create_connection(("127.0.0.1", port), timeout=5)
        account.sendall(b"CONNECT account.test:443 HTTP/1.1\r\n\r\n")
        assert account.recv(1024).startswith(b"HTTP/1.1 200")
        try:
            public.sendall(b"leak")
            leaked = public.recv(1024)
        except OSError:
            leaked = b""
        assert leaked == b""  # tunnel public coupé avant tout octet du compte
        account.sendall(b"hello")
        assert account.recv(1024) == b"hello"
        public.close()
        account.close()
    assert "https://public.test/" in proxy.blocked
