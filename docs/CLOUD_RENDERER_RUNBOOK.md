# Cloud video renderer — runbook

Cette branche prépare le passage du rendu vidéo lourd vers un worker distant sans changer le contrat des agents.

## Séquence réelle

```text
CONVERT job
  -> FORGE
  -> VideoService
  -> CloudVideoRenderer
  -> provider queue (RunPod au premier déploiement)
  -> video_worker
  -> Chatterbox/TTS
  -> Remotion CashShort
  -> FFmpeg mux
  -> QC technique
  -> object storage
  -> résultat + frames
  -> FORGE/GROWTH
```

## Modes

- `PODALUX_VIDEO_RENDERER=local` : pipeline FORGE historique.
- `PODALUX_VIDEO_RENDERER=cloud` : envoi vers le renderer cloud.
- `PODALUX_VIDEO_PROVIDER=runpod` : adaptateur RunPod Serverless.
- `PODALUX_VIDEO_PROVIDER=http` : endpoint HTTP générique compatible avec le contrat.

Aucun secret ne doit être placé dans `job.json` ou dans le payload vidéo. Les secrets sont des variables d'environnement du processus/worker.

## RunPod — paramètres

```text
PODALUX_VIDEO_RENDERER=cloud
PODALUX_VIDEO_PROVIDER=runpod
PODALUX_RUNPOD_ENDPOINT_ID=<endpoint>
PODALUX_RUNPOD_API_TOKEN=<secret>
PODALUX_VIDEO_STORAGE_BACKEND=s3
PODALUX_VIDEO_S3_BUCKET=<bucket>
AWS_REGION=<region>
CHATTERBOX_URL=<endpoint TTS accessible depuis le worker>
```

Le worker ne suppose pas que Chatterbox est local à `127.0.0.1`. Sans `CHATTERBOX_URL`, le script conserve le défaut local historique ; en cloud, cela doit donc être explicitement configuré ou remplacé par un backend TTS disponible dans le worker.

## Stockage

Préfixe logique recommandé :

```text
videos/<offer_id>/<job_id>/
  manifest.json
  final.mp4
  qc_metrics.json
  audio/mix.wav
  audio/vo.wav
  audio/captions.json
  remotion/captions.ts
  remotion/job.ts
  frames/frame-0.jpg ... frame-5.jpg
  logs/*.log
```

Le worker peut utiliser un bucket S3/S3-compatible. Les URLs retournées au contrôleur doivent être des URLs de lecture temporaires/signées.

## Contrôle des coûts

Le budget LLM `CYCLE_BUDGET_USD` d'OCTOPUS ne représente pas à lui seul le coût du rendu cloud. Une métrique séparée du provider devra être remontée avant passage en production. Le même `job_id` doit conserver la même identité de rendu pour éviter les doublons accidentels.

## Ce qui reste à vérifier sur la machine

Depuis GitHub seul, impossible de confirmer le fonctionnement réel de :

- Pinokio/Wan2GP local ;
- MiniMax H3 local ;
- service Chatterbox `:4123` ;
- performance réelle du worker GPU/CPU ;
- compatibilité exacte de l'installation Chromium avec la version Remotion du dépôt.

Ne jamais considérer ces points comme "validés" tant qu'un test réel n'a pas été exécuté.
