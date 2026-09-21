# OCTOPUS

**Un atelier économique supervisé : tester un besoin, faire le travail, livrer, mesurer, décider.**

MARKET FIRST. AUTOMATION SECOND. GENERALIZATION LAST.

OCTOPUS possède une queue durable, des outils et des protections éprouvés par des tests.
Cela ne prouve pas encore qu'un client achète son travail. Le prochain jalon est une expérience
commerciale réelle, pas une nouvelle infrastructure ou une refonte d'interface.

## Un seul chemin principal

```text
objectif humain + limites
 → hypothèse / expérience bornée             strategy
 → travail manuel ou tâche durable          tasks / worker
 → action externe autorisée                 actions (ou intervention humaine tracée)
 → livraison / retour client                 strategy evidence
 → encaissement et coûts                     economy / ledger
 → temps humain + inconnues + rapport        economy outcome
 → continuer / corriger / arrêter             strategy decision + revue humaine
 → si bottleneck mesuré : development.task → nouvelle mesure sur la même mission
```

`task done` ≠ livré ≠ accepté ≠ utilisé ≠ payé ≠ rentable.
`supports` est un verdict sur **une métrique**, pas une certification commerciale.

## Utiliser le système maintenant

Le [protocole supervisé](docs/HANDOFF_WORK.md) utilise les commandes existantes pour lancer
un pilote d'enrichissement factuel sourcé de fiches produits. Aucun nouveau business module,
scraper, CRM, LLM ou service payant n'est nécessaire pour commencer.

```bash
python -m octopus strategy --help
python -m octopus economy --help
python -m octopus economy outcome BUSINESS EXPERIMENT_ID
python -m octopus strategy snapshot BUSINESS
```

Le rapport sépare travail technique, livraison, acceptation, usage, cash, coûts et temps humain.
Les inconnues restent inconnues. La contribution affichée ne couvre que les écritures explicitement
classées ; ce n'est pas une marge complète ni un calcul de coût humain.

## Architecture réellement conservée

Monolithe Python, journal SQLite : `strategy`, `tasks`, `worker`, `actions`, `economy`.
Un seul ledger. Les preuves sont immuables et rétractables ; les liens réutilisent les objets existants.
CLI, rapports, logs et revue humaine constituent le chemin d'exploitation prioritaire.

Les agents/vidéos Podalux, la veille, le Studio et la GUI CustomTkinter restent disponibles.
Leur extension est gelée jusqu'à un besoin observé. Ils ne sont pas requis pour tenir un pilote.
`development.task` n'est plus chargé par un worker ordinaire ; l'atelier reste accessible via
`night-shift` ou le chargement explicite documenté dans `docs/HANDOFF_WORK.md`.

## Invariants

- Aucune dépense sans autorisation ; pas de retry ambigu aveugle.
- L'humain gouverne accès, budgets, secrets, règles de preuve et promotion.
- Aucun revenu, client, coût ou résultat inventé ; sources déclarées à vérifier.
- LLM `zero_cost` par défaut, jamais de repli payant implicite.
- Compute payant via `GuardedComputeManager` ; protections et watchdog conservés.
- Pas de changement Git distant ni de fusion automatique dans main.

## Sources de vérité

| Document | Rôle |
|---|---|
| [AGENTS.md](AGENTS.md) | Constitution et dette de complexité |
| [VISION.md](docs/VISION.md) | Direction produit |
| [CURRENT_STATE.md](docs/CURRENT_STATE.md) | Réalité vérifiée et limites |
| [HANDOFF_WORK.md](docs/HANDOFF_WORK.md) | Golden path exécutable |
| [ACCEPTANCE_GATES.md](docs/ACCEPTANCE_GATES.md) | Preuves techniques et économiques |
| [EVIDENCE_ACCEPTANCE.md](docs/EVIDENCE_ACCEPTANCE.md) | Autorité de l'evidence et limites |
| [NEXT_STEPS.md](NEXT_STEPS.md) | Prochaine expérience, pas catalogue de chantiers |

Les plans spécialisés et archives ne constituent pas la roadmap active.

## Installation et tests

Voir [LOCAL_SETUP.md](docs/LOCAL_SETUP.md) pour l'installation Windows et les dépendances historiques.
Sous un environnement Python configuré, les commandes économiques ne démarrent ni GUI, ni LLM,
ni worker vidéo. `OCTOPUS_HOME` et `OCTOPUS_DB` permettent d'isoler un journal de travail.

```bash
python -m pytest -o addopts='' -q tests/test_economy.py tests/test_strategy.py tests/test_economy_act_cli.py
python -m pytest -o addopts='' -q
```

Les tests utilisent des bases temporaires et des transports simulés, jamais des preuves commerciales.