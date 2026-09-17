# RunPod — mise en place du cloud vidéo

Cette étape concerne le **cloud uniquement**. Le PC Windows n'a pas besoin des poids vidéo, de Remotion, de FFmpeg ou de Chatterbox pour le chemin normal `PODALUX_VIDEO_RENDERER=cloud`.

## 1. Deux charges cloud distinctes

OCTOPUS distingue volontairement :

```text
FORGE vidéo standard
  → PODALUX_RUNPOD_ENDPOINT_ID
  → worker `video_worker/`
  → TTS configuré par `CHATTERBOX_URL`
  → Remotion + FFmpeg + QC

MiniMax H3
  → OCTOPUS_MINIMAX_H3_ENDPOINT_ID
  → endpoint H3/ComfyUI séparé
  → workflow H3 construit par `octopus/media/minimax_h3_cloud.py`
```

H3 ne doit jamais être téléchargé sur le PC local.

## 2. Endpoint Serverless

RunPod documente actuellement les endpoints Serverless comme des endpoints à file : `POST /run` pour soumettre une tâche puis `GET /status/{job_id}` pour récupérer le résultat. La rétention annoncée pour les résultats d'un appel asynchrone `/run` est de 30 minutes. Vérifier ces paramètres dans la documentation officielle au moment du déploiement.

Le client OCTOPUS utilise donc :

```text
https://api.runpod.ai/v2/<ENDPOINT_ID>/run
https://api.runpod.ai/v2/<ENDPOINT_ID>/status/<JOB_ID>
```

L'authentification se fait par :

```text
Authorization: Bearer <RUNPOD_API_TOKEN>
```

## 3. Timeout à régler

Le client OCTOPUS attend jusqu'à 45 minutes par défaut. La configuration d'un endpoint RunPod doit donc autoriser une durée d'exécution au moins équivalente au pire cas réel du worker, avec une marge pour le cold start. Vérifier le `execution timeout` de l'endpoint avant le premier rendu.

Pour H3, prévoir une marge suffisante pour le cold start et la génération.

## 4. Worker FORGE standard

Le point d'entrée est :

```text
video_worker/runpod_handler.py
```

L'image est construite depuis :

```text
video_worker/Dockerfile
```

Le worker attend notamment :

```text
CHATTERBOX_URL=https://<tts-cloud>/v1/audio/speech
REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium
PODALUX_VIDEO_STORAGE_BACKEND=s3
PODALUX_VIDEO_S3_BUCKET=<bucket>
PODALUX_VIDEO_S3_PREFIX=videos
```

Les credentials S3/S3-compatible restent dans les secrets/env du worker. Le `VideoJob` ne doit jamais contenir de credential.

Le backend S3 OCTOPUS génère des URLs présignées et calcule un SHA-256 pour chaque artefact publié.

## 5. Artefacts retournés

Le worker publie au minimum :

```text
<offer_id>/<job_id>/manifest.json
<offer_id>/<job_id>/final.mp4
<offer_id>/<job_id>/qc_metrics.json
<offer_id>/<job_id>/frames/*
<offer_id>/<job_id>/logs/*
```

Le manifeste est la source de vérité du résultat. Le control-plane télécharge `final.mp4` et les artefacts disponibles puis vérifie leur SHA-256 lorsqu'il est fourni.

## 6. H3

L'endpoint H3 doit avoir les modèles/node packs nécessaires au workflow ComfyUI H3. OCTOPUS n'embarque pas les poids dans le dépôt.

Variables côté control-plane :

```text
OCTOPUS_MINIMAX_H3_ENDPOINT_ID=<endpoint H3>
OCTOPUS_MINIMAX_H3_API_TOKEN=<token>
```

La génération utilise actuellement :

```text
24 fps
grid temporelle 17n+5
canvas natif autour de 768x1344
workflow T2VA ComfyUI
```

## 7. Premier test sans risque de double facturation

1. Déployer l'image worker.
2. Configurer le stockage et le TTS.
3. Configurer un timeout d'exécution compatible avec le temps réel attendu.
4. Envoyer une seule tâche de test avec un `job_id` déterministe.
5. Vérifier `COMPLETED`, `final.mp4`, `qc_metrics.json` et `manifest.json`.
6. Renvoyer exactement le même job : le worker doit réutiliser le manifeste complet au lieu de refaire le rendu.

Ne pas lancer un batch commercial avant ce test.

## 8. Référence actuelle

Documentation RunPod à consulter avant tout changement d'infrastructure :

- https://docs.runpod.io/serverless/overview
- https://docs.runpod.io/serverless/endpoints/endpoint-configurations
- https://docs.runpod.io/serverless/quickstart
