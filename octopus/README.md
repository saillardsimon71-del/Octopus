# OCTOPUS : noyau commun des activités

> **Référence technique historique (v0.2), pas vision courante.** Lire d'abord `../README.md`
> et `../docs/HANDOFF_WORK.md`. Le chemin principal est `strategy → travail → evidence/ledger
> → economy outcome → décision humaine`. Les profils et budgets ci-dessous décrivent des
> compatibilités ; le profil normal est `zero_cost` et le paiement exige une politique explicite.

Version 0.2. Quatre briques, utilisées par Podalux sans changer ses signatures :

| Brique | Fichier | Rôle |
|---|---|---|
| Journal | `journal.py` | SQLite `data/octopus.db` : runs imbriqués (cycle > agent > outil), appels LLM, résultats du banc |
| Passerelle LLM | `llm.py`, `catalog.py`, `pricing.py` | un seul point d'entrée : choix du modèle par profil, budget vérifié **avant** l'appel, coût à la grille officielle (heures pleines, cache), justification de chaque appel payant |
| Banc | `bench.py`, `agents/evals.py` | compare code, modèles OpenRouter gratuits et DeepSeek payants sur les vraies tâches, avec des vérifications déterministes |
| File de tâches | `tasks.py`, `worker.py` | tâches persistées, ressources exclusives, baux, reprises, annulation, demandes humaines, planifications |

## Profils (`OCTOPUS_PROFILE`)

| Profil | Comportement |
|---|---|
| `legacy` (compatibilité explicite) | modèle imposé par le code, requêtes identiques à l'historique (vérifié par `tests/test_legacy_compat.py`) |
| `zero_cost` | OpenRouter gratuit seulement ; un modèle n'est utilisé que s'il a réussi le banc (5 essais, 90 %, moins de 60 jours) |
| `economical` | deux routes et trois requêtes gratuites au plus, puis DeepSeek seul sous le plafond LLM configuré pour le démarrage autonome |
| `low_cost` | OpenRouter gratuit validé d'abord, payant en dernier recours, dans le budget |
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
python -m octopus models --refresh           découverte et état du cache
python -m octopus models --browser-candidates candidats techniques, qualification séparée
python -m octopus report --days 7 --legacy-db agents/data/podalux.db
python -m octopus bench --models code --tasks podalux.arbitrate
python -m octopus bench --models deepseek/flash --allow-paid --max-cost 0.05
python -m pytest                             tests hors-ligne (aucun appel réseau)
```

Le banc refuse les modèles payants sans `--allow-paid` et s'arrête au plafond `--max-cost`.

## File de tâches et worker (M2)

Tables `tasks`, `events`, `human_requests`, `schedules` dans `data/octopus.db` (schéma v2, migration automatique).

| Élément | Comportement |
|---|---|
| Prise de tâche | transaction `BEGIN IMMEDIATE` : un seul worker par tâche ; priorité puis ancienneté ; `not_before` pour les délais |
| Ressources | une tâche `cpu_heavy` (traitement CPU) à la fois ; les tâches sans ressource passent à côté |
| Bail | identifiant unique par exécution du worker, renouvelé avant expiration ; un bail expiré ne peut plus écrire ni être renouvelé ; reprise s'il reste des tentatives, sinon échec |
| Échec | nouvelle tentative différée (`max_attempts`, `retry_delay_s` du handler) |
| Annulation | immédiate en file ; coopérative en cours (`ctx.check_cancel()`, relayée à l'arrêt Podalux) |
| Humain | `ctx.ask_human(clé, question)` met la tâche en attente ; `python -m octopus answer ID "texte"` la remet en file, le handler est rejoué et retrouve la réponse |
| Planification | `python -m octopus schedule ...` ; une occurrence encore active n'est pas empilée |
| Idempotence | `idempotency_key` unique (ex. `publish:<offre>:<sha256>`) |
    | Coûts | chaque tâche tourne dans un run du journal, avec le budget déclaré par son handler |

Un handler est une fonction `fn(ctx) -> sortie JSON`, enregistrée pour un type de tâche :

```python
@handler("atelier.prepare_delivery", resource="cpu_heavy", budget_usd=1.0)
def prepare_delivery(ctx):
    ...
```

- Chaque tâche tourne dans un run du journal (budget par tâche, coûts rattachés).
- `ctx.ask_human(clé, question)` : renvoie la réponse si elle existe, sinon met la tâche en attente ;
  elle sera relancée depuis le début après la réponse (les handlers doivent être rejouables :
  `ctx.memo(clé, fonction)` conserve le résultat des étapes coûteuses).
- `ctx.cancelled()` : annulation demandée (coopérative). `ctx.enqueue(...)` : tâche suivante.

Handlers chargés : `octopus.builtin_handlers` (`octopus.cost_report`), `agents.task_handlers` (`podalux.agent_message`, `podalux.mission`, `orbit.mission`) et `businesses.veille.handlers` (`veille.brief`, voir `businesses/veille/README.md`), surchargeables par `OCTOPUS_HANDLERS`. Une tâche rejouée après une réponse humaine retrouve ses étapes coûteuses via `ctx.memo`. Aucune planification n'est créée d'office. Le moteur vidéo historique a été retiré le 2026-09-25.

```
python -m octopus worker                      boucle (Ctrl+C pour arrêter)
python -m octopus worker --task ID             exécuter une tâche spécifique en une seule tentative
python -m octopus enqueue octopus octopus.cost_report
python -m octopus tasks / events / ask / answer 3 "oui" / cancel 12
python -m octopus schedule octopus octopus.cost_report --every 86400
```

### Garanties de reprise

Le worker vérifie son bail lors des transitions, du rattachement du run et de l'enregistrement des étapes. Une ancienne exécution ne peut pas écraser le résultat de sa remplaçante, même si les deux workers portent le même nom. Un échec de validation du bail à la fin du handler clôt le run en erreur, pas en succès.

`ctx.memo` conserve aussi les résultats `None`, `False` ou vides et contrôle le bail avant de démarrer le calcul. Les appels bas niveau à `save_step` et `set_run` acceptent `owner=` pour ce contrôle transactionnel ; leur signature historique sans propriétaire reste disponible, sans cette protection.

Ces garanties concernent la persistance OCTOPUS : un bail ne tue pas un processus ni un outil externe. Un crash entre un effet externe et son checkpoint peut encore rejouer cet effet ; les handlers doivent rester coopératifs et utiliser l'idempotence de l'outil lorsqu'elle existe.

## Catalogue et qualification

Deux fournisseurs actifs : OpenRouter dynamique gratuit et DeepSeek direct.
Les modèles OpenRouter ne sont pas ajoutés au JSON ni dupliqués dans le code.
Les tâches déclarent leurs capacités requises, puis la preuve décide de l'éligibilité.
Le benchmark général ne donne jamais le contrôle du navigateur.

Lire [le catalogue OpenRouter](../docs/OPENROUTER_CATALOG.md) et
[la politique de routage](../docs/LLM_ROUTING_POLICY.md).
