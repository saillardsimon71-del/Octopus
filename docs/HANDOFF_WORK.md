# Handoff — sortie de la plomberie et retour au business

**Mise à jour : 21 septembre 2026**

## État de départ

Référence Git attendue :

~~~text
main = 951d398272ee9e13c1c3c1bf7775de97a5f41fb0
~~~

Les PR #37 et #38 sont mergées.

Trois modules Python ont déjà passé un canari réel :

- capabilities.py
- resources.py
- connectors.py

Une quatrième surface est enregistrée mais pas encore exécutée en réel :

- businesses.py
- oracle : tests/test_businesses.py
- plan : octopus/config/night_shift_python_businesses_canary_v1.json

## Mission immédiate

**Faire uniquement le dernier canari businesses.**

Ne pas élargir d'abord vers d'autres modules.

### Procédure

1. synchroniser le worktree local sur origin/main ;
2. vérifier git status --short vide ;
3. reprendre/assainir les anciens night runs avec python -m octopus night-resume ;
4. vérifier l'image Docker canary ;
5. exécuter tests/test_night_shift.py, tests/test_dev_worker.py et tests/test_businesses.py ;
6. lancer night_shift_python_businesses_canary_v1.json avec 1 task / 1 failure max ;
7. exécuter le promotion gate ;
8. vérifier que seul octopus/businesses.py a changé ;
9. pousser une branche de promotion ;
10. merger uniquement après les checks GitHub requis verts.

## Critère de sortie

Si le run businesses :

- termine backlog_complete ;
- utilise python_canary ;
- utilise Docker ;
- garde max_files_changed=1 ;
- passe l'oracle businesses avec le même node set ;
- n'utilise aucun fallback ;
- produit une promotion git_verified: true ;
- ne modifie que octopus/businesses.py ;

alors **arrêter la plomberie générale**.

Ne pas transformer actions.py, economy.py, strategy.py ou compute_finance.py en nouveaux canaris sans besoin produit concret.

## Mission suivante

Après cette preuve, la priorité devient une **boucle économique réelle**.

Ordre recommandé :

~~~text
1. choisir une activité réelle unique
2. enregistrer un canal réel
3. construire l'executor minimal nécessaire
4. créer une expérience mesurable
5. agir dans le monde réel
6. récupérer une métrique/source observée
7. fermer evaluate_experiment
8. prendre la décision suivante
9. viser le premier cash-in observé
~~~

Le premier jalon commercial doit être un revenu réellement encaissé et traçable, pas une nouvelle couche d'infrastructure.

## Contraintes inchangées

- aucun secret dans Git ;
- aucun paid call implicite ;
- aucune dépense sans allowance ;
- aucune donnée business inventée ;
- aucune preuve observed sans source ;
- pas de modification directe de main ;
- changements petits, testés et réversibles ;
- les frontières financières existantes restent fail-closed.

## Documents à lire

1. AGENTS.md
2. docs/VISION.md
3. docs/CURRENT_STATE.md
4. docs/ACCEPTANCE_GATES.md
5. ce fichier

Les anciens handoffs sont historiques.
