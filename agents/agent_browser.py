"""Backend navigateur Hermes pour OCTOPUS : CLI `agent-browser` + Chromium.

Hermes (NousResearch/hermes-agent@59004a62356f3a4697ab0fe8ad5086d2b405e2a6, MIT) pilote son
navigateur par le CLI natif `agent-browser` (vercel-labs, Apache-2.0), version 0.26.0 figée dans
`pm/lock.json`. OCTOPUS reprend ce même backend et adapte la couche de commande de Hermes :

- `tools/browser_tool_session.py` : lancement du CLI avec sorties dans des fichiers temporaires
  (le démon hérite des descripteurs : des pipes ne verraient jamais EOF), drapeaux Windows,
  contournement des shims `.cmd` (`eval --base64`, `batch` sur stdin), interprétation JSON ;
- `tools/browser_tool_snapshot.py` : troncature d'un arbre d'accessibilité aux limites de ligne ;
- `agent/redact.py` : motifs de secrets masqués à la frontière du modèle.

Aucune dépendance au runtime Hermes (config, PM, bot desktop, secret scope, cloud providers).
Différences voulues : environnement enfant construit par liste blanche, AUCUNE installation
implicite (voir `scripts/install_agent_browser.py`), aucune relance automatique d'une commande
(une commande à effet relancée à l'aveugle pourrait agir deux fois), trafic Chromium forcé à
travers le proxy de garde OCTOPUS (`web_guard.GuardProxy`), y compris le loopback.
"""
from __future__ import annotations

import base64
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import config

AGENT_BROWSER_VERSION = "0.26.0"
AGENT_BROWSER_SHA256 = "8a48cf4110d7dc2c12c1c4d6d25e0babdfa31604b537f05df0dc835fe2f854bf"
AGENT_BROWSER_URL = f"https://registry.npmjs.org/agent-browser/-/agent-browser-{AGENT_BROWSER_VERSION}.tgz"

DEFAULT_TIMEOUT_S = 30
OPEN_TIMEOUT_S = 60
FIRST_OPEN_TIMEOUT_S = 120
DAEMON_IDLE_S = 300
_EMPTY_OK = frozenset({"close"})

# Variables transmises au CLI : système, affichage et bibliothèques uniquement. Aucune clé LLM,
# aucun proxy hérité, aucun AGENT_BROWSER_* utilisateur (il pourrait élargir la politique).
_ENV_ALLOW = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ", "DISPLAY",
    "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
    "LD_LIBRARY_PATH", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
})

# Chromium ne doit sortir que par le proxy de garde : loopback compris (`<-loopback>`), sans QUIC
# ni UDP WebRTC non proxifié. Les services propres au navigateur (rapports de plantage, modèles de
# phishing, fiabilité de domaine, pings d'audit, configuration de field trials) sont coupés : sous
# Windows, Playwright fournit Chrome for Testing, qui embarque les clés des services Google et
# les contacte de lui-même (le Chromium open source sans clés ne le fait pas). Interrupteurs repris
# de la liste éprouvée de Playwright (`chromiumSwitches.ts`), en plus de ceux qu'agent-browser
# pose déjà (`--disable-background-networking`, `--disable-component-update`, `--disable-sync`...).
# Pas de `--disable-features` : agent-browser découpe `--args` aux virgules et Chrome ne garde que
# la dernière occurrence ; les points de service restants sont refusés par le proxy
# (`web_guard.browser_service`).
_GUARD_ARGS = ("--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
               "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
               "--disable-field-trial-config", "--disable-breakpad", "--disable-client-side-phishing-detection",
               "--disable-component-extensions-with-background-pages", "--disable-domain-reliability",
               "--no-pings", "--metrics-recording-only")


# Page de démarrage. Sans URL, Chrome for Testing ouvre `chrome://new-tab-page` ; agent-browser
# ignore cet onglet interne et crée le sien, mais la page Nouvel onglet reste ouverte et contacte
# Google d'elle-même (`www.google.com/async/newtab_ogb`, `newtab_promos`, `ddljson`, puis la barre
# OneGoogle : `www.gstatic.com`, `ogads-pa.clients6.google.com`). Un argument non-option est une
# URL à ouvrir À LA PLACE de cette page ; agent-browser reprend alors cet onglet `about:blank`.
STARTUP_URL = "about:blank"


class BackendUnavailable(RuntimeError):
    """Le binaire agent-browser ou Chromium est absent : rien n'est installé implicitement."""


# --- découverte (jamais d'installation) -------------------------------------------------------

def platform_target() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if sys.platform.startswith("win"):
        return "win32-x64"  # agent-browser ne publie que x64 ; ARM64 l'émule (comme Hermes)
    if sys.platform == "darwin":
        return f"darwin-{arch}"
    return f"linux-{arch}"


def binary_name(target: str | None = None) -> str:
    target = target or platform_target()
    return f"agent-browser-{target}{'.exe' if target.startswith('win32') else ''}"


def install_dir() -> Path:
    return config.DATA_DIR / "bin"


def find_binary() -> str | None:
    """OCTOPUS_AGENT_BROWSER, puis le binaire installé par le script dédié, puis le PATH."""
    explicit = os.environ.get("OCTOPUS_AGENT_BROWSER", "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else None
    local = install_dir() / binary_name()
    if local.is_file():
        return str(local)
    return shutil.which("agent-browser")


@functools.lru_cache(maxsize=1)
def _playwright_chromium() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        return path if path and Path(path).is_file() else None
    except Exception:
        return None


def chromium_executable() -> str | None:
    """OCTOPUS_CHROMIUM, sinon le Chromium déjà installé pour Playwright (aucun second navigateur)."""
    explicit = os.environ.get("OCTOPUS_CHROMIUM", "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else None
    return _playwright_chromium()


def availability() -> dict:
    binary, chromium = find_binary(), chromium_executable()
    return {"agent_browser": binary, "chromium": chromium, "ready": bool(binary and chromium),
            "expected_version": AGENT_BROWSER_VERSION, "target": platform_target()}


def require_backend() -> tuple[str, str]:
    info = availability()
    if not info["agent_browser"]:
        raise BackendUnavailable("agent-browser introuvable : lancer `python scripts/install_agent_browser.py` "
                                 "(ou définir OCTOPUS_AGENT_BROWSER)")
    if not info["chromium"]:
        raise BackendUnavailable("Chromium introuvable : lancer `python -m playwright install chromium` "
                                 "(ou définir OCTOPUS_CHROMIUM)")
    return info["agent_browser"], info["chromium"]


def plain_path(path) -> str:
    """Chemin absolu ordinaire, sans préfixe verbatim Windows `\\\\?\\`.

    Chrome annule tout téléchargement dont le répertoire porte ce préfixe ou des barres obliques
    (vercel-labs/agent-browser#1659) ; seul `C:\\dossier` avec des barres inverses est accepté.
    Les chemins UNC verbatim (`\\\\?\\UNC\\...`) redeviennent `\\\\serveur\\partage`.
    """
    text = os.fspath(path)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\") and len(text) > 5 and text[5] == ":":
        text = text[4:]
    return os.path.abspath(text)


def launch_args_ok(args) -> bool:
    """agent-browser découpe `--args` aux virgules et aux retours à la ligne : un argument qui en
    contient serait éclaté en morceaux que Chromium interpréterait autrement."""
    return all("," not in a and "\n" not in a and "\r" not in a for a in args)


def child_env(socket_dir: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOW}
    env["AGENT_BROWSER_SOCKET_DIR"] = socket_dir
    env["AGENT_BROWSER_IDLE_TIMEOUT_MS"] = str(DAEMON_IDLE_S * 1000)
    return env


# --- couche de commande adaptée de Hermes browser_tool_session.py ------------------------------

def shim_safe_args(argv0: str, command: str, args: list[str]) -> tuple[str, list[str], bytes | None]:
    """cmd.exe re-parse les arguments d'un shim `.cmd` : un saut de ligne coupe l'argument et `%VAR%`
    est développé. `eval` passe en base64 ; toute autre commande porteuse de ces caractères est
    envoyée comme `batch` JSON sur stdin. Un exécutable natif reçoit l'argv brut."""
    if not args or not argv0.lower().endswith((".cmd", ".bat")):
        return command, args, None
    if command == "eval":
        script, *rest = args
        return command, ["--base64", base64.b64encode(script.encode("utf-8")).decode("ascii"), *rest], None
    if command == "batch" or not any(ch in arg for arg in args for ch in "\r\n%"):
        return command, args, None
    return "batch", [], json.dumps([[command, *args]]).encode("utf-8")


def unwrap_batch_result(result: Any, command: str) -> dict:
    if not isinstance(result, list):
        return result
    if len(result) != 1 or not isinstance(result[0], dict):
        return {"success": False, "error": f"sortie batch inattendue pour {command!r}: {json.dumps(result)[:300]}"}
    entry = result[0]
    return {"success": bool(entry.get("success")), "data": entry.get("result"), "error": entry.get("error")}


def interpret_output(command: str, stdout: str, stderr: str, returncode: int) -> dict:
    text = (stdout or "").strip()
    if not text:
        if returncode != 0:
            return {"success": False, "error": (stderr or "").strip()[:2000] or f"code {returncode}",
                    "returncode": returncode}
        if command in _EMPTY_OK:
            return {"success": True, "data": {}}
        return {"success": False, "error": f"commande {command!r} sans sortie (démon périmé ?)",
                "returncode": returncode}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"success": False, "error": f"sortie non JSON pour {command!r}: {text[:300]}",
                "returncode": returncode}
    if not isinstance(parsed, (dict, list)):
        return {"success": False, "error": f"sortie inattendue pour {command!r}", "returncode": returncode}
    if isinstance(parsed, dict):
        parsed.setdefault("returncode", returncode)
    return parsed


def _popen(argv: list[str], env: dict, work_dir: str, tag: str, stdin_payload: bytes | None):
    """Sorties vers des fichiers : le CLI forke un démon qui hérite des descripteurs. Windows :
    CREATE_NO_WINDOW seulement et STARTF_USESTDHANDLES + close_fds (repris de Hermes)."""
    paths = [os.path.join(work_dir, f"_{slot}_{tag}") for slot in ("stdout", "stderr")]
    fds = [os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600) for p in paths]
    stdin: Any = subprocess.DEVNULL
    if stdin_payload is not None:
        stdin_path = os.path.join(work_dir, f"_stdin_{tag}")
        Path(stdin_path).write_bytes(stdin_payload)
        fds.append(os.open(stdin_path, os.O_RDONLY))
        stdin = fds[-1]
    try:
        extra: dict = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
            extra = {"creationflags": 0x08000000, "close_fds": True, "startupinfo": startup}
        return subprocess.Popen(argv, stdout=fds[0], stderr=fds[1], stdin=stdin, env=env, **extra), paths
    finally:
        for fd in fds:
            os.close(fd)


def _read_and_unlink(paths: list[str]) -> list[str]:
    out = []
    for path in paths:
        try:
            out.append(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            out.append("")
        try:
            os.unlink(path)
        except OSError:
            pass
    return out


def _socket_root() -> str:
    # Chemin court : limite AF_UNIX (~108 octets) pour le socket du démon et celui de Chrome.
    root = Path("/tmp" if os.name != "nt" and Path("/tmp").is_dir() else tempfile.gettempdir()) / "octopus-ab"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


class Session:
    """Une session agent-browser nommée (un démon, un Chromium, des refs @eN stables)."""

    def __init__(self, name: str, *, proxy_url: str, profile_dir: Path | None = None, headed: bool = False,
                 download_dir: Path | None = None, extra_args: tuple[str, ...] = ()):
        self.binary, self.chromium = require_backend()
        self.name = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:40]
        self.proxy_url = proxy_url
        self.profile_dir = profile_dir
        self.headed = headed
        self.download_dir = download_dir
        self.extra_args = tuple(extra_args) + tuple(
            a.strip() for a in os.environ.get("OCTOPUS_BROWSER_ARGS", "").split(",") if a.strip())
        self.work_dir = os.path.join(_socket_root(), self.name)
        os.makedirs(self.work_dir, mode=0o700, exist_ok=True)
        self._opened = False

    def _global_args(self) -> list[str]:
        launch = [*_GUARD_ARGS, f"--proxy-server={self.proxy_url}", "--proxy-bypass-list=<-loopback>",
                  *self.extra_args, STARTUP_URL]
        if not launch_args_ok(launch):
            raise ValueError("argument Chromium contenant une virgule ou un retour à la ligne : agent-browser "
                             "le découperait (OCTOPUS_BROWSER_ARGS attend des arguments séparés par des virgules)")
        args = ["--session", self.name, "--executable-path", self.chromium, "--args", ",".join(launch)]
        if self.profile_dir is not None:
            args += ["--profile", str(self.profile_dir)]
        if self.download_dir is not None:
            args += ["--download-path", plain_path(self.download_dir)]
        if self.headed:
            args.append("--headed")
        return args

    def run(self, command: str, args: list[str] | tuple = (), *, timeout: float | None = None,
            should_abort: Callable[[], bool] | None = None) -> dict:
        """Exécute UNE commande, sans relance. `should_abort` (arrêt humain) tue le CLI : le
        résultat devient alors indéterminé pour une commande à effet, jamais un succès."""
        args = [str(a) for a in args]
        if timeout is None:
            timeout = (FIRST_OPEN_TIMEOUT_S if not self._opened else OPEN_TIMEOUT_S) if command == "open" \
                else DEFAULT_TIMEOUT_S
        spawn_command, spawn_args, stdin_payload = shim_safe_args(self.binary, command, args)
        argv = [self.binary, *self._global_args(), "--json", spawn_command, *spawn_args]
        tag = f"{command}_{os.getpid()}_{time.monotonic_ns()}"
        proc, paths = _popen(argv, child_env(self.work_dir), self.work_dir, tag, stdin_payload)
        deadline = time.monotonic() + float(timeout)
        aborted = timed_out = False
        polls = 0
        while True:
            try:
                proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                polls += 1
                if should_abort is not None and polls % 5 == 0 and should_abort():
                    aborted = True
                elif time.monotonic() >= deadline:
                    timed_out = True
                if aborted or timed_out:
                    proc.kill()
                    proc.wait()
                    break
        stdout, stderr = _read_and_unlink(paths)
        if stdin_payload is not None:
            _read_and_unlink([os.path.join(self.work_dir, f"_stdin_{tag}")])
        if aborted:
            return {"success": False, "error": "commande interrompue (arrêt demandé)", "indeterminate": True}
        if timed_out:
            return {"success": False, "error": f"délai de {timeout:.0f} s dépassé pour {command!r}",
                    "indeterminate": True}
        result = unwrap_batch_result(interpret_output(command, stdout, stderr, proc.returncode), command)
        if command == "open" and result.get("success"):
            self._opened = True
        return result

    def close(self) -> None:
        try:
            self.run("close", timeout=15)
        except Exception:
            pass
        self._opened = False


# --- vue modèle : troncature (browser_tool_snapshot.py) et masquage (agent/redact.py) -----------

_SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9_-](?:\.?[A-Za-z0-9_-]){9,}", r"ghp_[A-Za-z0-9]{10,}", r"github_pat_[A-Za-z0-9_]{10,}",
    r"gh[ousr]_[A-Za-z0-9]{10,}", r"xox[baprs]-[A-Za-z0-9-]{10,}", r"AIza[A-Za-z0-9_-]{30,}",
    r"AKIA[A-Z0-9]{16}", r"[sr]k_(?:live|test)_[A-Za-z0-9]{10,}", r"SG\.[A-Za-z0-9_-]{10,}",
    r"hf_[A-Za-z0-9]{10,}", r"npm_[A-Za-z0-9]{10,}", r"pplx-[A-Za-z0-9]{10,}", r"r8_[A-Za-z0-9]{10,}",
)
SECRET_RE = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(_SECRET_PATTERNS) + r")(?![A-Za-z0-9_-])")


def contains_secret(text: str) -> bool:
    from urllib.parse import unquote
    text = str(text or "")
    return bool(SECRET_RE.search(text) or SECRET_RE.search(unquote(text)))


def redact(text: str) -> str:
    return SECRET_RE.sub(lambda m: m.group(1)[:4] + "…[masqué]", str(text or ""))


_NOISE_LINE_RE = re.compile(r'^\s*- (?:ListMarker "[^"]*"|StaticText "[\s\W_]*")\s*$')


def prune_snapshot(text: str) -> str:
    """Arbre complet sans le bruit (puces, séparateurs de ponctuation). Le mode compact (`-c`) de
    agent-browser, utilisé par défaut par Hermes, supprime aussi les éléments de liste purement
    textuels : l'IA ne verrait pas le contenu d'une page de résultats."""
    return "\n".join(line for line in str(text or "").split("\n") if not _NOISE_LINE_RE.match(line))


def truncate_snapshot(text: str, max_chars: int) -> str:
    """Coupe aux limites de ligne (jamais au milieu d'un élément) et indique le reste."""
    if len(text) <= max_chars:
        return text
    lines, kept, size = text.split("\n"), [], 0
    reserve = min(120, max_chars // 2)
    for line in lines:
        if size + len(line) + 1 > max_chars - reserve:
            break
        kept.append(line)
        size += len(line) + 1
    remaining = len(lines) - len(kept)
    kept.append(f"[... {remaining} lignes tronquées : browser_scroll ou browser_snapshot(full=true)]")
    return "\n".join(kept)
