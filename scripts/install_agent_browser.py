"""Installe le binaire natif agent-browser utilisé par le backend navigateur (repris de Hermes).

Action explicite de l'opérateur : OCTOPUS n'installe jamais rien implicitement. Le paquet npm
officiel (version et sha256 figés comme dans `pm/lock.json` de Hermes @59004a6) est téléchargé,
vérifié, puis seul le binaire de la plateforme courante est extrait dans `agents/data/bin/`
(ou `--dest`). Aucun Node.js requis. Chromium reste celui de Playwright :
`python -m playwright install chromium`.

    python scripts/install_agent_browser.py            # Windows, Linux, macOS
    python scripts/install_agent_browser.py --tarball agent-browser-0.26.0.tgz   # hors ligne
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import stat
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import agent_browser  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=None, help="dossier cible (défaut : agents/data/bin)")
    parser.add_argument("--tarball", type=Path, default=None, help="archive npm déjà téléchargée")
    parser.add_argument("--target", default=None, help="plateforme (défaut : détectée), ex. win32-x64")
    args = parser.parse_args(argv)

    if args.tarball:
        blob = args.tarball.read_bytes()
    else:
        print(f"téléchargement {agent_browser.AGENT_BROWSER_URL}")
        with urllib.request.urlopen(agent_browser.AGENT_BROWSER_URL, timeout=120) as response:
            blob = response.read()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != agent_browser.AGENT_BROWSER_SHA256:
        print(f"ÉCHEC : sha256 {digest} != {agent_browser.AGENT_BROWSER_SHA256} attendu", file=sys.stderr)
        return 2

    name = agent_browser.binary_name(args.target)
    dest_dir = args.dest or agent_browser.install_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        member = archive.getmember(f"package/bin/{name}")
        data = archive.extractfile(member).read()
    target = dest_dir / name
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(tmp, target)
    print(f"agent-browser {agent_browser.AGENT_BROWSER_VERSION} installé : {target}")
    print("Chromium : `python -m playwright install chromium` puis `python -m octopus browser doctor`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
