"""Espace de travail navigateur d'OCTOPUS, sur le backend navigateur de Hermes.

Hermes fournit l'infrastructure d'interaction (CLI `agent-browser` + Chromium, refs `@eN` issues
de l'arbre d'accessibilité, commandes navigate/snapshot/click/type/press/scroll/back : voir
`agents/agent_browser.py`). OCTOPUS garde tout le reste : l'IA de la mission observe la page,
décide librement de l'action suivante et appelle ces outils ; ce module applique en code ce
qu'aucun prompt ne peut garantir :

- permissions : lire (naviguer, observer, suivre un lien, télécharger) est libre dans le garde
  web ; modifier une page (saisir, choisir, cocher, cliquer un bouton, valider) exige un canal
  économique `active` dont l'humain a accordé l'accès `act` pour ce site (`browser_workspace`) ;
- anti-exfiltration : tout le trafic Chromium passe par `web_guard.GuardProxy` (taint des comptes,
  réseaux privés refusés), aucun secret n'est saisi ni renvoyé au modèle, pas de champ de mot de
  passe/paiement ;
- économie : plafond de commandes et d'actions à effet par espace de travail ;
- persistance : chaque action à effet est écrite dans `channel_actions` AVANT d'être exécutée
  (état `proposed`), puis `executed`, `verified`, `failed` ou `ambiguous`. Le point de reprise de
  la page est un `task_step` de la tâche durable.

Reprise sans répétition aveugle : une action est identifiée par une empreinte (canal, page,
cible, valeurs saisies sur la page). Après une interruption, une action restée `proposed` devient
`ambiguous` ; toute tentative de refaire la même empreinte est refusée tant que l'effet n'a pas
été vérifié sur la page (`browser_verify`) ou tranché par l'humain
(`python -m octopus browser resolve`).
"""
from __future__ import annotations

import atexit
import contextvars
import hashlib
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from agents import agent_browser, web_guard
from agents import config as agents_config

from . import actions, journal, strategy, tasks

CAPABILITY = "browser_workspace"
CHECKPOINT_KEY = "browser.workspace"
LAB_ORIGINS_ENV = "OCTOPUS_BROWSER_LAB_ORIGINS"
MAX_COMMANDS = 300
MAX_EFFECTS = 30
SNAPSHOT_CHARS = 6000
FULL_SNAPSHOT_CHARS = 15000
PAGE_TEXT_CHARS = 20000
VERIFY_WAIT_S = 5.0
UPLOAD_MAX_BYTES = 25 * 1024 * 1024
UNRESOLVED = ("proposed", "ambiguous", "executed")

READ_SAFE_KEYS = frozenset({"Tab", "Shift+Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
                            "PageUp", "PageDown", "Home", "End", "Space"})
_REF_RE = re.compile(r"^@?(e\d+)$")
# Un lien est une navigation (lecture) sauf si son libellé annonce un effet.
_RISKY_LINK_RE = re.compile(
    r"supprim|delete|remove|envoy|send|publi|payer|pay\b|paiement|achet|buy|checkout|confirm|valid|submit|"
    r"soumet|abonn|subscri|désinscri|desinscri|unsubscri|annul|cancel|command|order|accept|invit|partag|share|"
    r"like|follow|suivre|sign|logout|déconnex|deconnex|retirer|withdraw|transf", re.IGNORECASE)
# Champs réservés à l'humain : identifiants, second facteur, paiement.
_HUMAN_ONLY_FIELD_RE = re.compile(
    r"mot de passe|password|passcode|passphrase|\b2fa\b|\botp\b|code de (vérification|verification|sécurité)|"
    r"verification code|security code|\bcvv\b|\bcvc\b|cryptogramme|numéro de carte|card number|\biban\b|\bbic\b",
    re.IGNORECASE)
# Échecs connus survenus AVANT tout clic (aucun effet possible) ; tout le reste est ambigu.
_PRE_DISPATCH_RE = re.compile(r"not found|no element|unknown ref|invalid ref|stale|not visible|not attached|"
                              r"strict mode|resolved to \d+ elements|no node|could not find", re.IGNORECASE)


class Refused(PermissionError):
    """Refus de politique : aucun effet n'a eu lieu."""


@dataclass(frozen=True)
class Scope:
    key: str
    business: str
    task_id: int | None


def current_scope() -> Scope:
    """Un espace de travail par tâche durable (même tâche = même session, même reprise)."""
    run = journal.current_run()
    if run is None:
        return Scope("local", "octopus", None)
    task_id = None
    try:
        rows = journal.query("SELECT id FROM tasks WHERE run_id=? ORDER BY id DESC LIMIT 1", (run.root_id,))
        task_id = int(rows[0]["id"]) if rows else None
    except Exception:
        task_id = None
    return Scope(f"t{task_id}" if task_id else f"r{run.root_id}", run.business, task_id)


def lab_origins() -> set[str]:
    """Applications locales explicitement déclarées par l'opérateur (tests, outils internes)."""
    raw = __import__("os").environ.get(LAB_ORIGINS_ENV, "")
    return {_origin(item) for item in raw.split(",") if item.strip() and _origin(item)}


def _origin(url: str) -> str:
    parts = urlsplit(str(url).strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{parts.hostname.lower()}:{port}"


def _page_key(url: str) -> str:
    parts = urlsplit(str(url or ""))
    return f"{_origin(url)}{parts.path or '/'}"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _files_dir(business: str) -> Path:
    return agents_config.DATA_DIR / "browser_files" / re.sub(r"[^A-Za-z0-9_-]", "_", business)


def outbox_dir(business: str) -> Path:
    return _files_dir(business) / "outbox"


def _safe_filename(name: str) -> str:
    name = str(name or "").strip()
    if not name or name in {".", ".."} or re.search(r"[\\/:\x00]", name) or agent_browser.contains_secret(name):
        raise Refused("nom de fichier invalide (pas de chemin, pas de secret)")
    return name[:120]


class Workspace:
    """Session navigateur persistante d'une tâche, pilotée par l'IA via les outils browser_*."""

    def __init__(self, scope: Scope, state: web_guard.BrowseState, *, session_factory=None):
        self.scope = scope
        self.state = state
        self.lab = lab_origins()
        self.account_mode = False
        self.anonymous_domains: set[str] = set()
        self._session_factory = session_factory or agent_browser.Session
        self._session = None
        self._proxy: web_guard.GuardProxy | None = None
        self._refs: dict[str, dict] = {}
        self._url = ""
        self._typed: dict[str, dict[str, str]] = {}
        self._commands = 0
        self._effects = 0
        self._resume: dict | None = None
        self._resume_reported = False

    # -- cycle de vie ------------------------------------------------------------------------
    def _is_lab(self, url: str) -> bool:
        return bool(self.lab) and _origin(url) in self.lab

    def _guard(self, url: str) -> bool:
        if self._is_lab(url):
            return not self.state.account_read
        return web_guard.request_allowed(url, self.state, account_context=self.account_mode,
                                         anonymous_account_domains=tuple(self.anonymous_domains))

    def _allow_private(self, host: str, port: int) -> bool:
        return any(_origin(f"{scheme}://{host}:{port}/") in self.lab for scheme in ("http", "https"))

    def _start(self) -> None:
        if self._session is not None:
            return
        self._proxy = web_guard.GuardProxy(self._guard, allow_private=self._allow_private).start()
        inbox = _files_dir(self.scope.business) / "inbox" / self.scope.key
        inbox.mkdir(parents=True, exist_ok=True)
        self._inbox = inbox
        profile = (agents_config.DATA_DIR / "browser_profile") if self.account_mode else None
        headed = self.account_mode and __import__("os").environ.get("OCTOPUS_BROWSER_HEADLESS") != "1"
        try:
            self._session = self._session_factory(f"oct-{self.scope.key}", proxy_url=self._proxy.url,
                                                  profile_dir=profile, headed=headed, download_dir=inbox)
            self._session.close()  # démon périmé d'un processus interrompu : repartir d'un état propre
        except Exception:
            self._proxy.stop()
            self._proxy, self._session = None, None
            raise
        self._reconcile()

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None

    def _reconcile(self) -> None:
        """Reprise : une action restée `proposed` a pu agir sans confirmation -> `ambiguous`."""
        rows = journal.query(
            "SELECT id, action FROM channel_actions WHERE business=? AND status='proposed' AND idempotency_key LIKE ?",
            (self.scope.business, f"browser:%:{self.scope.key}"))
        with tasks._tx() as conn:
            for row in rows:
                actions._set(conn, int(row["id"]), self.scope.business, "ambiguous",
                             reason="interruption pendant l'action : effet inconnu, ne pas répéter sans vérifier",
                             decided_by="policy:browser_workspace")
        ambiguous = journal.query(
            "SELECT id, action, payload FROM channel_actions WHERE business=? AND status='ambiguous' "
            "AND idempotency_key LIKE 'browser:%' ORDER BY id", (self.scope.business,))
        checkpoint = tasks.step_value(self.scope.task_id, CHECKPOINT_KEY, None) if self.scope.task_id else None
        if checkpoint or ambiguous:
            self._resume = {
                "checkpoint_url": (checkpoint or {}).get("url"),
                "ambiguous_actions": [_action_summary(r) for r in ambiguous],
            }

    # -- commandes ---------------------------------------------------------------------------
    def _cmd(self, command: str, args=(), *, timeout: float | None = None) -> dict:
        from agents import cancel
        self._commands += 1
        if self._commands > MAX_COMMANDS:
            raise Refused(f"plafond de {MAX_COMMANDS} commandes navigateur atteint pour cette tâche")
        return self._session.run(command, list(args), timeout=timeout, should_abort=cancel.requested)

    def _read(self, command: str, args=(), *, timeout: float | None = None) -> dict:
        result = self._cmd(command, args, timeout=timeout)
        if not result.get("success"):
            raise RuntimeError(agent_browser.redact(str(result.get("error") or "commande échouée"))[:500])
        return result.get("data") or {}

    def _page_text(self) -> str:
        try:
            return str(self._read("get", ["text", "body"]).get("text") or "")[:PAGE_TEXT_CHARS]
        except RuntimeError:
            return ""

    def _observe(self, *, full: bool = False) -> dict:
        data = self._read("snapshot")
        self._refs = {k: v for k, v in (data.get("refs") or {}).items() if isinstance(v, dict)}
        url = str(data.get("origin") or "")
        try:
            url = str(self._read("get", ["url"]).get("url") or url)
            title = str(self._read("get", ["title"]).get("title") or "")
        except RuntimeError:
            title = ""
        if _page_key(url) != _page_key(self._url):
            self._typed.pop(_page_key(url), None)  # nouvelle page : les saisies repartent de zéro
        self._url = url
        snapshot = agent_browser.truncate_snapshot(agent_browser.redact(agent_browser.prune_snapshot(
            str(data.get("snapshot") or ""))),
                                                   FULL_SNAPSHOT_CHARS if full else SNAPSHOT_CHARS)
        self._checkpoint()
        blocked = self._proxy.blocked[-5:] if self._proxy else []
        view = {"ok": True, "url": agent_browser.redact(url), "title": agent_browser.redact(title),
                "snapshot": snapshot, "refs": len(self._refs), "source": web_guard.UNTRUSTED_NOTE}
        if blocked:
            view["blocked_requests"] = [agent_browser.redact(u) for u in blocked]
            self._proxy.blocked.clear()
        if self._resume and not self._resume_reported:
            view["reprise"] = self._resume
            self._resume_reported = True
        return view

    def _checkpoint(self) -> None:
        if self.scope.task_id and self._url:
            tasks.save_step(self.scope.task_id, CHECKPOINT_KEY,
                            {"url": self._url, "account_mode": self.account_mode, "at": time.time()})

    def _ensure_page(self) -> None:
        """Premier appel autre qu'une navigation : rouvrir la dernière page de la tâche."""
        self._start()
        if self._url:
            return
        url = (self._resume or {}).get("checkpoint_url")
        if not url:
            raise Refused("aucune page ouverte : commence par browser_navigate")
        self._open(url)

    def _open(self, url: str) -> None:
        result = self._cmd("open", [url])
        if not result.get("success"):
            raise RuntimeError(agent_browser.redact(str(result.get("error") or "navigation échouée"))[:500])
        self._url = str((result.get("data") or {}).get("url") or url)

    def _settle_page(self) -> None:
        """Après un clic : laisser partir une éventuelle navigation puis attendre un document
        complet (retour immédiat si rien ne charge ; `wait --load` bloquerait jusqu'au délai)."""
        time.sleep(0.3)
        self._cmd("wait", ["--fn", "document.readyState === 'complete'"], timeout=15)

    def _target(self, ref: str) -> tuple[str, dict]:
        match = _REF_RE.match(str(ref or "").strip())
        if not match:
            raise Refused("ref attendue de la forme @e12 (issue du dernier snapshot)")
        key = match.group(1)
        if key not in self._refs:
            raise Refused(f"ref @{key} absente du dernier snapshot : la page a changé, observe-la à nouveau")
        return "@" + key, self._refs[key]

    # -- permissions -------------------------------------------------------------------------
    def _channel(self, channel_id) -> dict:
        page = self._url
        rows = journal.query("SELECT * FROM economic_channels WHERE business=? AND status='active' AND access='act'",
                             (self.scope.business,))
        candidates = []
        for row in rows:
            channel = dict(row)
            try:
                capabilities = json.loads(channel.get("capabilities") or "[]")
            except ValueError:
                capabilities = []
            locator = str(channel.get("locator") or "")
            if CAPABILITY not in capabilities or not _origin(locator):
                continue
            if urlsplit(locator).scheme != "https" and _origin(locator) not in self.lab:
                continue
            if _origin(locator) != _origin(page) or not (urlsplit(page).path or "/").startswith(
                    urlsplit(locator).path or "/"):
                continue
            candidates.append(channel)
        if channel_id not in (None, ""):
            chosen = next((c for c in candidates if int(c["id"]) == int(channel_id)), None)
            if chosen is None:
                raise Refused(f"canal #{channel_id} non autorisé pour agir sur cette page (actif, accès act "
                              f"accordé par l'humain, capacité {CAPABILITY}, même site)")
            return chosen
        if not candidates:
            raise Refused("modifier cette page exige un canal actif avec accès 'act' accordé par l'humain pour "
                          f"ce site (capacité {CAPABILITY}). Lecture seule en attendant : demande l'accès "
                          "(request_resource / ask_human) ou continue en lecture.")
        return max(candidates, key=lambda c: len(urlsplit(str(c["locator"])).path or "/"))

    def _effect_budget(self) -> None:
        if self._effects >= MAX_EFFECTS:
            raise Refused(f"plafond de {MAX_EFFECTS} actions à effet atteint pour cette tâche")

    # -- registre des actions à effet --------------------------------------------------------
    def _fingerprint(self, channel_id: int, kind: str, target: str) -> tuple[str, str]:
        page = _page_key(self._url)
        form = _digest(self._typed.get(page, {}))
        return _digest([int(channel_id), kind, page, target, form])[:32], form

    def _ledger(self, fingerprint: str) -> list[dict]:
        return [dict(r) for r in journal.query(
            "SELECT * FROM channel_actions WHERE business=? AND idempotency_key LIKE ? ORDER BY id",
            (self.scope.business, f"browser:{fingerprint}:%"))]

    def _effect(self, kind: str, target_sig: str, command: str, args: list[str], *, expect: str | None,
                channel_id) -> dict:
        from agents import cancel
        channel = self._channel(channel_id)
        self._effect_budget()
        expect = str(expect or "").strip() or None
        if expect and (len(expect) > 200 or agent_browser.contains_secret(expect)):
            raise Refused("expect : court texte visible attendu après l'action, sans secret")
        fingerprint, form = self._fingerprint(int(channel["id"]), kind, target_sig)
        key = f"browser:{fingerprint}:{self.scope.key}"
        mine = next((r for r in self._ledger(fingerprint) if r["idempotency_key"] == key), None)
        for row in self._ledger(fingerprint):
            if row["idempotency_key"] == key:
                continue
            if row["status"] in UNRESOLVED:
                raise Refused(f"action identique #{row['id']} ({row['status']}) non vérifiée dans une autre tâche : "
                              "ne pas la refaire. Vérifie son effet sur le site (browser_verify avec action_id) "
                              "ou laisse l'humain trancher (`python -m octopus browser resolve`).")
        if mine is not None and mine["status"] in ("verified", "executed"):
            return {"ok": True, "already_done": True, "action_id": mine["id"], "status": mine["status"],
                    "note": "action déjà exécutée dans cette tâche : non répétée. Passe à la suite, ou "
                            "browser_verify si son effet n'est pas encore constaté."}
        if mine is not None and mine["status"] in ("proposed", "ambiguous"):
            raise Refused(f"action #{mine['id']} au résultat inconnu (interruption) : ne pas la répéter. "
                          "Vérifie sur le site si elle a eu lieu (browser_verify) ou demande à l'humain.")
        pre_text = self._page_text()
        if expect and expect.casefold() in pre_text.casefold():
            raise Refused(f"expect={expect!r} est déjà visible avant l'action : il ne prouverait rien. "
                          "Choisis un texte qui n'apparaîtra qu'après (confirmation, référence...).")
        payload = {"scope": self.scope.key, "task_id": self.scope.task_id, "kind": kind, "target": target_sig,
                   "page": _page_key(self._url), "form_digest": form, "expect": expect,
                   "pre_text": pre_text[:8000]}
        now = time.time()
        with tasks._tx() as conn:  # persisté AVANT l'action externe
            if mine is None:
                action_id = int(conn.execute(
                    "INSERT INTO channel_actions (business, channel_id, action, payload, status, requested_by, "
                    "idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?)",
                    (self.scope.business, int(channel["id"]), f"browser.{kind}",
                     json.dumps(payload, ensure_ascii=False), f"agent:browser:{self.scope.key}", key, now,
                     now)).lastrowid)
            else:
                action_id = int(mine["id"])
                conn.execute("UPDATE channel_actions SET payload=? WHERE id=?",
                             (json.dumps(payload, ensure_ascii=False), action_id))
                actions._set(conn, action_id, self.scope.business, "proposed", reason=None)
        self._effects += 1
        try:
            result = self._cmd(command, args)
        except Refused:
            self._settle(action_id, "failed", "refusée avant exécution (plafond)")
            raise
        except cancel.Cancelled:
            self._settle(action_id, "ambiguous", "arrêt demandé pendant l'action : effet inconnu")
            raise
        except BaseException as exc:
            self._settle(action_id, "ambiguous", f"{type(exc).__name__} pendant l'action : effet inconnu")
            raise
        if not result.get("success"):
            error = agent_browser.redact(str(result.get("error") or ""))[:300]
            if not result.get("indeterminate") and _PRE_DISPATCH_RE.search(error):
                self._settle(action_id, "failed", f"aucun effet : {error}")
                return {"ok": False, "action_id": action_id, "status": "failed", "error": error}
            self._settle(action_id, "ambiguous", f"résultat inconnu : {error}")
            return {"ok": False, "action_id": action_id, "status": "ambiguous", "error": error,
                    "note": "effet inconnu : ne pas répéter ; vérifie sur le site (browser_verify)"}
        self._settle_page()
        status, evidence = "executed", None
        if expect:
            if self._wait_for(expect):
                status = "verified"
                evidence = self._evidence(action_id, int(channel["id"]), kind, f"texte attendu observé : {expect}")
        self._settle(action_id, status, None if status == "verified" else
                     ("attendu non observé après l'action" if expect else "exécutée, effet non vérifié"),
                     result={"url": self._current_url(), "expect": expect}, evidence_id=evidence)
        self._typed.pop(_page_key(payload["page"]), None)
        view = self._observe()
        view["effect"] = {"action_id": action_id, "status": status,
                          "verified": status == "verified",
                          "hint": None if status == "verified" else
                          "constate l'effet sur la page puis browser_verify(text, action_id)"}
        return view

    def _current_url(self) -> str:
        try:
            return str(self._read("get", ["url"]).get("url") or self._url)
        except RuntimeError:
            return self._url

    def _wait_for(self, text: str) -> bool:
        deadline = time.monotonic() + VERIFY_WAIT_S
        while True:
            if text.casefold() in self._page_text().casefold():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.4)

    def _evidence(self, action_id: int, channel_id: int, kind: str, observation: str) -> int | None:
        url = self._current_url()
        try:
            return strategy.create(
                "evidence", self.scope.business, f"Effet vérifié de l'action navigateur #{action_id} ({kind})",
                created_by="policy:browser_workspace", nature="observed", source_type="channel_action",
                source_ref=url or f"channel_actions#{action_id}", captured_at=time.time(),
                observation=observation[:900], channel_id=channel_id)
        except Exception:
            return None

    def _settle(self, action_id: int, status: str, reason: str | None, *, result: dict | None = None,
                evidence_id: int | None = None) -> None:
        fields: dict = {"reason": reason, "decided_by": "policy:browser_workspace"}
        if result is not None:
            fields["result"] = json.dumps(result, ensure_ascii=False)
        if evidence_id is not None:
            fields["evidence_id"] = evidence_id
        with tasks._tx() as conn:
            actions._set(conn, action_id, self.scope.business, status, **fields)

    def _edit(self, kind: str, ref: str, value_for_digest, command: str, args: list[str], channel_id) -> dict:
        """Modification locale d'un formulaire : autorisée par le canal, enregistrée dans l'empreinte
        de la page ; l'envoi effectif reste l'action (clic, touche) qui suit."""
        target, info = self._target(ref)
        name = str(info.get("name") or "")
        if _HUMAN_ONLY_FIELD_RE.search(name):
            raise Refused(f"champ « {name} » réservé à l'humain (identifiants, second facteur, paiement)")
        self._channel(channel_id)
        result = self._cmd(command, [target, *args])
        if not result.get("success"):
            return {"ok": False, "error": agent_browser.redact(str(result.get("error") or ""))[:300]}
        sig = f"{info.get('role')}:{name}"
        self._typed.setdefault(_page_key(self._url), {})[sig] = _digest([kind, value_for_digest])
        return self._observe()

    # -- outils exposés au runtime -----------------------------------------------------------
    def navigate(self, url: str) -> dict:
        url = str(url or "").strip()
        if agent_browser.contains_secret(url):
            raise Refused("URL contenant un secret refusée (anti-exfiltration)")
        self._start()
        if self._is_lab(url):
            if self.state.account_read:
                raise Refused("un compte a déjà été lu dans cette exécution : sortie refusée")
        else:
            try:
                kind = web_guard.check(url, self.state)
            except web_guard.BrowseRefused as exc:
                raise Refused(str(exc)) from exc
            if kind == web_guard.ACCOUNT and not self.account_mode:
                from agents import browser
                host = (urlsplit(url).hostname or "").lower()
                if browser.profile_has_cookies(url):
                    self.close()
                    self.account_mode = True
                    self._start()
                else:
                    self.anonymous_domains.add(host)
        self._open(url)
        return self._observe()

    def snapshot(self, full: bool = False) -> dict:
        self._ensure_page()
        return self._observe(full=bool(full))

    def click(self, ref: str, expect: str | None = None, channel_id=None) -> dict:
        self._ensure_page()
        target, info = self._target(ref)
        role, name = str(info.get("role") or ""), str(info.get("name") or "")
        if role == "link" and not _RISKY_LINK_RE.search(name) and channel_id in (None, "") and not expect:
            result = self._cmd("click", [target])  # simple navigation : lecture
            if not result.get("success"):
                return {"ok": False, "error": agent_browser.redact(str(result.get("error") or ""))[:300]}
            self._settle_page()
            return self._observe()
        return self._effect("click", f"{role}:{name}", "click", [target], expect=expect, channel_id=channel_id)

    def type(self, ref: str, text: str, channel_id=None) -> dict:
        self._ensure_page()
        text = str(text or "")
        if agent_browser.contains_secret(text):
            raise Refused("saisie d'un secret refusée (anti-exfiltration)")
        return self._edit("type", ref, text, "fill", [text], channel_id)

    def select(self, ref: str, value: str, channel_id=None) -> dict:
        self._ensure_page()
        return self._edit("select", ref, str(value), "select", [str(value)], channel_id)

    def check(self, ref: str, channel_id=None) -> dict:
        self._ensure_page()
        return self._edit("check", ref, True, "check", [], channel_id)

    def press(self, key: str, expect: str | None = None, channel_id=None) -> dict:
        self._ensure_page()
        key = str(key or "").strip()
        if not key or len(key) > 40:
            raise Refused("touche invalide")
        if key in READ_SAFE_KEYS and not expect:
            result = self._cmd("press", [key])
            if not result.get("success"):
                return {"ok": False, "error": agent_browser.redact(str(result.get("error") or ""))[:300]}
            return self._observe()
        return self._effect("press", f"key:{key}", "press", [key], expect=expect, channel_id=channel_id)

    def scroll(self, direction: str = "down") -> dict:
        self._ensure_page()
        direction = str(direction or "down").lower()
        if direction not in {"up", "down", "left", "right"}:
            raise Refused("direction : up, down, left ou right")
        self._read("scroll", [direction, "600"])
        return self._observe()

    def back(self) -> dict:
        self._ensure_page()
        self._read("back")
        self._settle_page()
        return self._observe()

    def verify(self, text: str, action_id=None) -> dict:
        """Constat sur la page réelle. Avec une action à effet non vérifiée, la marque `verified`
        seulement si le texte est présent maintenant et absent avant l'action."""
        self._ensure_page()
        text = str(text or "").strip()
        if not text:
            raise Refused("text requis : ce qui doit être visible sur la page")
        page = self._page_text()
        present = text.casefold() in page.casefold()
        row = self._pending_action(action_id)
        view = {"ok": True, "present": present, "url": agent_browser.redact(self._current_url())}
        if row is None:
            return view
        payload = json.loads(row["payload"] or "{}")
        view["action_id"] = row["id"]
        if not present:
            view.update(verified=False, status=row["status"], note="texte absent : effet non constaté")
            return view
        if text.casefold() in str(payload.get("pre_text") or "").casefold():
            view.update(verified=False, status=row["status"],
                        note="ce texte était déjà présent avant l'action : il ne prouve rien")
            return view
        channel = journal.query("SELECT locator FROM economic_channels WHERE id=?", (row["channel_id"],))
        if not channel or _origin(channel[0]["locator"]) != _origin(self._current_url()):
            view.update(verified=False, status=row["status"], note="constat à faire sur le site du canal")
            return view
        evidence = self._evidence(int(row["id"]), int(row["channel_id"]), str(payload.get("kind")),
                                  f"constat après l'action : {text}")
        self._settle(int(row["id"]), "verified", None, result={"url": self._current_url(), "observed": text},
                     evidence_id=evidence)
        view.update(verified=True, status="verified")
        return view

    def _pending_action(self, action_id) -> dict | None:
        if action_id not in (None, ""):
            rows = journal.query("SELECT * FROM channel_actions WHERE id=? AND business=? AND "
                                 "idempotency_key LIKE 'browser:%'", (int(action_id), self.scope.business))
            if not rows:
                raise Refused(f"action navigateur #{action_id} introuvable")
            row = dict(rows[0])
            if row["status"] not in UNRESOLVED:
                raise Refused(f"action #{action_id} déjà {row['status']}")
            return row
        rows = journal.query(
            "SELECT * FROM channel_actions WHERE business=? AND status IN ('proposed','ambiguous','executed') "
            "AND idempotency_key LIKE ? ORDER BY id DESC LIMIT 1", (self.scope.business, f"browser:%:{self.scope.key}"))
        return dict(rows[0]) if rows else None

    def download(self, ref: str, filename: str) -> dict:
        self._ensure_page()
        target, info = self._target(ref)
        name = _safe_filename(filename)
        if str(info.get("role")) != "link" or _RISKY_LINK_RE.search(str(info.get("name") or "")):
            self._channel(None)
        path = self._inbox / name
        data = self._read("download", [target, str(path)], timeout=60)
        saved = Path(str(data.get("path") or path))
        if not saved.is_file() or self._inbox not in saved.resolve().parents:
            return {"ok": False, "error": "téléchargement non constaté dans l'espace de la tâche"}
        blob = saved.read_bytes()
        return {"ok": True, "file": str(saved), "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
                "url": agent_browser.redact(self._current_url())}

    def upload(self, ref: str, filename: str, channel_id=None) -> dict:
        self._ensure_page()
        path = outbox_dir(self.scope.business) / _safe_filename(filename)
        if not path.is_file():
            raise Refused(f"fichier absent de {outbox_dir(self.scope.business)} (seuls les fichiers préparés "
                          "pour l'envoi sont téléversables)")
        if path.stat().st_size > UPLOAD_MAX_BYTES:
            raise Refused("fichier trop volumineux")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return self._edit("upload", ref, digest, "upload", [str(path)], channel_id)


def _action_summary(row) -> dict:
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        payload = {}
    return {"action_id": row["id"], "action": row["action"], "page": payload.get("page"),
            "target": payload.get("target"), "task_id": payload.get("task_id")}


# --- registre des espaces de travail (un par tâche, fermés en fin de mission) --------------------

_ACTIVE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("octopus_browser_workspaces", default=None)
_DETACHED: dict[str, Workspace] = {}


@contextmanager
def mission_scope():
    """Portée d'une exécution (mission ou agent) : les sessions navigateur y sont partagées par les
    sous-agents puis fermées à la sortie de la portée la plus externe."""
    if _ACTIVE.get() is not None:
        yield
        return
    token = _ACTIVE.set({})
    try:
        yield
    finally:
        spaces = _ACTIVE.get() or {}
        _ACTIVE.reset(token)
        for space in spaces.values():
            try:
                space.close()
            except Exception:
                pass


def workspace() -> Workspace:
    scope = current_scope()
    spaces = _ACTIVE.get()
    if spaces is None:
        spaces = _DETACHED
    space = spaces.get(scope.key)
    if space is None:
        space = spaces[scope.key] = Workspace(scope, web_guard.current())
    return space


def close_detached() -> None:
    for space in list(_DETACHED.values()):
        try:
            space.close()
        except Exception:
            pass
    _DETACHED.clear()


atexit.register(close_detached)


def call(method: str, **kwargs) -> dict:
    """Point d'entrée des outils du runtime : refus et pannes deviennent des observations."""
    try:
        return call_on(workspace(), method, **kwargs)
    except Refused as exc:
        return {"ok": False, "refused": True, "reason": str(exc)}
    except agent_browser.BackendUnavailable as exc:
        return {"ok": False, "error": f"navigateur indisponible : {exc}"}
    except RuntimeError as exc:
        return {"ok": False, "error": agent_browser.redact(str(exc))[:500]}


# --- tranche humaine et métriques ------------------------------------------------------------

def resolve(business: str, action_id: int, *, executed: bool, actor: str = "human") -> dict:
    """L'humain tranche une action ambiguë : exécutée (ne jamais la refaire) ou non (refaisable)."""
    if not str(actor).startswith("human"):
        raise PermissionError("seul l'humain tranche une action navigateur ambiguë")
    rows = journal.query("SELECT * FROM channel_actions WHERE id=? AND business=? AND idempotency_key LIKE 'browser:%'",
                         (int(action_id), business))
    if not rows:
        raise ValueError(f"action navigateur #{action_id} introuvable pour {business!r}")
    row = dict(rows[0])
    if row["status"] not in UNRESOLVED:
        raise ValueError(f"action #{action_id} déjà {row['status']}")
    status, reason = ("executed", "exécution attestée par l'humain") if executed else \
        ("failed", "non exécutée selon l'humain : peut être refaite")
    with tasks._tx() as conn:
        actions._set(conn, int(action_id), business, status, reason=reason, decided_by=actor)
    return {"action_id": int(action_id), "status": status}


def list_actions(business: str, *, status: str | None = None, task_id: int | None = None,
                 limit: int = 100) -> list[dict]:
    pattern = f"browser:%:t{int(task_id)}" if task_id is not None else "browser:%"
    sql = ("SELECT id, channel_id, action, status, reason, evidence_id, payload FROM channel_actions "
           "WHERE business=? AND idempotency_key LIKE ?")
    params: list = [business, pattern]
    if status:
        sql += " AND status=?"
        params.append(status)
    rows = []
    for row in journal.query(sql + " ORDER BY id DESC LIMIT ?", (*params, int(limit))):
        item = dict(row)
        payload = json.loads(item.pop("payload") or "{}")
        item.update(task_id=payload.get("task_id"), page=payload.get("page"), target=payload.get("target"))
        rows.append(item)
    return rows


def task_actions(business: str, task_id: int) -> list[dict]:
    return list(reversed(list_actions(business, task_id=task_id)))


def smoke() -> dict:
    """Vraie navigation de diagnostic : page locale servie ici, garde et backend réels, aucun effet."""
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            body = ("<!doctype html><title>OCTOPUS smoke</title><h1>Espace navigateur</h1>"
                    "<label for=q>Recherche</label><input id=q><button>Chercher</button>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    previous = os.environ.get(LAB_ORIGINS_ENV)
    os.environ[LAB_ORIGINS_ENV] = origin
    space = Workspace(Scope("doctor", "octopus", None), web_guard.BrowseState())
    started = time.monotonic()
    try:
        view = space.navigate(origin + "/")
        box = next((k for k, v in space._refs.items() if v.get("role") == "textbox"), "e0")
        refused = call_on(space, "type", ref="@" + box, text="x")
        return {"ok": bool(view.get("ok")) and "Espace navigateur" in view.get("snapshot", ""),
                "title": view.get("title"), "refs": view.get("refs"),
                "write_without_channel_refused": bool(refused.get("refused")),
                "seconds": round(time.monotonic() - started, 2)}
    finally:
        space.close()
        server.shutdown()
        server.server_close()
        if previous is None:
            os.environ.pop(LAB_ORIGINS_ENV, None)
        else:
            os.environ[LAB_ORIGINS_ENV] = previous


def call_on(space: Workspace, method: str, **kwargs) -> dict:
    try:
        return getattr(space, method)(**kwargs)
    except Refused as exc:
        return {"ok": False, "refused": True, "reason": str(exc)}
