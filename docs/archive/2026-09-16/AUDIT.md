# AUDIT — quelle base pour l'usine à Shorts ?

Audit réalisé le **16/09/2026** sur les deux bases trouvées sur le disque.
Objectif : choisir la base de la couche qualité « vidéos qui convertissent ».

---

## Base A — `C:\Users\saill\Projects\MoneyPrinterTurbo`

Clone **propre** de l'upstream [`harry0703/MoneyPrinterTurbo`](https://github.com/harry0703/MoneyPrinterTurbo).

| Élément | Constat |
|---|---|
| Version | **1.3.5** (`pyproject.toml`), dernier commit `eb8c237` du **28/08/2026** |
| État git | **propre** (aucun fichier modifié) — clone upstream intact |
| Python requis | `>=3.11` → machine : **3.11.6** ✅ |
| Gestionnaire | **`uv` présent** + `uv.lock` → install reproductible et rapide ✅ |
| Stack | `moviepy==2.2.1`, `streamlit`, `edge-tts`, **`faster-whisper`**, `fastapi`, `litellm`, `google-genai`, `azure-cognitiveservices-speech`, `pydub`, `loguru` |
| Installé ? | ❌ **non** : ni `.venv`, ni `config.toml` |
| Deux installs cassées | `…\Projects\_to_delete\MoneyPrinterTurbo_broken{,_2}_*` (28/08 12:59) |

### Points forts décisifs
- **CLI scriptable** : `uv run python cli.py` avec `--video-source local --video-materials "./1.mp4,./2.mp4"`,
  `--voice-name`, `--voice-volume`, `--voice-rate`, **`--batch-file`** (JSON ou JSONL, jusqu'à 100 tâches, 1 Mio),
  et **`--stop-at <étape>`** pour ne faire tourner qu'un segment.
- **Étapes séparables** : `script · terms · audio · subtitle · materials · video` →
  on peut brancher son propre script/audio et n'utiliser MPT que pour *materials + video*.
- **9 fournisseurs TTS** déjà câblés : `edge-tts` (défaut, gratuit), **`chatterbox`** (serveur local
  expressif sur `http://127.0.0.1:4123/v1`), **`azure-tts-v2`**, `fish_audio`, `minimax`,
  `elevenlabs`, `siliconflow`, `gemini`, `mimo`, + `no-voice` (apporter son propre audio).
- **Sources de matériel** : `pexels`, `pixabay`, `coverr`, `local`, `wavespeed`, `volcengine_seedance`, `loomloom`.
- Services déjà écrits : `material.py`, `material_cache.py`, `subtitle.py`, `video.py`, `voice.py`,
  `bgm.py`, `llm.py`, `task.py`, `task_artifacts.py` + WebUI Streamlit.
- Tests avec seuil de couverture de branches fixé à **70 %**.

### Ce qui rend ses vidéos « pas dingues » (valeurs par défaut)
| Défaut constaté | Cause par défaut | Paramètre |
|---|---|---|
| Voix robotique / plate | `edge-tts` par défaut | `tts_server`, `voice_name`, `voice_rate` |
| Effet diaporama | `video_transition_mode = "None"` (coupes sèches) | `video_transition_mode` |
| Rythme lent | `video_clip_duration = 3` s | `video_clip_duration` |
| Manque de vie | `video_clip_speed = 1.0` figé, B-roll Pexels générique | `video_clip_speed`, `video_source`, `material_directory` |
| Pas de couleur / typo étrange | sous-titres `font_name = "MicrosoftYaHeiBold.ttc"` (police chinoise), pas d'étalonnage | `font_name`, `stroke_*`, `subtitle_background`, `rounded_subtitle_background` |
| Hook absent, pas de SFX | **non prévu par le moteur** | → à construire |

## Base B — `C:\Users\saill\gta6-content-agent`

Pipeline TypeScript maison (autonome, orienté GTA VI / YouTube Shorts).

| Élément | Constat |
|---|---|
| Dernier commit | `a893810` du **23/08/2026** — « WIP editorial runtime integration » |
| État git | ⚠️ **87 fichiers modifiés non commités** |
| `node_modules` | ✅ installé |
| `dist/` | ❌ jamais construit |
| `.env` | ❌ absent (seul `.env.example`) |
| Preuve de fonctionnement | un seul mp4 : `content\videos\fr-001\final\fr-001.mp4` (1,8 Mo, 28/08) |
| Forces | stages très lisibles (`idea→research→script→factcheck→blueprint→shotPlan→assets→voice→subtitles→video→qc→final`), QC éditorial avec scoring, tests vitest, gestion des droits des rushes (`media.json`, `rights`/`quoted_extract`) |

⚠️ **C'est un chantier en cours, non terminé et non buildé** — pas une base fiable pour démarrer.

---

## DÉCISION

> ### 🎯 `MoneyPrinterTurbo` = **moteur de rendu**.
> ### 🎯 `video-factory` (nouveau) = **couche qualité** par-dessus.
> ### 🎯 `gta6-content-agent` = **réservoir d'idées**, pas une base.

### Pourquoi (5 raisons)

1. **MPT est terminé et maintenu** ; gta6 est en WIP avec 87 fichiers modifiés non commités.
   On ne construit pas une couche qualité sur un socle instable.
2. **MPT a déjà résolu les parties ingrates** : acquisition de matériel + cache, abstraction de
   9 moteurs TTS, sous-titres via `faster-whisper`, assemblage moviepy, gestion d'état et
   d'artefacts de tâches, batch CLI, WebUI. Les réécrire serait du temps perdu.
3. **MPT est pilotable en CLI avec `--stop-at`** → on peut n'utiliser que les étapes utiles
   (`materials` + `video`) et fournir son propre script et son propre audio.
4. **Ses défauts par défaut sont exactement les reproches du commanditaire** → le gain est
   borné, mesurable, et ne demande pas de réécrire un moteur.
5. **Le clone est propre** : on peut `git pull` les améliorations upstream tout en gardant
   notre couche qualité isolée dans `video-factory`.

### Ce que la couche qualité `video-factory` doit apporter

Ce que MPT **ne sait pas faire** — c'est le vrai travail :

| # | Manque | Rôle de la couche |
|---|---|---|
| 1 | **Hook ≤ 1 s** (douleur concrète en une phrase) | générer le préambule et l'imposer au montage |
| 2 | **Preuve / split AVANT–APRÈS ≤ 3 s** | plan de montage dédié |
| 3 | **Cuts ≤ 1–2 s intentionnels** | surcharge des paramètres MPT plan par plan |
| 4 | **Design sonore** (SFX, respirations, accent sur le CTA) | passe audio post-MPT |
| 5 | **Étalonnage / couleur** | filtre ffmpeg post-rendu |
| 6 | **Packaging** (`publish.json`) | génération titre/description/tags/thumb brief |
| 7 | **QC mesuré** (SHIP + WARM, /35) | script d'évaluation + extraction de frames |
| 8 | **Choix de voix** | bench A/B/C de voix FR, puis verrouillage |

### Le contrat entre les deux

```
video-factory\render.cmd  <job.json>
   ├─ 1. lit job.json          (offre, douleur, preuve, CTA, prix, voix, durée)
   ├─ 2. génère script + plan  (hook, preuve, cuts courts)
   ├─ 3. appelle MPT en CLI    (uv run python cli.py --stop-at video …)
   ├─ 4. passe qualité ffmpeg  (SFX, loudnorm, étalonnage)
   ├─ 5. QC SHIP+WARM /35      (extrait les frames, mesure)
   └─ 6. écrit publish.json    → out\<offer_id>\final.mp4 + publish.json
```

### Environnement validé

| Outil | Version | Usage |
|---|---|---|
| Python | **3.11.6** (`AppData\Local\Programs\Python\Python311`) | runtime MPT |
| **uv** | **0.12.7** (`C:\Users\saill\.local\bin\uv.exe`) | install reproductible via `uv.lock` |
| ffmpeg / ffprobe | **8.1.1** (winget `Gyan.FFmpeg`) | rendu + passes qualité |
| git | présent | suivi de version |
| Espace disque C: | **168,8 Go libres** | largement suffisant |

### Risques identifiés

- **Install MPT jamais réussie jusqu'ici** (2 échecs le 28/08) → l'étape 1 de la mission est
  justement de la réussir et de documenter la cause du précédent échec.
- **Clés API absentes** (Pexels, LLM) → prévoir un mode dégradé : `video_source = "local"`
  + `llm_provider` local, ou scripts écrits à la main pour les 4 offres.
- **Chatterbox** nécessite un serveur TTS local (port 4123) non installé → à évaluer
  face à `edge-tts` avec un réglage de débit, avant de s'engager.
- **Aucune qualité audio/vidéo ne doit être jugée « à l'oreille »** : la grille /35 de la v1
  (voir `video-factory-reference.md`) reste la référence, et les visages de frames doivent
  être extraits et regardés.

