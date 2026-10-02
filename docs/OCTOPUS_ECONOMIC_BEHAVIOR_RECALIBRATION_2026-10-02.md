# Recalibrage économique — audit causal du 2026-10-02

Base auditée : `65391d6a747741900e646b3c4c5c4eac09168d96` (#119, après #118).
Ce document a été écrit avant les changements de production. Les observations des runs
proviennent du récit opérateur ; le DataRoot et ses requêtes exactes ne sont pas accessibles
ici. Aucun test scripté ne mesure la qualité du raisonnement d'un vrai modèle.

## Diagnostic et réponses aux douze questions

| Question | Constat dans la base |
| --- | --- |
| 1. Fixation précoce | `assess()` choisit une `retained` dès la première proposition, sans seuil de maturité commerciale. Cela classe une option, sans prouver sa valeur. Le verrou de continuation transforme ensuite ce classement en obligation. |
| 2. Poids de retained | `pursuit_proposals()` place les anciennes retained avant les propositions courantes dans une fenêtre de douze. Les annotations sont persistées par hypothèse ; leur présence n'établit pas un engagement commercial. |
| 3. is_substitution | Comparaison lexicale avec le statement et `reasoning_goal()`. Même une recherche différente sur A ou l'observation de B est considérée comme substitution. `apply_pursuit_choice()` réécrit alors next_goal, et peut annuler une pause si un outil manque. |
| 4. Largeur du cold start | Le prompt laisse libres marchés et hypothèses, mais ne demande pas réellement de comparer des familles différentes avant d'approfondir. Aucun catalogue ne l'interdit non plus. |
| 5. Planner | Les tâches autonomes et les étapes bornées distinguent réflexion et exécution. L'arbitrage générique ORBIT est néanmoins décrit seulement par les expériences et le cash observé, trop étroit pour une découverte sans historique. |
| 6. Apprentissage | `strategy.learning_context()` utilise des expériences évaluées et des preuves canoniques, pas des conclusions libres. Une épuisement informationnel de recherche peut déjà être conservé dans la décision, son rationale et le travail précédent, sans devenir une réfutation. |
| 7. Piste indépendante | Le stockage permet plusieurs hypothèses proposées/inconclusives. Le verrou de next_goal empêche actuellement leur exploration indépendante ; aucune nouvelle table ou transition d'hypothèse n'est nécessaire pour le lever. |
| 8. Capacités absentes | Le classement économique ignore correctement l'inventaire. Le problème se situe dans la continuation, orientée sur les outils manquants, et dans une distinction conceptuelle insuffisamment explicite dans les prompts. |
| 9. Moyens ordinaires | Les stratégies acceptent des identifiants libres ; les moyens du monde ne sont pas limités au catalogue d'exécuteurs. Le ToolRegistry reste l'autorité pour l'exécution actuelle. Il faut expliciter cette distinction, pas construire une ontologie. |
| 10. Levier IA | Justification économique, rang relatif, signal attendu et critère d'arrêt peuvent déjà représenter automatisation, volume, distribution et coûts marginaux. Aucune élimination par revenu unitaire faible dans le code. Ces dimensions ne sont cependant pas demandées explicitement. Le ledger mesure les coûts/cash réels, pas une rentabilité prévisionnelle supposée. |
| 11. Trois cycles | Borne de coût et d'exécution, pas trois phases économiques. Elle permet des observations différentes ou un approfondissement utile. Elle ne garantit ni validation commerciale ni exploitation ; aucune raison démontrée de l'augmenter. |
| 12. Options ou obligations | Les objets canoniques représentent des options. Le couplage retained → contrôle lexical de next_goal leur donne involontairement le rôle d'obligations de recherche. |

## Périmètre transversal vérifié

`supervisor._pursuit_mission`, `execute_pursuit`, `start_pursuit` et `_queue_pursuit` :
reprise de l'objectif, observations du travail précédent, checkpoints dégradés, trois cycles,
budget USD 0.20, collecte publique seulement. Une reprise après pause crée une nouvelle
recherche avec la mémoire précédente ; une reprise dégradée réutilise les sous-tâches achevées.

`strategy_separation` : normalisation bornée, priorité cash/marge/récurrence/autonomie/croissance,
rang à critère égal, annotations persistées, inventaire déterministe, autorisation/dispatch.
Le rang est une appréciation du modèle, pas une mesure du cash ni un score de rentabilité.
Les anciens rangs peuvent devenir obsolètes ; une nouvelle comparaison doit les actualiser.

`strategy` : hypothèses, décisions, preuves sourcées, résultats supports/refutes/inconclusive,
reconsidération sur preuve nouvelle. Une étude de capacité ou un succès technique n'est pas
une preuve économique. `economy.evaluate_experiment` conserve inconnu et inconclusif ; la
contribution enregistrée exclut notamment le temps humain et ne prétend pas être la marge totale.

`capability_acquisition` : étude informative des écarts de la meilleure option, exigences et
coûts connus/inconnus ; son estimation interne n'est pas le classement des stratégies ni une
preuve. Le parcours pursuit ne construit, ne commande, ne demande et n'acquiert aucune capacité.

`agents/runtime`, `ToolRegistry`, gardes Web et `llm` : autonomie des tâches, réutilisation des
observations, validators, outils autorisés, frontières humaines et finance déterministes.
Le fallback syntaxique #119, modèles, providers et budgets ne nécessitent aucun changement.
Workbench : projection en lecture seule des tâches, décisions, état et coûts ; aucune nouvelle UI nécessaire.

## Changements minimaux retenus après audit

1. Rendre explicite le périmètre d'observation de pursuit aux deux fonctions de séparation
   existantes. Les propositions courantes passent avant l'historique dans la projection bornée ;
   les anciennes options et leurs connaissances restent persistées. Un but d'observation ou
   une pause ne sont plus réécrits pour protéger une stratégie contre une substitution d'exécution.
   Un historique entièrement réfuté ne ferme pas une recherche indépendante sans proposition
   actuelle ; une proposition actuelle réfutée et la répétition d'une hypothèse invalidée
   restent soumises aux gardes existantes et à la preuve nouvelle.
2. Interdire le dispatch de stratégie dans ce parcours d'observation, même si la meilleure
   option a des outils disponibles. L'autorisation économique d'une option n'est pas un commit.
   Les gardes d'exécution existantes restent inchangées pour leurs autres consommateurs.
3. Quelques phrases dans les prompts existants : comparer plusieurs possibilités distinctes,
   chercher l'information marginale utile, permettre une piste indépendante sans réfutation
   artificielle, distinguer intérêt/faisabilité conceptuelle/exécutabilité actuelle, et considérer
   le levier IA avec les coûts réels et la distribution. Aucun exemple métier imposé, quota,
   score, nouveau champ JSON, moteur ou machine à états.

Modèle cible : les options sont comparées et mémorisées ; la prochaine observation est libre
dans les limites publiques. Continuer A, examiner B ou suspendre A dépend de la valeur de
l'information. Une expérience réelle reste un travail distinct nécessitant preuves, capacités,
permissions et budgets ; ce patch ne crée pas un parcours autonome vers l'encaissement.

## Causalité et limites

Le couplage de continuation est démontrable hors provider. La répétition API-cost du vrai run
est compatible avec ce mécanisme, mais ses requêtes exactes et décisions brutes ne sont pas
disponibles ici : on ne peut lui attribuer toute la causalité ni garantir la largeur d'un vrai LLM.
L'effet des nouvelles phrases sur le raisonnement reste à observer lors d'un run autorisé ultérieur.
Le nom OCTOPUS n'appelle aucune nouvelle interdiction lexicale. Foundation reste intacte.

La mémoire de découverte est bornée (travail précédent, décisions et stratégies pertinentes),
pas une archive intégrale dans chaque prompt. Les études historiques restent des études,
et `retained` reste un classement provisoire. Les tests vérifient droits et transitions, pas
la rentabilité des exemples ni la capacité à acquérir des clients.

## Validation

Le scénario A échoue avant le patch : le but de comparaison libre est remplacé par
`reasoning_goal(retained)`. Après le patch, ce but est transmis au travail suivant.

| Scénario scripté | Frontière vérifiée |
| --- | --- |
| A — largeur | Trois familles conservées/comparées ; objectif de comparaison préservé. |
| B — démonstration proactive | Option et faisabilité déclarée conservées ; email absent ne baisse pas le rang, aucune exécution. |
| C — masse | Volume et automatisation représentables dans la justification ; coûts/distribution inconnus, aucun profit enregistré. Une pause informationnelle n'est pas annulée pour un outil absent. |
| D — pivot/reprise | A reste proposée après épuisement sans réfutation ; reprise du même objectif avec sa décision en contexte, recherche B effectuée par le vrai runtime et ToolRegistry avec LLM/outil simulés. |
| E — exécution | A meilleure mais téléphone absent ; B disponible ne la remplace pas et aucun trigger appelé. |
| F — meilleure preuve | Nouvelle preuve observée canonique et comparaison actualisée retiennent B ; aucun encaissement déduit. |
| G — continuer A | Nouvelle observation informative sur A conservée comme prochain but, aucun pivot forcé. |
| H — livraison | Cinq moyens de livraison conceptuels absents restent des besoins d'exécution, aucune autorité inventée. |

Cas supplémentaires : priorité aux propositions courantes dans la fenêtre de douze, historique
réfuté sans fermeture de la découverte, prompt économique court, et zéro dispatch même pour
une option exécutable. **16 nouveaux cas**. Les tests historiques de crash/reprise, annotations
immuables, refus humain et non-substitution conservent leurs gardes ; seules leurs assertions
sur la réécriture du but ou l'ancien hook sans effet sont adaptées au périmètre d'observation.
Les deux tests #119 de synthèse dégradée sans recollecte font partie des vérifications.

Tests ciblés : **735 passed** (gateway/JSON, pursuit/learning/recovery, stratégie,
runtime/registry, pile intégrée, acquisition, économie/finance, Workbench).
Base exacte : **1877 passed, 97 skipped**.
Suite patch : **1893 passed, 97 skipped**, soit 16 cas supplémentaires, aucun nouvel échec.
Les 97 identités de tests ignorés sont identiques dans les JUnit de la base et du patch.
Commande complète : `python -m pytest -o addopts='' -q --junitxml=/tmp/full-tests.xml`.
Tests exclusivement scriptés/fakes sur des DataRoots temporaires, hors provider.

Capture structurale identique, sans réseau (tokens estimés par caractères / 4) :

| Messages capturés | Base : caractères / tokens | Patch : caractères / tokens |
| --- | ---: | ---: |
| Goal cold start | 1662 / 416 | 2064 / 516 |
| Planner | 3789 / 947 | 4261 / 1065 |
| Première action | 1824 / 456 | 1980 / 495 |
| Détermination | 3689 / 922 | 4196 / 1049 |

Ce sont des tailles de la même fixture, pas le coût ou la qualité d'un run réel.

Fichiers de production modifiés : `octopus/supervisor.py`, `octopus/strategy_separation.py`,
`agents/runtime.py`. Tests : nouveau `tests/test_economic_discovery.py` et assertions ciblées
de `tests/test_strategy_separation.py`. Aucun changement Foundation, #119/fallback JSON,
catalogue/provider/modèles, budget, permissions, preuve/ledger, acquisition, UI ou schéma DB.
La limite de trois cycles, six étapes par agent, 120 secondes et Web public-only reste intacte.

Le périmètre validé est la découverte large bornée : il permet d'explorer, comparer, approfondir
et pivoter. Il ne prouve ni la qualité d'un futur LLM, ni une rentabilité, ni un parcours complet
d'acquisition/exécution/encaissement ; les expériences et effets réels restent derrière leurs
frontières existantes. Prochaine observation : un run ultérieurement autorisé examine-t-il
plusieurs familles pertinentes et choisit-il la prochaine observation selon son gain réel ?

Aucun provider LLM réel, run économique réel, contact, acquisition, achat, compte, dépense,
permission élargie ou merge. Seule publication prévue : commit et PR draft explicitement demandés.

**READY FOR BROAD ECONOMIC DISCOVERY RUN** — pour le périmètre de découverte publique
bornée validé ici, sans promesse de qualité stratégique ou d'encaissement.
