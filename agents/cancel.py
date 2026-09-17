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


class Cancelled(RuntimeError):
    """Arrêt demandé par l'humain."""


@contextmanager
def scope():
    """Délimite une exécution arrêtable. Imbriquée, elle garde le début de l'exécution englobante."""
    outer = _since.get()
    if outer is not None:
        yield outer
        return
    token = _since.set(time.time())
    try:
        yield _since.get()
    finally:
        _since.reset(token)


def requested() -> bool:
    since = _since.get()
    return since is not None and db.stop_requested(since)


def checkpoint(where: str = "") -> None:
    if requested():
        raise Cancelled("arrêt demandé par l'humain" + (f" ({where})" if where else ""))
