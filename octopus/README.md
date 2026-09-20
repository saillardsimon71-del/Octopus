# OCTOPUS : noyau commun des activités

Version 0.2. Quatre briques, utilisées par Podalux sans changer ses signatures :

| Brique | Fichier | Rôle |
|---|---|---|
| Journal | `journal.py` | SQLite `data/octopus.db` : runs imbriqués (cycle > agent > outil), appels LLM, résultats du banc |
| Passerelle LLM | `llm.py`, `catalog.py`, `pricing.py` | un seul point d'entrée : choix du modèle par profil, budget vérifié **avant** l'appel, coût à la grille officielle (heures pleines, cache), justification de chaque appel payant |
| Banc | `bench.py`, `agents/evals.py` | compare code, modèles locaux, gratuits et payants sur les vraies tâches, avec des vérifications déterministes |
| File de tâches | `tasks.py`, `worker.py` | tâches persistées, ressources exclusives, baux, reprises, annulation, demandes humaines, planifications |

## Profils (`OCTOPUS_PROFILE`)

| Profil | Comportement |
|---|---|
| `legacy` (défaut) | modèle imposé par le code, requêtes identiques à l'historique (vérifié par `tests/test_legacy_compat.py`) |
| `zero_cost` | local et quotas gratuits seulement ; un modèle n'est utilisé que s'il a réussi le banc (5 essais, 90 %, moins de 60 jours) |
| `low_cost` | local et gratuit validés d'abord, payant en dernier recours, dans le budget |
| `quality_first` | meilleur modèle validé d'abord, repli sur un modèle gratuit si le budget bloque |
| `bench` | réservé au banc |

Coupe-circuit : l'appel direct historique exige simultanément `OCTOPUS=off` et `OCTOPUS_ALLOW_LEGACY_DIRECT=1` (ni journal, ni passerelle). `OCTOPUS=off` seul refuse l'appel LLM direct.

## Budgets

- Par run : `run_cycle`, `run_agent` et `run_mission` ouvrent un run de 1 $ (`config.CYCLE_BUDGET_USD`). Le budget d'un run inclut ses sous-runs. Il remplace le coût cumulé à vie de l'ancien code (audit C3).
- Par jour : `budgets.daily_usd` dans `config/catalog.json` (2 $).
- L'estimation avant appel est une borne haute (sortie maximale, aucun cache). Un appel bloqué est journalisé avec le statut `blocked`.

## Commandes

```
python -m octopus doctor                     installation, fournisseurs joignables, clés présentes
python -m octopus models                     catalogue et preuves du banc
python -m octopus report --days 7 --legacy-db agents/data/podalux.db
python -m octopus bench --models code,ollama/qwen3.5-2b [--tasks podalux.arbitrate]
python -m octopus bench --models deepseek/flash --allow-paid --max-cost 0.05
python -m pytest                             tests hors-ligne (aucun appel réseau)
```

Le banc refuse les modèles payants sans `--allow-paid` et s'arrête au plafond `--max-cost`.

## File de tâches et worker (M2)

Tables `tasks`, `events`, `human_requests`, `schedules` dans `data/octopus.db` (schéma v2, migration automatique).

| Élément | Comportement |
|---|---|
| Prise de tâche | transaction `BEGIN IMMEDIATE` : un seul worker par tâche ; priorité puis ancienneté ; `not_before` pour les délais |
| Ressources | une tâche `cpu_heavy` (TTS, rendu) à la fois ; les tâches sans ressource passent à côté |
| Bail | identifiant unique par exécution du worker, renouvelé avant expiration ; un bail expiré ne peut plus écrire ni être renouvelé ; reprise s'il reste des tentatives, sinon échec |
| Échec | nouvelle tentative différée (`max_attempts`, `retry_delay_s` du handler) |
| Annulation | immédiate en file ; coopérative en cours (`ctx.check_cancel()`, relayée à l'arrêt Podalux) |
| Humain | `ctx.ask_human(clé, question)` met la tâche en attente ; `python -m octopus answer ID "texte"` la remet en file, le handler est rejoué et retrouve la réponse |
| Planification | `python -m octopus schedule ...` ; une occurrence encore active n'est pas empilée |
| Idempotence | `idempotency_key` unique (ex. `publish:<offre>:<sha256>`) |
| Coûts | chaque tâche tourne dans un run du journal, avec le budget déclaré par son handler |

Handlers chargés : `octopus.builtin_handlers` (`octopus.cost_report`), `agents.task_handlers` (`podalux.video_cycle`, `podalux.agent_message`, `podalux.mission`) et `businesses.veille.handlers` (`veille.brief`, voir `businesses/veille/README.md`), surchargeables par `OCTOPUS_HANDLERS`. Une tâche rejouée après une réponse humaine retrouve ses étapes coûteuses via `ctx.memo`. Aucune planification n'est créée d'office : un cycle vidéo planifié consomme du CPU et des appels payants, c'est à décider.

```
python -m octopus worker                      boucle (Ctrl+C pour arrêter)
python -m octopus enqueue podalux podalux.video_cycle --input "{\"offer_id\": \"cash_devis_cgv01\"}"
python -m octopus tasks / events / ask / answer 3 "oui" / cancel 12
python -m octopus schedule octopus octopus.cost_report --every 86400
```

### Garanties de reprise

Le worker vérifie son bail lors des transitions, du rattachement du run et de l'enregistrement des étapes. Une ancienne exécution ne peut pas écraser le résultat de sa remplaçante, même si les deux workers portent le même nom. Un échec de validation du bail à la fin du handler clôt le run en erreur, pas en succès.

`ctx.memo` conserve aussi les résultats `None`, `False` ou vides et contrôle le bail avant de démarrer le calcul. Les appels bas niveau à `save_step` et `set_run` acceptent `owner=` pour ce contrôle transactionnel ; leur signature historique sans propriétaire reste disponible, sans cette protection.

Ces garanties concernent la persistance OCTOPUS : un bail ne tue pas un processus ni un outil externe. Un crash entre un effet externe et son checkpoint peut encore rejouer cet effet ; les handlers doivent rester coopératifs et utiliser l'idempotence de l'outil lorsqu'elle existe.

## Ajouter un fournisseur ou un modèle

1. `providers` : `base_url` compatible OpenAI, `api_key_env` (jamais la clé elle-même), `kind` `local` ou `cloud`.
2. `models` : `api_model`, `cost_class` (`local`, `free_quota`, `paid` avec `price`), `capabilities`.
3. `tasks.<tâche>.candidates.<profil>` : ordre de préférence.
4. `python -m pytest tests/test_pricing_catalog.py` vérifie la cohérence (capacités, classes de coût autorisées, tâches sensibles locales).
5. Lancer le banc sur la tâche : sans preuve, le modèle n'est jamais choisi par `zero_cost`, `low_cost` ou `quality_first`.

Les quotas et politiques de données du catalogue datent du 16/09/2026 : à revérifier avant usage commercial.
