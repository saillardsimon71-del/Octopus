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
import zlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "octopus-podalux/1.0 (https://github.com/saillardsimon71-del/Octopus)"}
# Requetes en anglais : les banques d'images sont indexees en anglais.
# Plusieurs formulations par role : les banques libres sont inegalement fournies, la premiere
# requete qui donne un resultat gagne. Sans aucun resultat, l'image du depot est conservee.
ROLE_QUERIES = {
    "hook": ["laptop desk office", "freelancer working", "working computer"],
    "douleur": ["office stress", "worried man", "tired working"],
    "preuve": ["signing contract", "business document", "contract signature"],
    "soulagement": ["business handshake", "handshake deal", "business meeting"],
    "cta": ["phone hand", "using smartphone", "mobile phone"],
}
# Openverse agrege aussi des musees : sans filtre, "worried man" renvoie un buste romain.
# On se limite aux banques de photos de stock libres, ce qui garde le CC0 et un rendu utilisable.
OPENVERSE_SOURCES = "stocksnap,rawpixel,nappy"
FREE_LICENSES = ("cc0", "pdm")
SIZE = "1080:1350"


def _get(url: str, headers: dict | None = None, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pexels(query: str, key: str, page: int = 1) -> list[dict]:
    url = (f"https://api.pexels.com/v1/search?orientation=portrait&per_page=6&page={page}&query="
           + urllib.parse.quote(query))
    data = json.loads(_get(url, {"Authorization": key}))
    return [{"url": photo["src"]["large2x"], "credit": f"Photo de {photo['photographer']} sur Pexels",
             "source": photo["url"], "licence": "Pexels License", "provider": "pexels"}
            for photo in data.get("photos", [])]


def pixabay(query: str, key: str, page: int = 1) -> list[dict]:
    url = (f"https://pixabay.com/api/?key={key}&image_type=photo&orientation=vertical&per_page=6&page={page}&q="
           + urllib.parse.quote(query))
    data = json.loads(_get(url))
    return [{"url": hit["largeImageURL"], "credit": f"Image de {hit['user']} sur Pixabay",
             "source": hit["pageURL"], "licence": "Pixabay Content License", "provider": "pixabay"}
            for hit in data.get("hits", [])]


def openverse(query: str, _key: str = "", page: int = 1) -> list[dict]:
    url = (f"https://api.openverse.org/v1/images/?license_type=commercial&size=large&page_size=8&page={page}"
           f"&source={OPENVERSE_SOURCES}&category=photograph&extension=jpg&q=" + urllib.parse.quote(query))
    results = json.loads(_get(url)).get("results", [])
    results = [r for r in results if _relevant(r, query)]  # le moteur elargit : on reste sur le sujet
    results.sort(key=lambda r: 0 if (r.get("license") or "").lower() in FREE_LICENSES else 1)
    return [{"url": item["url"], "credit": item.get("attribution") or item.get("title", ""),
             "source": item.get("foreign_landing_url") or item["url"],
             "licence": f"{item.get('license', '?')} {item.get('license_version', '')}".strip(),
             "provider": "openverse"}
            for item in results if item.get("url")]


def _relevant(item: dict, query: str) -> bool:
    """Au moins un mot de la requete dans le titre ou les mots-cles du resultat."""
    words = {w for w in query.lower().split() if len(w) > 3}
    haystack = " ".join([str(item.get("title", "")), " ".join(t.get("name", "") for t in item.get("tags") or [])]).lower()
    return any(w in haystack for w in words) if words else True


def providers() -> list[tuple[str, callable, str]]:
    import os
    return [("pexels", pexels, os.environ.get("PEXELS_API_KEY", "").strip()),
            ("pixabay", pixabay, os.environ.get("PIXABAY_API_KEY", "").strip()),
            ("openverse", openverse, "always")]


def fetch_candidates(query: str, page: int = 1, wanted: int = 2) -> list[dict]:
    """Jusqu'a `wanted` images distinctes pour une requete, chez le premier fournisseur qui repond.

    `page` decale la recherche : deux offres qui partagent un role n'ont pas les memes images.
    Une requete etroite peut n'avoir qu'une page : on retombe alors sur la page 1.
    """
    for name, fn, key in providers():
        if not key:
            continue
        found: list[dict] = []
        seen: set[str] = set()
        for attempt in ([page, 1] if page != 1 else [1]):
            try:
                items = fn(query, key, attempt) if name != "openverse" else fn(query, page=attempt)
            except Exception as exc:  # reseau, quota, format : on passe au fournisseur suivant
                print(f"broll: {name} indisponible ({type(exc).__name__}: {str(exc)[:100]})")
                break
            for item in items or []:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    found.append(item)
                if len(found) >= wanted:
                    return found
        if found:
            return found
    return []


def fetch_one(query: str, page: int = 1) -> dict | None:
    found = fetch_candidates(query, page, wanted=1)
    return found[0] if found else None


def _download_crop(url: str, jpg: Path) -> Path | None:
    """Telecharge puis recadre en 1080x1350 ; None si le telechargement ou ffmpeg echoue."""
    raw = jpg.with_suffix(".src")
    try:
        raw.write_bytes(_get(url, timeout=60))
    except Exception as exc:
        print(f"broll: telechargement echoue ({type(exc).__name__}: {str(exc)[:80]})")
        return None
    crop = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-vf",
                           f"scale={SIZE}:force_original_aspect_ratio=increase,crop={SIZE}",
                           "-q:v", "3", str(jpg)], capture_output=True, text=True)
    raw.unlink(missing_ok=True)
    if crop.returncode != 0 or not jpg.exists():
        print(f"broll: recadrage echoue ({crop.stderr[-120:].strip()})")
        return None
    return jpg


def main() -> int:
    job_path, offer = Path(sys.argv[1]), sys.argv[2]
    job = json.loads(job_path.read_text(encoding="utf-8"))
    keywords = " ".join(job.get("keywords", [])[:2])
    target_dir = ROOT / "remotion" / "public" / "img" / offer
    target_dir.mkdir(parents=True, exist_ok=True)
    credits, used = [], 0
    visuel = job.setdefault("visuel", {})
    page = 1 + zlib.crc32(offer.encode()) % 4  # variete entre offres, deterministe pour une offre donnee
    for role, queries in ROLE_QUERIES.items():
        found: list[dict] = []
        for base_query in queries:
            found = fetch_candidates(base_query, page, wanted=2)
            if found:
                break
        if not found:
            print(f"broll: {role} -> image par defaut du depot")
            continue
        for rank, item in enumerate(found[:2]):
            suffix = "" if rank == 0 else "_b"
            jpg = _download_crop(item["url"], target_dir / f"{role}{suffix}.jpg")
            if jpg is None:
                continue
            visuel.setdefault(role, {})["img" if rank == 0 else "img2"] = f"{offer}/{role}{suffix}.jpg"
            credits.append({"role": role, "query": f"{base_query} ({keywords})", **item})
            used += 1
            print(f"broll: {role}{suffix} <- {item['provider']} ({item['licence']})")

    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    out = ROOT / "out" / offer
    out.mkdir(parents=True, exist_ok=True)
    (out / "credits.json").write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "credits.txt").write_text(
        "\n".join(f"{c['credit']} — {c['licence']} — {c['source']}" for c in credits), encoding="utf-8")
    print(f"broll: {used} images pour {len(ROLE_QUERIES)} segments, credits dans {out / 'credits.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
