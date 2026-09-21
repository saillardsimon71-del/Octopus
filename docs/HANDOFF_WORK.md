# Handoff — Phase 2 : première boucle économique réelle

**Mise à jour : 21 septembre 2026**

## État de départ

~~~text
main = cd8a3b315d6d000580b1674013880030b11ec206
~~~

La plomberie générale est terminée.

Les quatre canaris Python supervisés ont été exécutés et promus. Le legacy `core/` + `businesses/short_video/` a été supprimé.

## Mission

Fermer une première boucle économique réelle, pas construire un nouveau framework.

~~~text
objectif
→ hypothèse
→ expérience
→ canal réel
→ action réelle
→ source observée
→ métrique / cash
→ evaluate_experiment
→ décision
→ prochaine action
~~~

## Ordre de travail

1. auditer ce qui existe déjà dans `actions.py`, `economy.py`, `strategy.py` et les agents ;
2. choisir **une seule activité réelle** et **un seul canal** ;
3. construire uniquement l'executor minimal manquant ;
4. enregistrer le canal et l'accès requis ;
5. créer une expérience mesurable ;
6. réaliser une action réelle ;
7. récupérer une source externe vérifiable ;
8. enregistrer métrique/coûts/cash ;
9. fermer l'expérience et persister la décision suivante.

## Critère de réussite

Le premier jalon n'est pas une nouvelle couche d'infrastructure.

Le jalon est :

~~~text
une vraie action externe + une vraie observation
~~~

Puis :

~~~text
premier euro encaissé et enregistré comme observed
~~~

## Non-objectifs

- pas de nouvelle surface python_canary par défaut ;
- pas de multiplication des business/canaux ;
- pas de paid call implicite ;
- pas de réécriture de l'orchestrateur sans blocker concret ;
- pas de métriques inventées.

## Issue de référence

GitHub issue **#40 — Phase 2: close the first real economic loop**.
