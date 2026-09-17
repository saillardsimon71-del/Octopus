# Worker cloud vidéo — spécification d'image

## Objectif

L'image doit exécuter le FORGE existant, pas un nouveau moteur :

1. recevoir `VideoJob`;
2. écrire les données temporaires du job dans un workspace isolé;
3. produire l'audio via TTS configurable;
4. rendre le template Remotion existant;
5. muxer avec FFmpeg;
6. calculer le QC technique;
7. uploader les artefacts vers object storage;
8. retourner un manifest sans secrets.

## Dépendances à figer après validation locale

- Python 3.11.x
- Node 22.x (ou version réellement supportée par le package Remotion)
- Chromium fourni par la chaîne Remotion
- FFmpeg version déterminée par le benchmark
- backend TTS réellement choisi

Ne pas copier une installation Windows ou le `.venv` MoneyPrinterTurbo dans l'image.

## Artefacts minimum

```text
manifest.json
final.mp4
qc_metrics.json
publish.json
frames/*.jpg
logs/worker.log
```

## Invariants

- sortie vidéo 1080x1920, 30 fps;
- aucune écriture permanente requise sur le disque du worker;
- un `job_id` ne doit pas être rendu deux fois après un résultat valide;
- un échec de TTS/Remotion/FFmpeg/QC est `FAILED` et jamais `COMPLETED`;
- l'URL vidéo retournée est une référence d'object storage, idéalement signée et temporaire;
- les secrets arrivent par environnement/secret manager, jamais par `VideoJob`.

## GPU

Le GPU ne doit pas être imposé à toute l'image avant mesure. Remotion, FFmpeg mux, ffprobe et QC technique n'en justifient pas intrinsèquement l'usage. Le backend TTS est le premier candidat à benchmarker CPU vs GPU.
