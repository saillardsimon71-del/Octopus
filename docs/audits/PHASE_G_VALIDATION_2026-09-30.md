# Phase G — preuves structurées de validation

- **HEAD validé** : `8d346777462a1f64844c36ccb97ccdf8a3e0299a`
- **Branche** : `arena/01a0f109-octopus` (base `61fa0b2` de `handoff/octopus-phase-g-20260930`)
- **Arbre** : propre au moment des mesures (`git status --porcelain` vide)
- **Date** : 2026-09-30
- **Environnement** : sandbox Arena, `Linux e2b.local 6.1.158+ x86_64`, Python 3.11.2, pytest 9.1.1,
  venv isolé, aucun réseau utilisé par les tests, aucune ressource payante, aucun compte réel.
- **Verdict** : **PARTIAL — preuve manquante : critère I (constructeur Astra)**, non exécutable ici
  et hors périmètre de cette session. Critères A (hors constructeur/GUI), B, C, D, E, F, G, H
  démontrés.

## Commandes exactes

```bash
python -m pytest -q -o addopts='' -p no:cacheprovider \
  --ignore=tests/test_gui.py \
  --ignore=tests/test_astra_constructor.py \
  --ignore=tests/test_astra_thin_harness.py
```

Résultat : **1382 passed, 9 skipped in 72.77s**, code de retour 0.

## Preuves par critère

| Critère | Résultat | Preuve exécutée |
| --- | --- | --- |
| A Dépôt | PARTIEL | Suite produit 1382 passés / 9 ignorés / 0 échec. Voir « Hors périmètre » pour le constructeur et la GUI. |
| B Routage LLM | 74 passés | `tests/test_gateway.py`, `tests/test_provider_cooldown.py`, `tests/test_omniroute.py` |
| C Exécution bornée | 79 passés | `tests/test_bounded_execution.py`, `tests/test_runtime_react.py` |
| D + E + F + G | 91 passés | `tests/test_autonomous_loop.py`, `tests/test_tasks_worker.py` |
| H Observabilité | 6 passés | `tests/test_runtime_status.py` |
| État épistémique | 67 passés | `tests/test_strategy.py`, `tests/test_journal.py`, `tests/test_second_business_loop.py` |

Ces groupes se recouvrent partiellement avec la suite complète ; leurs nombres ne s'additionnent pas.

### A — Dépôt

- Arbre propre, diff relu, `git diff --check` sans erreur, `python -m compileall -q octopus agents` OK.
- Les 9 tests ignorés sont environnementaux, pas des régressions masquées :
  8 × `tests/test_browser_integration.py` (Chromium Playwright absent) et
  1 × `tests/test_gui_smoke.py` (`tkinter` absent).
- Aucun test valide supprimé ni relaxé par ce changement.

### B — Routage LLM (hors ligne)

- 413 déterministe classé à part et journalisé une seule fois :
  `test_gateway.py::test_http_413_skips_same_provider_for_same_prompt_across_calls`
  (`transport.models.count("groq/openai/gpt-oss-120b") == 1`, `auto/best-free` jamais appelé,
  une seule ligne `status='request_too_large'`, y compris après vidage des cooldowns en mémoire).
- Repli vers une route compatible : la même assertion montre `kilo/auto-free` prendre le relais.
- Transitoire ≠ incompatibilité déterministe : `test_provider_cooldown.py`
  (`test_429_stays_model_level_and_second_omniroute_route_can_run`,
  `test_provider_recovers_after_cooldown_expiry`,
  `test_structured_output_error_does_not_set_provider_cooldown`,
  `test_auth_error_does_not_set_provider_cooldown`, `test_connection_classifier_is_conservative`).
- Comptabilité : `test_gateway.py::test_transport_error_is_journaled_and_raised`
  (`cost_usd == 0` sur appel échoué) et `test_zero_cost_without_evidence_never_pays`.

### C — Exécution bornée (hors ligne)

- Bornes de timeout réellement appliquées au client, pas seulement déclarées :
  `test_bounded_execution.py::test_transport_applies_the_configured_timeout_to_the_client`
  (`OpenAI(timeout=37, max_retries=1)`), `test_every_provider_declares_a_finite_timeout_and_bounded_retries`,
  `test_health_probe_is_bounded_too`.
- Durée murale opposable : `test_runtime_react.py::test_mission_duration_stops_before_next_subtask_and_keeps_degraded_result`,
  `test_mission_duration_stops_before_business_signal_replan`,
  `test_mission_duration_does_not_validate_late_synthesis`,
  `test_mission_rejects_invalid_duration_before_planning`.
- Plus de mission indéfiniment `running` : `test_autonomous_loop.py::test_killed_worker_leaves_no_running_task_or_run_behind`
  (état exact d'un SIGKILL : les runs passent à `abandoned`, événement `run.abandoned`, idempotent).
  Le défaut avait été reproduit avec un vrai `SIGKILL` sur un worker enfant avant correction :
  `runs` restaient `status='running'`, `finished_at IS NULL`.
- Cohérence après annulation/timeout et politique de reprise explicite :
  `test_tasks_worker.py` (`test_expired_lease_is_retried_then_failed`, `test_lost_lease_prevents_stale_completion`,
  `test_expired_completion_is_not_journaled_as_success`, `test_cooperative_cancellation`,
  `test_reclaimed_task_rejects_stale_checkpoint_and_run`).

### D — Frontière humaine (hors ligne)

`test_tasks_worker.py::test_idle_worker_wakes_for_task_and_human_answer_without_repeating_step` :
étape persistée → attente → réponse → reprise → étape non rejouée.
Et dans la boucle autonome :
`test_autonomous_loop.py::test_autonomous_loop_requests_human_boundary_then_resumes_without_repeating_work`
(une seule mission par tâche de travail, `len(calls) == 2` pour deux tâches distinctes,
demande humaine non dupliquée par le tick suivant).

### E — Durabilité du worker (hors ligne)

`test_autonomous_loop.py::test_single_worker_start_claims_objective_work_while_idle` :
un seul démarrage, file vide, worker vivant au repos, objectif créé ensuite, exécution automatique,
retour au repos. Aucune seconde commande.

### F — Boucle autonome (obligatoire, hors ligne, fausses missions)

`tests/test_autonomous_loop.py`, un seul `worker.loop` en thread, aucune commande CLI entre les
transitions :

- `test_autonomous_loop_reaches_objective_without_any_manual_command` : objectif atteint,
  une seule mission, décision `satisfied` approuvée, preuve `computed` (`value == 1`),
  citations persistées, tick suivant déjà en file.
- `test_autonomous_loop_creates_next_work_when_result_is_inconclusive` : prochaine tâche créée
  automatiquement, objectif toujours actif.
- `test_autonomous_loop_pauses_objective_after_exhausted_attempts` : budget de tentatives borné,
  objectif `paused`.
- `test_autonomous_loop_requests_human_boundary_then_resumes_without_repeating_work` : frontière
  humaine réelle puis reprise.
- `test_autonomous_loop_never_declares_success_without_a_measurement` : sans critère mesurable,
  `success is None`, `measured is False`, aucune preuve créée, objectif jamais `achieved`.
- `test_supervisor_bootstrap_is_idempotent`, `test_supervisor_bootstrap_rejects_invalid_bounds`,
  `test_criterion_parsing_only_accepts_measurable_metrics`.
- `test_objective_work_initializes_shared_agent_state_before_a_mission` : régression du point
  d'entrée réel. Vérifié qu'elle **échoue** sans le correctif
  (`OperationalError: no such table: state`, tâche remise en file) et **passe** avec.

### G — Redémarrage / reprise

- `test_autonomous_loop.py::test_runtime_restart_recovers_pending_work_without_repeating_completed_steps` :
  panne après une étape mémoïsée → nouvelle tentative → worker neuf → `steps == [1]`,
  `len(missions) == 1`, tâche `done`.
- `test_autonomous_loop.py::test_killed_worker_leaves_no_running_task_or_run_behind`.
- `test_tasks_worker.py::test_memo_preserves_falsey_results_across_restart`.

### H — Observabilité

`tests/test_runtime_status.py` : chaque fait exigé par le contrat est présent dans
`octopus.status.snapshot` et rendu par la CLI — objectif courant, tâche/mission courante, run et
sous-runs, dernière progression, raison d'attente humaine, raison d'échec/timeout, travail suivant
planifié, routage LLM (refus déterministes 413 et replis observés), coût cumulé. Sortie `--json`
exploitable, runtime au repos rendu sans erreur, `runtime` échoue en code 2 si les handlers du
superviseur sont absents.

Deux défauts réels ont été trouvés par ces tests pendant leur écriture et corrigés :
`splitlines()[0]` sur un message d'erreur vide (`IndexError`) et `current_run_id` qui pointait sur
un sous-run plutôt que sur la racine de l'exécution.

## Entrée runtime unique

```bash
python -m octopus runtime
```

Amorce le superviseur de façon idempotente puis lance la boucle du worker existant. Le tick se
réarme lui-même : aucune commande n'est nécessaire entre objectif, mission, preuve, évaluation et
tâche suivante. `python -m octopus status` pour l'état, `ask` / `answer` pour les frontières humaines.

## Démonstration réelle du point d'entrée sur ce HEAD

Exécutée avec les vraies commandes CLI, sans LLM disponible, sans réseau et sans dépense :

```text
$ octopus strategy add objective octopus "Premier client" --by human \
    --set statement="Identifier une demande ouverte et qualifiable" \
    --set success_criteria="usable_browse_count>=2"
objective #1 créé (octopus)
$ octopus strategy move objective 1 octopus active --by human
objective #1 -> active

$ timeout 25 python -m octopus runtime --tick-every 3 --poll 0.5
[runtime] superviseur amorcé : tâche #1 (supervisor.tick) ; tick toutes les 3 s
[worker] #1 supervisor.tick (tentative 1/1)      -> done
[worker] #2 supervisor.objective_work (1/2)      -> done_degraded
[worker] #3 supervisor.tick en attente de la demande humaine #1 -> waiting_human

$ octopus ask
#1 (tâche #3, octopus) Objectif #1 (Premier client) : aucune route LLM gratuite disponible
(quota/429) ; aucun fallback payant autorisé ...

$ octopus answer 1 "aucune route LLM gratuite dans cet environnement"
réponse enregistrée ; tâche #3 remise en file

$ timeout 15 python -m octopus runtime --tick-every 3 --poll 0.5
[worker] #3 supervisor.tick -> done                 (reprise après réponse)
[worker] #6 supervisor.objective_work -> done_degraded
[worker] #5 supervisor.tick -> waiting_human        (frontière toujours réelle)

$ octopus status
Objectifs :            #1 [active] octopus — Identifier une demande ouverte et qualifiable
Tâches en cours :      #5 [waiting_human] octopus/supervisor.tick | run #9
Attente humaine :      demande #2 (tâche #5 supervisor.tick) ... aucune route LLM gratuite ...
Échecs / timeouts :    aucun
Coût cumulé :          aujourd'hui 0.0 USD | runs actifs 0.0 USD | total 0.0 USD
file de tâches         done 4, done_degraded 2, waiting_human 1
```

Ce que cette démonstration prouve : le démarrage unique, la création autonome de travail, la
persistance du résultat, l'évaluation, l'escalade vers une frontière humaine genuine, la reprise
après réponse, et l'absence de fallback payant. Ce qu'elle ne prouve pas : la disponibilité réelle
d'un fournisseur LLM, une acquisition réelle, ni un quelconque résultat économique.

## Hors périmètre / non démontré ici

- **Critère I (constructeur Astra)** : `tests/test_astra_constructor.py` et
  `tests/test_astra_thin_harness.py` → 80 échecs, 1 passé. Classification exhaustive des échecs :
  79 × `FileNotFoundError: 'powershell'`, 1 × `FileNotFoundError: 'docker'`. Aucun échec logique.
  Ces binaires sont absents de la sandbox et le constructeur est explicitement hors périmètre de
  cette session ; il n'a pas été modifié.
- **GUI** : `tests/test_gui.py` n'est pas collectable (`ModuleNotFoundError: No module named
  'tkinter'`), `tests/test_gui_smoke.py` ignoré pour la même raison. La GUI est exclue de la mission.
- Ces deux limites sont environnementales et documentées, pas des régressions masquées.

## Limites résiduelles

- La durée murale d'une mission est coopérative : un appel fournisseur déjà en vol conserve son
  propre timeout de transport.
- Une demande humaine qui expire sans réponse met la tâche de tick en échec et arrête la chaîne du
  superviseur ; `python -m octopus runtime` la réamorce. Aucun réarmement automatique n'a été ajouté
  pour ne pas rejouer indéfiniment une erreur déterministe.
- Chaque tick laisse une tâche durable dans le journal (~288 lignes/jour au pas par défaut de 300 s).
- Seule `usable_browse_count` est mesurable sans LLM ; un objectif sans critère mesurable finit
  suspendu après épuisement du budget de tentatives.
- Aucune preuve économique : la boucle prouve l'exécution supervisée, pas un résultat commercial.
