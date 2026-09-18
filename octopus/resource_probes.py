"""Sondes de ressources : elles constatent, elles n'inventent pas.

Une sonde renvoie `ok=True` (constate que la ressource repond), `ok=False` (constate qu'elle ne
repond pas) ou `ok=None` (ne sait pas : l'etat de la ressource reste inchange). Une ressource dont
la disponibilite depend d'une connexion humaine (marketplace, reseau social, banque) n'a pas de
sonde automatique : elle reste `declared` jusqu'a ce qu'un humain ou un agent constate son etat.
"""
from __future__ import annotations

import os
import shutil
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

UA = {"User-Agent": "octopus-resources/1.0"}


@dataclass(frozen=True)
class ProbeResult:
    ok: bool | None
    detail: str
    source_ref: str | None = None
    capabilities: list[str] = field(default_factory=list)
    degraded: bool = False


def env_probe(resource: dict) -> ProbeResult:
    """La ressource depend de variables d'environnement (cle d'API, jeton)."""
    names = [n for n in resource.get("probe_args", {}).get("vars", []) if str(n).strip()]
    if not names:
        return ProbeResult(None, "aucune variable declaree")
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if missing:
        return ProbeResult(False, "variables absentes : " + ", ".join(missing))
    return ProbeResult(True, "variables presentes : " + ", ".join(names), source_ref="env:" + ",".join(names))


def http_probe(resource: dict) -> ProbeResult:
    """La ressource est une URL publique : on regarde si elle repond."""
    url = str(resource.get("probe_args", {}).get("url") or resource.get("locator") or "").strip()
    if not url:
        return ProbeResult(None, "aucune URL declaree")
    req = urllib.request.Request(url, headers=UA, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            code = response.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:  # DNS, TLS, reseau coupe : constat d'indisponibilite
        return ProbeResult(False, f"{type(exc).__name__}: {str(exc)[:120]}", source_ref=url)
    if 200 <= code < 400:
        return ProbeResult(True, f"HTTP {code}", source_ref=url)
    if code in (401, 403):  # la ressource existe mais l'acces demande une authentification
        return ProbeResult(True, f"HTTP {code} : authentification requise", source_ref=url, degraded=True)
    return ProbeResult(False, f"HTTP {code}", source_ref=url)


def dns_probe(resource: dict) -> ProbeResult:
    host = str(resource.get("probe_args", {}).get("host") or resource.get("locator") or "").strip()
    host = host.replace("https://", "").replace("http://", "").split("/")[0]
    if not host:
        return ProbeResult(None, "aucun domaine declare")
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError as exc:
        return ProbeResult(False, f"resolution impossible : {exc}", source_ref=f"dns:{host}")
    return ProbeResult(True, "resout vers " + ", ".join(addresses[:3]), source_ref=f"dns:{host}")


def command_probe(resource: dict) -> ProbeResult:
    names = [n for n in resource.get("probe_args", {}).get("commands", []) if str(n).strip()]
    if not names:
        return ProbeResult(None, "aucune commande declaree")
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        return ProbeResult(False, "commandes absentes : " + ", ".join(missing))
    return ProbeResult(True, "commandes presentes : " + ", ".join(names), source_ref="which:" + ",".join(names))


def host_probe(resource: dict) -> ProbeResult:
    """La machine qui execute OCTOPUS : elle est disponible par definition, on note ce qu'elle est."""
    import platform
    usage = shutil.disk_usage(os.path.expanduser("~"))
    detail = (f"{platform.system()} {platform.release()}, {os.cpu_count()} coeurs, "
              f"{usage.free / 2**30:.0f} Go libres")
    return ProbeResult(True, detail, source_ref=f"host:{socket.gethostname()}")


PROBES: dict[str, Callable[[dict], ProbeResult]] = {
    "env": env_probe,
    "http": http_probe,
    "dns": dns_probe,
    "command": command_probe,
    "host": host_probe,
}


def register(name: str, probe: Callable[[dict], ProbeResult]) -> None:
    PROBES[str(name).strip().lower()] = probe


def run(resource: dict) -> ProbeResult:
    name = str(resource.get("probe") or "").strip().lower()
    if not name:
        return ProbeResult(None, "aucune sonde : etat a constater par un humain ou un agent")
    probe = PROBES.get(name)
    if probe is None:
        return ProbeResult(None, f"sonde inconnue : {name}")
    try:
        return probe(resource)
    except Exception as exc:  # une sonde qui casse ne prouve rien sur la ressource
        return ProbeResult(None, f"sonde en erreur : {type(exc).__name__}: {str(exc)[:120]}")
