"""Entrée Runpod Serverless.

Le worker lui-même reste indépendant du fournisseur ; ce module ne fait que traduire
le format `event['input']` de Runpod vers le contrat OCTOPUS.
"""
from __future__ import annotations

from typing import Any, Mapping

from .worker import process


def handler(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("input") if isinstance(event, Mapping) else None
    if not isinstance(payload, Mapping):
        raise ValueError("Runpod event.input doit être un objet JSON")
    return process(payload)


def main() -> None:
    try:
        import runpod
    except ImportError as exc:
        raise SystemExit("dépendance runpod absente : installer video_worker/requirements.txt") from exc
    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
