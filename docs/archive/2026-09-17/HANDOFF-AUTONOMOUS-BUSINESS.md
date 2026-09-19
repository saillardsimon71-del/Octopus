# OCTOPUS — handoff d'implémentation « Autonomous Business »

> Document de reprise pour Claude sur la machine locale.  
> Branche de référence : `feat/cloud-video-foundation`  
> HEAD observé : `2fd61795b6a9e034515d0e10258588568ca56adf`

## 1. Vision cible

OCTOPUS ne doit plus être pensé comme une usine à vidéos. Le produit cible est un **control-plane entrepreneurial multi-business** capable de maintenir une boucle longue :

`Découvrir → Valider → Offre → Contenu → Funnel → Clients → Réinvestir → Revue stratégique → recommencer`

La GUI est le poste de pilotage. Elle ne doit pas devenir un deuxième moteur d'agents.

Le moteur d'exécution reste :

`GUI → mission ORBIT → tâches durables → worker/runtime → agents → tools/connecteurs → journal/résultats`

Le business actif et ses offres connues doivent être propagés dans les missions. Les données absentes doivent rester explicitement absentes : **jamais de revenus, marges, clients ou analytics inventés**.

## 2. Ce qui existe déjà sur cette branche

- Workbench GUI orienté Business Workspace.
- Registre local des businesses/offres.
- Page **Intelligence & stratégie**.
- Huit actions stratégiques : discover, validate, offer, content, funnel, clients, reinvest, review.
- Chaque action compose un objectif et lance le chemin de mission ORBIT existant.
- Tests de la couche strategy/intelligence.
- Missions, queue durable, worker, journal et cycle SOUT → CONVERT → FORGE → GROWTH → LEDGER → ORBIT.
- Production vidéo cloud-first et protections d'idempotence.
- Browser guard et handoff humain.

Références : `agents/gui/intelligence.py`, `agents/gui/strategy.py`, `agents/gui/workspaces.py`, `agents/gui/workbench.py`, `agents/cycle.py`, `octopus/tasks.py`, `octopus/worker.py`, `octopus/journal.py`.

## 3. Ce qui manque réellement

### P0 — rendre la stratégie persistante

Créer une couche métier persistante, dans la base OCTOPUS existante, pour :

- objectifs business long-terme ;
- hypothèses ;
- expériences ;
- décisions ;
- résultats/observations ;
- revues stratégiques ;
- prochaine date de revue ;
- statut : active / paused / validated / rejected / archived.

Ne pas créer une deuxième base SQLite. La persistence business doit converger vers le stockage OCTOPUS existant, avec des tables métier dédiées si nécessaire.

### P0 — faire de la mission stratégique un objet traçable

Une action Intelligence doit produire une mission/résultat identifiable avec :

- business_id ;
- action/phase ;
- objectif ;
- offre(s) concernée(s) ;
- run/task IDs ;
- preuves produites ;
- décisions ;
- erreurs ;
- prochaine action.

Le Workbench doit pouvoir afficher l'historique de cette boucle sans reconstruire l'état depuis des logs texte.

### P1 — boucle périodique

Ajouter un mécanisme de revue planifiée qui s'appuie sur le scheduler existant :

- revue hebdomadaire/mensuelle configurable ;
- mission ORBIT de revue ;
- collecte des résultats réellement disponibles ;
- génération d'un compte rendu structuré ;
- création des prochaines décisions/expériences ;
- handoff humain si une décision irréversible est nécessaire.

Ne pas lancer de boucle infinie non contrôlée.

### P1 — modèle de données « Evidence first »

Introduire un contrat d'artefact/résultat suffisamment générique pour rattacher :

- source ;
- observation ;
- timestamp ;
- métrique éventuelle ;
- provenance ;
- confiance/qualité si disponible ;
- lien vers run/task/mission.

Une conclusion stratégique doit pouvoir citer les éléments qui la justifient.

### P1 — connecteur CRM

Définir une interface générique, sans imposer immédiatement un fournisseur :

- customers ;
- leads ;
- lifecycle stage ;
- interactions ;
- tasks/follow-ups ;
- source/channel ;
- revenue attribué lorsque réellement fourni par la source.

Commencer par un adapter local/mock pour tests. Ne brancher un fournisseur réel qu'après stabilisation du contrat.

### P1 — ledger revenus/coûts/marges

Créer une interface de données financières opérationnelles :

- période ;
- revenus ;
- coûts directs ;
- coûts d'acquisition ;
- coûts d'infrastructure ;
- marge calculée seulement si les entrées nécessaires sont présentes ;
- provenance.

Le module de réinvestissement doit consommer ce ledger, pas des valeurs codées dans la GUI.

### P1 — analytics contenu/social

Définir un contrat commun pour :

- contenu publié ;
- plateforme ;
- date ;
- impressions/reach ;
- vues ;
- engagement ;
- clics ;
- leads/conversions lorsqu'ils sont réellement attribuables.

La stratégie de contenu doit pouvoir comparer les performances dans le temps sans dépendre d'un fournisseur particulier.

### P2 — funnels et messagerie

Définir des primitives d'exécution :

`CTA → inbound event → lead/contact → qualification → message → handoff/offer → conversion`

Commencer en mode dry-run/simulation. Toute action externe irréversible doit conserver le contrôle humain jusqu'à preuve de fiabilité.

### P2 — publication et distribution

Brancher progressivement les plateformes de publication au contrat d'artefact existant. Conserver :

- idempotency key ;
- statut remote ;
- tentative ;
- résultat ;
- rollback/annulation lorsque supporté ;
- dry-run par défaut tant que non validé.

## 4. Architecture cible

```text
                           OCTOPUS
                              │
             ┌────────────────┼─────────────────┐
             │                │                 │
          BUSINESS         ORBIT             DATA
             │                │                 │
     goals/offers/state   missions/review   evidence/metrics
             │                │                 │
             └───────────────┼─────────────────┘
                             │
                    durable tasks / worker
                             │
        ┌──────────────┬─────┴──────┬──────────────┐
        │              │            │              │
      RESEARCH        OFFER       CONTENT         CRM
        │              │            │              │
     browser/web    catalog      Remotion      customer source
        │              │         video cloud        │
        └──────────────┴────────────┼────────────────┘
                                   │
                              FINANCE / SOCIAL
                                   │
                              evidence + KPIs
                                   │
                              STRATEGIC REVIEW
                                   │
                              next experiment
```

### Règle d'or d'architecture

Ne pas faire :

`GUI → logique métier Tkinter`  
`Agent → Agent → Task → Agent` sans contrat clair  
`business DB` parallèle à `octopus journal/tasks`  
`provider-specific data` directement dans ORBIT.

Faire :

`GUI → command/mission → durable task → workflow/agent → connector → evidence → journal/business state`.

## 5. Interfaces minimales à stabiliser

Avant d'ajouter beaucoup de connecteurs, formaliser des types/protocoles testables :

- `BusinessContext`
- `StrategicObjective`
- `MissionResult`
- `Evidence`
- `Experiment`
- `Decision`
- `CustomerSource`
- `FinancialSource`
- `ContentAnalyticsSource`
- `DistributionTarget`
- `ReviewSchedule`

Ces interfaces doivent être indépendantes de Tkinter et des fournisseurs.

## 6. GUI attendue après implémentation

### Business

- business actif ;
- portefeuille ;
- offres ;
- objectifs ;
- santé de la boucle.

### Intelligence

- 8 phases ;
- dernière mission par phase ;
- dernière preuve ;
- décision courante ;
- prochaine revue ;
- bouton « lancer » ;
- bouton « revoir » ;
- alertes de données manquantes.

### Missions

- mission ;
- statut ;
- business ;
- phase ;
- run/task ;
- résultats ;
- handoffs.

### Clients / Finance / Analytics

Ces pages peuvent être introduites lorsque leurs sources existent. Avant cela, afficher clairement **non connecté** et proposer la configuration du connecteur, sans simuler de données.

## 7. Ordre d'implémentation pour Claude

1. Lire `CLAUDE.md`, ce document et `docs/GUI.md`.
2. Vérifier HEAD, branche et CI avant modification.
3. Cartographier les tables SQLite existantes dans `octopus/journal.py`, `octopus/tasks.py` et la persistence business actuelle.
4. Implémenter les modèles persistence stratégie/objectifs/hypothèses/expériences/décisions/revues.
5. Ajouter tests unitaires et tests d'intégration mission ↔ persistence.
6. Faire retourner aux missions stratégiques un résultat structuré et traçable.
7. Connecter l'Intelligence GUI à cet état persistant.
8. Ajouter le scheduler de revue, avec garde contre les doublons et les boucles infinies.
9. Implémenter les interfaces Evidence/CRM/Finance/Social en mock/local d'abord.
10. Brancher les données réelles une par une, avec provenance et idempotence.
11. Ajouter les funnels/messages/publication en dry-run.
12. Seulement ensuite activer les exécutions externes réelles.

## 8. Critères d'acceptation

Le travail n'est pas considéré terminé si :

- un objectif stratégique disparaît après redémarrage ;
- une mission ne peut pas être reliée à son business ;
- une décision n'a aucune provenance ;
- une revue planifiée crée des doublons ;
- ORBIT invente une donnée manquante ;
- le GUI contient de la logique métier/persistence propriétaire ;
- un connecteur externe contourne les garde-fous existants ;
- une publication ou opération client irréversible se déclenche sans contrat/handoff approprié ;
- les tests nouveaux ne couvrent pas persistence, idempotence, reprise après erreur et contexte business.

## 9. Tests minimums à ajouter

- persistence CRUD + redémarrage ;
- isolation par business ;
- mission stratégique → run/task → résultat ;
- evidence provenance ;
- décision sans métrique inventée ;
- scheduler idempotent ;
- revue périodique ;
- CRM mock ;
- finance mock + calcul de marge uniquement avec données suffisantes ;
- analytics mock ;
- funnel dry-run ;
- publication dry-run/idempotence ;
- GUI intelligence avec état persistant ;
- non-régression du cycle Podalux et de la production vidéo.

## 10. Contraintes à ne pas violer

- H3 reste cloud-only.
- `web_guard` reste obligatoire pour les missions navigateur concernées.
- Publication reste dry-run tant que le chemin réel n'est pas validé.
- Orca reste optionnel et réservé au développement du dépôt.
- Ne pas créer un deuxième moteur vidéo.
- Ne pas créer une deuxième queue de tâches.
- Ne pas créer une deuxième base métier concurrente.
- Ne pas bloquer le thread Tkinter.
- Ne jamais prétendre qu'une donnée existe parce qu'elle serait utile à une décision.
- Ne jamais considérer une perte de contact externe comme une preuve de succès ou d'échec sans état vérifiable.

## 11. Definition of Done globale

OCTOPUS sera au niveau « Autonomous Business control-plane » lorsque, pour un business donné, le système peut :

1. conserver ses objectifs et hypothèses ;
2. lancer une expérience via ORBIT ;
3. collecter des preuves provenant de sources identifiées ;
4. enregistrer résultat et décision ;
5. produire/mesurer contenu lorsque les connecteurs sont disponibles ;
6. suivre leads/clients lorsque le CRM est connecté ;
7. consolider revenus/coûts lorsque les sources financières sont connectées ;
8. exécuter ou préparer un funnel avec idempotence et handoff ;
9. déclencher une revue périodique ;
10. décider d'une prochaine expérience à partir de l'état persistant ;
11. recommencer la boucle sans perdre le contexte business.

Le but n'est pas de tout automatiser immédiatement. Le but est que **chaque nouvelle capacité s'insère dans une boucle persistante, traçable, multi-business et evidence-first**.
