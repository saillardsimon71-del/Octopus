"""Executor SMTP borné pour une première action économique réelle.

Activation explicite requise :
- OCTOPUS_ENABLE_SMTP_EXECUTOR=1
- OCTOPUS_SMTP_HOST
- OCTOPUS_SMTP_USERNAME
- OCTOPUS_SMTP_PASSWORD
- OCTOPUS_SMTP_FROM

Le secret reste uniquement dans l'environnement. TLS est obligatoire.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

_REQUIRED = ("OCTOPUS_SMTP_HOST", "OCTOPUS_SMTP_USERNAME", "OCTOPUS_SMTP_PASSWORD", "OCTOPUS_SMTP_FROM")


def configured() -> bool:
    return os.environ.get("OCTOPUS_ENABLE_SMTP_EXECUTOR", "").strip() == "1" and all(
        os.environ.get(name, "").strip() for name in _REQUIRED
    )


def _address(value, name: str) -> str:
    raw = str(value or "").strip()
    if "\r" in raw or "\n" in raw:
        raise ValueError(f"{name} contient un retour à la ligne")
    _, address = parseaddr(raw)
    if not address or "@" not in address or address != raw:
        raise ValueError(f"{name} doit être une adresse email simple")
    return address


def _settings() -> dict:
    if os.environ.get("OCTOPUS_ENABLE_SMTP_EXECUTOR", "").strip() != "1":
        raise RuntimeError("executor SMTP désactivé (OCTOPUS_ENABLE_SMTP_EXECUTOR=1 requis)")
    missing = [name for name in _REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("configuration SMTP incomplète : " + ", ".join(missing))
    security = os.environ.get("OCTOPUS_SMTP_SECURITY", "starttls").strip().lower()
    if security not in {"starttls", "ssl"}:
        raise RuntimeError("OCTOPUS_SMTP_SECURITY doit être starttls ou ssl")
    raw_port = os.environ.get("OCTOPUS_SMTP_PORT", "").strip()
    try:
        port = int(raw_port) if raw_port else (465 if security == "ssl" else 587)
    except ValueError as exc:
        raise RuntimeError("OCTOPUS_SMTP_PORT invalide") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("OCTOPUS_SMTP_PORT hors limites")
    return {
        "host": os.environ["OCTOPUS_SMTP_HOST"].strip(),
        "port": port,
        "username": os.environ["OCTOPUS_SMTP_USERNAME"].strip(),
        "password": os.environ["OCTOPUS_SMTP_PASSWORD"],
        "from": _address(os.environ["OCTOPUS_SMTP_FROM"], "OCTOPUS_SMTP_FROM"),
        "security": security,
    }


def send_email(channel: dict, payload: dict) -> dict:
    """Envoie exactement un email texte et retourne une preuve SMTP traçable."""
    cfg = _settings()
    recipient = _address(payload.get("to"), "to")
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("text") or "")
    if not subject or "\r" in subject or "\n" in subject:
        raise ValueError("subject requis, sur une seule ligne")
    if not body.strip():
        raise ValueError("text requis")
    if len(subject) > 200 or len(body) > 50_000:
        raise ValueError("email hors limites de taille")

    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    reply_to = payload.get("reply_to")
    if reply_to:
        msg["Reply-To"] = _address(reply_to, "reply_to")
    msg.set_content(body)

    context = ssl.create_default_context()
    if cfg["security"] == "ssl":
        client = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20, context=context)
    else:
        client = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
    with client:
        if cfg["security"] == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(cfg["username"], cfg["password"])
        refused = client.send_message(msg, from_addr=cfg["from"], to_addrs=[recipient])
    if refused:
        raise RuntimeError(f"serveur SMTP a refusé le destinataire : {recipient}")

    message_id = str(msg["Message-ID"])
    return {
        "observation": f"email accepté par {cfg['host']} pour {recipient}",
        "source_ref": f"smtp:{message_id}",
        "metric": "outreach_sent",
        "value": 1,
        "unit": "email",
    }


def register() -> None:
    if configured():
        from . import actions
        actions.register_executor(
            "email", "send", send_email, cost_class="free_quota", requires_idempotency=True
        )
