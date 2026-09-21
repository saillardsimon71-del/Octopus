# État actuel d'OCTOPUS

**Date de mise à jour : 21 septembre 2026**

## 1. Référence Git

`main` vérifié :

~~~text
cd8a3b315d6d000580b1674013880030b11ec206
~~~

Ce HEAD contient la promotion du quatrième canari Python supervisé via la PR **#42**.

Aucune PR n'était ouverte juste après cette fusion.

## 2. Phase plomberie : TERMINÉE

Les quatre surfaces supervisées ont maintenant passé un canari réel, une vérification Git indépendante et une promotion via PR :

1. `octopus/capabilities.py`
2. `octopus/resources.py`
3. `octopus/connectors.py`
4. `octopus/businesses.py`

Dernier run :

- run : `20260921-144425-9ac816`
- policy : `python_canary`
- status : `backlog_complete`
- modèle : `kilo/stepfun/step-3.7-flash:free`
- sandbox : Docker
- oracle : 8 tests
- `git_verified: true`
- un seul fichier modifié
- diff : +5 / -1
- PR #42 : checks requis verts puis merge

La généralisation du self-development est considérée **suffisante pour V1**.

Ne pas ajouter de nouvelles surfaces canary juste pour augmenter la couverture. Les modules sensibles ne sont modifiés que lorsqu'un besoin produit concret l'exige.

## 3. Nettoyage legacy

La PR #41 a supprimé :

- `core/`
- `businesses/short_video/`

Soit 33 fichiers / 616 lignes d'une ancienne architecture parallèle non utilisée.

Restent déclarés :

- `podalux` : encore relié aux handlers/runtime historiques actifs ;
- `veille` : business OCTOPUS complet et testé ;
- `studio` : namespace runtime du moteur vidéo, conservé tant que le Studio l'utilise.

## 4. Phase active : première boucle économique réelle

La priorité est maintenant l'issue **#40 — Phase 2: close the first real economic loop**.

Chaîne exigée :

~~~text
objectif
→ hypothèse
→ expérience
→ action réelle
→ preuve observée
→ ledger
→ evaluate_experiment
→ décision
→ prochaine action
~~~

Premier jalon commercial :

~~~text
1 € réellement encaissé
→ source vérifiable
→ écriture ledger observed
→ attribution business / canal / expérience
~~~

## 5. Contraintes inchangées

- zéro dépense implicite ;
- aucune preuve `observed` sans source vérifiable ;
- aucune donnée business inventée ;
- accès `act` explicite pour un canal réel ;
- actions externes idempotentes ;
- secrets hors Git ;
- changements petits et testés.

## 6. Gates restant à fermer par preuve réelle

- G1 live : attestation du pool OmniRoute free-only ;
- G3/G4/G5 : compute GPU live / benchmark / coût vidéo ;
- G6 : canal et action externes réels ;
- G7 : expérience économique réelle fermée ;
- G8 : V1 exploitable bout en bout.

La priorité produit est désormais G6/G7 et le premier cash-in, pas l'ajout de plomberie abstraite.
