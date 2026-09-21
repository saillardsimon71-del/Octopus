from __future__ import annotations

import json
import smtplib

import pytest

from octopus import smtp_executor
from octopus.strategy import StrategyError


ENV = {
    "OCTOPUS_ENABLE_SMTP_EXECUTOR": "1",
    "OCTOPUS_SMTP_HOST": "smtp.example.test",
    "OCTOPUS_SMTP_PORT": "587",
    "OCTOPUS_SMTP_USERNAME": "sender@example.test",
    "OCTOPUS_SMTP_PASSWORD": "secret-test-only",
    "OCTOPUS_SMTP_FROM": "sender@example.test",
    "OCTOPUS_SMTP_SECURITY": "starttls",
}


def _env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def _channel(recipient="prospect@example.test"):
    return {
        "name": "Prospect autorisé",
        "locator": f"mailto:{recipient}",
        "capabilities": json.dumps([smtp_executor.CAPABILITY]),
    }


def test_smtp_executor_is_fail_closed_until_explicitly_enabled(monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert smtp_executor.configured() is False
    with pytest.raises(RuntimeError, match="désactivé"):
        smtp_executor.send_email(
            _channel(),
            {"to": "prospect@example.test", "subject": "Bonjour", "text": "Texte"},
        )


def test_smtp_executor_uses_tls_auth_and_returns_traceable_message_id(monkeypatch):
    _env(monkeypatch)
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append(("close",))

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context):
            assert context is not None
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, msg, from_addr, to_addrs):
            calls.append(("send", from_addr, tuple(to_addrs), msg["Subject"], msg.get_content().strip()))
            return {}

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    result = smtp_executor.send_email(
        _channel(),
        {
            "to": "prospect@example.test",
            "subject": "Audit accessibilité",
            "text": "Bonjour, test.",
            "metric": "emails_accepted",
        },
    )

    assert calls[0] == ("connect", "smtp.example.test", 587, 20)
    assert ("starttls",) in calls
    assert ("login", "sender@example.test", "secret-test-only") in calls
    assert ("send", "sender@example.test", ("prospect@example.test",), "Audit accessibilité", "Bonjour, test.") in calls
    assert result["source_ref"].startswith("smtp:<") and result["source_ref"].endswith(">")
    assert result["metric"] == "emails_accepted" and result["value"] == 1 and result["unit"] == "email"


def test_smtp_executor_refuses_recipient_outside_channel(monkeypatch):
    _env(monkeypatch)
    with pytest.raises(StrategyError, match="destinataire différent"):
        smtp_executor.send_email(
            _channel("allowed@example.test"),
            {"to": "other@example.test", "subject": "Bonjour", "text": "Texte"},
        )


@pytest.mark.parametrize("channel,payload,message", [
    (
        {"locator": "mailto:a@example.test", "capabilities": "[]"},
        {"to": "a@example.test", "subject": "ok", "text": "x"},
        "capability",
    ),
    (
        _channel("a@example.test"),
        {"to": "a@example.test\nbcc:x@example.test", "subject": "ok", "text": "x"},
        "retour à la ligne",
    ),
    (
        _channel("a@example.test"),
        {"to": "a@example.test", "subject": "bad\nheader", "text": "x"},
        "subject requis",
    ),
    (
        _channel("a@example.test"),
        {"to": "a@example.test", "subject": "ok", "text": ""},
        "text requis",
    ),
])
def test_smtp_executor_rejects_unsafe_or_invalid_messages(monkeypatch, channel, payload, message):
    _env(monkeypatch)
    with pytest.raises(StrategyError, match=message):
        smtp_executor.send_email(channel, payload)
