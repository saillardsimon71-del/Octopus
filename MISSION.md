# MISSION — Faire des Shorts qui CONVERTISSENT

> Ce document **est** le prompt de travail. Colle-le (ou pointe ce fichier) dans la
> conversation qui va exécuter le chantier.
> Audit des bases et décision : `AUDIT.md` (dans ce dossier).

---

## 0. À lire d'abord (chemins qui EXISTENT)

1. `C:\Users\saill\Projects\video-factory\AUDIT.md` — l'audit et la décision (2 min)
2. `C:\Users\saill\.cline\data\workspaces\chat\grok-bot-recovery\video-factory-reference.md`
   — l'archive de la v1 : grille QC, échecs mesurés, offres, prix, liens Stripe/YouTube
3. `C:\Users\saill\.cline\data\workspaces\chat\grok-bot-recovery\extraits-techniques.md`
   — les messages bruts sur la qualité, si tu veux le détail

**Contexte** : une v1 d'usine à Shorts a été construite dans Grok Bot le 15/09.
Elle produisait des vidéos fonctionnelles mais **pas vendeuses**. Tous ses fichiers ont
disparu avec le sandbox cloud. Le commanditaire : « elles ne sont vraiment pas dingues —
ça manque d'humanité, de vivant, de couleurs ; il faut donner envie ; si la qualité n'y est
pas, les gens ne paieront pas. »

## 1. Base technique — DÉCIDÉE, ne la rediscute pas

**Moteur de rendu : `C:\Users\saill\Projects\MoneyPrinterTurbo`** (upstream v1.3.5, clone propre).
C'est un pipeline vidéo complet et maintenu. **Ne le réécris pas.** Tu vas t'en servir.

- Installation via **uv** (déjà présent, `uv.lock` fourni) : `cd C:\Users\saill\Projects\MoneyPrinterTurbo; uv sync`
- Python **3.11.6**, ffmpeg/ffprobe **8.1.1** disponibles.
- Son CLI est scriptable et **séquençable** (`--stop-at`) :
  ```
  uv run python cli.py --help
  uv run python cli.py --video-source local --video-materials "./1.mp4,./2.mp4" \
      --voice-name <voix> --voice-rate 1.0 --stop-at video
  uv run python cli.py --batch-file ./tasks.jsonl      # jusqu'à 100 tâches
  ```
  Étapes disponibles : `script · terms · audio · subtitle · materials · video`
- Fournisseurs TTS déjà câblés : `edge-tts` (défaut), `chatterbox` (serveur local
  `http://127.0.0.1:4123/v1`, expressif), `azure-tts-v2`, `fish_audio`, `minimax`,
  `elevenlabs`, + `no-voice` (apporter son propre audio).
- Sources de matériel : `pexels`, `pixabay`, `coverr`, `local`, `wavespeed`, `volcengine_seedance`.
- Réglages : `config.example.toml` à la racine (à copier en `config.toml`).

**Ton projet : `C:\Users\saill\Projects\video-factory`** (ce dossier-ci).
Tu y écris **la couche qualité**. Tu ne modifies MPT que par un `config.toml` local ;
garde son clone git propre pour pouvoir `git pull`.

**Réservoir d'idées** (à piller, pas à réparer) : `C:\Users\saill\gta6-content-agent`
— ses stages `editorial/`, son scoring, son QC et sa gestion des droits des rushes sont
de bonnes sources d'inspiration. **Il est en WIP (87 fichiers non commités) : n'en fais
pas ta base.**

---

## 2. LE problème à résoudre — priorité absolue

Les vidéos sont **fonctionnelles mais pas vendeuses**. Mesures de la v1, ton point d'échec de départ :

| Contenu | Score v1 | Détail |
|---|---|---|
| Devis / CGV v2 | **20/35 WARM_FAIL** | humanité 2/5 · motion « diaporama » 2 · son 2 |
| Impayés v1 | — | humanité 3/10 · motion 3/10 · TTS robot 4/10 · CTA 6/10 |
| LinkedIn RDV v1 | — | binaire OK mais « trop clinique, peu d'envie » |

**Diagnostic déjà établi** : ces défauts sont *exactement* les valeurs par défaut du moteur
(voir `AUDIT.md`). Donc : **ne réécris pas un moteur — corrige les réglages et ajoute la
couche qui manque.**

---

## 3. Livrable — critère de done

Depuis `C:\Users\saill\Projects\video-factory` :

```
render.cmd  <chemin>\job.json
   →  out\<offer_id>\final.mp4        # 1080×1920, 20–30 s
   →  out\<offer_id>\publish.json
   →  out\<offer_id>\frames\*.jpg     # 6 frames extraites pour le QC
```

- **Une seule commande.** Aucun appel à Grok ou à un service cloud pour produire.
- `publish.json` : `title_fr`, `description` (1 seul lien), `tags`, `thumb_brief` (1 ligne),
  `sub_id`, `stripe_link`, `duration_s`, `score_qc` (`{ship, warm, total, verdict}`).
- `job.json` porte : `offer_id`, `douleur`, `preuve`, `cta`, `prix`, `langue`, `voix`,
  `duree_cible_s`, `assets` (ou `material_directory`), `hook`, `sub_id`.
- Documente le schéma de `job.json` **dans le README de ce dossier**, pas dans un `.md` de plus.

---

## 4. La barre de qualité — impose-la, mesure-la, prouve-la

**Deux QC indépendants** : `SHIP` (technique, binaire) **et** `WARM` (ressenti).
Notation **/35**, seuil de publication **≥ 24**.
**`WARM_FAIL` si humanité < 3/5.** Un fail = **une liste de fixes**, pas de « presque ».
Axes notés : hook · douleur · preuve · CTA · lisibilité · **humanité** · motion · son · pacing.

### 4.1 Corrections à appliquer sur les réglages du moteur

| Défaut visé | Action | Où |
|---|---|---|
| Voix robotique | sortir de `edge-tts` par défaut : tester **1)** Edge TTS avec `voice_rate` travaillé, **2)** **Chatterbox** local (port 4123), **3)** `azure-tts-v2` ou `fish_audio`. Comparer 3 voix FR sur **le même texte** | `config.toml` (`[chatterbox]`, `[azure]`, `[fish_audio]`) + `--voice-name` / `--voice-rate` |
| Effet diaporama | `video_transition_mode` ≠ `"None"` (FadeIn / FadeOut / Shuffle) | `[ui]` / params CLI |
| Rythme lent | `video_clip_duration` entre **1,5 et 2 s** | idem |
| Manque de vie | `video_clip_speed` variable (0,9–1,15) selon le plan ; `video_source = "local"` + `material_directory` pour éviter le stock générique | idem |
| Typo étrange | `font_name` **français** (jamais `MicrosoftYaHeiBold.ttc`), `stroke_width` lisible, `subtitle_background` / `rounded_subtitle_background` activés, position testée | idem |
| Son plat | `bgm_volume` réglé + **SFX placés** (−8 à −12 dB sous la VO) + passe **loudnorm** | `[app]` + post-traitement ffmpeg |

### 4.2 Les 8 choses que le moteur NE SAIT PAS FAIRE → **c'est ton vrai travail**

1. **Hook ≤ 1 s** : une douleur concrète en une phrase dès l'ouverture.
   Exemple validé : « Ton devis Word, le client ne te prend pas au sérieux. »
2. **Preuve / split AVANT–APRÈS ≤ 3 s.**
3. **Cuts ≤ 1–2 s intentionnels** (surcharger les paramètres plan par plan, pas globalement).
4. **Design sonore** : SFX, respirations, accent musical sur le CTA.
5. **Étalonnage couleur** : contraste/saturation maîtrisés, pas de fond gris de slide.
6. **Packaging automatique** : `publish.json` (titre FR style A, description, tags, thumb brief, sub_id).
7. **QC mesuré** : script qui note /35, **extrait 6 frames** et les rend inspectables.
8. **Choix de voix** : bench A/B/C documenté, puis verrouillage de la voix retenue.

### 4.3 Méthode obligatoire

- **Extrais des frames du mp4 et REGARDE-les.** Ne déclare jamais « c'est bon » sans mesure.
- **Avant / après systématique** : même plan, même texte, pour prouver chaque gain.
- Notifie chaque défaut sous forme `constat → mesure → correctif → nouvelle mesure`.
- Ne publie rien sans `WARM_PASS`.

### 4.4 QC **mesurable sans les yeux** (obligatoire si tu ne vois pas les images)

Toutes les commandes sont à lancer sur le `final.mp4`. Elles transforment les
impressions en chiffres — c'est ce qui a permis de qualifier le point zéro.

```powershell
$v = "out\<offer_id>\final.mp4"

# Loudness : viser ~ -14 LUFS (Shorts) et un LRA >= 5 LU (sinon "audio plat")
ffmpeg -hide_banner -i $v -af ebur128 -f null - 2>&1 | Select-String 'I:|LRA:'

# Nombre de cuts : $n cuts sur $duree s  ->  rythme réel
$n = @(ffmpeg -hide_banner -i $v -vf "select='gt(scene,0.25)',showinfo" -f null - 2>&1 |
       Select-String 'pts_time').Count

# Images figées : un "diaporama" se détecte ici
ffmpeg -hide_banner -i $v -vf "freezedetect=n=-60dB:d=1.2" -an -f null - 2>&1 |
  Select-String 'freeze_start|freeze_duration'

# Couleur : SATAVG moyen (viser >= 25 ; < 10 = image terne)
ffmpeg -hide_banner -i $v -vf "select='not(mod(n,30))',signalstats,metadata=print:key=lavfi.signalstats.SATAVG" -an -f null - 2>&1 |
  Select-String 'SATAVG=' | ForEach-Object { [double](($_.Line -split '=')[-1]) } | Measure-Object -Average -Maximum

# Frames de preuve à montrer
0..5 | ForEach-Object { ffmpeg -y -loglevel error -ss $([math]::Round($duree*($_+0.5)/6,2)) -i $v -frames:v 1 "out\<offer_id>\frames\frame-$_.jpg" }
```

Repères validés sur ce PC : le point zéro donnait **−20,6 LUFS / LRA 2,2 / SATAVG 7,7** —
soit 6,6 dB trop silencieux, un audio plat et une image quasi désaturée.
**C'est le niveau à battre, pas une opinion.**

---

## 5. Chemin « zéro clé API » — validé sur cette machine

Le CLI exige un LLM et une clé Pexels **seulement** pour la génération automatique.
Pour éviter toute clé et tout cloud, utilise **tes propres entrées** :

```powershell
cd C:\Users\saill\Projects\MoneyPrinterTurbo

uv run python cli.py `
  --video-script "Ton script complet, une phrase par plan." `
  --video-source local `
  --video-materials "C:\chemin\shot-000.mp4,C:\chemin\shot-001.mp4" `
  --video-aspect 9:16 `
  --video-transition-mode fade-in `
  --video-clip-duration 2 `
  --voice-name fr-FR-DeniseNeural-Female `
  --voice-rate 1.0 `
  --bgm-type none `
  --font-name <police>.ttf `
  --subtitle-position bottom `
  --subtitle-background-enabled `
  --rounded-subtitle-background
```

- `--video-script` → **aucun LLM nécessaire**
- `--video-source local --video-materials` → **aucune clé Pexels**
- `voice-name` en `edge-tts` FR → **aucune clé TTS**
- Des rushes mp4 réels sont déjà sur ce PC pour tester :
  `C:\Users\saill\gta6-content-agent\content\videos\fr-001\assets\shot-00{0,1,2,3}.mp4`

Options confirmées par `uv run python cli.py --help` :
`--video-subject` · `--video-script` · `--video-terms` · `--video-language` ·
`--video-source {pexels,pixabay,coverr,volcengine_seedance,local}` · `--video-materials` ·
`--stop-at {script,terms,audio,subtitle,materials,video}` · `--video-aspect {9:16,16:9,1:1}` ·
`--video-concat-mode {random,sequential}` ·
`--video-transition-mode {none,shuffle,fade-in,fade-out,slide-in,slide-out}` ·
`--video-clip-duration` · `--match-materials-to-script` ·
`--voice-name` · `--voice-volume` · `--voice-rate` · `--custom-audio-file` ·
`--bgm-type {none,random,custom,sonilo}` · `--bgm-volume` ·
`--subtitle-enabled` · `--font-name` · `--subtitle-position {top,center,bottom,custom}` ·
`--text-fore-color` · `--font-size` · `--stroke-color` · `--stroke-width` ·
`--subtitle-background-enabled` · `--subtitle-background-color` · `--rounded-subtitle-background` ·
`--task-id` | `--batch-file`

⚠️ **Réglage important dans `config.toml`** : `[whisper] model_size` vaut `"large-v3"` par
défaut → **~3 Go de téléchargement** et lent en CPU. Pour itérer : `"base"` ou `"small"`.
Réserve `"large-v3"` au rendu final.

⚠️ **Le premier rendu télécharge le modèle `faster-whisper`** : prévois quelques minutes.

⚠️ **OBLIGATOIRE sous Windows** — sans ça, le logger de MPT finit en
`UnicodeEncodeError: 'charmap' codec can't encode character '\u2464'` (console cp1252) :

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 | Out-Null
```

C'est très probablement ce qui a fait échouer les **deux tentatives d'installation du 28/08**
(voir `_to_delete\MoneyPrinterTurbo_broken*`).

---

## 6. Les 4 offres à produire

| # | `offer_id` | Offre | Score v1 | Angle |
|---|---|---|---|---|
| 1 | `cash_impayes_relance01` | **Impayés** — relance d'une facture impayée | 33/35 | le cash qui ne rentre pas |
| 2 | `cash_devis_cgv01` | **Devis / CGV** — devis pro + CGV (pack 37 €) | 30/35 → WARM_FAIL | passer pour un pro |
| 3 | `cash_avenant_scope01` | **Avenant** — faire signer un avenant de scope | 33/35 | le client qui élargit sans payer |
| 4 | `cash_linkedin_rdv01` | **LinkedIn RDV** — obtenir un rendez-vous | 28/35 | le prospect qui ne répond jamais |

CTA : **9 €** / **19 €** / **27 €** / **37 €**.
Landing, liens Stripe livemode, chaîne YouTube (GuideExpress, `@Sllrd3` sur X) et
`sub_id` de tracking : voir `grok-bot-recovery\video-factory-reference.md` §6.

---

## 7. Travail demandé, dans cet ordre

1. ~~Vérifier la base~~ → **DÉJÀ FAIT le 16/09/2026** :
   - `.venv` installé (120 paquets) — `C:\Users\saill\Projects\MoneyPrinterTurbo\.venv`
   - un **rendu réel de bout en bout a tourné sans aucune clé API** → `out\point-zero\`
   - le point zéro est **mesuré** : `−20,6 LUFS` · `LRA 2,2 LU` · `SATAVG 7,7/255`,
     et **les 3 mêmes rushes bouclés 5×** pour couvrir 18 s d'audio
   - → **lis `out\point-zero\POINT-ZERO.md`** (métriques, commande exacte, bug corrigé)
   - **Ton travail commence à l'étape 2, en battant ces chiffres.**
2. **Bench voix FR ×3** (Edge TTS retravaillé / Chatterbox / azure-v2 ou fish_audio).
   Écoute, compare, verrouille une voix. Documente le choix en 5 lignes.
3. **Corrections de réglages** (§4.1) : transitions, `clip-duration`, vitesse, sous-titres, audio.
   Refais le rendu et compare au point zéro (**avant/après**).
4. **Couche qualité maison** (§4.2) : hook ≤1 s, preuve ≤3 s, cuts courts, SFX, étalonnage,
   `publish.json`, script de QC /35 avec extraction de frames.
5. **Boucle** jusqu'à **≥ 30/35 WARM_PASS sur DEUX offres différentes** (Impayés + Devis/CGV).
6. **Seulement après** : variations, batching (`--batch-file`), packaging publication.

À chaque étape, montre **les frames** et **le score**. Pas de déclaration sans mesure.

---

## 8. Anti-patterns / interdits

- ❌ Réécrire un moteur vidéo : il existe, répare la qualité.
- ❌ TTS plat, diaporama, coupes sèches généralisées, texte statique > 2 s.
- ❌ « Doc-bloat » : du **code** et des **mp4**, pas des `.md` de spec en série.
- ❌ Publier sans `WARM_PASS`. Brûler un crédit cloud (Grok, ElevenLabs) pour produire.
- ❌ Toucher au clone git de MoneyPrinterTurbo au-delà d'un `config.toml` local.
- ❌ Me demander de valider chaque micro-étape : avance, itère, montre des résultats.

## 9. Reporting attendu

Un message = **un résultat mesuré** :
`score /35 (SHIP + WARM) + 6 frames + chemins des fichiers + liste des fixes appliqués`.
Si tu es bloqué : dis précisément où, et propose 2 options.
