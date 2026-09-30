# Agnes Video Generator — Installation reproductible sur Windows

Date: 2026-09-30
Pin upstream: `lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b`
Licence: MIT

Ce document décrit l'installation **reproductible** du moteur vidéo officiel Agnes
pour OCTOPUS, avec une configuration locale sécurisée de `AGNES_API_KEY`.

> **Règle de sécurité**: la clé Agnes appartient **uniquement** au processus Agnes.
> Elle ne doit jamais apparaître dans les prompts LLM, les journaux OCTOPUS,
> le dépôt Git, les arguments visibles de ligne de commande, ou le navigateur OCTOPUS.

## 1. Prérequis Windows

- Windows 10/11
- Python 3.10+ (3.11 recommandé)
- Git
- ffmpeg (via `imageio-ffmpeg` pip, ou système)
- Docker Desktop **optionnel** (recommandé pour isolation)

Vérification:

```powershell
python --version
git --version
ffmpeg -version
docker --version   # optionnel
```

## 2. Récupération du pin exact

Le script existant télécharge le commit figé dans un cache ignoré par Git:

```powershell
# Depuis la racine OCTOPUS
powershell -ExecutionPolicy Bypass -File scripts/fetch_pinned_upstreams.ps1 -Target agnes
```

Résultat: `cache/upstreams/agnes-video-generator/` @ `a87162d6df73ffe72186838ca0ae9d461e68589b`

Vérification:

```powershell
git -C cache/upstreams/agnes-video-generator rev-parse HEAD
# doit afficher a87162d6df73ffe72186838ca0ae9d461e68589b
git -C cache/upstreams/agnes-video-generator status --porcelain
# doit être vide (pas de modifs)
```

## 3. Méthode sécurisée pour renseigner la clé

**Ne communique jamais ta clé dans Arena, ni dans un prompt.**

### Option A — Variable d'environnement utilisateur (recommandée pour natif)

```powershell
# Définir pour l'utilisateur courant (persistant, hors repo)
setx AGNES_API_KEY "ta_cle_ici"

# Pour la session courante uniquement:
$env:AGNES_API_KEY="ta_cle_ici"
```

Le processus Agnes lit `AGNES_API_KEY` via `os.environ`. OCTOPUS ne lit jamais cette variable.

### Option B — Fichier .env local hors Git (natif)

Dans `cache/upstreams/agnes-video-generator/.env` (ignoré par .gitignore d'OCTOPUS et par upstream):

```
AGNES_API_KEY=ta_cle_ici
HOST=127.0.0.1
PORT=8765
```

> Ce fichier est dans `cache/`, donc ignoré par Git. Ne le mets jamais dans `Octopus/.env`.

### Option C — Docker env file sécurisé

Crée un fichier `%APPDATA%\octopus\agnes.env` ou `C:\octopus\secrets\agnes.env`:

```
AGNES_API_KEY=ta_cle_ici
```

Puis lance avec `--env-file`.

### Option D — Windows Credential Manager (avancé)

```powershell
cmdkey /generic:agnes_api_key /user:agnes /pass:ta_cle_ici
# puis dans script de lancement, récupère via:
# $key = cmdkey /list:agnes_api_key ... (ou via PowerShell SecretManagement)
```

**Interdits**:
- `AGNES_API_KEY` dans `resources.toml`, `business.toml`, prompts, `journal.db`, logs, ou arguments CLI visibles (`--api-key xxx`)
- Clé dans le navigateur OCTOPUS (`browser_*` tools)

## 4. Installation native Windows (venv isolé)

```powershell
# 1. Pin déjà récupéré (voir §2)
cd cache/upstreams/agnes-video-generator

# 2. venv isolé (jamais dans OCTOPUS, préserve entre redémarrages)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Dépendances (pin source fixe, mais requirements utilisent ranges)
pip install --upgrade pip
pip install -r requirements.txt

# 4. Vérifier ffmpeg (imageio-ffmpeg fournit un binaire, mais on vérifie)
python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"

# 5. Configuration loopback only
$env:HOST="127.0.0.1"
$env:PORT="8765"
# AGNES_API_KEY déjà dans env utilisateur (voir §3)

# 6. Lancement
python server.py
```

Le service doit afficher:

```
Uvicorn running on http://127.0.0.1:8765
```

Vérifie qu'il n'écoute **que** sur loopback:

```powershell
netstat -ano | findstr 8765
# doit afficher 127.0.0.1:8765, pas 0.0.0.0:8765
```

**Persistance entre redémarrages**:
- `.working_dir/` contient tâches et vidéos (`final_video.mp4`)
- `.agnes_config/` contient config et workspaces
- Ne supprime jamais ces dossiers. Ils sont dans `cache/upstreams/agnes-video-generator/` et préservés.

### Script automatisé

```powershell
# Depuis racine OCTOPUS
powershell -ExecutionPolicy Bypass -File scripts/agnes_setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/agnes_run.ps1
```

## 5. Installation Docker (recommandée pour prod locale)

```powershell
# Depuis cache/upstreams/agnes-video-generator
docker build -t agnes-video:7.0.1-pinned -t agnes-video:pinned .

# Lancement loopback only, avec persistance
docker run -d --name agnes-video `
  -p 127.0.0.1:8765:8765 `
  -e HOST=127.0.0.1 -e PORT=8765 `
  -e AGNES_API_KEY=$env:AGNES_API_KEY `
  -v ${PWD}/agnes_data/working:/app/.working_dir `
  -v ${PWD}/agnes_data/config:/app/.agnes_config `
  --restart unless-stopped `
  agnes-video:pinned
```

Ou via compose (fichier fourni `ops/agnes/docker-compose.yml`):

```powershell
cd ops/agnes
$env:AGNES_API_KEY="ta_cle"   # ou via fichier env sécurisé
docker compose up -d
```

Vérification:

```powershell
curl http://127.0.0.1:8765/api/health
# {"ok": true, "service": "agnes-video-generator", "status": "healthy"}
```

**Persistance Docker**:
- Volumes bind `agnes_data/working` et `agnes_data/config` → sur disque hôte, survit à `docker rm`
- Même sans bind, Docker VOLUME garde données entre stop/start, mais `docker cp` nécessaire pour export.

## 6. Commandes pour lancer et arrêter

### Natif

```powershell
# Lancer (depuis racine OCTOPUS)
powershell -ExecutionPolicy Bypass -File scripts/agnes_run.ps1

# Arrêter: Ctrl+C dans le terminal, ou
powershell -ExecutionPolicy Bypass -File scripts/agnes_stop.ps1

# Health
curl http://127.0.0.1:8765/api/health
python -m octopus resources check agnes_video --json  # si ressource déclarée
```

### Docker

```powershell
# Lancer
docker start agnes-video
# ou
docker compose -f ops/agnes/docker-compose.yml up -d

# Arrêter (préserve fichiers)
docker stop agnes-video

# Supprimer container mais garder volumes (fichiers sur disque hôte si bind mount)
docker rm agnes-video

# Logs (ne doivent jamais contenir la clé)
docker logs agnes-video --tail 100
```

## 7. Intégration OCTOPUS

### Déclaration ressource (optionnel mais recommandé)

Dans `resources.toml`:

```toml
[[resource]]
key = "agnes_video"
kind = "video_engine"
label = "Agnes Video Generator local (127.0.0.1:8765)"
locator = "http://127.0.0.1:8765"
capabilities = ["video_generation", "agnes_submit", "agnes_stop"]
probe = "agnes"
probe_args = { url = "http://127.0.0.1:8765" }
```

Puis:

```powershell
python -m octopus resources sync
python -m octopus resources check agnes_video
```

### Canal économique (autorisation humaine)

```powershell
# Via CLI Python
python -c "
from octopus import economy
cid = economy.add_channel('octopus', 'agnes_video', 'Local Agnes', created_by='human',
                          locator='http://127.0.0.1:8765',
                          capabilities=['agnes_submit','agnes_stop'])
print(f'channel #{cid} created')
"

# Activer
python -m octopus strategy ...  # ou via GUI, ou:
python -c "
from octopus import economy
economy.update_channel('octopus', 1, actor='human', status='active', access='act')
"
```

Sans canal actif avec `act`, toute soumission est bloquée (frontière humaine).

### Vérifier intégration

```powershell
python -m pytest tests/test_agnes.py tests/test_agnes_production.py -v
python -m octopus worker --once --task <id>  # pour tester handler agnes.generate_video
```

## 8. Première génération réelle via OCTOPUS (après clé configurée)

**Aucune génération externe réelle ne doit être déclenchée pendant la session Arena.**

Quand tu es prêt sur ta machine Windows, avec clé configurée et service Agnes lancé sur `127.0.0.1:8765`:

```powershell
# 1. Health
curl http://127.0.0.1:8765/api/health

# 2. Smoke test réel (une seule génération courte, idempotente)
$env:OCTOPUS_HOME=$PWD
python -m octopus enqueue octopus agnes.generate_video --input '{\"prompt\": \"Une mer calme au coucher du soleil, plan large, 5 secondes\", \"idempotency_key\": \"smoke-2026-09-30\"}'

# 3. Lancer worker
python -m octopus worker --once

# 4. Vérifier résultat
python -c "
from octopus import agnes_production
gens = agnes_production.list_generations('octopus', limit=5)
print(gens[0])
from octopus import agnes
from pathlib import Path
print(agnes.verify_mp4(Path(gens[0]['output_path'])))
"

# 5. Via mission autonome (superviseur)
python -c "
from octopus import strategy
oid = strategy.create('objective', 'octopus', 'Vidéo test Agnes', created_by='human',
                      statement='Produire une vidéo de 5s d une mer calme via Agnes et vérifier le livrable',
                      success_criteria='verified_video_count>=1')
strategy.transition('objective', oid, 'octopus', 'active', actor='human')
print(f'objective #{oid} active')
"
python -m octopus runtime --once --business octopus
```

**Vérification finale attendue**:
- Fichier MP4 présent dans `data/agnes_videos/octopus/...` ou `cache/upstreams/agnes-video-generator/.working_dir/.../final_video.mp4`
- `verify_mp4` → `verified=True`, SHA-256 présent
- Evidence dans `strategy_evidence` avec `source_ref` = chemin fichier, `observation` contient SHA-256
- Objectif clos uniquement après vérification physique (pas seulement HTTP `completed`)

## 9. Coûts et quotas

- Upstream présenté comme gratuit (avec clé API gratuite)
- OCTOPUS déclare désormais `cost_class=free_quota` pour `agnes_video:submit`
- Limites conservées:
  - quantité: 30/jour/business (`MAX_DAILY_GENERATIONS`)
  - durée: 20s max par clip
  - quotas: 429 gère backoff exponentiel
  - coûts: observés via `economy` si jamais facturation réelle apparaît
  - autorisation humaine pour toute dépense réelle (si `cost_class=paid` réapparaît)

Ne considère jamais gratuit = illimité.

## 10. Sécurité checklist

- [ ] `HOST=127.0.0.1` (jamais 0.0.0.0 en prod locale)
- [ ] Port bind `127.0.0.1:8765:8765` en Docker
- [ ] `AGNES_API_KEY` uniquement dans env du processus Agnes, jamais dans Git, prompts, logs, args, navigateur
- [ ] `cache/upstreams/agnes-video-generator/.working_dir` et `.agnes_config` préservés
- [ ] Aucun log ne contient la clé (vérifie `docker logs`, `server.log`)
- [ ] Canal `agnes_video` actif + `act` accordé par humain
- [ ] Tests avec faux service passent sans appel externe
