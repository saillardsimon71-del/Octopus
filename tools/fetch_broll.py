"""Images de b-roll par offre : une image reelle par segment, au lieu des 4 photos livrees avec le depot.

Usage : python tools/fetch_broll.py <job.json> <offer_id>

Chaine de fournisseurs (le premier qui repond sert la requete) :
- pexels     : PEXELS_API_KEY (gratuit, 200 requetes/heure, usage commercial, lien vers Pexels demande)
- pixabay    : PIXABAY_API_KEY (gratuit, usage commercial)
- openverse  : sans compte ni cle ; filtre sur les licences autorisant l'usage commercial,
               CC0 et domaine public d'abord, sinon attribution enregistree dans credits.json

Sans reseau ni cle, le script ne casse rien : il laisse les images par defaut du depot.
Les images sont recadrees en 1080x1350 (format de la carte Remotion) et ecrites dans
remotion/public/img/<offer>/<role>.jpg ; le job recoit visuel.<role>.img.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "octopus-podalux/1.0 (https://github.com/saillardsimon71-del/Octopus)"}
# Requetes en anglais : les banques d'images sont indexees en anglais.
ROLE_QUERIES = {
    "hook": "freelancer laptop desk work",
    "douleur": "stressed office worker deadline",
    "preuve": "contract signature document pen",
    "soulagement": "business handshake agreement",
    "cta": "smartphone notification hand",
}
FREE_LICENSES = ("cc0", "pdm")
SIZE = "1080:1350"


def _get(url: str, headers: dict | None = None, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pexels(query: str, key: str) -> dict | None:
    url = ("https://api.pexels.com/v1/search?orientation=portrait&per_page=1&query="
           + urllib.parse.quote(query))
    data = json.loads(_get(url, {"Authorization": key}))
    for photo in data.get("photos", []):
        return {"url": photo["src"]["large2x"], "credit": f"Photo de {photo['photographer']} sur Pexels",
                "source": photo["url"], "licence": "Pexels License", "provider": "pexels"}
    return None


def pixabay(query: str, key: str) -> dict | None:
    url = (f"https://pixabay.com/api/?key={key}&image_type=photo&orientation=vertical&per_page=3&q="
           + urllib.parse.quote(query))
    data = json.loads(_get(url))
    for hit in data.get("hits", []):
        return {"url": hit["largeImageURL"], "credit": f"Image de {hit['user']} sur Pixabay",
                "source": hit["pageURL"], "licence": "Pixabay Content License", "provider": "pixabay"}
    return None


def openverse(query: str, _key: str = "") -> dict | None:
    url = ("https://api.openverse.org/v1/images/?license_type=commercial&size=large&page_size=8&q="
           + urllib.parse.quote(query))
    results = json.loads(_get(url)).get("results", [])
    results.sort(key=lambda r: 0 if (r.get("license") or "").lower() in FREE_LICENSES else 1)
    for item in results:
        if item.get("url"):
            return {"url": item["url"], "credit": item.get("attribution") or item.get("title", ""),
                    "source": item.get("foreign_landing_url") or item["url"],
                    "licence": f"{item.get('license', '?')} {item.get('license_version', '')}".strip(),
                    "provider": "openverse"}
    return None


def providers() -> list[tuple[str, callable, str]]:
    import os
    return [("pexels", pexels, os.environ.get("PEXELS_API_KEY", "").strip()),
            ("pixabay", pixabay, os.environ.get("PIXABAY_API_KEY", "").strip()),
            ("openverse", openverse, "always")]


def fetch_one(query: str) -> dict | None:
    for name, fn, key in providers():
        if not key:
            continue
        try:
            found = fn(query, key) if name != "openverse" else fn(query)
        except Exception as exc:  # reseau, quota, format : on passe au suivant
            print(f"broll: {name} indisponible ({type(exc).__name__}: {str(exc)[:100]})")
            continue
        if found:
            return found
    return None


def main() -> int:
    job_path, offer = Path(sys.argv[1]), sys.argv[2]
    job = json.loads(job_path.read_text(encoding="utf-8"))
    keywords = " ".join(job.get("keywords", [])[:2])
    target_dir = ROOT / "remotion" / "public" / "img" / offer
    target_dir.mkdir(parents=True, exist_ok=True)
    credits, used = [], 0
    visuel = job.setdefault("visuel", {})
    for role, base_query in ROLE_QUERIES.items():
        found = fetch_one(base_query)
        if not found:
            print(f"broll: {role} -> image par defaut du depot")
            continue
        raw = target_dir / f"{role}.src"
        try:
            raw.write_bytes(_get(found["url"], timeout=60))
        except Exception as exc:
            print(f"broll: telechargement {role} echoue ({type(exc).__name__}: {str(exc)[:80]})")
            continue
        jpg = target_dir / f"{role}.jpg"
        crop = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf",
                               f"scale={SIZE}:force_original_aspect_ratio=increase,crop={SIZE}",
                               "-q:v", "3", str(jpg)], capture_output=True, text=True)
        raw.unlink(missing_ok=True)
        if crop.returncode != 0 or not jpg.exists():
            print(f"broll: recadrage {role} echoue ({crop.stderr[-120:].strip()})")
            continue
        visuel.setdefault(role, {})["img"] = f"{offer}/{role}.jpg"
        credits.append({"role": role, "query": f"{base_query} ({keywords})", **found})
        used += 1
        print(f"broll: {role} <- {found['provider']} ({found['licence']})")
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ROOT / "out" / offer
    out.mkdir(parents=True, exist_ok=True)
    (out / "credits.json").write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "credits.txt").write_text(
        "\n".join(f"{c['credit']} — {c['licence']} — {c['source']}" for c in credits), encoding="utf-8")
    print(f"broll: {used}/{len(ROLE_QUERIES)} images, credits dans {out / 'credits.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
