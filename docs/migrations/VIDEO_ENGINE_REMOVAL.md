# Suppression de l'ancien moteur vidéo

Date: 2026-09-25

Objectif: retirer le moteur vidéo Podalux/Remotion/RunPod/TTS/B-roll devenu hors cible, puis le remplacer par l'atelier Agnes autonome décrit dans `AGNES_VIDEO_REPLACEMENT.md`.

## Principe

La suppression doit être une chirurgie, pas une réécriture d'OCTOPUS.

Le moteur OCTOPUS (journal, worker, économie, agents, stratégie, capacités) reste.

## Points d'accrochage noyau identifiés

1. `octopus/businesses.py`
   - `ENGINE_HANDLERS = ("octopus.builtin_handlers", "octopus.media.handlers")`
   - supprimer `octopus.media.handlers` du moteur par défaut.

2. `agents/task_handlers.py`
   - supprimer le handler `podalux.video_cycle`;
   - conserver les handlers agent/mission et H3;
   - mettre à jour le docstring historique.

3. `octopus/__main__.py`
   - supprimer l'import `from .media import cli as media_cli`;
   - supprimer la commande CLI `video`.

4. `octopus/worker.py`
   - exemple/docstring `podalux.video_cycle` à remplacer par un exemple neutre.

5. `octopus/businesses.py::overview`
   - le champ `media_generations` du journal peut rester temporairement pour compatibilité historique;
   - ne pas le confondre avec le nouvel atelier Agnes, qui est une application autonome.

## Arbre à supprimer après découplage

### Ancien moteur vidéo
- `octopus/video/`
- `octopus/media/`
- `video_worker/`
- `remotion/`

### Outils vidéo historiques
- `tools/render_short.py`
- `tools/fetch_broll.py`
- `tools/tts_providers.py`
- `tools/bench_voices.py`
- `tools/make_audio.py`
- `tools/make_audio_chatterbox.py`
- `tools/make_audio_chatterbox_full.py`
- `tools/make_ref_voice.py`
- `tools/qc_metrics.py`
- `tools/qc_vision.py`
- `tools/word_sync.py`

### Workflows vidéo historiques
- `.github/workflows/video-batch.yml`
- `.github/workflows/video-render-e2e.yml`

`.github/workflows/video-foundation.yml` ne doit PAS être supprimé mécaniquement tant qu'il porte encore des tests non vidéo. Il devra être renommé/recomposé après audit.

### Tests à supprimer ou remplacer
- `tests/test_video_cloud.py`
- `tests/test_video_cloud_client.py`
- `tests/test_video_contract.py`
- `tests/test_video_executor.py`
- `tests/test_video_service.py`
- `tests/test_video_state.py`
- `tests/test_video_storage.py`
- `tests/test_broll.py`
- `tests/test_tts_providers.py`
- `tests/test_minimax_h3_cloud.py`
- `tests/test_wangp_mcp.py` si uniquement lié au moteur vidéo après vérification.

## Jobs/offres

Les fichiers `jobs/cash_*.json` ne sont pas automatiquement des fichiers vidéo.
Ne pas les supprimer sans vérifier s'ils servent encore d'offres/expériences économiques.

## Séquence de suppression

1. Débrancher le noyau des modules vidéo.
2. Lancer tests.
3. Supprimer modules/worker/remotion/outils historiques.
4. Réparer imports morts.
5. Lancer suite complète.
6. Rechercher les mots-clés:
   - `octopus.media`
   - `octopus.video`
   - `video_worker`
   - `remotion`
   - `podalux.video_cycle`
   - `render_short`
7. Zéro référence runtime résiduelle.
8. Conserver git history comme archive; pas de dossier legacy mort dans main.

## Critère de réussite

Après suppression:
- OCTOPUS démarre;
- worker charge ses handlers;
- H3 fonctionne;
- stratégie/économie/journal fonctionnent;
- aucune dépendance Node/Remotion/TTS/RunPod n'est requise pour la suite Python générale;
- aucun test non vidéo ne casse;
- l'atelier Agnes vit séparément dans `apps/agnes-video/index.html`.
