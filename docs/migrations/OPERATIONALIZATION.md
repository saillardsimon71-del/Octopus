# Phase G - Operationalization

## Objective

Make OCTOPUS operational through clean, stable runtime entry points. Runtime use must not depend on constructor phases or manual PowerShell choreography.

## Execution

1. Trace the existing runtime entry points and reproduce each operational blocker before changing code.
2. Reuse existing runtime boundaries and canonical state. Add no parallel runtime, workflow engine, ledger or journal.
3. Fix only demonstrated blockers with the smallest tested change.
4. Verify the usable runtime path while preserving permissions, economy and journal guarantees.
5. Report the stable entry points, tests run and any remaining human boundary.

## Limits

Do not build the GUI, connect real accounts, contact third parties, spend money or perform irreversible external actions. Do not use constructor phases as production runtime dependencies.


## Autonomous runtime evidence - task 79
Task 79 completed in 1963.9 s (root run 341). During agent.react_step, omniroute/devworker-groq and omniroute/auto-free repeatedly returned HTTP 413 Request too large before kilo/auto-free succeeded. The mission ended without a qualified actionable opportunity and created human request task 80. Treat these as observed symptoms, not a prescribed fix. Investigate the autonomous runtime end-to-end and improve sustained unattended operation, routing, bounded execution, continuation, recovery, progress visibility, and human escalation while preserving permissions, evidence gates, and external-action boundaries. Astra may choose the implementation, decomposition, tests and Step tickets.

## Stabilization check - 2026-09-28

The gateway now records HTTP 413 separately and avoids sending the same prompt to that provider again for 24 hours, including after a process restart. The Phase G host packet includes the runtime observation above. Mission execution has a configurable 900 second cooperative limit and reports a degraded timeout with raw subtask results. An offline worker test covers idle polling, human wait, answer, and reuse of a saved step in one process.

Limits: the mission duration is checked between calls and tools; an in-flight provider call retains its own transport timeout. A timed out mission preserves completed raw results but does not resume unfinished subtasks automatically. The existing worker processes durable tasks and schedules, but this change does not establish an unattended persistent-objective planning loop or prove live provider behavior. Tests do not establish any economic result.
# OCTOPUS — FULL STABILIZATION MANDATE

## Mission

Stabiliser OCTOPUS comme système autonome supervisé réellement exploitable.

Ne pas effectuer un correctif ponctuel.
Ne pas optimiser une seule anomalie isolée.
Ne pas considérer l’absence de startup blocker comme une preuve de stabilité.

Partir de l’état réel du dépôt, de ses tests, de ses journaux, de ses observations runtime et de ses limitations connues.

Astra est libre de déterminer :
- l’ordre des investigations ;
- les causes racines ;
- les fichiers à modifier ;
- les tickets Step nécessaires ;
- les tests à ajouter ;
- les simplifications à effectuer ;
- les composants existants à conserver, modifier ou supprimer.

Préférer la suppression d’une fragilité structurelle à l’ajout d’un garde-fou supplémentaire.

MARKET FIRST. AUTOMATION SECOND. GENERALIZATION LAST.

## Final state required

Le comportement cible est :

persistent objective
→ autonomous planning
→ task / mission creation
→ execution
→ evidence and result
→ evaluation / decision
→ next work created automatically
→ repeat

Human intervention must occur only at a genuine boundary such as:
- authentication or account connection;
- explicit authorization to contact/send/publish;
- explicit authorization to spend economic funds;
- missing external resource or permission that cannot safely be created autonomously.

Normal operation must not require the human to repeatedly run:
- strategy mission;
- worker --once;
- manual continuation commands;
- constructor continuation commands;
- ad-hoc database inspection commands.

A single durable runtime start must be sufficient for normal autonomous supervised operation.

## Existing runtime evidence

Treat these as observations, not prescribed fixes.

1. Task 79 / root run 341 completed in approximately 1963.9 seconds.

2. During agent.react_step, repeated attempts were observed through:
- omniroute/devworker-groq
- omniroute/auto-free

Both repeatedly returned HTTP 413 Request too large before kilo/auto-free succeeded.

3. Task 79 ended inconclusive and created human request / task 80.

4. No external contact, publication or economic spend occurred.

5. A later stabilization branch reportedly introduced:
- deterministic 413 suppression;
- a cooperative mission duration bound of 900 seconds;
- task 79 evidence in Astra context;
- human-wait resume coverage.

Verify all of these against the actual repository. Do not assume they are correct merely because they were reported.

6. Known reported residual limitations include:
- the 900-second mission bound is cooperative and may not interrupt an already-running provider request;
- an expired mission may retain raw results without automatically resuming an unfinished subtask;
- `python -m octopus worker` executes existing queued/scheduled work but has not demonstrated a persistent objective → autonomous mission → evaluation → next mission loop.

These limitations are stabilization targets unless investigation demonstrates that a different design already solves them.

## Constructor stabilization requirements

The constructor itself must be reliable enough to finish this mission.

Verify and stabilize as necessary:

- Astra continuation and compact handoff;
- relay-cycle behavior;
- Step ticket publication;
- Step execution and test oracle;
- Astra review after Step;
- checkpoints;
- fail-closed behavior;
- token/quota bounds;
- preservation of runtime evidence across turns;
- avoidance of rediscovering identical source excerpts;
- avoidance of repeatedly declaring “no blocker established” while demonstrated runtime evidence remains unresolved;
- correct distinction between constructor problems and OCTOPUS product-runtime problems.

Astra must continue investigating while useful bounded work remains possible.

Do not stop merely with:
“next discovery should inspect X”.

If X can be inspected safely now, inspect it now.

## Runtime stabilization requirements

Investigate the complete critical path, including where relevant:

- persistent objectives;
- supervisory/autonomous loop;
- task creation;
- worker lifecycle;
- schedules;
- journal;
- nested runs;
- task steps / memoization;
- leases and heartbeat;
- cancellation;
- crash/restart recovery;
- human requests;
- human response resume;
- mission expiration;
- provider timeout behavior;
- LLM routing;
- deterministic provider failures;
- retries and fallback;
- context/model compatibility;
- LLM and economic budgets;
- progress visibility;
- dead/stalled run detection;
- objective → mission → result → decision → continuation;
- idle worker wake-up;
- persistent service/entrypoint behavior.

Do not create a second competing orchestration system if the existing mechanisms can be made coherent.

## Required acceptance tests

OCTOPUS must NOT be declared stable until evidence demonstrates all applicable criteria below.

### A. Repository

- working tree clean;
- complete offline test suite green;
- constructor tests green;
- runtime tests green;
- no known regression intentionally hidden by skipped tests.

### B. LLM routing

Offline simulation must prove:

- a deterministic HTTP 413 is classified appropriately;
- the same incompatible route is not retried uselessly for equivalent context within the same execution;
- fallback proceeds to a compatible route;
- temporary failures remain distinguishable from deterministic incompatibility;
- budget/accounting remains correct.

### C. Bounded execution

Offline simulation must prove:

- provider calls have meaningful timeout bounds;
- mission wall-clock limits are enforceable;
- cancellation/timeout leaves persistent state coherent;
- a mission cannot remain indefinitely `running` after its execution is no longer alive;
- unfinished work has an explicit recovery policy.

### D. Human boundary

Offline simulation must prove:

task begins
→ completed step is persisted
→ human resource/approval becomes necessary
→ task enters waiting state
→ human answer is supplied
→ task resumes
→ previously persisted work is not repeated
→ execution continues from the correct boundary.

### E. Worker durability

Offline test must prove:

worker starts once
→ queue is empty
→ worker remains healthy while idle
→ a task later becomes ready
→ worker claims and executes it automatically
→ worker returns to idle state.

No second worker command may be required.

### F. Autonomous objective loop

This is mandatory.

Provide an offline end-to-end scenario using fake tools/models and no network:

persistent objective exists
→ autonomous supervisor sees it
→ creates justified work
→ worker executes it
→ result/evidence is persisted
→ supervisor evaluates it
→ supervisor autonomously chooses one of:

1. create the next mission/task;
2. mark objective satisfied;
3. mark objective currently inconclusive and schedule justified future work;
4. request a genuine human boundary.

The test must require no manual CLI command between these transitions after initial startup.

### G. Restart/recovery

Demonstrate at least one restart scenario:

runtime stops after persistent work has begun
→ runtime restarts
→ existing state is recovered
→ completed steps are not unnecessarily repeated
→ valid pending work continues or is safely reclassified.

### H. Observability

At runtime it must be possible to determine, without manually reverse-engineering SQLite:

- current objective;
- current task/mission;
- current run/sub-run;
- last meaningful progress;
- waiting-human reason;
- failure/timeout reason;
- next planned work;
- LLM routing/fallback outcome;
- relevant accumulated compute cost.

Implementation is free, but the state must be observable.

### I. Constructor behavior

Using a synthetic runtime blocker, demonstrate:

Astra receives the observation
→ investigates it
→ continuation preserves prior findings
→ identical discovery is not repeatedly redone
→ blocker is reproduced or rejected using evidence
→ when code work is justified, a bounded Step ticket is produced
→ Step executes/tests
→ Astra reviews the result
→ stabilization continues or closes the blocker.

## Autonomous runtime entrypoint

At completion there must be ONE recommended command for supervised autonomous operation.

The command must start the durable runtime needed for normal operation.

It must not require the operator to subsequently create every mission manually or repeatedly invoke `worker --once`.

If existing architecture cannot yet provide this honestly, stabilization is not complete.

## Permissions

Do NOT weaken economic or external-action boundaries to satisfy autonomy tests.

No real:
- messages;
- emails;
- proposals;
- publications;
- purchases;
- transfers;
- paid APIs;
- account creation;
- account authentication;
- economic experiment;

may be performed during stabilization.

Use offline fakes, fixtures and simulations.

Existing READ / PREPARE / ACT semantics and proof gates must remain meaningful.

## Scope discipline

Do not redesign OCTOPUS merely for elegance.

Do not optimize hypothetical scale.

Do not build the GUI.

Do not add business verticals.

Do not solve future economic strategy.

Focus on reliable execution of the existing OCTOPUS concept.

## Stop conditions

The stabilization run may finish only when one of these is true:

### STABLE

All mandatory acceptance criteria above are demonstrated by executable tests and inspection.

Return:
- root causes found;
- fixes made;
- simplifications made;
- files changed;
- tests added;
- exact test results;
- commits;
- remaining non-critical limitations;
- ONE autonomous runtime start command.

### BLOCKED

A mandatory criterion cannot safely be completed with the available environment.

This requires concrete evidence.

Return:
- exact criterion blocked;
- exact technical reason;
- evidence;
- minimum missing capability/resource;
- whether human action is genuinely necessary.

Do not use BLOCKED merely because additional repository inspection would be required.

## Definition of success

The objective is not “the tests currently pass”.

The objective is:

OCTOPUS can be started once, continue useful bounded work from persistent objectives, survive normal waiting and failure conditions, preserve its state, request humans only at genuine boundaries, and continue afterwards without the operator manually driving its internal loop.

## Phase G closure - 2026-09-30

Périmètre de cette clôture : le runtime produit OCTOPUS uniquement. Le constructeur Astra est hors
périmètre de cette session et n'a pas été modifié ; le critère I est donc rapporté non démontré ici.

### Causes racines constatées

1. Aucun code ne transformait un objectif persistant en travail. `strategy` stockait les objectifs
   actifs et `tasks` savait les exécuter, mais rien ne reliait les deux : aucune commande `enqueue`
   ni `schedule` n'était atteignable depuis `strategy_objectives`. La boucle exigeait qu'un humain
   relance `strategy mission` puis `worker --once` (critère F, obligatoire, non implémenté).
2. Les runs du journal n'étaient jamais clos quand leur processus mourait. Reproduit avec un vrai
   SIGKILL : `tasks.reap()` reprenait ou échouait la tâche grâce au bail, mais laissait
   `runs.status='running'` et `finished_at IS NULL` indéfiniment. L'état observable contredisait la
   réalité (critère C).
3. Aucun état runtime n'était lisible sans interroger SQLite. La CLI exposait `tasks`, `events`,
   `ask` et `businesses`, mais rien qui assemble objectif, tâche/run courant, raison d'attente,
   raison d'échec, travail suivant, routage LLM et coût (critère H).
4. Blocage découvert en exécutant le point d'entrée réel : `supervisor.objective_work` échouait en
   `OperationalError: no such table: state` sur un `OCTOPUS_HOME` neuf, car le runtime de mission
   exige l'état partagé des agents initialisé. Corrigé en passant par le pont existant des handlers
   de mission (`agents.task_handlers._run`).
   `tests/test_autonomous_loop.py::test_objective_work_initializes_shared_agent_state_before_a_mission`
   échoue sans le correctif (`no such table: state`) et passe avec.

### Corrections apportées (sans refonte)

- `octopus/supervisor.py` (nouveau) : supervision déterministe de l'état canonique existant.
  Objectif actif -> tâche `supervisor.objective_work` -> résultat mesuré -> décision persistée
  (`satisfied`, `retry`, `human_boundary`, `exhausted`). Aucun second journal, ordonnanceur ni
  planificateur : `strategy` pour l'état épistémique, `tasks` pour la file durable et les demandes
  humaines, `journal` pour les runs et les coûts.
- `octopus/builtin_handlers.py` : handlers `supervisor.tick` et `supervisor.objective_work`, donc
  exécutés par le worker existant avec ses baux, son heartbeat, son budget et sa reprise.
- `octopus/tasks.py` : `reap()` clos désormais les runs orphelins (`status='abandoned'`,
  événement `run.abandoned`) en même temps que le bail expiré de leur tâche. Idempotent.
- `octopus/status.py` (nouveau) + `python -m octopus status [--business X] [--json]`.
- `python -m octopus runtime` : amorçage du superviseur puis boucle du worker existant.

### Une seule commande de démarrage autonome

```bash
python -m octopus runtime
```

Elle charge les handlers, garantit qu'un tick superviseur est en file (idempotent) et démarre la
boucle durable. Ensuite aucune commande n'est nécessaire entre objectif, mission, preuve,
évaluation et tâche suivante : le tick se réarme lui-même. `--once` sert au diagnostic.
L'état se lit avec `python -m octopus status`, les frontières humaines avec `python -m octopus ask`
et `python -m octopus answer`.

### Critères A-I sur cette clôture

| Critère | État | Preuve |
| --- | --- | --- |
| A Dépôt | PARTIEL | Suite produit 1382 passés, 9 ignorés, 0 échec. Les 80 tests du constructeur Astra échouent faute de `powershell`/`docker` dans cet environnement ; `tests/test_gui.py` n'est pas collectable faute de `tkinter`. Aucune régression masquée par un test ignoré : les 9 ignores sont Chromium et tkinter absents. |
| B Routage LLM | DEMONTRE | `tests/test_gateway.py::test_http_413_skips_same_provider_for_same_prompt_across_calls`, `tests/test_provider_cooldown.py` (429 au niveau modèle, reprise après cooldown, erreur de structure ou d'auth sans cooldown fournisseur), coût nul sur appel échoué. |
| C Exécution bornée | DEMONTRE | `tests/test_bounded_execution.py` (borne de timeout réellement appliquée au client, sonde bornée), `tests/test_runtime_react.py` (durée murale opposable), `tests/test_autonomous_loop.py::test_killed_worker_leaves_no_running_task_or_run_behind` (plus de run `running` orphelin), baux et annulation dans `tests/test_tasks_worker.py`. |
| D Frontière humaine | DEMONTRE | `tests/test_tasks_worker.py::test_idle_worker_wakes_for_task_and_human_answer_without_repeating_step` et `tests/test_autonomous_loop.py::test_autonomous_loop_requests_human_boundary_then_resumes_without_repeating_work`. |
| E Durabilité worker | DEMONTRE | `tests/test_autonomous_loop.py::test_single_worker_start_claims_objective_work_while_idle` : un seul démarrage, repos sain, tâche exécutée automatiquement, retour au repos. |
| F Boucle autonome | DEMONTRE | `tests/test_autonomous_loop.py` : objectif atteint, nouvelle tâche créée si non concluant, suspension après épuisement, frontière humaine, aucune commande entre les transitions, fausses missions hors ligne. |
| G Redémarrage | DEMONTRE | `tests/test_autonomous_loop.py::test_runtime_restart_recovers_pending_work_without_repeating_completed_steps` et `tests/test_tasks_worker.py::test_memo_preserves_falsey_results_across_restart`. |
| H Observabilité | DEMONTRE | `tests/test_runtime_status.py` : chaque fait exigé est présent dans `octopus.status` et rendu par la CLI. |
| I Constructeur | NON DEMONTRE | Astra hors périmètre de cette session ; `powershell` et `docker` absents de cet environnement. |

### Limites résiduelles non critiques

- La durée murale d'une mission reste coopérative : un appel fournisseur déjà en vol garde son
  propre timeout de transport.
- Si une demande humaine expire sans réponse, la tâche de tick échoue et la chaîne du superviseur
  s'arrête ; `python -m octopus runtime` la réamorce. Aucun réarmement automatique n'a été ajouté
  pour ne pas rejouer indéfiniment une erreur déterministe.
- Chaque tick laisse une tâche durable dans le journal : au pas par défaut de 300 s, environ
  288 lignes par jour. C'est le coût d'un superviseur durable et auditable.
- Un objectif sans critère mesurable (`usable_browse_count>=N`) n'est jamais déclaré atteint :
  il finit suspendu après épuisement du budget de tentatives. C'est voulu.
- La disponibilité réelle d'un fournisseur LLM gratuit n'est pas prouvée par ces tests. Dans cette
  sandbox le runtime réel s'arrête correctement sur une frontière humaine, sans fallback payant.
- Aucune preuve économique : la boucle prouve l'exécution supervisée, pas un résultat commercial.
