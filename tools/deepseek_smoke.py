"""Smoke test DeepSeek — valide la cle, les 2 modeles et le QC vision.

Usage :
  python tools/deepseek_smoke.py [dossier_frames] [texte_narration]

La cle est lue depuis la variable d'environnement DEEPSEEK_API_KEY.
Sous PowerShell, pour la charger sans redemarrer le terminal :
  $env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path

from openai import OpenAI

BASE = "https://api.deepseek.com"
KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
if not KEY:
    sys.exit("ERREUR: DEEPSEEK_API_KEY absente de l'environnement.")

cli = OpenAI(api_key=KEY, base_url=BASE)


def call(model, messages, **kw):
    """Appel robuste : tente reasoning_effort, retombe en extra_body si refuse."""
    try:
        return cli.chat.completions.create(model=model, messages=messages, **kw)
    except TypeError:
        kw.pop("reasoning_effort", None)
        return cli.chat.completions.create(model=model, messages=messages, **kw)


print("=" * 64)
print("1/3  MODELE TEXTE   deepseek-v4-pro   (thinking + reasoning high)")
r = call(
    "deepseek-v4-pro",
    [{"role": "user", "content": "En une phrase : quel modele es-tu ?"}],
    max_tokens=300,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)
print(r.choices[0].message.content.strip())
print("usage:", r.usage)

print("=" * 64)
print("2/3  MODELE VISION  deepseek-flash")

frames_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out/point-zero/frames")
narration = sys.argv[2] if len(sys.argv) > 2 else (
    "Ton devis Word, le client ne te prend pas au serieux. Resultat: il compare, "
    "il hesite, il repousse la signature. Avec un devis clair et des conditions ecrites, "
    "tu signes plus vite. Deux minutes pour un document propre. Lien en description."
)

frames = sorted(frames_dir.glob("*.jpg"))
print(f"frames trouvees : {len(frames)} dans {frames_dir}")
if not frames:
    sys.exit("aucune frame .jpg trouvee")


def data_url(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


GRID = (
    "Note ce Short sur 35. Bareme STRICT : hook/5, douleur/4, preuve/4, cta/4, "
    "lisibilite/4, humanite/5 (mets 0 si la voix semble synthetique ; ECHEC si <3), "
    "motion/4, son/3, pacing/2. total = somme exacte.\n"
    "Reponds UNIQUEMENT par un objet JSON, sans texte ni balise autour :\n"
    '{"hook":0,"douleur":0,"preuve":0,"cta":0,"lisibilite":0,"humanite":0,'
    '"motion":0,"son":0,"pacing":0,"total":0,"warm_pass":false,'
    '"impression":"on s arrete ou on skippe, et pourquoi, en 2 phrases",'
    '"humanite_detail":"la voix et l image font-elles humaines ?",'
    '"coherence_narration_image":"les images correspondent-elles a la narration ?",'
    '"couleurs":"l image donne-t-elle envie, ou est-elle terne et froide ?",'
    '"defauts":["..."],"fixes":["..."]}'
)

content = [
    {
        "type": "text",
        "text": (
            "Voici 6 frames d'un Short vertical 1080x1920 de 18,3 s.\n"
            "Narration exacte :\n« " + narration + " »\n\n"
            "Evalue-le comme un spectateur qui scrolle : on s'arrete ou on skippe ?\n\n"
            + GRID
        ),
    }
]
content += [
    {"type": "image_url", "image_url": {"url": data_url(f), "detail": "low"}}
    for f in frames
]

r2 = call(
    "deepseek-flash",
    [{"role": "user", "content": content}],
    max_tokens=8000,
    extra_body={"thinking": {"type": "disabled"}},
)
raw = (r2.choices[0].message.content or "").strip()
print("finish_reason:", r2.choices[0].finish_reason)
print(raw)
print("usage:", r2.usage)
if not raw:
    rc = getattr(r2.choices[0].message, "reasoning_content", None)
    print("!! content vide. reasoning_content (extrait) :", (rc or "")[:800])

print("=" * 64)
print("3/3  VERDICT PERSISTE")
out = frames_dir.parent / "qc_vision_smoke.json"
match = re.search(r"\{.*\}", raw, re.S)
if not match:
    sys.exit("JSON introuvable dans la reponse vision.")
parsed = json.loads(match.group(0))
out.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"ecrit dans : {out}")
print(f"TOTAL = {parsed.get('total')}/35   WARM_PASS = {parsed.get('warm_pass')}")
print(f"humanite = {parsed.get('humanite')}/5   motion = {parsed.get('motion')}/4")
