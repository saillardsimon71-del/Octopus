# BRIEF — Reconstruire l'usine « Podalux » : moteur vidéo + groupe d'agents + navigateur

> **Ce document est le prompt de travail.** Colle-le (ou pointe ce fichier) dans la conversation
> qui exécute le chantier.
> Écrit le **16/09/2026**, après trois vérifications réelles effectuées sur cette machine :
> installation du moteur, rendu de bout en bout, et **QC vision DeepSeek opérationnel**.

---

## 0. Note sur les rôles (lis en premier — c'est une source de confusion)

Tu es Cline et tu tournes sur **DeepSeek V4 Pro en raisonnement xhigh**. C'est ton cerveau
**de développeur**.

Le logiciel que tu vas construire utilise **la même clé DeepSeek** de l'utilisateur pour faire
tourner un **groupe d'agents**. Ce sont deux usages distincts :

| Rôle | Modèle | Usage |
|---|---|---|
| Toi (développeur) | DeepSeek V4 Pro, thinking | écrire le code, l'archi, déboguer |
| Agents du produit — décisions | `deepseek-v4-pro` | arbitrages, stratégie, rédaction |
| Agents du produit — courant | `deepseek-flash` | tâches routinières, classement, résumé |
| **QC visuel du produit** | **`deepseek-flash`** | **le seul à accepter des images** |

Utilise ton raisonnement pour concevoir juste, mais **garde tes messages courts** : du code,
des résultats mesurés, des chemins. Pas d'essais philosophiques.

### 🔑 La clé API est DÉJÀ INSTALLÉE — n'en demande pas

`DEEPSEEK_API_KEY` est posée en **variable d'environnement utilisateur** (35 caractères, `sk-…`).

- **Ne l'écris dans aucun fichier** (pas de `.env`, pas de `.md`, pas de commit).
- Un terminal ouvert **avant** la pose ne la voit pas. Charge-la en tête de chaque script :

```powershell
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')
```

- Vérifie qu'elle est là avant de coder :
```powershell
& 'C:\Users\saill\Projects\MoneyPrinterTurbo\.venv\Scripts\python.exe' tools\deepseek_smoke.py
```

## 1. À lire d'abord (chemins qui EXISTENT — vérifie-les avant de coder)

**Archive complète du pipeline précédent** (construit dans l'app « Grok Bot », aujourd'hui
inaccessible : ses fichiers vivaient dans un sandbox cloud. Tout a été récupéré en local.)

- `C:\Users\saill\.cline\data\workspaces\chat\grok-bot-recovery\video-factory-reference.md`
  → **LA référence** : recette vidéo, grille QC, offres, prix, liens Stripe/YouTube
- `…\grok-bot-recovery\extraits-techniques.md` (89 Ko) → les messages bruts sur la production,
  les échecs mesurés, les itérations qualité v1→v4
- `…\grok-bot-recovery\agents-podalux.md` → le roster exact des agents + un `store.db` par agent
- `…\grok-bot-recovery\salons\00-INDEX.md` + `01..16` → les 16 conversations intégrales
- `…\grok-bot-recovery\chronologie.md` → 450 messages horodatés

**Travail déjà réalisé — à reprendre, pas à refaire :**

- `C:\Users\saill\Projects\video-factory\AUDIT.md` → audit des bases + décision d'architecture
- `C:\Users\saill\Projects\video-factory\README.md` → architecture + schéma de `job.json`
- `C:\Users\saill\Projects\video-factory\MISSION.md` → mission précédente (garde la structure et la barre de qualité)
- ⭐ `C:\Users\saill\Projects\video-factory\out\point-zero\POINT-ZERO.md`
  → **le rendu de référence MESURÉ + son verdict vision 21/35** (voir §3)
- `C:\Users\saill\Projects\video-factory\tools\deepseek_smoke.py`
  → **smoke test déjà fonctionnel** (texte + vision + persistance du verdict JSON)
- `C:\Users\saill\Projects\video-factory\out\point-zero\qc_vision_smoke.json` → exemple de verdict

**Moteur installé et validé :**

- `C:\Users\saill\Projects\MoneyPrinterTurbo` — upstream v1.3.5, clone git **propre**,
  `.venv` installé (120 paquets via `uv`), `config.toml` créé.
  **Ne le réécris pas** et ne le modifie qu'en `config.toml`.
  Il reste utile pour sa **plomberie** (batch, matériel, sous-titres whisper) — pas pour son style visuel.

**Réservoir d'idées** (à piller, **pas** à réparer) :

- `C:\Users\saill\gta6-content-agent` — pipeline TS en **WIP** (87 fichiers non commités, jamais
  buildé, pas de `.env`). À réutiliser pour ses idées : `src/editorial/` (scoring, blueprint,
  shotPlan), **`src/pipeline/assets/relevance.ts`** (« juger la pertinence d'une image sur ce
  qu'elle MONTRE, pas sur son nom de fichier »), `media/media.json` (`subjects[]`, `rights`).

  ⚠️ **N'utilise PAS ses rushes** (`content\videos\fr-001\assets\*.mp4`) : ce sont des plans
  **GTA VI**. Le QC vision a identifié que c'est précisément ce qui rend le point zéro incohérent
  pour une offre « devis / impayés ». Voir `POINT-ZERO.md`.

## 2. La machine (environnement VÉRIFIÉ : Windows, PowerShell)

| Outil | Version / État |
|---|---|
| Python | 3.11.6 (`C:\Users\saill\AppData\Local\Programs\Python\Python311\python.exe`) |
| Python du projet | `C:\Users\saill\Projects\MoneyPrinterTurbo\.venv\Scripts\python.exe` (**a déjà `openai` 2.24**) |
| **uv** | 0.12.7 |
| ffmpeg / ffprobe | **8.1.1** (winget `Gyan.FFmpeg`) |
| Node / npm | **v22.16.0** / 10.9.2 |
| **Playwright** | navigateurs **déjà installés** : chromium-1223, firefox-1522, webkit-2287, ffmpeg-1011 (`%LOCALAPPDATA%\ms-playwright`) |
| Espace libre C: | ~167 Go |
| `DEEPSEEK_API_KEY` | ✅ **posée** (variable User) — voir §0 |
| GPU | aucun — CPU uniquement pour le rendu |

**Obligatoire avant tout appel à MoneyPrinterTurbo** (sinon son logger crashe en cp1252) :
```powershell
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"; chcp 65001 | Out-Null
```

## 3. Le verdict : LA VIDÉO NE VA PAS. Voilà les mesures, et la preuve que le QC marche

Verdict du commanditaire, sans filtre :
> « Ça ne va pas du tout : **l'image ne correspond pas à ce qui est dit**, **voix très robotique**,
> le genre de vidéo qu'on **skippe instantanément**. Rien à voir avec ce que faisait le pipeline GrokBot. »

Il a raison. Voici ce que produit le moteur actuel
(`out\point-zero\point-zero.mp4` — 1080×1920, 30 fps, 548 frames, 18,27 s) :

### 3.1 Métriques objectives (ffmpeg)

| Métrique | Trouvé | Cible Shorts | Diagnostic |
|---|---|---|---|
| Loudness intégrée | **−20,6 LUFS** | ≈ −14 LUFS | 6,6 dB trop silencieux → skip dans un feed |
| Plage dynamique (LRA) | **2,2 LU** | ≥ 5 LU | **audio plat** = « voix robotique » |
| Saturation moyenne (SATAVG) | **7,7 / 255** | ≥ 25 | **image quasi désaturée** = « pas de couleurs » |
| Matière vs audio | **9,57 s de rushes pour 18,36 s d'audio → 5 boucles des 3 mêmes plans** | matière ≥ audio | **diaporama répétitif** |
| `match_materials_to_script` | **`false`** | `true` | **plans piochés AU HASARD** = « l'image ne correspond pas » |
| `bgm_type` | **vide** | lit musical | aucune musique |

### 3.2 Verdict vision (DeepSeek) — **21/35**, et il a tout dit lui-même

Évaluation faite sur 6 frames par `deepseek-flash`, **sans lui indiquer les défauts attendus**.
Il a reproduit spontanément les trois griefs du commanditaire :

> **Impression** : « Je **skippe dès la première seconde** car les visuels de type "breaking news"
> sur **GTA VI** n'ont **aucun rapport** avec la promesse d'un devis Word. Le décalage est trop
> violent pour que je reste voir la suite. »
>
> **Humanité** : « La **voix semble synthétique** et l'image est une simple superposition de
> **calques statiques**, ce qui donne un aspect très robotique et impersonnel. »
>
> **Couleurs** : « L'image est assez **terne et froide**, avec une dominance de gris et de vert
> sombre qui manque de dynamisme. »

**Ce que ça démontre :** le QC vision est **opérationnel, fiable et aligné** avec le commanditaire.
Il confirme par une voie indépendante les métriques ffmpeg (« terne et froide » ↔ `SATAVG 7,7`).

### 3.3 Cause racine — à comprendre une fois pour toutes

MoneyPrinterTurbo assemble du **stock footage au hasard** sous une voix off.
Le pipeline GrokBot faisait l'inverse : du **Remotion kinetic typography** — **le texte animé EST
l'image**. L'alignement narration/image y est donc **structurel**, pas un réglage.

**Conclusion : on ne corrige pas MPT, on ajoute le moteur qui manque.** MPT garde sa plomberie.

## 4. LE LIVRABLE N°1 : le rendu vidéo « FORGE v4 »

Reproduis **exactement** la recette que le QC de l'archive a fini par **valider** (version v4).

### 4.1 Moteur
**Remotion** (React → MP4), composant paramétré par un `job.json`. C'était le choix validé par
l'archive (« Remotion est installé. Je monte le Short dessus avec animations/captions propres —
plus de slides années 80 »).

⚠️ **Vérifie la licence Remotion** selon le statut de l'utilisateur (particulier / petite société)
**avant** d'industrialiser. Si elle bloque : replie-toi sur **ffmpeg + drawtext/overlay animés**,
mais **dis-le-moi avant** et donne les limites de cette option.

### 4.2 Éléments OBLIGATOIRES du rendu (issus des itérations v1→v4 de l'archive)

1. **Kinetic typography réelle** : typing, `type/scale-in`, springs. **Le mot clé prononcé apparaît
   à l'écran au moment exact où il est dit.**
2. **Zéro hard cut** : transitions dynamiques partout (wipes/fades).
3. **Ken Burns RÉEL** : zoom 1.00 → **1.22** sur les fonds et visuels. Les QC avaient rejeté v2 et v3
   parce que le Ken Burns était « annoncé mais absent ou imperceptible » → **mesure-le** (§7.2).
4. **Bed mélodique** + **SFX** : whoosh, pop, typing, révélations de texte.
   **SFX sous la VO** (−8 à −12 dB), jamais par-dessus.
5. **Palette chaude** : le QC v1 a rejeté explicitement le « noir/néon clinique ».
6. **Un élément humain** : B-roll ou visage (demande explicite : « un élément humain »).
7. **Icônes** pour casser le mur de texte.
8. **Highlights subtils** sur les mots clés.
9. **VO avec le hook placé AVANT le premier item** (« VO hook avant "Un :" »).
10. **Tonalité envie/soulagement**, pas seulement la peur (grief « trop peur/clinique » du QC).

### 4.3 La voix — le 2ᵉ motif de skip

Sortir d'`edge-tts` par défaut. Dans l'ordre :

1. **Chatterbox** en local (serveur OpenAI-compatible sur `http://127.0.0.1:4123/v1`) —
   MoneyPrinterTurbo le supporte **déjà** (`[chatterbox]` dans `config.toml`) ; il gère
   l'expressivité et l'exagération, ce qui attaque directement la « robotique »
2. `azure-tts-v2` · `fish_audio` · `minimax`
3. `piper` FR en repli (déjà présent dans `gta6-content-agent`)

Puis applique une **prosodie dirigée** — jamais un débit constant :
- **marques de respiration et de pause** dans le texte source
- **carte de débit par phrase** : hook lent et bas, preuve rapide, CTA net
- **accentuation** des mots clés
- ❌ interdit : un seul `voice_rate` global pour toute la vidéo

Cible mesurable : **LRA ≥ 5 LU** (contre 2,2 au point zéro).

### 4.4 Alignement narration ↔ image — le grief n°1

- **Règle dure** : le texte à l'écran **est** la phrase prononcée de ce segment. Aucun plan dont
  le contenu n'est pas relié à la narration.
- B-roll humain : mapping **mot-clé → asset** avec `subjects[]` (cf. `gta6-content-agent\media\media.json`)
  et **scoring de pertinence** (cf. `assets/relevance.ts`).
- ❌ **Interdits** : `video_concat_mode: random`, `match_materials_to_script: false`, et le
  **bouclage** d'un même plan (le pipeline doit **refuser de sortir** s'il doit boucler > 1 fois).
- **Contrôle automatique** : chaque scène du `job.json` porte
  `{debut_s, fin_s, texte_ecran, narration}` et un script vérifie que
  `texte_ecran == segment de narration` à **±100 ms**.

## 5. LE LIVRABLE N°2 : le groupe d'agents (recréer « Podalux »)

Reproduis l'organisation du groupe GrokBot, avec DeepSeek comme cerveau.
**Roster exact** (repris de `agents-podalux.md`) :

| Agent | Rôle | Propriété |
|---|---|---|
| **ORBIT** | CEO / Chief of Staff | arbitre, valide l'ordre des priorités, tient la barre qualité |
| **GROWTH** | Acquisition & Distribution | **propriétaire du QC SHIP+WARM**, publication, UTM |
| **LEDGER** | Data / Finance / Intelligence | rubric /35, coûts, quotas, `cash_metrics.csv`, go/no-go |
| **FORGE** | Production | rendu vidéo, itérations qualité |
| **CONVERT** | Monétisation | offres, prix, CTA, checkout, affiliation |
| **SOUT** | Recherche & Opportunités | niches, programmes, veille, sélection d'offres |

**Architecture minimale (pas plus) :**
- **un salon de groupe** + **un fil par agent**, persistés en **SQLite** (un `store.db` par agent,
  comme `/home/box/sand-data/agents/<uuid>/store.db` dans l'archive)
- **@mentions** pour router ; ORBIT dispatche, LEDGER mesure, GROWTH juge la qualité
- **routines** : tâches planifiées (veille, reprise de cycle) — l'archive les mentionne
  explicitement (« routines », « tâches planifiées », « triggers »)
- **outils exposés** : fichiers, shell **sur liste blanche**, ffmpeg, QC métrique,
  **QC vision**, navigateur, base de métriques
- **coût** : logge **chaque appel** (agent, modèle, tokens, tâche) en SQLite.
  Route le courant sur `deepseek-flash`, réserve `deepseek-v4-pro` + `reasoning_effort: high`
  aux arbitrages. **Plafonne** : le salon GrokBot a produit **450 messages en 7 h** — sans plafond,
  la facture explose. Active le **context caching** pour les prompts système longs et stables.

**La « boucle économique » à respecter** (extraite de la spec d'origine) :
```
RECHERCHE → OPPORTUNITÉ → HYPOTHÈSE → OFFRE → CONTENU → DISTRIBUTION
→ TRAFIC → CONVERSION → REVENU → MESURE → APPRENTISSAGE → OPTIMISATION
```
Règles à ne pas violer :
- **« Ne confonds jamais activité et progrès. »** Produire 100 vidéos n'est pas un succès.
- Hiérarchie : **PROFIT → REVENUS → CONVERSIONS → CLICS → TRAFIC → AUDIENCE → ATTENTION**
- **Responsabilité collective** : aucun agent ne dit « ce n'est pas mon problème ».

## 6. LE LIVRABLE N°3 : le navigateur intégré

- **Playwright Python**, profil **persistant** (`--user-data-dir` dédié) pour conserver les
  sessions (YouTube Studio, Stripe, X, Reddit…) entre les runs
- Deux modes : **headless** (recherche, veille, lecture) et **visible avec passation humaine**
  (login, 2FA, captcha : l'agent s'arrête, demande à l'utilisateur, reprend)
- API minimale : `goto`, `snapshot`, `click`, `type`, `wait_for`, `screenshot`, `download`, `handoff`
- Les **captures d'écran partent dans le QC vision** (`deepseek-flash`) → l'agent « voit » la page
- ⚠️ **Préfère les API officielles** : **YouTube Data API** pour l'upload, **Stripe API** pour le
  checkout. Automatiser des logins est fragile et peut **violer les ToS** — on ne veut pas faire
  bannir la chaîne de l'utilisateur.

## 7. La barre de qualité — impose-la, mesure-la, prouve-la

**Deux QC indépendants** : `SHIP` (technique, binaire) et `WARM` (ressenti).
Notation **/35**, publication à **≥ 24**, cible **≥ 30**. **`WARM_FAIL` si humanité < 3/5.**
Un fail = **une liste de fixes**, jamais un « presque ». Un fail = **pas d'upload**.

### 7.1 Grille /35 — utilise EXACTEMENT ces barèmes

| Axe | Max |
|---|---|
| Hook | 5 |
| Douleur / clarté du problème | 4 |
| Preuve / démonstration | 4 |
| CTA | 4 |
| Lisibilité | 4 |
| **Humanité** | **5** (fail si < 3) |
| Motion | 4 |
| Son | 3 |
| Pacing | 2 |
| **TOTAL** | **35** |

### 7.2 QC mesurable, sans les yeux (gratuit, ffmpeg) — filtre primaire

```powershell
$v = "out\<offer_id>\final.mp4"
$duree = [double]((ffprobe -v error -show_entries format=duration -of csv=p=0 $v) -join '')

# Loudness : cible ~ -14 LUFS, LRA >= 5 LU
ffmpeg -hide_banner -i $v -af ebur128 -f null - 2>&1 | Select-String 'I:|LRA:'

# Cuts : compter les changements de scène
$n = @(ffmpeg -hide_banner -i $v -vf "select='gt(scene,0.25)',showinfo" -f null - 2>&1 |
       Select-String 'pts_time').Count

# Diaporama : images figées
ffmpeg -hide_banner -i $v -vf "freezedetect=n=-60dB:d=1.2" -an -f null - 2>&1 |
  Select-String 'freeze_start|freeze_duration'

# Couleur : SATAVG moyen (cible >= 25 ; < 10 = image terne)
ffmpeg -hide_banner -i $v -vf "select='not(mod(n,30))',signalstats,metadata=print:key=lavfi.signalstats.SATAVG" -an -f null - 2>&1 |
  Select-String 'SATAVG=' | ForEach-Object { [double](($_.Line -split '=')[-1]) } | Measure-Object -Average -Maximum

# Ken Burns : compare la variance du cadrage entre les frames extraites.
#   Deux frames identiques dans une même scène = Ken Burns absent
#   (c'est exactement ce que le QC de l'archive a reproché aux versions v2 et v3).

# Frames de preuve
0..5 | ForEach-Object { ffmpeg -y -loglevel error -ss $([math]::Round($duree*($_+0.5)/6,2)) -i $v -frames:v 1 "out\<offer_id>\frames\frame-$_.jpg" }
```

### 7.3 QC vision (DeepSeek) — **VALIDÉ, reprends cette recette exacte**

Script de départ qui **fonctionne déjà** : `tools\deepseek_smoke.py`.

```python
import base64, json, os, re
from openai import OpenAI

cli = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")

def b64(p):  # JPEG/PNG/WebP, base64 inline OK (max 32 Mo, 8192 px par côté)
    return "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()

GRID = (
    "Note ce Short sur 35. Barème STRICT : hook/5, douleur/4, preuve/4, cta/4, lisibilité/4, "
    "humanité/5 (0 si la voix semble synthétique ; ÉCHEC si <3), motion/4, son/3, pacing/2. "
    "total = somme exacte.\nReponds UNIQUEMENT par un objet JSON, sans texte ni balise autour :\n"
    '{"hook":0,"douleur":0,"preuve":0,"cta":0,"lisibilite":0,"humanite":0,"motion":0,"son":0,'
    '"pacing":0,"total":0,"impression":"on s arrete ou on skippe, en 2 phrases",'
    '"humanite_detail":"la voix et l image font-elles humaines ?",'
    '"coherence_narration_image":"les images correspondent-elles a la narration ?",'
    '"couleurs":"l image donne-t-elle envie, ou est-elle terne et froide ?",'
    '"defauts":["..."],"fixes":["..."]}'
)

content = [{"type": "text", "text":
    f"Voici 6 frames d'un Short vertical 1080x1920.\nNarration exacte :\n« {narration} »\n\n"
    "Évalue comme un spectateur qui scrolle.\n\n" + GRID}]
content += [{"type": "image_url", "image_url": {"url": b64(f), "detail": "low"}} for f in frames]

# thinking DÉSACTIVÉ : sinon reasoning_tokens consomme tout max_tokens et content revient VIDE
r = cli.chat.completions.create(
    model="deepseek-flash", messages=[{"role": "user", "content": content}],
    max_tokens=8000, extra_body={"thinking": {"type": "disabled"}})

verdict = json.loads(re.search(r"\{.*\}", r.choices[0].message.content, re.S).group(0))

# NE JAMAIS croire le modèle sur un seuil : recalcule le verdict EN CODE
verdict["total_calcule"] = sum(verdict[k] for k in
    ("hook","douleur","preuve","cta","lisibilite","humanite","motion","son","pacing"))
verdict["warm_pass"] = (verdict["total_calcule"] >= 24) and (verdict["humanite"] >= 3)
verdict["ship_pass"]  = verdict["total_calcule"] >= 24
print(json.dumps(verdict, ensure_ascii=False, indent=2))
```

**Trois pièges relevés en conditions réelles — ne les redécouvre pas :**

1. `thinking` **activé** sur `deepseek-flash` → `reasoning_tokens` a mangé **tout** le `max_tokens`
   (1600/1600) et **`content` est revenu vide**.
   → `thinking: disabled` + `max_tokens: 8000`
2. Le modèle a répondu **`warm_pass: true` alors qu'il s'était noté 21/35**.
   → **tout seuil se calcule en code**, jamais par le modèle.
3. Les images ne sont acceptées que dans les messages **`user`**
   (en `system` ou `assistant` → **400**).

**Ajoute ces 3 questions** (elles couvrent exactement les griefs du commanditaire) :
- cohérence narration ↔ image : « liste les frames sans lien avec la phrase prononcée à ce moment »
- humanité de la voix : « humaine ou synthétique ? qu'est-ce qui la trahit ? »
- couleurs : « l'image donne-t-elle envie, ou est-elle terne et froide ? »

### 7.4 Règle de méthode (non négociable)

- **Mesure avant de déclarer.** Jamais « c'est bon » sans chiffre ni verdict vision.
- **Avant / après systématique** : même texte, même durée, même offre.
- Chaque défaut s'écrit `constat → mesure → correctif → nouvelle mesure`.
- **Bats le point zéro** : `−20,6 LUFS · LRA 2,2 · SATAVG 7,7 · 5 boucles · 21/35 vision`.
  **C'est la barre à dépasser, pas une opinion.**

## 8. Les offres à produire (contenu imposé) et l'existant réutilisable

| `offer_id` | Offre | Score v1 | Angle |
|---|---|---|---|
| `cash_impayes_relance01` | **Impayés** — relance de facture impayée | 33/35 | le cash qui ne rentre pas |
| `cash_devis_cgv01` | **Devis / CGV** — devis pro + CGV (pack 37 €) | 30/35 → WARM_FAIL | passer pour un pro |
| `cash_avenant_scope01` | **Avenant** — faire signer un avenant de scope | 33/35 | le client qui élargit sans payer |
| `cash_linkedin_rdv01` | **LinkedIn RDV** — obtenir un rendez-vous | 28/35 | le prospect qui ne répond jamais |

CTA : **9 € / 19 € / 27 € / 37 €**.

Déjà en place, à réutiliser (détails dans `video-factory-reference.md` §6) :
- landing **https://guideexpress-prompts.vercel.app**
- Stripe livemode `buy.stripe.com/8x27sM0Dg9Ipf133X11oI0a`
  (`client_reference_id=ai_prompts_freelance_short01_offre`)
- chaîne **GuideExpress**, compte X **@Sllrd3**
- 4 Shorts déjà publiés : `youtube.com/shorts/AswB6W9u4yk`, `JffIUPKOs6Q`, `VKy1fSayTK0`, `yx4vz1Bk9C8`

## 9. Ordre des travaux — avec critère de sortie à chaque phase

| Phase | Contenu | Critère de sortie |
|---|---|---|
| **0 · Socle** | valider la clé (`tools\deepseek_smoke.py`), arborescence, garde-fous | les 2 modèles répondent, le verdict JSON est persisté |
| **1 · QC complet** ⭐ | `qc_metrics.py` (ffmpeg) + `qc_vision.py` (DeepSeek, §7.3) + recalcul des seuils en code | le point zéro est re-noté automatiquement à **21/35** (±2) et les seuils sont calculés en code |
| **2 · Rendu Remotion** ⭐⭐ | composant `CashShort` paramétré par `job.json` : kinetic typography, Ken Burns 1.0→1.22, transitions, SFX/bed, palette chaude, élément humain, **synchro mot à mot** | **un mp4 de 20–30 s sur `cash_impayes_relance01`, ≥ 30/35, WARM_PASS**, qui **bat les 4 métriques** du point zéro |
| **3 · Voix** | Chatterbox local + prosodie dirigée + bench 3 voix FR (même texte, comparaison) | **LRA ≥ 5 LU** et verdict vision « voix humaine » |
| **4 · Groupe d'agents** | orchestrateur + 6 agents + SQLite + bus + routines + log de coûts | un cycle `SOUT → CONVERT → FORGE → GROWTH → LEDGER` produit une vidéo **sans intervention** |
| **5 · Navigateur** | service Playwright persistant + passation humaine + screenshots vers la vision | connexion à une plateforme réelle, session conservée au run suivant |
| **6 · Boucle** | enchaînement complet, batch, métriques, publication | 2 offres publiées, métriques J+1 en base |

**Priorité absolue : la phase 2.** Un groupe d'agents qui produit des vidéos qu'on skippe ne
rapporte rien et consomme des tokens à chaque itération.
**Le rendu d'abord, l'automatisation ensuite.**

## 10. Garde-fous (à implémenter, pas à promettre)

- **Secrets** : `DEEPSEEK_API_KEY` reste en variable d'environnement. **Rien dans le repo, rien
  dans les logs, rien dans les prompts envoyés aux agents.**
- **Shell des agents** : liste blanche de commandes ; pas de suppression récursive ; rien de
  destructif hors du dossier de travail. Les agents tournent sur la **vraie machine** de
  l'utilisateur — pas dans un sandbox (contrairement à Grok Bot) : la prudence est de mise.
- **Sortant** : tout ce qui sort de la machine (upload, email, post, achat, dépense) passe par un
  **`--dry-run` par défaut** + **confirmation humaine**. **Aucune dépense sans validation explicite.**
- **Sauvegarde** : avant toute refonte d'un dossier existant, copie horodatée.
  ⚠️ Ne réutilise pas et ne supprime pas `C:\Users\saill\Projects\_to_delete\MoneyPrinterTurbo_broken*`
  (traces d'échecs du 28/08 : cause identifiée = crash d'encodage cp1252, voir §2).
- **ToS** : API officielles pour YouTube/Stripe. Ne mets pas la chaîne de l'utilisateur en danger.
- **Droits d'image** : pour tout rush de tiers, garder un champ `rights` et une attribution à
  l'écran (le QC de l'archive refusait au-delà de **35 %** d'extraits cités).

## 11. Comment travailler (le style attendu)

- **Du code et des mp4**, pas des `.md` de spécifications en série. Le pipeline précédent a laissé
  8 fichiers de doc pour un seul mp4 : **ne refais pas ça**. Les `.md` existants suffisent.
- **Une seule commande** doit produire le livrable :
  `render.cmd <job.json>` → `final.mp4` + `publish.json` + `frames/` + `qc.json`
- **Lance les appels d'outils en parallèle** quand ils sont indépendants. Ne fais pas valider
  chaque micro-étape.
- **Ne réécris pas un moteur vidéo existant** : MoneyPrinterTurbo garde sa plomberie
  (batch, matériel, sous-titres whisper) ; **ajoute Remotion à côté**.
- **N'invente aucun chiffre.** Si tu n'as pas mesuré, écris « non mesuré ».
- Si tu es bloqué : dis **où** précisément, et propose **2 options** avec leurs compromis.
- Tranche toi-même quand tu as les informations ; ne renvoie pas la décision à l'utilisateur.

## 12. Rapport attendu à chaque message

Un message = **un résultat mesuré** :

```
OFFRE   : <offer_id>
SCORE   : SHIP <n>/35  ·  WARM <n>/35 (<n>/5 humanité)  ·  verdict <PASS|FAIL>
MÉTRIQUES: LUFS <n> · LRA <n> · SATAVG <n> · cuts <n> · boucles <n> · Ken Burns <oui/non>
VISION  : <1 phrase de l'impression + incohérences relevées>
FICHIERS: out\<offer_id>\final.mp4 · publish.json · qc.json · frames\*.jpg
FIXES   : <liste des correctifs appliqués depuis l'itération précédente>
```

À chaque itération : **montre les frames** (ou les verdicts vision qui les commentent) —
c'est la seule façon de sortir du diaporama.

---

## Premier message que j'attends de toi

1. Confirme que tu as lu `video-factory-reference.md`, `POINT-ZERO.md`, `AUDIT.md` et
   `tools\deepseek_smoke.py`, et cite en **5 lignes** ce que tu retiens du diagnostic vidéo.
2. Confirme que le workspace de travail est bien `C:\Users\saill\Projects\video-factory`
   et propose l'arborescence que tu vas créer (où, quoi).
3. Dis-moi ce que tu penses de la **licence Remotion** selon le statut de l'utilisateur ;
   si elle bloque, propose l'alternative ffmpeg et **ses limites honnêtes**.
4. Lance la **Phase 0** (validation de la clé) puis attaque la **Phase 1** (QC complet).
   N'attends mon feu vert que si tu as une décision d'architecture à me soumettre.


