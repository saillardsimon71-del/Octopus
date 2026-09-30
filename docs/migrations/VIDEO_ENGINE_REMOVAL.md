# Suppression de l'ancien moteur vidéo

Date: 2026-09-25

Objectif: retirer le moteur vidéo Podalux/Remotion/RunPod/TTS/B-roll devenu hors cible, puis le remplacer par une frontière mince vers le moteur externe pinné `lcy362/agnes-video-generator`, décrite dans `AGNES_VIDEO_REPLACEMENT.md`.

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
   - ne pas le confondre avec le moteur Agnes externe pinné, qui vit comme service indépendant derrière un adapter OCTOPUS minimal.

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
- le moteur Agnes vit séparément comme service upstream pinné; OCTOPUS ne conserve qu'un adapter minimal.

## État de la phase B — 2026-09-25

Suppression implémentée dans l'arbre de travail de la fenêtre Astra :
- moteur, worker, outils et workflows exclusivement vidéo retirés ;
- CLI vidéo/cycle/batch, handler de cycle, outils agent render_offer/qc et
  commandes de lancement Cycle/Studio retirés ;
- déclaration studio conservée sans handler pour l'identité des données historiques ;
- workflow python-foundation recomposé : tests de contrôle, H3 agent/mission,
  sandbox et evidence gate conservés, dépendances vidéo supprimées ;
- diagnostic et setup local découplés des credentials et dépendances vidéo.

Les consommateurs constatés élargissent la liste de suppression préparée :
agents/cycle.py, agents/gui/studio.py, classe FORGE de rendu et tests exclusivement
liés aux modules retirés. Les tests mixtes gardent les assertions sur les offres,
les validateurs, le coût, les sous-processus, les verrous, l'annulation et le journal.
Le test d'annulation du handler utilise désormais podalux.agent_message.

Conservés : jobs/cash_*.json, rôles du runtime agent, évaluations et métadonnées des
offres historiques, lecture des artefacts existants, publication dry-run, ledger,
stratégie et primitives compute partagées. Le schéma media_generations et sa
réconciliation dans tasks restent inchangés ; les tests utilisent des lignes SQL
historiques sans importer le moteur retiré. Les anciennes tâches/schedules vidéo
persistés ne sont ni migrés ni réactivés automatiquement.

Aucun adapter Agnes n'est encore intégré à cette phase. Aucun rendu réel, appel
payant, promotion ni fusion vers main n'a été lancé. Les caches/fichiers générés
ignorés et les artefacts utilisateur ne sont pas supprimés ; Git conserve l'archive
du moteur. Les anciens snapshots documentaires restent des références historiques.

Validation finale : voir le résultat consigné ci-dessous avant checkpoint.

### Validation exécutée dans le sandbox Astra

- Tests ciblés finaux : 84 passent, 0 échec
  (cache/astra-relay/phase-b-targeted.xml).
- Suite Python complète exécutée une seule fois après les changements Python :
  1 126 passent, 31 échouent, 8 ignorés, 0 erreur de collection (154 s).
  Logs : cache/astra-relay/phase-b-full.log et phase-b-full.xml.
- Les 30 échecs workshop/Git concernent dev_worker, night_shift et promotion :
  les clones de dépôts temporaires sont bloqués par le helper Git-for-Windows
  (sh.exe, couldn't create signal pipe, Win32 error 5), avec échecs en cascade
  des assertions qui attendent un clone ou un résultat de worker.
- Le test test_subprocess_tree_is_killed_on_stop échoue aussi isolément :
  taskkill retourne exit 1, « Accès refusé », puis le délai atteint environ 22 s
  au lieu des moins de 15 s attendues. Diagnostic capturé dans
  cache/astra-relay/phase-b-cancellation-diagnostic.log. Le code de kill_tree
  et l'assertion temporelle n'ont pas été modifiés.
- Scan des six références runtime préparées : aucune référence résiduelle
  dans agents, octopus, businesses, ops, scripts, tools et les workflows.
  Les références de tests négatifs, d'ignore et de documents historiques restent.
- Diff relu et git diff --check sans erreur. YAML des deux jobs du workflow
  python-foundation validé ; syntaxe PowerShell du setup validée sans exécution
  (encodage UTF-8 avec BOM pour Windows PowerShell).

La suite n'est donc pas annoncée verte dans ce sandbox. Prochaine observation
requise : résultat de la suite sur ce diff exact par le superviseur hors sandbox,
notamment clones Git et annulation de l'arbre de processus. Ne pas contourner
ces limites ni assouplir les tests ; ne pas confondre ce checkpoint avec une
acceptation produit ou une preuve économique.
