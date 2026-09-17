Tu reprends le repo Octopus.

Ne repars surtout pas de zéro et ne refonds pas l’architecture existante.

## CONTEXTE

Le repo est actuellement orienté vers une vision plus large qu’une usine à vidéos : OCTOPUS doit devenir un système opérationnel capable, à terme, de :

Découvrir → Valider → Construire une offre → Produire du contenu → Construire des funnels → Acquérir/servir des clients → Mesurer → Réinvestir → Faire une revue stratégique → Recommencer.

La GUI possède déjà une couche **Intelligence & stratégie** reliée à ORBIT.

Le code existant contient déjà :

* Business Workspace
* Intelligence GUI
* ORBIT
* SOUT / CONVERT / FORGE / GROWTH / LEDGER
* `octopus.tasks`
* Worker / leases / retries
* Journal
* LLM gateway
* Browser + `web_guard`
* VideoService / cloud rendering
* etc.

La couche Intelligence actuelle expose :

1. Découvrir
2. Valider
3. Construire l’offre
4. Moteur contenu
5. Construire le funnel
6. Boucle client
7. Réinvestir
8. Revue stratégique

Ces actions lancent déjà de vraies missions ORBIT avec le business actif et ses offres connues.

## TERRAIN PRÉPARÉ

Une branche dédiée existe :

`feat/autonomous-business-foundation`

Le document d’architecture/implémentation est :

`docs/AUTONOMOUS-BUSINESS-IMPLEMENTATION.md`

Et l’issue GitHub correspondante est :

`#1 — Autonomous Business OS — persist the strategic loop`

Lis impérativement ces documents avant de modifier le code.

Lis également :

* `CLAUDE.md`
* `docs/GUI.md`
* `agents/gui/intelligence.py`
* `agents/gui/strategy.py`
* `agents/gui/workspaces.py`
* `agents/cycle.py`
* `agents/runtime.py`
* `octopus/tasks.py`
* `octopus/worker.py`
* `octopus/journal.py`
* les tests GUI/ORBIT/tasks pertinents

## OBJECTIF

Faire passer OCTOPUS d’une couche qui sait LANCER des boucles stratégiques à une architecture qui sait les CONSERVER, les MESURER, les REPRENDRE et les RÉÉVALUER dans le temps.

La boucle cible est :

BUSINESS
↓
OBJECTIVE
↓
HYPOTHESIS
↓
EXPERIMENT
↓
ORBIT MISSION
↓
TASK / WORKER
↓
AGENTS + TOOLS + CONNECTORS
↓
EVIDENCE
↓
RESULT
↓
DECISION
↓
STRATEGIC REVIEW
↓
NEXT EXPERIMENT
↺

## RÈGLE ARCHITECTURALE ABSOLUE

Ne crée PAS :

* un deuxième runtime d’agents ;
* une deuxième queue ;
* un deuxième scheduler ;
* une deuxième source de vérité des runs ;
* une deuxième architecture métier parallèle à Octopus.

Réutilise :

* `octopus.tasks`
* `octopus.worker`
* `octopus.journal`
* ORBIT
* les agents existants
* les mécanismes de scheduling existants
* le Business Workspace existant.

La GUI reste une couche de contrôle/observation.

La logique métier ne doit pas migrer dans Tkinter.

## PHASE 1 — PERSISTENCE STRATÉGIQUE

Implémente les plus petits modèles persistants nécessaires pour :

* `BusinessObjective`
* `Hypothesis`
* `Experiment`
* `Decision`
* `StrategicReview`

Ne construis pas un framework enterprise inutile.

Chaque objet doit au minimum avoir :

* ID stable
* business_id
* timestamps
* status/lifecycle
* relations parent/enfant pertinentes
* résumé lisible
* provenance lorsque nécessaire.

Le modèle doit permettre :

Objective
→ Hypothesis
→ Experiment
→ Mission
→ Result
→ Decision
→ Review.

Respecte les abstractions de persistence déjà présentes dans le repo.

Avant d’ajouter une nouvelle table, inspecte très précisément `octopus.journal`, l’état existant et les mécanismes SQLite.

## PHASE 2 — EVIDENCE / PROVENANCE

Construis une abstraction cohérente d’Evidence.

Une evidence doit pouvoir représenter par exemple :

* URL/source
* timestamp de capture
* type de source
* titre/résumé
* observation/fait extrait
* provenance
* niveau de confiance
* objet stratégique auquel elle est liée.

Le système doit distinguer explicitement :

* fait observé ;
* donnée fournie par l’utilisateur ;
* inférence du modèle ;
* recommandation.

NE JAMAIS transformer une estimation LLM en fait business.

## PHASE 3 — ORBIT

Les missions stratégiques doivent pouvoir transporter leur contexte durable :

* business_id
* objective_id
* hypothesis_id
* experiment_id
* review_id / decision_id si nécessaire.

Le lancement depuis la GUI doit continuer à utiliser le chemin ORBIT existant.

Ne mets pas la logique d’exécution stratégique dans `intelligence.py`.

À la fin d’une mission, il doit devenir possible de rattacher :

* résultat ;
* evidence ;
* outcome de l’expérience ;
* décision candidate ;
* prochaine mission.

## PHASE 4 — BOUCLES LONG TERME

Utilise le système de scheduling existant.

Exemples :

* revue stratégique hebdomadaire ;
* suivi d’expérience après X jours ;
* revue mensuelle du portefeuille ;
* rappel d’expérience bloquée.

Ne crée pas un scheduler dans la GUI.

La première version peut simplement programmer une mission de revue.

Aucune action financière ou irréversible ne doit être exécutée automatiquement.

## PHASE 5 — CONNECTEURS

Prépare des interfaces propres pour :

* CRM
* Finance
* Social Analytics
* Messaging/Funnel
* Publication

Mais NE SIMULE PAS les données.

Une source indisponible doit apparaître comme indisponible.

Par exemple :

`FinancialSource`

doit pouvoir indiquer :

* source non configurée ;
* données disponibles ;
* période ;
* provenance.

Même principe pour CRM/social/etc.

## PHASE 6 — RÉINVESTISSEMENT

Ne construis PAS immédiatement un système qui déplace réellement de l’argent.

Construis d’abord un système de décision :

1. lire les données financières vérifiées ;
2. séparer coûts engagés / réserve / budget expérimental ;
3. calculer des ratios transparents ;
4. proposer des allocations ;
5. enregistrer la justification ;
6. demander validation humaine avant toute action réelle.

Aucun chiffre financier ne doit être inventé.

## PHASE 7 — GUI INTELLIGENCE

Une fois la persistence en place, transforme progressivement Intelligence en cockpit stratégique réel.

Elle doit pouvoir montrer :

* objectifs actifs ;
* hypothèses ;
* expériences ;
* résultats ;
* evidence ;
* décisions ;
* prochaines revues ;
* données manquantes ;
* intégrations absentes ;
* vue portefeuille multi-business.

Conserve le principe :

**Business Workspace first.**

## PHASE 8 — TEST MULTI-BUSINESS

C’est extrêmement important.

Le système n’est pas considéré comme réellement générique tant qu’un deuxième business ne peut pas :

1. être créé ;
2. être sélectionné ;
3. avoir ses propres objectifs ;
4. avoir ses propres hypothèses ;
5. avoir ses propres expériences ;
6. lancer ORBIT ;
7. collecter des evidence ;
8. produire des décisions ;
9. planifier une revue ;

sans branche spécifique à Podalux.

Crée un business de test minimal pour démontrer cela.

## TESTS

Ajoute des tests pour :

* CRUD des objets stratégiques ;
* isolation business ;
* relations Objective/Hypothesis/Experiment ;
* provenance Evidence ;
* propagation du contexte vers ORBIT ;
* création de Decision depuis un résultat ;
* création d’une StrategicReview ;
* scheduling ;
* connecteurs absents ;
* second business ;
* régression Intelligence GUI ;
* absence de métriques inventées.

Ensuite exécute réellement les tests pertinents.

Puis exécute la suite complète si possible.

NE DIS PAS « tests verts » sans les avoir réellement exécutés.

## ORDRE DE TRAVAIL

1. Audite l’existant.
2. Identifie les points d’extension minimaux.
3. Implémente persistence.
4. Ajoute les tests.
5. Lie les missions ORBIT.
6. Implémente Evidence.
7. Implémente les reviews/schedules.
8. Ajoute les interfaces de connecteurs.
9. Branche la GUI sur la persistence.
10. Prouve le fonctionnement avec un deuxième business.
11. Lance les tests.
12. Corrige les régressions.
13. Mets à jour documentation et handoff.

## IMPORTANT

Ne profite pas de cette mission pour faire un grand nettoyage architectural.

Pas de réécriture de `agents/runtime.py` sans nécessité concrète.

Pas de remplacement d’ORBIT.

Pas de deuxième moteur vidéo.

Pas de deuxième queue.

Pas de deuxième DB stratégique indépendante.

Pas de logique métier dans Tkinter.

Pas de données CRM/finance/social inventées.

Pas de publication réelle.

Pas de transfert d’argent réel.

Pas de contournement de `web_guard`.

Pas de H3 local.

Pas de dépendance obligatoire à Orca.

## DEFINITION OF DONE

Je considère cette phase réussie lorsque :

Business
→ Objective
→ Hypothesis
→ Experiment
→ ORBIT Mission
→ Evidence
→ Result
→ Decision
→ Strategic Review

fonctionne réellement de bout en bout avec persistence.

Et lorsqu’un deuxième business peut parcourir la même boucle sans code Podalux spécifique.

À la fin :

1. donne-moi les fichiers modifiés ;
2. donne-moi les migrations ajoutées ;
3. donne-moi les tests ajoutés ;
4. donne-moi les commandes réellement exécutées ;
5. donne-moi les résultats réels des tests ;
6. donne-moi les éventuels points restant bloqués ;
7. mets à jour `CLAUDE.md`/documentation si nécessaire ;
8. ne prétends jamais avoir validé quelque chose qui n’a pas été exécuté.

Commence par l’audit de l’architecture existante et la conception de la persistence. Ne code pas avant d’avoir identifié où cette fonctionnalité doit naturellement vivre dans OCTOPUS.
