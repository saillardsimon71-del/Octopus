# Vidéo cloud — fondations

Cette branche prépare OCTOPUS à sortir le rendu vidéo lourd du poste local.

## Principe

```text
OCTOPUS / agents
    -> VideoJob v1
    -> CloudVideoRenderer
    -> provider queue
    -> worker Docker
    -> Remotion + TTS + FFmpeg + technical QC
    -> object storage
    -> VideoResult
    -> GROWTH / LEDGER / publication
```

## Ce qui est volontairement déjà figé

- `VideoJob` et `VideoResult` sont versionnés (`schema_version=1`).
- États métier : `QUEUED`, `RUNNING`, `RENDERING`, `QC`, `COMPLETED`, `FAILED`, `CANCELLED`, `EXPIRED`.
- `job_id` est l'identité métier ; le fournisseur distant reste un détail d'implémentation.
- Les payloads refusent les clés ressemblant à des secrets.
- Le renderer est derrière `VideoRenderer` ; aucun SDK RunPod/Modal/AWS n'est imposé au cœur OCTOPUS.
- Le client cloud utilise HTTP/JSON et peut donc être adapté à RunPod, Cloud Run, Modal ou un autre endpoint.
- Le worker refuse actuellement de prétendre avoir produit une vidéo : il échoue explicitement tant que le pipeline FORGE réel n'a pas été installé et validé dans l'image.

## Configuration cloud

Variables prévues :

```text
PODALUX_VIDEO_RENDERER=cloud
PODALUX_VIDEO_SUBMIT_URL=...
PODALUX_VIDEO_STATUS_URL_TEMPLATE=.../{job_id}
PODALUX_VIDEO_CANCEL_URL_TEMPLATE=.../{job_id}   # optionnel
PODALUX_VIDEO_API_TOKEN=...                       # secret runtime uniquement
PODALUX_VIDEO_JOB_TIMEOUT_S=2700
```

Aucune clé ou URL secrète ne doit être commitée.

## TTS

Le dépôt actuel dépend d'un serveur Chatterbox local (`127.0.0.1:4123`). Cette hypothèse ne doit pas être propagée au worker. Le worker doit recevoir un backend TTS configurable et, pour la première version, peut embarquer Chatterbox dans le même conteneur que Remotion/FFmpeg si les benchmarks le justifient.

## Pourquoi le worker est encore un squelette

Nous n'avons pas accès au poste local depuis GitHub : impossible de vérifier ici l'installation Pinokio/Wan2GP/MiniMax H3, le serveur Chatterbox réel, la présence de Chromium/FFmpeg côté Windows, ni les performances GPU. Copier ou simuler cette intégration serait trompeur.

La prochaine étape locale doit donc être une validation reproductible de l'image worker, pas une modification aveugle du runtime agents.

## Migration prévue

1. Construire l'image Linux avec les versions exactes de Node, Remotion, Chromium et FFmpeg.
2. Porter le générateur audio vers un endpoint TTS configurable.
3. Exécuter le pipeline FORGE existant dans le worker sans le réécrire.
4. Publier `final.mp4`, `qc_metrics.json`, `frames/`, `publish.json` et `manifest.json` dans object storage.
5. Brancher `FORGE` sur `CloudVideoRenderer` derrière un feature flag.
6. Tester submit/retry/duplicate/cancel/timeout/worker crash.
7. Seulement après E2E vert, faire du cloud le chemin par défaut.

## Ce que cette branche ne fait pas

Elle ne prétend pas intégrer Wan2GP/MiniMax H3 : ces informations sont locales au PC et doivent être inspectées sur cette machine. Elle ne remplace pas non plus le moteur Remotion actuel ni MoneyPrinterTurbo.
