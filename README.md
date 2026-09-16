# video-factory

Couche qualité par-dessus **MoneyPrinterTurbo** — pour produire des Shorts 1080×1920
qui **convertissent**, pas seulement qui existent.

## Documents

| Fichier | Rôle |
|---|---|
| ⭐ **`BRIEF-DEEPSEEK.md`** | **Le prompt de travail courant** — version DeepSeek : moteur Remotion, groupe d'agents, navigateur. Contient le diagnostic chiffré et le QC vision validé |
| **`MISSION.md`** | Mission précédente (MoneyPrinterTurbo seul) — garde la structure et la barre de qualité |
| **`AUDIT.md`** | Audit des deux bases trouvées sur le disque + décision et justification |
| **`out/point-zero/POINT-ZERO.md`** | Le **rendu de référence mesuré** (ffmpeg) **+ son verdict vision DeepSeek 21/35** |
| **`tools/deepseek_smoke.py`** | Smoke test **fonctionnel** : `deepseek-v4-pro` (texte+thinking) et `deepseek-flash` (vision) |

## Architecture

```
video-factory\  (ce dossier : la couche qualité)
   │  render.cmd <job.json>
   │    1. lit job.json        → offre, douleur, preuve, CTA, prix, voix, durée
   │    2. génère script+plan  → hook ≤1 s, preuve ≤3 s, cuts 1–2 s
   │    3. appelle le moteur   → uv run python cli.py --stop-at video …
   │    4. passe qualité       → SFX, loudnorm, étalonnage (ffmpeg)
   │    5. QC SHIP+WARM /35    → note + 6 frames extraites
   │    6. écrit publish.json  → titre FR, description, tags, thumb brief, sub_id
   └─  out\<offer_id>\final.mp4 + publish.json + frames\*.jpg

C:\Users\saill\Projects\MoneyPrinterTurbo\   (le moteur — ne pas modifier hors config.toml)
```

## Le moteur

- `C:\Users\saill\Projects\MoneyPrinterTurbo` — upstream v1.3.5, clone git **propre**
- Installé via **uv** : `.venv` présent (120 paquets), Python 3.11.6
- Entrée : `uv run python cli.py --help`
- Entièrement pilotable **sans aucune clé API** avec `--video-script` +
  `--video-source local --video-materials` + `edge-tts` (voir `MISSION.md` §5)

## Schéma de `job.json`

> À compléter par la mission (étape 1). Champs prévus :

```jsonc
{
  "offer_id": "cash_impayes_relance01",   // identifiant, sert de nom de dossier de sortie
  "langue": "fr",
  "duree_cible_s": 25,
  "hook": "Ton devis Word, le client ne te prend pas au sérieux.",
  "douleur": "…",                          // formulée en 1 phrase concrète
  "preuve": "…",                           // visuel AVANT/APRÈS montré ≤3 s
  "cta": "…",
  "prix": "9 €",
  "stripe_link": "https://buy.stripe.com/…",
  "sub_id": "ai_prompts_freelance_short01_offre",
  "voix": { "moteur": "edge-tts", "nom": "fr-FR-DeniseNeural-Female", "rate": 1.0 },
  "assets": { "source": "local", "materials": ["C:\\…\\shot-000.mp4"] }
}
```

## Sortie attendue

- `final.mp4` — 1080×1920, 20–30 s
- `publish.json` — `title_fr`, `description` (1 seul lien), `tags`, `thumb_brief`,
  `sub_id`, `stripe_link`, `duration_s`, `score_qc { ship, warm, total, verdict }`
- `frames\*.jpg` — 6 images extraites, pour prouver le QC visuellement

## Règle d'or

**Aucune vidéo n'est déclarée bonne sans mesure.** Grille /35 (SHIP + WARM),
`WARM_FAIL` si humanité < 3/5, publication seulement à **≥ 24/35** — viser **≥ 30/35**.
