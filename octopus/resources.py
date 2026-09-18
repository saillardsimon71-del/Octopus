"""Environnement de ressources reelles : ce dont OCTOPUS dispose vraiment, et dans quel etat.

Ce module est un INVENTAIRE, pas une strategie. Il ne dit jamais quoi faire d'une ressource : il dit
ce qui existe, ce qui repond, ce qui manque, et ce qui exige un humain. Les decisions (utiliser,
combiner, abandonner) restent du ressort de la boucle strategique (`octopus.strategy`) et de la
boucle economique (`octopus.economy`).

Primitives reutilisees, rien de duplique :
- le journal et la file de taches portent les evenements et l'execution (`octopus.tasks`) ;
- la frontiere humaine est la demande humaine existante : la tache reprend seule apres la reponse ;
- une ressource qui devient un canal economique pointe vers `economic_channels` (l'argent reste la-bas) ;
- une valeur chiffree constatee est une preuve `octopus.strategy` (nature `observed`), pas un champ ici.

Regle d'honnetete : l'etat d'une ressource vient d'une sonde ou d'un constat humain. Sans constat,
elle reste `declared` : declaree par l'humain, pas encore verifiee.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import journal, paths, resource_probes, tasks

STATES = ("declared", "available", "degraded", "unavailable", "retired")
ACCESS = ("none", "observe", "act")            # meme vocabulaire que les canaux economiques
NATURES = ("observed", "unverified", "hypothesis")
# Frontieres qui exigent reellement un humain (acces ou realite juridique).
HUMAN_NEEDS = ("login", "oauth", "2fa", "captcha", "kyc", "signature", "bank_validation", "legal", "payment_method")
COLUMNS = ("key", "kind", "label", "locator", "business", "state", "access", "nature", "capabilities", "needs",
           "probe", "probe_args", "source_ref", "notes", "declared_at", "last_check_at", "last_check_ok",
           "last_check_detail", "channel_id", "created_by", "created_at", "updated_at")
_cache: dict[str, tuple[float, dict]] = {}


class ResourceError(ValueError):
    pass


def declarations_path() -> Path:
    return paths.home() / "resources.toml"


def declarations() -> dict[str, dict]:
    """Inventaire fourni par l'humain : `resources.toml` a la racine. Absent = inventaire vide."""
    path = declarations_path()
    if not path.exists():
        return {}
    stamp = path.stat().st_mtime
    cached = _cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    found: dict[str, dict] = {}
    for entry in raw.get("resource", []):
        key = str(entry.get("key", "")).strip().lower()
        if not key:
            raise ResourceError("chaque ressource declaree a besoin d'une cle")
        if key in found:
            raise ResourceError(f"ressource declaree deux fois : {key!r}")
        needs = [str(n).strip().lower() for n in entry.get("needs", []) if str(n).strip()]
        unknown = [n for n in needs if n not in HUMAN_NEEDS]
        if unknown:
            raise ResourceError(f"{key} : frontiere humaine inconnue {unknown} (attendu : {HUMAN_NEEDS})")
        found[key] = {
            "key": key,
            "kind": str(entry.get("kind", "autre")).strip().lower(),
            "label": str(entry.get("label") or key),
            "locator": entry.get("locator"),
            "business": entry.get("business"),
            "capabilities": sorted({str(c).strip().lower() for c in entry.get("capabilities", []) if str(c).strip()}),
            "needs": needs,
            "probe": (str(entry.get("probe")).strip().lower() if entry.get("probe") else None),
            "probe_args": {k: v for k, v in (entry.get("probe_args") or {}).items()},
            "notes": entry.get("notes"),
        }
    _cache[str(path)] = (stamp, found)
    return found


def _row(row) -> dict:
    item = dict(row)
    item["capabilities"] = json.loads(item.get("capabilities") or "[]")
    item["needs"] = json.loads(item.get("needs") or "[]")
    item["probe_args"] = json.loads(item.get("probe_args") or "{}")
    return item


def get(key: str) -> dict | None:
    rows = journal.query("SELECT * FROM resources WHERE key=?", (str(key).strip().lower(),))
    return _row(rows[0]) if rows else None


def list_resources(*, state: str | None = None, kind: str | None = None, capability: str | None = None,
                   business: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM resources WHERE 1=1", []
    if state:
        sql += " AND state=?"
        params.append(state)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if business:
        sql += " AND business=?"
        params.append(business)
    rows = [_row(r) for r in journal.query(sql + " ORDER BY kind, key", tuple(params))]
    if capability:
        rows = [r for r in rows if capability.strip().lower() in r["capabilities"]]
    return rows


def declare(key: str, kind: str, label: str, *, created_by: str, locator: str | None = None,
            capabilities: list[str] | None = None, needs: list[str] | None = None, probe: str | None = None,
            probe_args: dict | None = None, notes: str | None = None, business: str | None = None) -> int:
    """Ajoute une ressource a l'inventaire. Etat initial `declared` : rien n'est suppose disponible."""
    key = str(key).strip().lower()
    if not key:
        raise ResourceError("cle de ressource vide")
    now = time.time()
    with tasks._tx() as conn:
        existing = conn.execute("SELECT id FROM resources WHERE key=?", (key,)).fetchone()
        if existing:
            raise ResourceError(f"ressource deja declaree : {key!r}")
        resource_id = int(conn.execute(
            "INSERT INTO resources (key, kind, label, locator, business, capabilities, needs, probe, probe_args, "
            "notes, declared_at, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, str(kind).strip().lower() or "autre", str(label).strip() or key, locator, business,
             json.dumps(sorted({str(c).strip().lower() for c in capabilities or [] if str(c).strip()})),
             json.dumps([str(n).strip().lower() for n in needs or []]), probe,
             json.dumps(probe_args or {}, ensure_ascii=False), notes, now, str(created_by), now, now)).lastrowid)
        tasks._emit(conn, business or "octopus", None, "resources.declared", {"key": key, "kind": kind})
    return resource_id


def sync(*, created_by: str = "declaration") -> dict:
    """Aligne la base sur `resources.toml` : ajoute les nouvelles, met a jour ce qui est declare.

    L'etat constate (state, nature, acces, derniere sonde) n'est jamais ecrase par une declaration.
    """
    declared = declarations()
    added, updated = [], []
    for key, entry in declared.items():
        current = get(key)
        if current is None:
            declare(key, entry["kind"], entry["label"], created_by=created_by, locator=entry["locator"],
                    capabilities=entry["capabilities"], needs=entry["needs"], probe=entry["probe"],
                    probe_args=entry["probe_args"], notes=entry["notes"], business=entry["business"])
            added.append(key)
            continue
        changes = {}
        for field in ("kind", "label", "locator", "probe", "notes", "business"):
            if entry[field] is not None and entry[field] != current[field]:
                changes[field] = entry[field]
        if entry["capabilities"] != current["capabilities"]:
            changes["capabilities"] = json.dumps(entry["capabilities"])
        if entry["needs"] != current["needs"]:
            changes["needs"] = json.dumps(entry["needs"])
        if entry["probe_args"] != current["probe_args"]:
            changes["probe_args"] = json.dumps(entry["probe_args"], ensure_ascii=False)
        if changes:
            _write(key, changes)
            updated.append(key)
    return {"declared": sorted(declared), "added": added, "updated": updated,
            "unknown": sorted(r["key"] for r in list_resources() if r["key"] not in declared)}


def _write(key: str, changes: dict) -> None:
    changes["updated_at"] = time.time()
    unknown = set(changes) - set(COLUMNS)
    if unknown:
        raise ResourceError(f"champs inconnus : {sorted(unknown)}")
    with tasks._tx() as conn:
        conn.execute(f"UPDATE resources SET {', '.join(f'{k}=?' for k in changes)} WHERE key=?",
                     [*changes.values(), key])


def update(key: str, *, actor: str, state: str | None = None, access: str | None = None, nature: str | None = None,
           capabilities: list[str] | None = None, needs: list[str] | None = None, business: str | None = None,
           source_ref: str | None = None, notes: str | None = None) -> dict:
    """Constat ou decision sur une ressource. `act` reste une autorisation humaine."""
    resource = get(key)
    if resource is None:
        raise ResourceError(f"ressource inconnue : {key!r}")
    changes: dict = {}
    if state is not None:
        if state not in STATES:
            raise ResourceError(f"etat invalide : {state!r} (attendu : {STATES})")
        changes["state"] = state
    if access is not None:
        if access not in ACCESS:
            raise ResourceError(f"acces invalide : {access!r} (attendu : {ACCESS})")
        if access == "act" and actor != "human":
            raise ResourceError("seul un humain accorde le droit d'agir sur une ressource")
        changes["access"] = access
    if nature is not None:
        if nature not in NATURES:
            raise ResourceError(f"nature invalide : {nature!r} (attendu : {NATURES})")
        if nature == "observed" and not str(source_ref or resource["source_ref"] or "").strip():
            raise ResourceError("une ressource observee exige source_ref")
        changes["nature"] = nature
    if capabilities is not None:
        changes["capabilities"] = json.dumps(sorted({str(c).strip().lower() for c in capabilities if str(c).strip()}))
    if needs is not None:
        unknown = [n for n in needs if n not in HUMAN_NEEDS]
        if unknown:
            raise ResourceError(f"frontiere humaine inconnue : {unknown}")
        changes["needs"] = json.dumps([str(n).strip().lower() for n in needs])
    for field, value in (("business", business), ("source_ref", source_ref), ("notes", notes)):
        if value is not None:
            changes[field] = value
    if not changes:
        return resource
    _write(key, changes)
    with tasks._tx() as conn:
        tasks._emit(conn, resource["business"] or "octopus", None, "resources.updated",
                    {"key": key, "actor": actor, "fields": sorted(k for k in changes if k != "updated_at")})
    return get(key)


def check(key: str) -> dict:
    """Passe la sonde de la ressource et enregistre ce qu'elle constate (jamais ce qu'elle suppose)."""
    resource = get(key)
    if resource is None:
        raise ResourceError(f"ressource inconnue : {key!r}")
    result = resource_probes.run(resource)
    changes = {"last_check_at": time.time(), "last_check_ok": None if result.ok is None else int(result.ok),
               "last_check_detail": result.detail[:400]}
    if result.source_ref:
        changes["source_ref"] = result.source_ref
    if result.ok is True:
        changes["state"] = "degraded" if result.degraded else "available"
        changes["nature"] = "observed" if result.source_ref else resource["nature"]
    elif result.ok is False and resource["state"] != "retired":
        changes["state"] = "unavailable"
        changes["nature"] = "observed" if result.source_ref else resource["nature"]
    if result.capabilities:
        merged = sorted(set(resource["capabilities"]) | {c.lower() for c in result.capabilities})
        changes["capabilities"] = json.dumps(merged)
    _write(key, changes)
    with tasks._tx() as conn:
        tasks._emit(conn, resource["business"] or "octopus", None, "resources.checked",
                    {"key": key, "ok": result.ok, "detail": result.detail[:200],
                     "state": changes.get("state", resource["state"])})
    return get(key)


def check_all(*, kind: str | None = None) -> list[dict]:
    return [check(r["key"]) for r in list_resources(kind=kind) if r["state"] != "retired"]


def blocked() -> list[dict]:
    """Ressources declarees mais pas utilisables : etat non constate, indisponible, ou acces manquant."""
    out = []
    for resource in list_resources():
        if resource["state"] in ("available", "degraded") and resource["access"] != "none":
            continue
        if resource["state"] == "retired":
            continue
        reason = ("jamais constatee" if resource["state"] == "declared" else
                  "indisponible" if resource["state"] == "unavailable" else "acces non accorde")
        out.append({**resource, "reason": reason})
    return out


def request(key: str, need: str, question: str, *, created_by: str, business: str | None = None,
            label: str | None = None, kind: str = "autre") -> int:
    """Demande a l'humain de creer, connecter ou autoriser une ressource.

    Met en file la tache `resources.acquire` : elle pose la question, attend la reponse humaine,
    puis repasse la sonde. L'operation reprend donc seule, sans intervention supplementaire.
    """
    need = str(need).strip().lower()
    if need not in HUMAN_NEEDS and need != "create":
        raise ResourceError(f"frontiere humaine inconnue : {need!r} (attendu : {(*HUMAN_NEEDS, 'create')})")
    key = str(key).strip().lower()
    resource = get(key)
    if resource is None:
        declare(key, kind, label or key, created_by=created_by, needs=[need] if need in HUMAN_NEEDS else [],
                business=business)
        resource = get(key)
    return tasks.enqueue(resource["business"] or business or "octopus", "resources.acquire",
                         {"key": key, "need": need, "question": question, "requested_by": created_by})


def promote_to_channel(key: str, business: str, *, created_by: str) -> int:
    """La ressource devient un canal economique : l'argent et les actions restent geres par `economy`."""
    from . import economy
    resource = get(key)
    if resource is None:
        raise ResourceError(f"ressource inconnue : {key!r}")
    if resource["channel_id"]:
        return int(resource["channel_id"])
    channel_id = economy.add_channel(
        business, resource["kind"], resource["label"], created_by=created_by, locator=resource["locator"],
        capabilities=resource["capabilities"], nature=resource["nature"], source_ref=resource["source_ref"],
        notes=f"ressource {key}")
    _write(key, {"channel_id": channel_id, "business": business})
    return channel_id


def overview() -> dict:
    rows = list_resources()
    by_state: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    capabilities: dict[str, int] = {}
    for r in rows:
        if r["state"] in ("available", "degraded"):
            for capability in r["capabilities"]:
                capabilities[capability] = capabilities.get(capability, 0) + 1
    return {"total": len(rows), "by_state": by_state, "by_kind": by_kind,
            "capabilities_available": capabilities, "blocked": [b["key"] for b in blocked()],
            "declarations": len(declarations())}


def render(rows: list[dict]) -> str:
    lines = []
    for r in rows:
        access = r["access"]
        checked = time.strftime("%d/%m %H:%M", time.localtime(r["last_check_at"])) if r["last_check_at"] else "jamais"
        needs = ("; humain : " + ", ".join(r["needs"])) if r["needs"] else ""
        lines.append(f"{r['key']:22} {r['state']:12} acces {access:7} {r['kind']:12} sonde {checked}{needs}")
        if r["last_check_detail"]:
            lines.append(f"{'':22} -> {r['last_check_detail'][:90]}")
    return "\n".join(lines) or "inventaire vide (voir resources.toml puis « octopus resources sync »)"
