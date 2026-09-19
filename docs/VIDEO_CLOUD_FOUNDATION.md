> **Statut : fondation historique du renderer cloud RunPod.** Elle décrit encore des briques réelles, mais la stratégie compute actuelle (Salad/GPU.ai, broker, breaker, watchdog) est dans `COMPUTE_GPU.md`. Ne pas utiliser ce fichier comme état global du projet.

# Vidéo cloud — fondations

Cette branche prépare OCTOPUS à sortir le rendu vidéo lourd du poste local sans créer un deuxième moteur de rendu.

## Architecture retenue

```text
OCTOPUS / agents
    -> VideoJob v1
    -> VideoService
    -> CloudVideoRenderer
    -> RunPod Serverless /run
    -> worker Docker isolé
       -> TTS configurable
       -> Remotion CashShort
       -> FFmpeg mux
       -> QC technique
       -> object storage
    -> VideoResult / manifest
    -> frames temporaires côté OCTOPUS
    -> GROWTH / LEDGER / publication
```

Le pipeline existant reste la source de vérité : `remotion/src/CashShort.tsx`,
`tools/make_audio_chatterbox_full.py` et `tools/qc_metrics.py` sont réutilisés.
Aucun nouveau moteur vidéo n'est introduit.

## État réel de la branche

Déjà implémenté :

- `VideoJob` / `VideoResult` versionnés (`schema_version=1`).
- États métier stables : `QUEUED`, `RUNNING`, `RENDERING`, `QC`, `COMPLETED`, `FAILED`, `CANCELLED`, `EXPIRED`.
- identité déterministe `job_id` calculée à partir du job canonique.
- protection contre l'envoi de clés ressemblant à des secrets dans le payload.
- renderer abstrait (`VideoRenderer`) avec implémentation RunPod Serverless.
- client HTTP fournisseur-agnostique pour permettre un autre provider sans toucher aux agents.
- backend object storage local de test + S3/S3-compatible.
- worker Docker Linux qui réutilise le pipeline FORGE existant dans un workspace isolé.
- contrôle d'idempotence par `manifest.json` : un résultat `COMPLETED` valide n'est pas rendu une seconde fois.
- les frames de QC sont remontées vers OCTOPUS pour conserver le `GROWTH` actuel.
- le serveur Chatterbox peut maintenant être déplacé de `127.0.0.1:4123` via `CHATTERBOX_URL`.
- `agents/cycle.py` route le rendu vers `VideoService` lorsque `PODALUX_VIDEO_RENDERER=cloud` et reste local par défaut.
- `runtime.py` n'est pas modifié.

Non validé ici :

- exécution réelle du conteneur Linux avec ton TTS Chatterbox ;
- présence exacte de Chromium/FFmpeg dans l'environnement cible ;
- benchmark CPU/GPU du TTS ;
- déploiement réel d'un endpoint RunPod ;
- accès et performances de ton stockage objet ;
- inspection de Pinokio/Wan2GP/MiniMax H3 sur le PC local.

## RunPod

Le client utilise l'API de file asynchrone :

```text
POST https://api.runpod.ai/v2/<ENDPOINT_ID>/run
body: {"input": <VideoJob>}
GET  https://api.runpod.ai/v2/<ENDPOINT_ID>/status/<REMOTE_JOB_ID>
```

Configuration côté OCTOPUS :

```text
PODALUX_VIDEO_RENDERER=cloud
PODALUX_VIDEO_PROVIDER=runpod
PODALUX_RUNPOD_ENDPOINT_ID=<id>
PODALUX_RUNPOD_API_TOKEN=<secret runtime>
PODALUX_RUNPOD_API_BASE_URL=https://api.runpod.ai/v2
PODALUX_VIDEO_JOB_TIMEOUT_S=2700
```

Le token ne doit jamais être placé dans `VideoJob`, dans Git ou dans un log.

## Object storage

Production recommandée : S3 ou stockage compatible S3, sans bucket public.

```text
PODALUX_VIDEO_STORAGE_BACKEND=s3
PODALUX_VIDEO_S3_BUCKET=<bucket>
PODALUX_VIDEO_S3_PREFIX=videos
AWS_REGION=<region>
PODALUX_VIDEO_PRESIGN_SECONDS=3600
```

Clés produites :

```text
videos/<offer_id>/<job_id>/final.mp4
videos/<offer_id>/<job_id>/qc_metrics.json
videos/<offer_id>/<job_id>/frames/frame-*.jpg
videos/<offer_id>/<job_id>/logs/*.log
videos/<offer_id>/<job_id>/manifest.json
```

Le worker n'utilise son disque que comme espace temporaire. Le manifest est la source de vérité du résultat rendu.

## TTS

Le script audio conserve la compatibilité avec le serveur local historique mais lit désormais :

```text
CHATTERBOX_URL=http://127.0.0.1:4123/v1/audio/speech
```

En cloud, le backend TTS doit être joignable depuis le worker. La première architecture garde TTS + Remotion + FFmpeg dans le même worker afin d'éviter un microservice supplémentaire. Le besoin GPU doit être décidé après benchmark réel.

## Idempotence et coûts

Le même `job_id` ne doit jamais déclencher deux rendus valides. Le worker vérifie d'abord le manifest du stockage objet.

Le budget OCTOPUS actuel (`CYCLE_BUDGET_USD`) couvre les coûts de la couche LLM tels qu'implémentés aujourd'hui ; il ne mesure pas encore automatiquement la facture du provider vidéo. Cette télémétrie devra être branchée avant de permettre des cycles cloud sans surveillance.

## QC

Le worker produit le QC technique existant. `GROWTH` continue d'effectuer le QC vision via DeepSeek après remontée des frames.

Important : les métriques `LRA`, `SATAVG` et `ken_burns_motion` sont mesurées par `tools/qc_metrics.py`, mais toutes ne sont pas encore des gates bloquantes dans `agents/config.py`. Il ne faut pas présenter ces cibles comme des critères de publication tant qu'elles ne le sont pas dans le code.

## Tests

Les tests dédiés sont dans :

```text
 tests/test_video_contract.py
 tests/test_video_service.py
 tests/test_video_cloud_client.py
 tests/test_video_executor.py
```

La CI dédiée est `.github/workflows/video-foundation.yml`.

## Validation locale à faire ensuite

Sur la machine cible :

```powershell
# 1. Vérifier Python / Node / npm / FFmpeg / Chromium selon l'installation cible
python --version
node --version
npm --version
ffmpeg -version

# 2. Vérifier le serveur Chatterbox actuel
$env:CHATTERBOX_URL="http://127.0.0.1:4123/v1/audio/speech"

# 3. Exécuter les tests du dépôt
python -m pytest -q

# 4. Construire l'image worker
 docker build -f video_worker/Dockerfile -t podalux-video-worker:dev .
```

Le test E2E cloud ne doit être lancé qu'après validation de l'image. Il doit couvrir : submit, récupération après timeout client, doublon, échec TTS, échec Remotion, annulation si le provider la supporte, expiration et crash du worker.

## Wan2GP / MiniMax H3

Le sous-système `octopus/media/*` reste séparé du chemin Remotion/Podalux. Cette branche ne prétend pas connaître l'installation Pinokio ni la machine locale. L'intégration H3 doit se faire derrière une abstraction vidéo distincte une fois le protocole local réellement inspecté et mesuré.
