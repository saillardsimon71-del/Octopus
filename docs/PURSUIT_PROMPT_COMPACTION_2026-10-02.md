# Décompression locale du prompt pursuit — 2026-10-02

Base exacte : `b6ca56e5c76ade9d90beadccc3e4b63e5b744e9a` (PR #117).
Branche : `fix/pursuit-cognitive-decompression`.

## Modification

Le cold start injectait Foundation complète, des catalogues de frontières et plusieurs
clauses répétées avant les faits disponibles. Le premier sous-agent portait aussi
l'identité `OCTOPUS (business octopus)` et le contrat d'un outil absent de son allowlist.

`octopus/supervisor.py` projette maintenant la finalité économique en un préambule court :
cash client réellement encaissé, marge, récurrence, autonomie, croissance ; liberté de
choisir marchés, problèmes, acheteurs, offres, hypothèses et ordre d'exploration ;
séparation pertinence/exécution ; distinction hypothèse/inférence/observation/preuve/
résultat ; inconnue ≠ zéro ; observation publique gratuite et analyse, budget externe 0 EUR.
L'état, les preuves admissibles, les décisions, les stratégies, les études et les
apprentissages pertinents conservent exactement leurs projections et leurs plafonds.
Le rappel sur les hypothèses invalidées et les coûts passés n'apparaît qu'avec un historique pertinent.

`agents/runtime.py` retire les noms d'outils des responsabilités génériques. Le contrat
`record_observation` apparaît uniquement quand l'outil est enregistré et exposé ; il est
absent de pursuit et reste disponible dans les autres contextes qui l'exposent.
L'identité système non déclarée devient « exploration économique ». Un business déclaré
conserve son nom humain ; les IDs, scopes et registres restent inchangés.
L'exemple de domaine dans la description de recherche est retiré de la projection du
contexte système ; le registre et le filtrage `site` restent inchangés.

Le planner conserve les responsabilités, la décomposition, la non-duplication,
la réutilisation des artefacts et la borne en étapes. Il précise que celle-ci s'applique
aux tâches exécutées maintenant, et demande à chaque tâche quoi observer et pourquoi
économiquement. Envisager une offre sans preuve commerciale préalable reste possible.

La détermination conserve l'interface JSON et condense ses instructions. Une proposition
n'est pas une action externe ; une capacité absente peut figurer dans une stratégie.
`continue` peut proposer une observation gratuite qui réduit une incertitude stratégique ;
`pause` signifie qu'aucune exploration admissible et utile n'est justifiée.
Les contrats du mode spécialisé `business_signal_focus` sont conservés.

Aucune transmission de contexte supplémentaire au premier sous-agent. Dans la capture,
son message est exactement la tâche autonome écrite par le planner simulé, sans Foundation.
Cela vérifie le chemin de transmission ; cela ne prouve pas qu'un vrai planner écrira
toujours une bonne tâche.

## Mesure reproductible, sans provider

`tests/pursuit_prompt_capture.py` convertit le harness de l'audit en capture réutilisable.
Deux processus séparés importent respectivement la base et le patch, sur le même état
froid et avec les mêmes réponses scriptées. Le réseau est bloqué dans le CLI.
Les caractères comptent les contenus système + utilisateur, sans les enveloppes JSON HTTP.
Les tokens sont estimés à quatre caractères par token, pas mesurés par un tokenizer.

| Couche | Base, caractères | Patch, caractères | Tokens estimés, base → patch |
|---|---:|---:|---:|
| Objectif froid | 7 987 | 1 662 | 1 997 → 416 |
| Planification complète | 9 925 | 3 789 | 2 481 → 947 |
| Premier ReAct | 2 089 | 1 824 | 522 → 456 |
| Synthèse complète | 11 909 | 3 689 | 2 977 → 922 |

La tâche scriptée décrit l'observation et sa raison économique. Elle est plus longue
de 86 caractères que celle de l'audit initial : cela explique les valeurs de base
ReAct/synthèse différentes de 2 003/11 823. L'état et les arguments d'exécution sont identiques.
Le préambule froid passe de 7 204 à 879 caractères. Les répétitions de Foundation,
séparation des capacités, acquisition, permissions, erreurs techniques et produits
historiques sont retirées ; aucune blacklist n'est ajoutée.

Reproduction depuis cette branche, avec une base déjà extraite :

```bash
python tests/pursuit_prompt_capture.py --repo /chemin/base --out /tmp/prompt-before
python tests/pursuit_prompt_capture.py --repo /chemin/patch --out /tmp/prompt-after
```

Les fichiers produits sont des captures et métriques structurelles. Les réponses
scriptées ne mesurent ni la diversité réelle des hypothèses, ni l'intelligence commerciale,
ni la disparition effective de la dérive Octopus Deploy. Aucun vrai LLM n'est appelé.
Le prochain run réel autorisé séparément reste le test qualitatif.

## Invariants et validation

Les nouveaux tests protègent la projection de la finalité, la liberté stratégique, la
vérité économique, la sélection réelle des contrats, l'identité neutre et le nom humain,
les limites existantes, l'expression d'une stratégie non exécutable et le refus avant effet
d'un outil hors allowlist. Ils démontrent aussi qu'une détermination scriptée peut
continuer sans preuve commerciale lorsqu'une observation permise reste proposée.
Les tests existants d'apprentissage, reprise, étude de capacités, finance et worker
conservent leurs assertions d'exécution. Seules les assertions de texte obsolètes sont adaptées.

- Tests ciblés prompt/runtime, pursuit, learning/recovery, séparation/acquisition, stack,
  gateway, economy/finance, web_guard, worker et Workbench : **803 passés, aucun échec**.
- Vérification Foundation-start et compatibilité legacy après correction de l'assertion
  obsolète et restauration du texte legacy non restreint : **50 passés, 1 ignoré**.
- Suite complète applicable sur le patch : **1 856 passés, 97 ignorés, aucun échec**.
- Suite complète sur la base exacte : **1 835 passés, 97 ignorés, aucun échec**.
  Les ensembles de tests ignorés sont identiques (comparaison des rapports JUnit).
- Aucun nouveau test d'exécution n'est supprimé ou affaibli ; **21 nouveaux cas**.

Commande de suite complète : `python -m pytest -o addopts='' -q --junitxml=/tmp/full-tests.xml`.

Foundation, budgets, cycles, timeouts, permissions, tools, web_guard, finance safety,
journal, worker, idempotence, acquisition, registres, providers et routing LLM restent
inchangés. La comparaison AST de `_pursuit_mission`, après retrait des seules affectations
de texte `foundation`/`goal`, est identique à la base. Les modules protégés sont identiques
octet pour octet. `strategy_separation.is_substitution` reste inchangé ; le cas connu de
réécriture de certains `next_goal` sera observé séparément au prochain vrai run.

Aucune acquisition réelle, aucun provider réel appelé pour la validation, aucun vrai
run économique, aucune dépense, aucun compte créé, aucun message envoyé et aucun merge.
