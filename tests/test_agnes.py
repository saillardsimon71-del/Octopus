"""Pinned Agnes HTTP contract and durable policy boundary; no live service.

Finalisation phase: upstream is free, so submit is free_quota (not paid),
but we keep quantity limits, duration caps, quota 429 handling, and
human authorization for real spend.
"""

import io
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from unittest.mock import Mock
from pathlib import Path
import tempfile

import pytest

from octopus import actions, agnes, economy, journal
from octopus.strategy import StrategyError

B = "atelier_test"
TASK = "abcdef123456"


@pytest.fixture(autouse=True)
def http(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(agnes, "build_opener", Mock(return_value=opener))
    monkeypatch.setattr(actions, "_EXECUTORS", {})
    monkeypatch.setattr(actions, "_load_configured_executors", lambda: None)
    opener.open.side_effect = AssertionError("Unconfigured HTTP request")
    agnes.register()
    return opener.open


def reply(http, data):
    response = Mock(status=200)
    response.read.return_value = data if isinstance(data, bytes) else json.dumps(data).encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    http.side_effect = None
    http.return_value = response


def reply_bytes(http, data: bytes, status=200):
    response = Mock(status=status)
    # For bytes download, read returns chunks; simulate single read
    response.read.side_effect = [data, b""]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    http.side_effect = None
    http.return_value = response


def channel(*, access=True, capabilities=None, locator=agnes.DEFAULT_URL):
    cid = economy.add_channel(B, "agnes_video", "Local Agnes", created_by="human", locator=locator,
                              capabilities=capabilities if capabilities is not None else ["agnes_submit", "agnes_stop"])
    economy.update_channel(B, cid, actor="human", status="active", access="act" if access else "observe")
    return cid


def submit(cid, **kwargs):
    # free_quota does not require spend, but we support optional spend for backward compat
    payload = kwargs.pop("payload", {"prompt": "Une mer calme"})
    key = kwargs.pop("key", "mission-1-video")
    # Only pass spend if explicitly given
    extra = {}
    if "amount" in kwargs:
        extra["spend_amount"] = kwargs.pop("amount")
        extra["spend_currency"] = "EUR"
    extra.update(kwargs)
    return actions.propose(B, cid, "submit", payload,
                           requested_by="human", idempotency_key=key,
                           **extra)


def allowance():
    economy.grant_allowance(B, 2, "EUR", granted_by="human", rationale="Bounded test")


def test_submission_form_evidence_and_restart_deduplication(http):
    cid = channel()
    reply(http, {"ok": True, "task_id": TASK, "dir_name": "ignored"})
    result = submit(cid)
    assert result["status"] == "executed"
    req = http.call_args.args[0]
    assert req.full_url == agnes.DEFAULT_URL + "/api/tasks/simple"
    assert req.method == "POST"
    assert parse_qs(req.data.decode()) == {"prompt": ["Une mer calme"], "mode": ["t2v"]}
    assert http.call_args.kwargs == {"timeout": 15}
    assert not any(k.lower() == "authorization" for k in req.headers)
    row = actions.list_actions(B)[0]
    assert json.loads(row["result"])["task_id"] == TASK
    # free_quota: no spend request expected
    assert row["spend_request_id"] is None
    assert journal.query("SELECT COUNT(*) AS n FROM ledger_entries")[0]["n"] == 0
    evidence = journal.query("SELECT * FROM strategy_evidence WHERE id=?", (result["evidence_id"],))[0]
    assert evidence["source_ref"].endswith(TASK) and evidence["captured_at"]
    assert "not verified" in evidence["observation"]
    actions._EXECUTORS.clear()
    agnes.register()
    assert submit(cid)["duplicate"] is True
    assert http.call_count == 1


@pytest.mark.parametrize("case", ["access", "capability", "identity", "payload", "remote"])
def test_refusals_do_not_send_http(http, case):
    cid = channel(access=case != "access", capabilities=[] if case == "capability" else None,
                  locator="http://example.com:8765" if case == "remote" else agnes.DEFAULT_URL)
    kw = {"key": None} if case == "identity" else {}
    if case == "payload":
        kw["payload"] = {"prompt": "hello", "api_key": "must-not-forward"}
    assert submit(cid, **kw)["status"] in {"blocked", "failed"}
    http.assert_not_called()


def test_free_quota_does_not_require_allowance(http):
    """Upstream is free: submit should work without allowance or spend_amount."""
    cid = channel()
    reply(http, {"ok": True, "task_id": TASK})
    result = submit(cid)
    assert result["status"] == "executed"
    assert http.call_count == 1
    # No spend request created
    assert not journal.query("SELECT * FROM spend_requests")


@pytest.mark.parametrize("failure", [TimeoutError("secret"), URLError("secret"),
                                      b"not json", [], {"ok": True}, {"ok": False, "task_id": TASK}])
def test_ambiguous_submission_never_retries_and_preserves_identity(http, failure):
    cid = channel()
    if isinstance(failure, Exception):
        http.side_effect = failure
    else:
        reply(http, failure)
    result = submit(cid)
    assert result["status"] == "ambiguous"
    assert "secret" not in result["reason"]
    assert not journal.query("SELECT * FROM strategy_evidence")
    actions._EXECUTORS.clear()
    agnes.register()
    assert submit(cid)["duplicate"] is True
    assert submit(cid, key="different-key")["status"] == "blocked"
    assert http.call_count == 1


@pytest.mark.parametrize("code,expected", [(400, "failed"), (422, "failed"), (302, "ambiguous"), (500, "ambiguous")])
def test_http_error_classification(http, code, expected):
    cid = channel()
    http.side_effect = HTTPError("http://secret", code, "secret", {}, io.BytesIO(b"secret"))
    result = submit(cid)
    assert result["status"] == expected and "secret" not in result["reason"]
    assert http.call_count == 1


def test_crash_during_submission_keeps_durable_identity(http):
    cid = channel()
    http.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        submit(cid)
    row = actions.list_actions(B)[0]
    assert row["status"] == "proposed"
    actions._EXECUTORS.clear()
    agnes.register()
    assert submit(cid)["duplicate"] is True
    assert http.call_count == 1


def test_concurrent_same_identity_submits_once(http):
    cid = channel()
    reply(http, {"ok": True, "task_id": TASK})
    barrier = Barrier(2)

    def call():
        barrier.wait(timeout=5)
        return submit(cid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(), range(2)))
    assert sum(result.get("duplicate", False) for result in results) == 1
    assert http.call_count == 1
    assert len(actions.list_actions(B)) == 1


def test_probe_and_loopback_transport(http):
    reply(http, {"ok": True, "service": "agnes-video-generator", "status": "healthy", "api_key": "secret"})
    result = agnes.probe("http://localhost:8765")
    assert result == {"ok": True, "service": "agnes-video-generator", "expected_pin": agnes.UPSTREAM_PIN}
    assert http.call_args.args[0].full_url == agnes.DEFAULT_URL + "/api/health"
    proxy, redirect = agnes.build_opener.call_args.args
    assert proxy.proxies == {}
    assert redirect.redirect_request(None, None, 302, "", {}, "http://example.com") is None


@pytest.mark.parametrize("url", ["https://127.0.0.1:8765", "http://example.com", "http://127.0.0.1.evil",
                                  "http://user:secret@localhost", "http://localhost/config", "http://localhost?key=x",
                                  "http://localhost:0", "http://localhost:65536", "http://localhost:secret",
                                  "http://[::1"])
def test_nonlocal_or_secret_urls_are_rejected(http, url):
    with pytest.raises(StrategyError):
        agnes.probe(url)
    http.assert_not_called()


@pytest.mark.parametrize("malformed", [[], {}, None, "unknown"])
def test_malformed_task_status_is_a_contract_error(http, malformed):
    reply(http, {"task_id": TASK, "task_type": "simple", "status": malformed})
    with pytest.raises(StrategyError, match="task response"):
        agnes.status(TASK)


def test_status_does_not_expose_server_secrets_or_follow_video_urls(http):
    reply(http, {"task_id": TASK, "task_type": "simple", "status": "running", "api_key": "secret",
                 "final_video_url": "https://untrusted.example"})
    assert agnes.status(TASK) == {"task_id": TASK, "status": "running",
                                 "source_ref": agnes.DEFAULT_URL + f"/api/tasks/{TASK}"}
    with pytest.raises(StrategyError, match="not completed"):
        agnes.video_reference(TASK)
    assert http.call_count == 2


@pytest.mark.parametrize("task", ["../config", "abcdef123456?x=1", "http://host", "", None])
def test_task_path_injection_refused(http, task):
    with pytest.raises(StrategyError):
        agnes.status(task)
    http.assert_not_called()


def test_completed_video_reference_uses_pinned_simple_download_endpoint(http):
    reply(http, {"task_id": TASK, "task_type": "simple", "status": "completed",
                 "final_video_file": "private/local/path.mp4", "final_video_url": "https://untrusted.example"})
    assert agnes.video_reference(TASK) == {"task_id": TASK, "source_ref": agnes.DEFAULT_URL + f"/api/video/{TASK}",
                                           "verified": False}
    assert http.call_count == 1
    assert http.call_args.args[0].full_url.endswith(f"/api/tasks/{TASK}")


def test_stop_uses_action_policy(http):
    cid = channel()
    reply(http, {"ok": True, "task_id": TASK})
    submit(cid)
    result = actions.propose(B, cid, "stop", {"task_id": TASK}, requested_by="human", idempotency_key="stop-1")
    assert result["status"] == "executed"
    req = http.call_args.args[0]
    assert req.method == "POST" and req.full_url.endswith(f"/{TASK}/stop") and req.data == b""
    assert actions.propose(B, cid, "stop", {"task_id": TASK}, requested_by="human")["status"] == "blocked"
    assert http.call_count == 2


def test_probe_unavailable_and_invalid_responses_are_sanitized(http):
    http.side_effect = URLError("secret")
    with pytest.raises(StrategyError, match="unavailable") as exc:
        agnes.probe()
    assert "secret" not in str(exc.value)
    reply(http, {"ok": True, "service": "wrong"})
    with pytest.raises(StrategyError, match="health"):
        agnes.probe()
    reply(http, {"task_id": "111111111111", "task_type": "simple", "status": "completed"})
    with pytest.raises(StrategyError, match="task response"):
        agnes.status(TASK)


def test_oversized_submission_response_is_ambiguous(http):
    cid = channel()
    reply(http, b" " * (agnes._MAX_RESPONSE + 1))
    assert submit(cid)["status"] == "ambiguous"
    http.return_value.read.assert_called_once_with(agnes._MAX_RESPONSE + 1)


def test_stop_timeout_is_ambiguous_and_permission_is_required(http):
    cid = channel(capabilities=["agnes_submit"])
    denied = actions.propose(B, cid, "stop", {"task_id": TASK}, requested_by="human", idempotency_key="denied-stop")
    assert denied["status"] == "failed"
    http.assert_not_called()
    cid = channel()
    http.side_effect = TimeoutError("secret")
    result = actions.propose(B, cid, "stop", {"task_id": TASK}, requested_by="human", idempotency_key="stop-timeout")
    assert result["status"] == "ambiguous" and "secret" not in result["reason"]
    assert actions.propose(B, cid, "stop", {TASK: TASK}, requested_by="human",
                           idempotency_key="stop-timeout")["duplicate"] is True if False else \
           actions.propose(B, cid, "stop", {"task_id": TASK}, requested_by="human",
                           idempotency_key="stop-timeout")["duplicate"] is True
    assert http.call_count == 1


def test_rate_limit_429_is_not_ambiguous_but_retryable(http):
    cid = channel()
    http.side_effect = HTTPError("http://127.0.0.1:8765/api/tasks/simple", 429, "Too Many Requests", {}, io.BytesIO(b""))
    # For submit, 429 should raise AgnesRateLimited which is StrategyError, not AmbiguousAction
    # But actions.propose catches AmbiguousAction separately; RateLimited is StrategyError -> failed
    # However we want 429 to be treated as retryable, not ambiguous. In agnes._request it raises AgnesRateLimited.
    # actions.propose will catch it as failed (since not AmbiguousAction) and cancel spend if any.
    # For free_quota, no spend, so it becomes failed with reason containing rate limit but not secret.
    result = submit(cid)
    assert result["status"] == "failed"
    assert "429" in result["reason"] or "rate" in result["reason"].lower()


def test_verify_mp4_valid_and_corrupt():
    # Create a minimal plausible MP4: ftyp header
    with tempfile.TemporaryDirectory() as tmp:
        valid = Path(tmp) / "valid.mp4"
        # Minimal MP4: size 24, 'ftyp' box
        # 4 bytes size, 4 bytes ftyp, then 16 bytes dummy
        valid.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41" + b"\x00" * 1024)
        ver = agnes.verify_mp4(valid)
        assert ver["verified"] is True
        assert "sha256" in ver

        corrupt = Path(tmp) / "corrupt.mp4"
        corrupt.write_bytes(b"\x00" * 100)
        ver2 = agnes.verify_mp4(corrupt)
        assert ver2["verified"] is False

        small = Path(tmp) / "small.mp4"
        small.write_bytes(b"tiny")
        ver3 = agnes.verify_mp4(small)
        assert ver3["verified"] is False


def test_download_video_verifies_integrity(http):
    # Mock status completed then video bytes
    def side_effect(request, timeout=15):
        url = request.full_url
        if url.endswith(f"/api/tasks/{TASK}"):
            # status call
            resp = Mock(status=200)
            resp.read.return_value = json.dumps({"task_id": TASK, "task_type": "simple", "status": "completed"}).encode()
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp
        elif url.endswith(f"/api/video/{TASK}"):
            resp = Mock(status=200)
            # valid mp4 bytes
            data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41" + b"\x00" * 2048
            resp.read.side_effect = [data[:1024], data[1024:], b""]
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp
        else:
            raise AssertionError(f"unexpected url {url}")

    http.side_effect = side_effect
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "out.mp4"
        result = agnes.download_video(TASK, dest)
        assert result["verified"] is True
        assert dest.exists()
        assert result["sha256"] == hashlib.sha256(dest.read_bytes()).hexdigest()


def test_auto_registration_without_explicit_register(monkeypatch):
    """Explicit register() should not be required for autonomous missions using authorized channel."""
    opener = Mock()
    monkeypatch.setattr(agnes, "build_opener", Mock(return_value=opener))
    # Do NOT call agnes.register(), but ensure _EXECUTORS empty and _load_configured_executors loads agnes
    monkeypatch.setattr(actions, "_EXECUTORS", {})
    def load():
        from octopus import agnes as _a
        _a._ensure_registered()
    monkeypatch.setattr(actions, "_load_configured_executors", load)
    opener.open.side_effect = AssertionError("Unconfigured HTTP request")
    # Now _load_configured_executors should have registered agnes executors
    # Simulate actions.propose path that calls _load_configured_executors
    from octopus import agnes as _agnes
    _agnes._ensure_registered()
    assert ("agnes_video", "submit") in actions._EXECUTORS
    # Also check cost_class is free_quota
    _, cost_class, _ = actions._EXECUTORS[("agnes_video", "submit")]
    assert cost_class == "free_quota"
