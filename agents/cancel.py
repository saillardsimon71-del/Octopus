"""Arrêt demandé par l'humain, respecté par les cycles, missions, agents et sous-processus (audit C7).

Le bouton Arrêter horodate la demande (`db.request_stop`). Une exécution s'arrête si la demande
est postérieure à son début : un arrêt ancien ne bloque pas un nouveau lancement, et un
nouveau lancement n'efface plus l'arrêt demandé sur une exécution en cours.
"""
from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager

from . import db

_since: contextvars.ContextVar[float | None] = contextvars.ContextVar("podalux_stop_since", default=None)
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("mission_deadline", default=None)


class Cancelled(RuntimeError):
    """Arrêt demandé par l'humain."""


@contextmanager
def scope(max_duration_s: float | None = None):
    """Délimite une exécution arrêtable. Imbriquée, elle garde le début de l'exécution englobante."""
    outer = _since.get()
    if outer is not None:
        deadline = _deadline.get()
        if max_duration_s is None or (deadline is not None and deadline <= time.monotonic() + max_duration_s):
            yield outer
            return
        deadline_token = _deadline.set(time.monotonic() + max_duration_s)
        try:
            yield outer
        finally:
            _deadline.reset(deadline_token)
        return
    token = _since.set(time.time())
    deadline_token = _deadline.set(time.monotonic() + max_duration_s if max_duration_s is not None else None)
    try:
        yield _since.get()
    finally:
        _deadline.reset(deadline_token)
        _since.reset(token)


def requested() -> bool:
    since = _since.get()
    return timed_out() or (since is not None and db.stop_requested(since))


def timed_out() -> bool:
    deadline = _deadline.get()
    return deadline is not None and time.monotonic() >= deadline


def checkpoint(where: str = "") -> None:
    if requested():
        reason = "durée maximale atteinte" if timed_out() else "arrêt demandé par l'humain"
        raise Cancelled(reason + (f" ({where})" if where else ""))
