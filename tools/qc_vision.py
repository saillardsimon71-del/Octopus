"""QC vision DeepSeek — verdict /35 sur 6 frames, seuils calculés EN CODE.

Usage :
    python tools/qc_vision.py <frames_dir> <narration> <out_json> [--duree 20.0]

- Modèle : deepseek-flash (seul à accepter les images).
- thinking DÉSACTIVÉ (sinon reasoning_tokens mange tout le max_tokens).
- Images dans les messages "user" uniquement.
- Le verdict WARM_PASS / SHIP_PASS est recalculé par le CODE, jamais cru au modèle.
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

GRID = (
    "Note ce Short sur 35. Bareme STRICT : hook/5, douleur/4, preuve/4, cta/4, "
    "lisibilite/4, humanite/5 (0 si la voix semble synthetique ; ECHEC si <3), "
    "motion/4, son/3, pacing/2. total = somme exacte.\n"
    "Reponds UNIQUEMENT par un objet JSON, sans texte ni balise autour :\n"
    '{"hook":0,"douleur":0,"preuve":0,"cta":0,"lisibilite":0,"humanite":0,'
    '"motion":0,"son":0,"pacing":0,"total":0,'
    '"impression":"on s arrete ou on skippe, et pourquoi, en 2 phrases",'
    '"humanite_detail":"la voix et l image font-elles humaines ?",'
    '"coherence_narration_image":"les images correspondent-elles a la narration ?",'
    '"couleurs":"l image donne-t-elle envie, ou est-elle terne et froide ?",'
    '"defauts":["..."],"fixes":["..."]}'
)

AXES = ("hook", "douleur", "preuve", "cta", "lisibilite",
        "humanite", "motion", "son", "pacing")


def data_url(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit("Usage: python tools/qc_vision.py <frames_dir> <narration> <out_json> [--duree 20.0]")

    frames_dir = Path(sys.argv[1])
    narration = sys.argv[2]
    out_json = Path(sys.argv[3])
    duree = 20.0
    if "--duree" in sys.argv:
        duree = float(sys.argv[sys.argv.index("--duree") + 1])

    frames = sorted(frames_dir.glob("*.jpg"))
    if not frames:
        sys.exit(f"Aucune frame .jpg dans {frames_dir}")
    print(f"frames : {len(frames)}")

    content = [
        {
            "type": "text",
            "text": (
                f"Voici {len(frames)} frames d'un Short vertical 1080x1920 de {duree} s.\n"
                f"Narration exacte :\n« {narration} »\n\n"
                "Evalue-le comme un spectateur qui scrolle : on s'arrete ou on skippe ?\n\n"
                + GRID
            ),
        }
    ]
    content += [
        {"type": "image_url", "image_url": {"url": data_url(f), "detail": "low"}}
        for f in frames
    ]

    r = cli.chat.completions.create(
        model="deepseek-flash",
        messages=[{"role": "user", "content": content}],
        max_tokens=8000,
        extra_body={"thinking": {"type": "disabled"}},
    )
    raw = (r.choices[0].message.content or "").strip()
    if not raw:
        rc = getattr(r.choices[0].message, "reasoning_content", None)
        sys.exit("content vide. reasoning_content : " + (rc or "")[:800])

    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        sys.exit("JSON introuvable dans la reponse vision :\n" + raw[:500])

    verdict = json.loads(match.group(0))

    # --- seuils recalculés EN CODE (jamais par le modèle) ---
    verdict["total_calcule"] = int(sum(verdict.get(k, 0) for k in AXES))
    verdict["humanite"] = int(verdict.get("humanite", 0))
    verdict["ship_pass"] = verdict["total_calcule"] >= 24
    verdict["warm_pass"] = (verdict["total_calcule"] >= 24) and (verdict["humanite"] >= 3)
    verdict["cible_atteinte"] = verdict["total_calcule"] >= 30
    verdict["usage"] = {
        "prompt_tokens": getattr(r.usage, "prompt_tokens", None),
        "completion_tokens": getattr(r.usage, "completion_tokens", None),
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(verdict, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
