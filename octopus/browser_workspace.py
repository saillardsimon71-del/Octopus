"""Espace de travail navigateur d'OCTOPUS, sur le backend navigateur de Hermes.

Hermes fournit l'infrastructure d'interaction (CLI `agent-browser` + Chromium, refs `@eN` issues
de l'arbre d'accessibilité, commandes navigate/snapshot/click/type/press/scroll/back : voir
`agents/agent_browser.py`). OCTOPUS garde tout le reste : l'IA de la mission observe la page,
décide librement de l'action suivante et appelle ces outils ; ce module applique en code ce
qu'aucun prompt ne peut garantir :

- contexte : une ressource confiée à l'activité donne l'autorité opérationnelle ordinaire ;
  paiement, engagement financier et création de compte restent réservés à l'humain ;
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

from . import actions, journal, strategy, tasks, mandates, resources

CAPABILITY = "browser_workspace"
CHECKPOINT_KEY = "browser.workspace"
LAB_ORIGINS_ENV = "OCTOPUS_BROWSER_LAB_ORIGINS"
MAX_COMMANDS = 300
MAX_EFFECTS = 30
SNAPSHOT_CHARS = 6000
VERIFY_WAIT_S = 5.0
UPLOAD_MAX_BYTES = 25 * 1024 * 1024
FILES_KEY = "browser.files"
DOWNLOAD_TIMEOUT_S = 30.0
DOWNLOAD_START_S = 5.0
_PARTIAL_SUFFIXES = (".crdownload", ".tmp", ".part", ".partial")
UNRESOLVED = ("proposed", "ambiguous", "executed")

READ_SAFE_KEYS = frozenset({"Tab", "Shift+Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
                            "PageUp", "PageDown", "Home", "End"})
_REF_RE = re.compile(r"^@?(e\d+)$")
# Un lien est une navigation (lecture) sauf si son libellé annonce un effet.
_RISKY_LINK_RE = re.compile(
    r"supprim|delete|remove|envoy|send|publi|payer|pay(?:ment|out)?s?\b|paiement|achet|buy|checkout|confirm|valid|submit|"
    r"soumet|abonn|subscri|désinscri|desinscri|unsubscri|annul|cancel|place.*order|order\s+now|pass.*command|accept|invit|partag|share|"
    r"like|follow|suivre|sign|logout|déconnex|deconnex|retirer|withdraw|transf", re.IGNORECASE)
# Champs réservés à l'humain : identifiants, second facteur, paiement.
_HUMAN_ONLY_FIELD_RE = re.compile(
    r"mot de passe|password|passcode|passphrase|\b2fa\b|\botp\b|code de (vérification|verification|sécurité)|"
    r"verification code|security code|\bcvv\b|\bcvc\b|cryptogramme|numéro de carte|card number|\biban\b|\bbic\b",
    re.IGNORECASE)
# Échecs connus survenus AVANT tout clic (aucun effet possible) ; tout le reste est ambigu.
_PRE_DISPATCH_RE = re.compile(r"not found|no element|unknown ref|invalid ref|stale|not visible|not attached|"
                              r"strict mode|resolved to \d+ elements|no node|could not find", re.IGNORECASE)


class HumanBrowserRequired(Exception):
    """A detected challenge stops the agent before any further interaction or page exposure."""
    def __init__(self, resource_key=None, *, need='captcha'):
        self.resource_key = resource_key
        self.need = need
        super().__init__('Frontière navigateur : intervention humaine requise, aucune résolution automatique')


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
        task_id = tasks.current_task_id()
    except Exception:
        task_id = None
    return Scope(f"t{task_id}" if task_id else f"r{run.root_id}", run.business, task_id)


def lab_origins() -> set[str]:
    """Applications locales explicitement déclarées par l'opérateur (tests, outils internes).

    Déclarées, elles rendent l'espace HERMÉTIQUE : seules ces origines sont joignables (navigation
    et toute requête passant par le proxy) ; tout le reste est refusé avant connexion."""
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


def task_inbox(business: str, scope_key: str) -> Path:
    """Fichiers téléchargés par une tâche. Chemin absolu ordinaire (jamais `\\\\?\\`) : c'est celui
    que reçoit Chromium comme répertoire de téléchargement au lancement."""
    return Path(agent_browser.plain_path(_files_dir(business) / "inbox" / scope_key))


def _within(child: Path, parent: Path) -> bool:
    """`child` est-il physiquement dans `parent` ? Les deux côtés sont résolus (liens, noms courts
    8.3 et préfixe `\\\\?\\` de Windows) avant comparaison."""
    try:
        child_real = Path(agent_browser.plain_path(Path(child).resolve()))
        parent_real = Path(agent_browser.plain_path(Path(parent).resolve()))
    except OSError:
        return False
    return parent_real in child_real.parents


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_filename(name: str) -> str:
    name = str(name or "").strip()
    if not name or name in {".", ".."} or re.search(r"[\\/:\x00]", name) or agent_browser.contains_secret(name):
        raise Refused("nom de fichier invalide (pas de chemin, pas de secret)")
    return name[:120]


def _account_text(text):
    text = re.sub(r'(?im)^.*(?:password|mot de passe|passcode|\botp\b|\b2fa\b|cookie|secret|api.?key|'
                  r'verification code|code de sécurité|code de vérification|security code|access token).*$',
                  '[contenu de sécurité masqué]', agent_browser.redact(text))
    # A conservative tradeoff for account observations: typical OTPs never reach the model/journal.
    return re.sub(r'(?<![\w.])\d{4,8}(?![\w.])', '[nombre protégé]', text)


IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_MAX_PIXELS = 16 * 1024 * 1024


def capture_page(session, *, business, scope_key, account, task_id=None, resource_key=None,
                 full_page=False):
    """Agent/verifier only. No caller-controlled path, values, cookies or human browser access."""
    import uuid
    diagnostic = {}
    if not resources.verify_account_page(session, account, require_marker=False, diagnostic=diagnostic):
        raise Refused(diagnostic.get('reason', 'backend_error'))
    def security_surface():
        result = session.run('eval', ['(() => { /* octopus_capture_safe */ return Boolean(document.body && '
            '!/api[ _-]?keys?|secret[ _-]?keys?|access[ _-]?tokens?|recovery (?:codes?|phrase)|'
            'seed phrase|codes? de récupération|clé secrète|clés? api|setup authenticator|'
            'verification code|security code|one.time (?:code|password)|code de (?:vérification|sécurité)|'
            '\\botp\\b|\\b2fa\\b|password\\s*:|mot de passe\\s*:|'
            'configurer.*authentification/i.test(document.body.innerText)); })()'])
        if not result.get('success') or (result.get('data') or {}).get('result') is not True:
            raise Refused('écran de sécurité sensible : capture interdite')
    security_surface()
    directory = task_inbox(business, scope_key) / 'screenshots'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex + '.png')
    try:
        result = session.run('screenshot', [str(path), *(['--full'] if full_page else [])])
        if not result.get('success'):
            raise Refused('capture indisponible')
        if path.is_file():
            path.chmod(0o600)
        if not _within(path, directory) or not path.is_file() or path.stat().st_size > IMAGE_MAX_BYTES:
            raise Refused('capture hors espace ou trop volumineuse')
        # Validate actual bytes/dimensions; a local file path alone is not an image.
        from PIL import Image
        with Image.open(path) as im:
            if im.format != 'PNG' or im.width * im.height > IMAGE_MAX_PIXELS:
                raise Refused('capture PNG bornée requise')
            im.verify()
        # Drop a capture if a login/security boundary appeared during capture.
        if not resources.verify_account_page(session, account, require_marker=False):
            raise Refused('frontière authentification apparue pendant la capture')
        security_surface()
        return {'path': str(path), 'sha256': _sha256(path), 'bytes': path.stat().st_size,
                'business': business, 'scope': scope_key, 'task_id': task_id,
                'resource_key': resource_key, 'at': time.time(), 'full_page': bool(full_page)}
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def image_part(image, *, business, scope_key):
    """Load trusted capture metadata within the current scope; never persist the returned part."""
    from agents import deepseek
    if image.get('business') != business or image.get('scope') != scope_key:
        raise Refused('capture appartenant à un autre business ou une autre tâche')
    path = Path(image['path'])
    directory = task_inbox(business, scope_key) / 'screenshots'
    if (not _within(path, directory) or not path.is_file() or path.stat().st_size > IMAGE_MAX_BYTES
            or _sha256(path) != image.get('sha256')):
        raise Refused('capture absente, déplacée ou modifiée')
    key = image.get('resource_key')
    if key:
        if not mandates.account_authority(business, key, 'read'):
            raise Refused('lecture de capture compte non mandatée')
        resources.account_browser_options(key)  # Human login must be closed, including on resume.
    return deepseek.build_vision_messages([str(path)], '')[0]['content'][1]


class Workspace:
    """Session navigateur persistante d'une tâche, pilotée par l'IA via les outils browser_*."""

    def __init__(self, scope: Scope, state: web_guard.BrowseState, *, session_factory=None):
        self.scope = scope
        self.state = state
        self.lab = lab_origins()
        task = tasks.get(scope.task_id) if scope.task_id else None
        self.public_only = bool((task or {}).get("input", {}).get("browser_public_only"))
        self.resource_key = (task or {}).get('input', {}).get('browser_resource_key')
        self.account_resource = resources.get(self.resource_key) if self.resource_key else None
        self.account_mode = bool(self.resource_key)
        if self.resource_key:
            if not mandates.account_authority(scope.business, self.resource_key, 'read'):
                raise Refused('lecture compte non mandatée ou session indisponible')
            self.state.account_domains = tuple(self.account_resource['web_account']['domains'])
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
        if self.resource_key:
            account = (resources.get(self.resource_key) or {}).get('web_account') or {}
            if not mandates.account_authority(self.scope.business, self.resource_key, 'read'):
                return False
            if (urlsplit(url).hostname or '').lower() not in account.get('domains', []):
                return False
        if self.lab:
            # Laboratoire hermétique : seules les origines déclarées sortent, quel que soit
            # l'émetteur (page, sous-ressource, script, ou Chrome lui-même).
            return self._is_lab(url) and not self.state.account_read
        return web_guard.request_allowed(url, self.state, account_context=self.account_mode,
                                         anonymous_account_domains=tuple(self.anonymous_domains))

    def _allow_private(self, host: str, port: int) -> bool:
        return any(_origin(f"{scheme}://{host}:{port}/") in self.lab for scheme in ("http", "https"))

    def _start(self) -> None:
        if self._session is not None:
            return
        self._proxy = web_guard.GuardProxy(self._guard, allow_private=self._allow_private).start()
        inbox = task_inbox(self.scope.business, self.scope.key)
        inbox.mkdir(parents=True, exist_ok=True)
        self._inbox = inbox
        profile = (resources.account_profile(self.resource_key) if self.resource_key else
                   (agents_config.DATA_DIR / "browser_profile") if self.account_mode else None)
        setting = __import__("os").environ.get("OCTOPUS_BROWSER_HEADLESS")
        headed = setting == "0" or (self.account_mode and setting != "1")
        try:
            self._session = self._session_factory(f"oct-{self.scope.key}", proxy_url=self._proxy.url,
                                                  profile_dir=profile, headed=headed, download_dir=inbox,
                                                  **(resources.account_browser_options(self.resource_key)
                                                     if self.resource_key else {}))
            self._session.close()  # démon périmé d'un processus interrompu : repartir d'un état propre
        except Exception:
            if self.resource_key and self.account_resource['web_account'].get('browser_kind') == 'chrome_stable':
                resources.set_account_session(self.resource_key, 'unavailable',
                    detail='Session Chrome stable indisponible ; vérifier ou reconnecter avant reprise')
            self._proxy.stop()
            self._proxy, self._session = None, None
            raise
        self._reconcile()

    def close(self) -> None:
        had_session = self._session is not None
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None
        if had_session:
            self.observe("close", {"ok": True, "session": "closed"})

    def observe(self, method: str, result: dict) -> None:
        if not self.scope.task_id:
            return
        previous = tasks.step_value(self.scope.task_id, "browser.observation", {})
        observation = {"session": "open" if self._session else "closed", "at": time.time(),
                       "url": self._safe_url(self._url), "action": method,
                       "ok": result.get("ok"), "refused": bool(result.get("refused")),
                       "reason": agent_browser.redact(str(result.get("reason") or result.get("error") or ""))[:1000],
                       "snapshot": result.get("snapshot", previous.get("snapshot", "")),
                       "title": result.get("title", previous.get("title", ""))}
        if method == "close" and previous:
            observation = {**previous, "session": "closed", "closed_at": time.time()}
        tasks.save_step(self.scope.task_id, "browser.observation", observation)
        tasks.emit(self.scope.business, self.scope.task_id, "browser.observation", observation)

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
        if checkpoint and isinstance(checkpoint.get("form_digests"), dict):
            self._typed = checkpoint["form_digests"]
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

    def _check_challenge(self):
        # Agent environment ONLY. Two fixed boolean predicates, no credentials or page text returned.
        traffic = self._read('eval', ["(() => { /* octopus_traffic */ return Boolean(document.body && "
            "/unusual traffic|trafic exceptionnel/i.test(document.body.innerText)); })()"]).get('result')
        if traffic is True:
            raise Refused('source Web inaccessible : protection anti-automation ; utiliser les search providers')
        challenge = self._read('eval', ["(() => { /* octopus_challenge */ return Boolean("
            "Array.from(document.querySelectorAll('iframe[src*=captcha],iframe[src*=challenge],#challenge-form'))"
            ".some(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden') || "
            "(document.body && /verify you are (?:not a bot|human)|performing security verification|"
            "security service to protect against malicious bots|montrez-nous que vous|"
            "confirmez que vous(?: n.êtes pas un robot| êtes humain)|(?:complete|solve|résolvez|validez) (?:the |le |ce )?captcha"
            "/i.test(document.body.innerText))); })()"]).get('result')
        if challenge is True:
            if self.resource_key:
                resources.set_account_session(self.resource_key, 'connection_required',
                    detail='Challenge : connexion Chrome stable humaine requise ; aucun contournement')
            raise HumanBrowserRequired(self.resource_key)

    def _check_account(self):
        if not self.resource_key:
            return
        if mandates.SECRET_SURFACE.search(self._url):
            raise Refused('surface sensible du compte réservée à l’humain ; contenu non lu')
        if not mandates.account_authority(self.scope.business, self.resource_key, 'read'):
            raise Refused('Ressource retirée de cette activité ou session indisponible.')
        account = resources.get(self.resource_key)['web_account']
        final_url = str(self._read('get', ['url']).get('url') or '')
        if mandates.SECRET_SURFACE.search(final_url) or agent_browser.contains_secret(final_url):
            raise Refused('surface sensible du compte réservée à l’humain ; contenu non lu')
        diagnostic = {}
        if not resources.verify_account_page(self._session, account, require_marker=False, diagnostic=diagnostic):
            reason = diagnostic.get('reason', 'backend_error')
            if reason == 'visible_login_field_present':
                resources.set_account_session(self.resource_key, 'expired', detail=reason)
                raise Refused('session expirée : login visible ; connexion humaine requise')
            raise Refused(reason)

    def _page_text(self) -> str:
        self._check_challenge()
        self._check_account()
        text = agent_browser.redact(str(self._read("get", ["text", "body"]).get("text") or ""))
        return _account_text(text) if self.resource_key else text

    def _observe(self, *, full: bool = False) -> dict:
        self._check_challenge()
        self._check_account()
        data = self._read("snapshot")
        self._refs = {k: v for k, v in (data.get("refs") or {}).items() if isinstance(v, dict)}
        url = str(data.get("origin") or "")
        try:
            url = str(self._read("get", ["url"]).get("url") or url)
            title = str(self._read("get", ["title"]).get("title") or "")
        except RuntimeError:
            title = ""
        if url != self._url:
            self._typed.pop(_page_key(url), None)  # nouvelle page : les saisies repartent de zéro
        self._url = url
        snapshot = agent_browser.redact(agent_browser.prune_snapshot(str(data.get("snapshot") or "")))
        if not full:
            snapshot = agent_browser.truncate_snapshot(snapshot, SNAPSHOT_CHARS)
        if self.resource_key:
            # Do not expose security surfaces or one-time codes even as ordinary text.
            snapshot = _account_text(snapshot)
        else:
            self.state.public_pages[url] = snapshot
        self._checkpoint()
        blocked = self._proxy.blocked[-5:] if self._proxy else []
        view = {"ok": True, "url": self._safe_url(url), "title": agent_browser.redact(title),
                "snapshot": snapshot, "refs": len(self._refs), "elements": self._element_metadata(full=full),
                "elements_truncated": not full and len(self._refs) > 40, "source": web_guard.UNTRUSTED_NOTE}
        if blocked:
            view["blocked_requests"] = [self._safe_url(u) for u in blocked]
            self._proxy.blocked.clear()
        if self._resume and not self._resume_reported:
            view["reprise"] = self._resume
            self._resume_reported = True
        self._qualify_page(view)
        return view

    def _element_metadata(self, *, full=False):
        """Only allowlisted observed attributes. No arbitrary JS, values, HTML or secrets."""
        requests = []
        observed = list(self._refs.items())
        if not full:
            observed = observed[:40]
        for ref, info in observed:
            if not re.fullmatch(r'e[0-9]+', ref):
                continue
            attrs = ['href'] if info.get('role') == 'link' else (
                ['aria-expanded', 'aria-haspopup', 'disabled', 'aria-checked'] if info.get('role') == 'button' else [])
            requests.extend((ref, attr) for attr in attrs)
        if requests:
            try:
                rows = self._read('batch', [f"get attr @{ref} {attr}" for ref, attr in requests], timeout=10).get('results', [])
                if isinstance(rows, list) and len(rows) == len(requests):
                    for (ref, attr), row in zip(requests, rows):
                        data = row.get('result') or row.get('data') or {}
                        value = data.get('value', data.get('attribute')) if isinstance(data, dict) else None
                        if value is None or not row.get('success'):
                            continue
                        if attr == 'href':
                            from urllib.parse import urljoin
                            href = urljoin(self._url, str(value))
                            parts = urlsplit(href)
                            if parts.scheme in ('http', 'https') and not parts.username and not parts.password and not agent_browser.contains_secret(href):
                                self._refs[ref]['href'] = self._safe_url(href)
                        elif attr in ('aria-expanded', 'disabled', 'aria-checked'):
                            self._refs[ref][{'aria-expanded': 'expanded', 'aria-checked': 'checked'}.get(attr, attr)] = str(value).lower() == 'true' or attr == 'disabled'
                        elif str(value) in ('true', 'menu', 'listbox', 'dialog', 'tree', 'grid'):
                            self._refs[ref]['haspopup'] = str(value)
            except (RuntimeError, ValueError, TypeError):
                pass  # Older backend: original accessibility snapshot remains usable.
        metadata = []
        for ref, info in observed:
            entry = {'ref': '@' + ref}
            for key in ('role', 'name', 'href', 'expanded', 'disabled', 'checked', 'haspopup'):
                if key in info:
                    value = info[key]
                    if isinstance(value, str):
                        value = agent_browser.redact(value)
                        value = _account_text(value) if self.resource_key else value
                        if key != 'href' and not full and len(value) > 300:
                            entry[key + '_truncated'] = True
                            value = value[:300]
                    entry[key] = value
            metadata.append(entry)
        return metadata

    def _safe_url(self, url):
        if self.resource_key:
            parts = urlsplit(url)
            return agent_browser.redact(f"{parts.scheme}://{parts.hostname}{':' + str(parts.port) if parts.port else ''}{parts.path}")
        return agent_browser.redact(url)

    def _checkpoint(self) -> None:
        if self.scope.task_id and self._url:
            tasks.save_step(self.scope.task_id, CHECKPOINT_KEY,
                            {"url": self._safe_url(self._url), "account_mode": self.account_mode,
                             "form_digests": self._typed, "at": time.time()})

    def _ensure_page(self) -> None:
        """Premier appel autre qu'une navigation : rouvrir la dernière page de la tâche."""
        self._start()
        if self._url:
            self._check_challenge()
            self._check_account()
            return
        url = (self._resume or {}).get("checkpoint_url")
        if not url:
            raise Refused("aucune page ouverte : commence par browser_navigate")
        self._open(url)
        self._check_challenge()
        self._check_account()

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
    def _channel(self, channel_id, effect=None) -> dict:
        page = self._url
        rows = journal.query("SELECT * FROM economic_channels WHERE business=? AND status='active'",
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
            authority = mandates.authorize(channel, effect or ('read' if self.resource_key else 'contact'))
            if authority['allowed'] and authority.get('resource_key') == self.resource_key:
                candidates.append(channel)
        if channel_id not in (None, ""):
            chosen = next((c for c in candidates if int(c["id"]) == int(channel_id)), None)
            if chosen is None:
                raise Refused("Cette ressource n'appartient pas au contexte de la page actuelle.")
            return chosen
        if not candidates:
            raise Refused("Cette page ne correspond à aucune ressource opérationnelle de cette activité.")
        return max(candidates, key=lambda c: len(urlsplit(str(c["locator"])).path or "/"))

    def _effect_budget(self) -> None:
        if self._effects >= MAX_EFFECTS:
            raise Refused(f"plafond de {MAX_EFFECTS} actions à effet atteint pour cette tâche")

    # -- registre des actions à effet --------------------------------------------------------
    def _control_identity(self, ref, info, *, click=False):
        sig = f"{info.get('role') or ''}:{info.get('name') or ''}"
        peers = [key for key, value in self._refs.items()
                 if (value.get('role'), value.get('name')) == (info.get('role'), info.get('name'))]
        peers.sort(key=lambda key: int(key[1:]))
        return [sig, peers.index(ref.lstrip('@'))] if click or len(peers) > 1 else sig

    def _fingerprint(self, channel_id: int, kind: str, target: str | list, *, url_context=True) -> tuple[str, str]:
        page = _page_key(self._url)
        form = _digest(self._typed.get(page, {}))
        identity = [int(channel_id), kind, page, target, form]
        parts = urlsplit(self._url)
        if url_context and (parts.query or parts.fragment):
            identity.append(_digest([parts.query, parts.fragment]))
        return _digest(identity)[:32], form

    def _ledger(self, fingerprint: str) -> list[dict]:
        return [dict(r) for r in journal.query(
            "SELECT * FROM channel_actions WHERE business=? AND idempotency_key LIKE ? ORDER BY id",
            (self.scope.business, f"browser:{fingerprint}:%"))]

    def _effect(self, kind: str, target_sig: str, command: str, args: list[str], *, expect: str | None,
                channel_id, target_context=None, effect=None) -> dict:
        from agents import cancel
        if mandates.IDENTITY_DELETION.search(target_sig):
            raise Refused("suppression irréversible de l'identité : hors des opérations ordinaires confiées à ces outils.")
        if mandates.SENSITIVE.search(target_sig + ' ' + self._url) or mandates.SECRET_SURFACE.search(target_sig):
            raise Refused('Paiement, engagement financier ou création de compte : intervention humaine requise ; les secrets restent protégés.')
        observed_effect = ('publish' if re.search(r'publi|publish|\bpost\b|deploy|déploi', target_sig, re.I) else
                           'contact' if re.search(r'envoy|send|reply|répond|message|invit', target_sig, re.I) else
                           'edit' if re.search(r'save|enregistr|modifi|update', target_sig, re.I) else None)
        required_effect = ('edit' if self.resource_key and command in ('fill', 'select', 'check') else
                           observed_effect or ('edit' if self.resource_key else 'contact'))
        if required_effect not in mandates.EFFECTS - {'read'}:
            raise Refused('effet non déléguable : intervention humaine requise')
        channel = self._channel(channel_id, required_effect)
        if effect and effect != required_effect:
            self._channel(channel['id'], effect)
        self._effect_budget()
        expect = str(expect or "").strip() or None
        if expect and (len(expect) > 200 or agent_browser.contains_secret(expect)):
            raise Refused("expect : court texte visible attendu après l'action, sans secret")
        target_identity = [target_sig, target_context] if target_context is not None else target_sig
        if command in ('click', 'fill', 'select', 'check'):
            ref, info = self._target(args[0])
            target_identity = self._control_identity(ref, info, click=command == 'click')
        fingerprint, form = self._fingerprint(int(channel["id"]), kind, target_identity)
        key = f"browser:{fingerprint}:{self.scope.key}"
        mine = next((r for r in self._ledger(fingerprint) if r["idempotency_key"] == key), None)
        lineage = _lineage(self.scope.task_id)
        if command in ('fill', 'select', 'check') and isinstance(target_identity, list):
            # Historical edits did not distinguish same-named fields; do not reinterpret their effects.
            for row in journal.query('SELECT * FROM channel_actions WHERE business=? AND channel_id=? AND action=?',
                                     (self.scope.business, channel['id'], f'browser.{kind}')):
                prior = json.loads(row['payload'])
                if prior.get('target') == target_sig and prior.get('page') == _page_key(self._url) and not prior.get('target_identity'):
                    if row['status'] in UNRESOLVED or row['status'] in ('executed', 'verified') and (
                            row['idempotency_key'].endswith(':' + self.scope.key) or lineage and _lineage(_row_task(row)) == lineage):
                        raise Refused("Une ancienne saisie ne distingue pas les champs de même nom. Vérifie son effet avant de la répéter.")
        legacy_keys = {self._fingerprint(int(channel['id']), kind, target, url_context=False)[0]
                       for target in (target_identity, target_sig)} - {fingerprint}
        for legacy in legacy_keys:
            for row in self._ledger(legacy):
                if row['status'] in UNRESOLVED or row['status'] == 'verified' and (
                        row['idempotency_key'].endswith(':' + self.scope.key) or
                        lineage and _lineage(_row_task(row)) == lineage):
                    raise Refused(f"L'ancienne action #{row['id']} ne distingue pas les contrôles ou leur contexte de page. "
                                  "Ne répète pas un effet déjà exécuté ou incertain ; vérifie son résultat sur la page.")
        for row in self._ledger(fingerprint):
            if row["idempotency_key"] == key:
                continue
            if row["status"] in UNRESOLVED:
                raise Refused(f"action identique #{row['id']} ({row['status']}) non vérifiée dans une autre tâche : "
                              "ne pas la refaire. Vérifie son effet sur le site (browser_verify avec action_id) "
                              "ou laisse l'humain trancher (`python -m octopus browser resolve`).")
            if row["status"] == "verified" and lineage and _lineage(_row_task(row)) == lineage:
                # Nouvelle tentative du MÊME objectif : l'effet est déjà constaté, jamais refait.
                return {"ok": True, "already_done": True, "action_id": row["id"], "status": row["status"],
                        "previous_task_id": _row_task(row),
                        "note": "action déjà exécutée et vérifiée lors d'une tentative précédente de cet objectif : "
                                "non répétée. Poursuis avec ce qui reste à faire (retrouve le résultat sur le site)."}
        if mine is not None and mine["status"] in ("verified", "executed"):
            return {"ok": True, "already_done": True, "action_id": mine["id"], "status": mine["status"],
                    "note": "action déjà exécutée dans cette tâche : non répétée. Passe à la suite, ou "
                            "browser_verify si son effet n'est pas encore constaté."}
        if self.resource_key and kind in ('click', 'press'):
            pending = journal.query("SELECT id,payload FROM channel_actions WHERE business=? AND channel_id=? "
                                    "AND status IN ('proposed','executed','ambiguous') "
                                    "AND action IN ('browser.click','browser.press')", (self.scope.business, channel['id']))
            for row in pending:
                prior = json.loads(row['payload'])
                if prior.get('page') == _page_key(self._url):
                    raise Refused("Un effet précédent n'est pas encore confirmé sur cette page. "
                                  "Constate son résultat avec browser_verify avant un autre envoi.")
        if mine is not None and mine["status"] in ("proposed", "ambiguous"):
            raise Refused(f"action #{mine['id']} au résultat inconnu (interruption) : ne pas la répéter. "
                          "Vérifie sur le site si elle a eu lieu (browser_verify) ou demande à l'humain.")
        pre_text = self._page_text()
        expect_is_new = bool(expect and expect.casefold() not in pre_text.casefold())
        authority = mandates.authorize(channel, required_effect)
        payload = {"mandate_id": authority.get("mandate_id"), "scope": self.scope.key, "task_id": self.scope.task_id, "kind": kind, "target": target_sig,
                   "page": _page_key(self._url), "effect": required_effect, "form_digest": form, "expect": expect,
                   "pre_text": pre_text, "pre_text_complete": True}
        if isinstance(target_identity, list):
            payload['target_identity'] = target_identity
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
            self._channel(channel_id, required_effect)  # Live scope check before the command.
            if effect and effect != required_effect:
                self._channel(channel['id'], effect)
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
        if expect_is_new:
            if self._wait_for(expect):
                status = "verified"
                evidence = self._evidence(action_id, int(channel["id"]), kind, f"texte attendu observé : {expect}")
        self._settle(action_id, status, None if status == "verified" else
                     ("attendu non observé après l'action" if expect_is_new else "exécutée, effet non vérifié"),
                     result={"url": self._current_url(), "expect": expect}, evidence_id=evidence)
        view = self._observe()
        view["effect"] = {"action_id": action_id, "status": status,
                          "verified": status == "verified",
                          "hint": None if status == "verified" else
                          "constate l'effet sur la page puis browser_verify(text, action_id)"}
        return view

    def _current_url(self) -> str:
        if self._session is None:
            return ''
        try:
            return self._safe_url(str(self._read("get", ["url"]).get("url") or self._url))
        except RuntimeError:
            return self._safe_url(self._url)

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

    def _edit(self, kind: str, ref: str, value_for_digest, command: str, args: list[str], channel_id, effect=None) -> dict:
        """Modification locale d'un formulaire : autorisée par le canal, enregistrée dans l'empreinte
        de la page ; sur un compte une éventuelle autosauvegarde est aussi journalisée."""
        target, info = self._target(ref)
        name = str(info.get("name") or "")
        if _HUMAN_ONLY_FIELD_RE.search(name) or mandates.SENSITIVE.search(name + " " + self._url):
            raise Refused(f"champ « {name} » réservé à l'humain (identifiants, second facteur, paiement)")
        identity = self._control_identity(target, info)
        sig = json.dumps(identity, ensure_ascii=False) if isinstance(identity, list) else identity
        if self.resource_key:
            # Account edits may autosave. Treat typing/select/check as real journaled effects.
            self._typed.setdefault(_page_key(self._url), {})[sig] = _digest([kind, value_for_digest])
            result = self._effect(kind, f"{info.get('role')}:{name}", command, [target, *args],
                                  expect=None, channel_id=channel_id, effect='edit')
            self._typed.setdefault(_page_key(self._url), {})[sig] = _digest([kind, value_for_digest])
            return result
        self._channel(channel_id, 'contact')
        result = self._cmd(command, [target, *args])
        if not result.get("success"):
            return {"ok": False, "error": agent_browser.redact(str(result.get("error") or ""))[:300]}
        self._typed.setdefault(_page_key(self._url), {})[sig] = _digest([kind, value_for_digest])
        return self._observe()

    # -- outils exposés au runtime -----------------------------------------------------------
    def navigate(self, url: str) -> dict:
        url = str(url or "").strip()
        if agent_browser.contains_secret(url):
            raise Refused("URL contenant un secret refusée (anti-exfiltration)")
        if self.lab and not self._is_lab(url):
            raise Refused(f"laboratoire hermétique ({LAB_ORIGINS_ENV}) : seules les origines déclarées sont "
                          "joignables, aucune autre sortie")
        if self.resource_key:
            account = resources.get(self.resource_key)['web_account']
            if (urlsplit(url).hostname or '').lower() not in account['domains']:
                raise Refused('sortie hors de la ressource compte isolée refusée')
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
                host = (urlsplit(url).hostname or "").lower()
                self.anonymous_domains.add(host)
        self._open(url)
        view = self._observe()
        self._qualify_page(view)
        return view

    def _qualify_page(self, view):
        page = self._safe_url(self._url)
        if self.resource_key:
            useful = True
        else:
            useful = mandates.covering(self.scope.business, 'public_business', 'contact')
        if not useful or not self.resource_key and not any(v.get('role') in ('textbox', 'button') for v in self._refs.values()):
            return
        from . import economy
        existing = journal.query('SELECT c.id FROM economic_channels c LEFT JOIN channel_authority a '
                                 'ON a.channel_id=c.id WHERE c.business=? AND c.locator=? '
                                 "AND COALESCE(a.resource_key,'')=?", (self.scope.business, page, self.resource_key or ''))
        cid = int(existing[0]['id']) if existing else economy.add_channel(
            self.scope.business, 'website', view.get('title') or page, created_by='policy:browser', locator=page,
            capabilities=[CAPABILITY], nature='observed', source_ref=page)
        if self.resource_key:
            mandates.bind_account(cid, self.scope.business, self.resource_key)
        else:
            mandates.qualify_public(cid, self.scope.business, source_url=page, observed_text=view.get('snapshot', ''))

    def snapshot(self, full: bool = False) -> dict:
        self._ensure_page()
        return self._observe(full=bool(full))

    def screenshot(self, full_page: bool = False) -> dict:
        self._ensure_page()
        self._check_challenge()
        self._check_account()
        # Recheck final URL, not the last commanded URL, before rendering a potentially redirected page.
        url = str(self._read('get', ['url']).get('url') or '')
        if not self._guard(url) or mandates.SECRET_SURFACE.search(url):
            raise Refused('surface de capture hors périmètre ou sensible')
        account = (resources.get(self.resource_key)['web_account'] if self.resource_key else
                   {'domains': [urlsplit(url).hostname]})
        image = capture_page(self._session, business=self.scope.business, scope_key=self.scope.key,
                             task_id=self.scope.task_id, resource_key=self.resource_key,
                             account=account, full_page=full_page)
        view = {'ok': True, 'url': self._safe_url(url), 'image': image, 'source': web_guard.UNTRUSTED_NOTE}
        if self.scope.task_id:
            tasks.save_step(self.scope.task_id, 'browser.capture:' + image['sha256'], image)
        return view

    def click(self, ref: str, expect: str | None = None, channel_id=None, effect=None) -> dict:
        self._ensure_page()
        target, info = self._target(ref)
        role, name = str(info.get("role") or ""), str(info.get("name") or "")
        # Observed disclosure controls open local UI; this is an affordance boundary,
        # not a choice of destination/action. Sensitive/effect controls remain guarded.
        disclosure = role == 'button' and isinstance(info.get('expanded'), bool) and info.get('haspopup') in ('true', 'menu', 'listbox')
        read_control = (role == 'link' or disclosure) and not _RISKY_LINK_RE.search(name) and not mandates.SENSITIVE.search(name)
        if read_control and channel_id in (None, ""):
            result = self._cmd("click", [target])  # simple navigation : lecture
            if not result.get("success"):
                return {"ok": False, "error": agent_browser.redact(str(result.get("error") or ""))[:300]}
            self._settle_page()
            return self._observe()
        return self._effect("click", f"{role}:{name}", "click", [target], expect=expect, channel_id=channel_id, effect=effect)

    def type(self, ref: str, text: str, channel_id=None, effect=None) -> dict:
        self._ensure_page()
        text = str(text or "")
        if agent_browser.contains_secret(text):
            raise Refused("saisie d'un secret refusée (anti-exfiltration)")
        return self._edit("type", ref, text, "fill", [text], channel_id, effect)

    def select(self, ref: str, value: str, channel_id=None, effect=None) -> dict:
        self._ensure_page()
        return self._edit("select", ref, str(value), "select", [str(value)], channel_id, effect)

    def check(self, ref: str, channel_id=None, effect=None) -> dict:
        self._ensure_page()
        return self._edit("check", ref, True, "check", [], channel_id, effect)

    def press(self, key: str, expect: str | None = None, channel_id=None, effect=None) -> dict:
        self._ensure_page()
        key = str(key or "").strip()
        if not key or len(key) > 40:
            raise Refused("touche invalide")
        if key in READ_SAFE_KEYS and not expect:
            result = self._cmd("press", [key])
            if not result.get("success"):
                return {"ok": False, "error": agent_browser.redact(str(result.get("error") or ""))[:300]}
            return self._observe()
        probe = self._read('eval', ['(() => { const octopus_commit_boundary = document.activeElement; '
            'const nodes = [octopus_commit_boundary]; '
            'if (octopus_commit_boundary && octopus_commit_boundary.form && octopus_commit_boundary.tagName !== "BUTTON") '
            'nodes.push(...octopus_commit_boundary.form.querySelectorAll("button, input[type=submit]")); '
            f'const pattern = new RegExp({json.dumps(mandates.SENSITIVE.pattern + "|" + mandates.IDENTITY_DELETION.pattern + "|" + mandates.SECRET_SURFACE.pattern)}, "i"); '
            'return nodes.filter(Boolean).some(n => n.matches("input[type=password]") || pattern.test([n.getAttribute("aria-label"), '
            'n.innerText, n.matches("input[type=submit]") ? n.value : ""].join(" "))); })()']).get('result')
        if probe is True:
            raise Refused('Paiement, engagement financier ou création de compte : intervention humaine requise.')
        if probe is not False:
            raise Refused('cible clavier indisponible : observe la page ou utilise une cible précise.')
        focus = self._read('eval', ['(() => { /* octopus_focus_identity */ '
            'let node = document.activeElement; if (!node) return null; '
            f'if ({json.dumps(key)} === "Enter" && node.form && '
            '(!node.matches("button,input[type=submit],input[type=button]") || node.type === "submit")) node = node.form; '
            'if (node.id && Array.from(document.querySelectorAll("[id]")).filter(n => n.id === node.id).length === 1) '
            'return JSON.stringify(["id", node.id]); '
            'const path = []; while (node) { const parent = node.parentElement; '
            'path.unshift([node.tagName, parent ? Array.from(parent.children).indexOf(node) : 0]); node = parent; } '
            'return JSON.stringify(path); })()']).get('result')
        if not isinstance(focus, str) or not focus:
            raise Refused('cible clavier indisponible : observe la page ou utilise une cible précise.')
        label = self._read('eval', ['(() => { /* octopus_commit_label */ const node = document.activeElement; '
            'if (!node) return null; const controls = node.form && !node.matches("button,input[type=submit]") '
            '? Array.from(node.form.querySelectorAll("button,input[type=submit]")) : [node]; '
            'return controls.map(n => [n.getAttribute("aria-label"), n.innerText, '
            'n.matches("input[type=submit]") ? n.value : ""].filter(Boolean).join(" ")).join(" "); })()']).get('result')
        if self.resource_key and (not isinstance(label, str) or not re.search(
                r'publi|publish|\bpost\b|deploy|déploi|envoy|send|reply|répond|message|invit|save|enregistr|modifi|update', label, re.I)):
            raise Refused('effet de la cible clavier inconnu : utiliser un contrôle observé précis')
        return self._effect("press", f"key:{key} {label or ''}", "press", [key], expect=expect, channel_id=channel_id,
                            target_context=_digest(focus), effect=effect)

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

    def verify(self, text: str = '', action_id=None, state=None) -> dict:
        """Constat sur la page réelle. Avec une action à effet non vérifiée, la marque `verified`
        seulement si le texte est présent maintenant et absent avant l'action."""
        self._ensure_page()
        if state is not None:
            if state not in ('authenticated', 'unauthenticated', 'challenge', 'uncertain'):
                raise Refused('état sémantique invalide')
            if action_id is not None or text:
                raise Refused('état de session et preuve d’action doivent rester séparés')
            if state in ('challenge', 'unauthenticated'):
                if self.resource_key:
                    resources.set_account_session(self.resource_key,
                        'connection_required' if state == 'challenge' else 'expired',
                        detail='semantic_' + state,
                        verification={'method': 'semantic_observation', 'state': state,
                                      'business': self.scope.business, 'task_id': self.scope.task_id})
                raise HumanBrowserRequired(self.resource_key, need='captcha' if state == 'challenge' else 'login')
            return {'ok': True, 'state': state, 'method': 'semantic_observation',
                    'note': 'interprétation du modèle, aucune nouvelle autorité ; poursuis les observations si uncertain'}
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
        if not payload.get('pre_text_complete', 0 < len(payload.get('pre_text') or '') < 8000):
            view.update(verified=False, status=row['status'],
                        note="L'observation préalable ancienne est incomplète : ce texte ne prouve pas un nouvel effet. Ne répète pas un effet incertain.")
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
        """Télécharge un fichier dans l'espace de la tâche et le constate sur le disque.

        La commande `download` d'agent-browser n'est PAS utilisée : sous Windows elle canonicalise
        le répertoire en chemin verbatim `\\\\?\\C:\\...` que `Browser.setDownloadBehavior` accepte
        puis dont Chrome annule chaque téléchargement (vercel-labs/agent-browser#1659, présent de
        0.26.0 à 0.33.x). Le répertoire de la tâche est donc donné à Chromium au lancement
        (`--download-path`, chemin ordinaire) et le lien est simplement cliqué ; seul un fichier
        complet, apparu dans ce répertoire après le clic, compte comme téléchargé.
        """
        self._ensure_page()
        target, info = self._target(ref)
        name = _safe_filename(filename)
        if str(info.get("role")) != "link" or _RISKY_LINK_RE.search(str(info.get("name") or "")):
            self._channel(None)
        before = {p.name for p in self._inbox.iterdir()}
        self._read("click", [target])
        found = self._await_download(before)
        if found is None:
            return {"ok": False, "error": "aucun fichier complet apparu dans l'espace de la tâche après le clic "
                                          "(lien non téléchargeable ou téléchargement annulé) ; observe la page"}
        path = self._inbox / name
        if found.name != name:
            found.replace(path)
        if not path.is_file() or not _within(path, self._inbox):
            return {"ok": False, "error": "téléchargement non constaté dans l'espace de la tâche"}
        digest, size = _sha256(path), path.stat().st_size
        url = agent_browser.redact(self._current_url())
        self._record_file(name, digest, size, url)
        return {"ok": True, "file": str(path), "bytes": size, "sha256": digest, "url": url}

    def _await_download(self, before: set[str]) -> Path | None:
        """Premier fichier nouveau, complet (pas de suffixe partiel) et de taille stable."""
        started = time.monotonic()
        seen: dict[str, int] = {}
        while time.monotonic() - started < DOWNLOAD_TIMEOUT_S:
            fresh = [p for p in self._inbox.iterdir() if p.name not in before]
            if not fresh and time.monotonic() - started > DOWNLOAD_START_S:
                return None
            for candidate in fresh:
                if candidate.name.lower().endswith(_PARTIAL_SUFFIXES) or not candidate.is_file():
                    continue
                size = candidate.stat().st_size
                if seen.get(candidate.name) == size:
                    return candidate
                seen[candidate.name] = size
            time.sleep(0.1)
        return None

    def _record_file(self, name: str, digest: str, size: int, url: str) -> None:
        """Registre persistant des fichiers conservés par la tâche (mesuré par le superviseur)."""
        if not self.scope.task_id:
            return
        files = [f for f in tasks.step_value(self.scope.task_id, FILES_KEY, []) if f.get("file") != name]
        files.append({"file": name, "sha256": digest, "bytes": size, "url": url, "at": time.time()})
        tasks.save_step(self.scope.task_id, FILES_KEY, files)

    def upload(self, ref: str, filename: str, channel_id=None, effect=None) -> dict:
        self._ensure_page()
        path = outbox_dir(self.scope.business) / _safe_filename(filename)
        if not _within(path, outbox_dir(self.scope.business)) or not path.is_file():
            raise Refused(f"fichier absent de {outbox_dir(self.scope.business)} (seuls les fichiers préparés "
                          "pour l'envoi sont téléversables)")
        if path.stat().st_size > UPLOAD_MAX_BYTES:
            raise Refused("fichier trop volumineux")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return self._edit("upload", ref, digest, "upload", [str(path)], channel_id, effect)


def _row_task(row) -> int | None:
    try:
        task_id = json.loads(row["payload"] or "{}").get("task_id")
        return int(task_id) if task_id else None
    except (TypeError, ValueError):
        return None


def _lineage(task_id: int | None) -> str | None:
    """Objectif dont la tâche est une tentative (travail du superviseur), sinon None."""
    if not task_id:
        return None
    from .supervisor import WORK_KIND
    task = tasks.get(int(task_id))
    if not task or task.get("kind") != WORK_KIND:
        return None
    objective_id = (task.get("input") or {}).get("objective_id")
    return f"{task['business']}:objective:{int(objective_id)}" if objective_id else None


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
def mission_scope(*, isolated=False):
    """Portée d'une exécution (mission ou agent) : les sessions navigateur y sont partagées par les
    sous-agents puis fermées à la sortie de la portée la plus externe."""
    if _ACTIVE.get() is not None and not isolated:
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


def kept_files(business: str, task_id: int) -> list[dict]:
    """Fichiers téléchargés par la tâche ET toujours présents, intacts, dans son espace.

    Un fichier absent, déplacé hors de l'espace ou modifié (sha256 différent) ne compte pas : la
    mesure porte sur le disque, jamais sur une déclaration du modèle ou un résultat d'outil.
    """
    inbox = task_inbox(business, f"t{int(task_id)}")
    kept = []
    for entry in tasks.step_value(int(task_id), FILES_KEY, []):
        try:
            path = inbox / _safe_filename(entry.get("file"))
        except Refused:
            continue
        if path.is_file() and _within(path, inbox) and _sha256(path) == entry.get("sha256"):
            kept.append({**entry, "path": str(path)})
    return kept


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
        result = getattr(space, method)(**kwargs)
    except Refused as exc:
        result = {"ok": False, "refused": True, "reason": str(exc)}
    except (agent_browser.BackendUnavailable, RuntimeError) as exc:
        result = {"ok": False, "error": agent_browser.redact(str(exc))[:500]}
    space.observe(method, result)
    return result


def trace_args(args):
    """Allowlist; form contents, filenames and arbitrary model-supplied extras are never logged."""
    out = {}
    if not isinstance(args, dict):
        return out
    for key in ('ref', 'effect', 'full', 'full_page', 'direction', 'state', 'action_id', 'channel_id'):
        value = args.get(key)
        if key == 'ref' and (not isinstance(value, str) or not re.fullmatch(r'@?e[0-9]+', value)):
            value = '[invalid ref]' if value is not None else None
        if key == 'effect' and value not in (None, 'contact', 'publish', 'edit'):
            value = '[invalid effect]'
        if key == 'direction' and value not in (None, 'up', 'down', 'left', 'right'):
            value = '[invalid direction]'
        if key == 'state' and value not in (None, 'authenticated', 'unauthenticated', 'challenge', 'uncertain'):
            value = '[invalid state]'
        if key in ('channel_id', 'action_id') and value is not None and type(value) is not int:
            value = '[invalid type]'
        if value is not None:
            out[key] = agent_browser.redact(str(value))[:80] if isinstance(value, str) else value if type(value) in (int, bool) else '[invalid type]'
    if 'url' in args and isinstance(args['url'], str):
        out['url'] = trace_url(args['url'])
    if 'text' in args:
        out['text'] = '[omitted]'
    if 'expect' in args:
        out['expect'] = '[omitted]'
    if 'key' in args:
        out['key'] = args['key'] if args['key'] in ('Tab', 'Escape', 'Enter', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight') else '[omitted]'
    return out


def trace_url(url):
    try:
        parts = urlsplit(str(url))
        if agent_browser.contains_secret(str(url)):
            return '[redacted]'
        return agent_browser.redact(f'{parts.scheme}://{parts.hostname or ""}{":" + str(parts.port) if parts.port else ""}{parts.path}')[:500]
    except ValueError:
        return '[invalid URL]'


class BrowserTrajectory:
    """Observe progress without choosing continuation or proving goal completion."""
    def __init__(self, steps=()):
        self.states, self.actions = [], []
        self.url = ''
        self.observation = None
        self.no_progress = 0
        self.errors = 0
        self.observed_urls = set()
        for step in steps:
            self.record(step.get('tool', ''), step.get('args') or {}, step.get('result_data') or {})

    def record(self, tool, args, result):
        before_url, before_hash = self.url, self.observation
        safe_args = args if isinstance(args, dict) else {}
        url_provenance = ('observed_or_historical' if isinstance(safe_args.get('url'), str) and safe_args['url'] in self.observed_urls else
                          'model_supplied_unobserved') if tool == 'browser_navigate' and isinstance(args, dict) else None
        result = result if isinstance(result, dict) else {}
        error = bool(result.get('refused') or result.get('error') or result.get('ok') is False)
        self.errors = self.errors + 1 if error else 0
        if not error and result.get('url'):
            self.url = trace_url(result['url'])
        observed = any(k in result for k in ('snapshot', 'image'))
        if observed and not error:
            if result.get('url'):
                self.observed_urls.add(result['url'])
            for element in result.get('elements') or []:
                if isinstance(element, dict) and element.get('href'):
                    self.observed_urls.add(element['href'])
            image = result.get('image') or {}
            # Ref churn alone is not information/progress.
            payload = [self.url, re.sub(r'@?e[0-9]+', '@ref', str(result.get('snapshot') or '')),
                       image.get('sha256') if isinstance(image, dict) else None]
            self.observation = _digest(payload)
        new = bool(observed and not error and self.observation not in self.states)
        self.no_progress = 0 if new else self.no_progress + 1
        if self.observation:
            self.states.append(self.observation)
        sig = _digest([tool, trace_args(args)])
        self.actions.append(sig)
        repeated = len(self.actions) >= 3 and len(set(self.actions[-3:])) == 1 and not new
        cycle = len(self.states) >= 4 and self.states[-4] == self.states[-2] and self.states[-3] == self.states[-1] and self.states[-1] != self.states[-2]
        reason = ('repeated_errors' if self.errors >= 2 else 'repeated_action' if repeated else
                  'navigation_cycle' if cycle else 'no_new_observation' if self.no_progress >= 3 else None)
        return {'url_before': before_url, 'url_after': self.url, 'observation_before': before_hash,
                'observation_after': self.observation, 'progress': new, 'stagnation': reason,
                'tool_ok': not error, 'url_provenance': url_provenance, 'screenshot_requested': tool == 'browser_screenshot'}


def record_controller_trace(trace):
    scope = current_scope()
    # This event contains no prompt, raw observation, form content or image bytes.
    tasks.emit(scope.business, scope.task_id, 'browser.controller', trace)
    if scope.task_id:
        tasks.save_step(scope.task_id, 'browser.controller', trace)
