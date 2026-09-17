# OCTOPUS : noyau commun des activités

Version 0.1 (M1). Trois briques, utilisées par Podalux sans changer ses signatures :

| Brique | Fichier | Rôle |
|---|---|---|
| Journal | `journal.py` | SQLite `data/octopus.db` : runs imbriqués (cycle > agent > outil), appels LLM, résultats du banc |
| Passerelle LLM | `llm.py`, `catalog.py`, `pricing.py` | un seul point d'entrée : choix du modèle par profil, budget vérifié **avant** l'appel, coût à la grille officielle (heures pleines, cache), justification de chaque appel payant |
| Banc | `bench.py`, `agents/evals.py` | compare code, modèles locaux, gratuits et payants sur les vraies tâches, avec des vérifications déterministes |

## Profils (`OCTOPUS_PROFILE`)

| Profil | Comportement |
|---|---|
| `legacy` (défaut) | modèle imposé par le code, requêtes identiques à l'historique (vérifié par `tests/test_legacy_compat.py`) |
| `zero_cost` | local et quotas gratuits seulement ; un modèle n'est utilisé que s'il a réussi le banc (5 essais, 90 %, moins de 60 jours) |
| `low_cost` | local et gratuit validés d'abord, payant en dernier recours, dans le budget |
| `quality_first` | meilleur modèle validé d'abord, repli sur un modèle gratuit si le budget bloque |
| `bench` | réservé au banc |

Coupe-circuit : `OCTOPUS=off` rétablit l'appel direct historique (ni journal, ni passerelle).

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

## Ajouter un fournisseur ou un modèle

1. `providers` : `base_url` compatible OpenAI, `api_key_env` (jamais la clé elle-même), `kind` `local` ou `cloud`.
2. `models` : `api_model`, `cost_class` (`local`, `free_quota`, `paid` avec `price`), `capabilities`.
3. `tasks.<tâche>.candidates.<profil>` : ordre de préférence.
4. `python -m pytest tests/test_pricing_catalog.py` vérifie la cohérence (capacités, classes de coût autorisées, tâches sensibles locales).
5. Lancer le banc sur la tâche : sans preuve, le modèle n'est jamais choisi par `zero_cost`, `low_cost` ou `quality_first`.

Les quotas et politiques de données du catalogue datent du 16/09/2026 : à revérifier avant usage commercial.
