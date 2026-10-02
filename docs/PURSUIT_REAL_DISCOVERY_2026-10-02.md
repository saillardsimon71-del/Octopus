# Pursuit — découverte réelle, 2026-10-02

Base exacte : `7e60c9f4c5a07b356102e249a17198e2e92b41c2` (#120).
Audit et design écrits avant les modifications de production.

## Faits observés

L'opérateur rapporte un nouveau run réel qui reprend directement API-cost après #120,
puis confond la fin d'une recherche sur cette piste avec l'absence de toute observation
économique utile. Les requêtes brutes et le DataRoot réel ne sont pas disponibles ici.
On ne peut donc pas établir une cause exclusive de cette sortie qualitative.

Dans le code, #120 préserve effectivement next_goal et bloque les dispatchs de stratégie.
Mais `_pursuit_mission` utilise uniquement determination ; business_signal_focus reste faux.
Le rapport précédent, douze observations, stratégies, décisions et six études de capacité
restent injectés sans distinction découverte/validation. Le mode multi-signaux existant
n'est donc pas actif, ni son replan unique, ni son intention SEARCH_PURPOSE_BUSINESS.

Fixture synthétique identique avant/après, historique entièrement API-cost (aucun provider) :
sur la base, goal **38 692 caractères / ~9 673 tokens**, état **37 411 caractères** ; champs
portant A **36 872 caractères, 98,6 % de l'état**, **430 mentions** de API-cost.
Travail précédent 16 336, stratégies 5 045, décisions 5 525, études 9 966 caractères.
Cette proportion mesure les champs historiques de la fixture, pas l'intelligence du modèle,
ni la répartition exacte du vrai run. La simple liberté textuelle de pivot ne corrige pas
cette dominance contextuelle.

`business_signal_focus` est adapté à la collecte : sources acquises, quatre citations
littérales de la même acquisition, gate déterministe, revue indépendante et un replan borné
si le planner concentre une mission multi-signaux dans une seule sous-tâche. Deux limites :
il exige `browse`, absent de l'allowlist pursuit ; son langage est orienté acheteur/douleur
B2B et sa synthèse standalone interdit les stratégies. La présence du champ buyer n'exclut
pas techniquement un autre acteur ; c'est surtout son sens et les types proposés qui biaisent.

`browse` peut lire des comptes hors pursuit. L'exposer sans appliquer browser_public_only
serait un élargissement involontaire : son chemin doit rester anonyme même avec cookies
persistés. L'acquisition publique garde ses protections HTTP/redirections et, pour les PDF,
son gate de quota gratuit ; aucune dépense implicite n'est autorisée.

## Hypothèses causales

Ancrage par la projection détaillée et absence de mission multi-signaux sont démontrés
structurellement. Leur contribution au résultat du vrai LLM reste une inférence corroborée
par le récit, pas une mesure contrôlée. Determination=True n'est pas en soi une erreur :
la collecte multi-signaux peut précéder la même synthèse qui choisit la prochaine observation.

La fin d'une fenêtre locale ne réfute pas une hypothèse et ne ferme pas l'espace économique.
L'action pause déjà déclarée suffit à constater la fin de cette fenêtre ; aucune analyse
lexicale fragile du rationale, aucun booléen économique inventé et aucun classifieur supplémentaire.

## Design minimal retenu

- Intention `discovery` ou `validation` dans les inputs/outputs/checkpoints des tâches
  existantes, sans état DB supplémentaire. Cold start et reprise d'une pause propre : discovery.
  Une continuation ciblée : validation. Une continuation interrompue uniquement par la borne
  des trois cycles reste ciblée à la reprise. Une collecte dégradée reprend son intention et
  ses acquisitions ; aucune découverte neuve ne remplace la synthèse manquante.
- Discovery active business_signal_focus, target **2** : plus petit ensemble permettant une
  comparaison ; deux voies de six étapes peuvent acquérir plusieurs sources sans dix agents.
  Ce target vise la collecte, sans quota de businesses ni obligation d'inventer des signaux.
  Planner, outils, durée, budget et gate restent ceux du runtime existant.
- Projection discovery : conclusion locale explicitement attribuée au modèle, références
  des tâches/hypothèses, quelques options compactes et apprentissages factuels. Aucun paquet
  de douze observations, justification économique répétée ou étude de capacité détaillée.
  Les données canoniques ne sont ni effacées ni invalidées. Validation conserve le contexte détaillé.
- Pause d'une validation achevée, sans frontière humaine/finance et avec cycles restants :
  retour discovery dans la tâche suivante. Pause d'une discovery : pause possible, puis nouvelle
  discovery lors d'une reprise ultérieure. Aucun choix artificiel du moins mauvais signal.
- Contrat réutilisé : buyer désigne l'acteur qui finance une monétisation, distinct de l'utilisateur ;
  les besoins/activités et sources de monétisation ne sont pas limités aux douleurs SaaS.
  Ajout minimal d'un type monétisation, aucune taxonomie de businesses. Citations, sources et
  validation ne sont pas assouplies ; le gate d'acquisition reste inchangé.
- Synthèse combinée : signaux sourcés puis stratégies/hypothèses et prochaine observation,
  sans contradiction entre deux formes JSON exactes. Les signaux ne sont pas des stratégies.
  La revue reste une mesure et ne vaut ni permission, ni preuve de rentabilité.
- Lecture publique `browse` ajoutée à l'allowlist, avec browser_public_only appliqué dans
  son handler ; aucun compte, canal actif, capacité ou permission nouvelle.

## Audit transversal et réponses aux quinze questions

1–5 : #120 lève le verrou de continuation, mais le contexte détaillé continue à sélectionner
implicitement A. Travail précédent, stratégies et décisions doivent être des leçons compactes
en découverte, tandis que les mêmes données restent consultables dans la base et utiles en validation.

6–9 : pursuit ne passait simplement pas les options business_signal du runtime ; aucun besoin
de second moteur. La primitive convient, sous réserve des incompatibilités browse/JSON/acteur
ci-dessus. Deux signaux visés, six étapes par voie et une replanification maximum ; le plafond
USD 0.20 reste vérifié avant chaque vrai appel. Les fakes ne prouvent pas une qualité à ce prix.

10–13 : l'intention de la tâche et sa décision pause/continue suffisent, sans nouveau lifecycle.
Une pause de validation clôt une fenêtre locale ; elle ne devient ni refutes, ni demande de
permission. La mémoire canonique reste complète, sa projection est adaptée au prochain travail.

14–15 : budget/durée/étapes/trois cycles bornent le brainstorming ; mission multi-signaux et
retour discovery après fenêtre locale évitent le tunnel architectural. Les marchés, requêtes,
sources, offres et stratégies restent choisis par le modèle, dont la qualité reste à observer.

Les autres modules audités ne nécessitent pas de changement : strategy/learning_context
admettent seulement des évaluations et preuves canoniques ; strategy_separation ignore
l'inventaire dans le rang et conserve #120 ; capability_acquisition ne fait qu'une étude dans
pursuit ; economy conserve inconnu, inconclusive et cash réellement observé ; llm garde
#119 et ses budgets ; ToolRegistry et search gardent leurs gardes ; Workbench projette les
résultats en lecture seule. Foundation n'a pas besoin d'être changée.

## Validation et limites restantes

Base : **1893 passed, 97 skipped**. Aucun provider ni run réel.
Les scénarios testent les chemins permis, pas une diversité sémantique déterministe ou la
rentabilité. Aucun score d'intelligence. Un modèle peut encore proposer deux signaux voisins :
le replan et le contrat encouragent l'indépendance, sans oracle sémantique ni catalogue forcé.

## Mesures reproductibles avant/après

Même fixture et même état initial, aucune entreprise imposée en production. Le script
`tests/pursuit_discovery_capture.py REPO DOSSIER_SORTIE` capture la projection sur chacun
des checkouts ; `tests/pursuit_prompt_capture.py` capture les couches froides du runtime.
Tokens estimés par caractères/4, sans tokenizer provider. Ces mesures ne sont pas un benchmark LLM.

| Projection de reprise API-cost | Base #120 | Patch |
| --- | ---: | ---: |
| Goal, caractères | 38 692 | 2 774 |
| Goal, tokens estimés | 9 673 | 694 |
| État JSON, caractères | 37 411 | 1 305 |
| Champs historiques portant A, caractères | 36 872 | 736 |
| Part de ces champs dans l'état | 98,6 % | 56,4 % |
| Part de ces champs dans le goal complet | 95,3 % | 26,5 % |
| Mentions API-cost | 430 | 7 |
| Travail précédent, caractères | 16 336 | 250 |
| Stratégies enregistrées, caractères | 5 045 | 486 |
| Décisions, caractères | 5 525 | 2 (`[]`) |
| Études de capacités, caractères | 9 966 | 2 (`[]`) |

Cette fixture n'a aucune expérience formelle vérifiée ; le champ vaut `[]` des deux côtés.
Des apprentissages formels, leurs cash/coûts/statuts et références de preuve restent projetés
lorsqu'ils existent. Les assertions learning protègent cette différence avec un simple rapport.
A reste donc visible ; on réduit surtout la taille et les répétitions de son historique.

| Capture cold start, entrée en caractères / tokens estimés | Base #120 | Patch |
| --- | ---: | ---: |
| Goal | 2 064 / 516 | 2 278 / 570 |
| Planner (dernier appel capturé) | 4 261 / 1 065 | 7 159 / 1 790 |
| Première action de sous-agent | 1 980 / 495 | 4 411 / 1 103 |
| Determination | 4 196 / 1 049 | 8 054 / 2 014 |

Le dernier planner est le replan dans le patch : les lignes ne sont pas une somme de coût.
L'activation du contrat de preuve allonge les couches froides ; il n'y a pas de promesse
de diminution universelle des tokens. La réduction porte sur l'historique ancrant de reprise.
La sortie determination reste plafonnée à **1 600 tokens** : aucun changement gateway.

Mesure du scénario 1 réel supervisor/runtime/gateway, avec seulement transport et Web simulés :

| Étape | Appels | Tokens d'entrée estimés, cumulés | Tokens de sortie estimés, cumulés | Coût fictif USD |
| --- | ---: | ---: | ---: | ---: |
| Planner + unique replan | 2 | 3 355 | 67 | 0,002 |
| Sous-agents (deux voies, trois appels par voie) | 6 | 7 648 | 100 | 0,006 |
| Determination | 1 | 2 350 | 536 | 0,001 |
| Revues des signaux | 2 | 746 | 52 | 0,002 |
| Total | 11 | 14 099 | 755 | 0,011 |

Chaque réponse fictive est sous sa limite de sortie. Le coût est fixé à 0,001 USD par
appel par le fake et réellement agrégé par le journal/Workbench ; il ne prédit aucun tarif
ou comportement provider. Test limite : après 0,199 USD fictif déjà compté, une estimation
maximale de 0,002 USD bloque le prochain appel AVANT transport. Plafond pursuit : 0,20 USD.

## Scénarios et recovery

`tests/test_pursuit_real_discovery.py` ajoute quinze cas, dont les douze scénarios demandés :

| Scénario | Vérification structurelle hors provider |
| --- | --- |
| 1, cold start | Un seul plan initial reçoit un unique replan ; deux voies acquièrent deux sources, deux signaux qualifiés et deux revues. |
| 2, historique ancrant | Projection < 3 500 caractères, ≤ 10 mentions, conclusion attribuée au modèle ; aucune preuve fabriquée. |
| 3, reprise de A | Même objectif, collecte B/C ; sortie historique, preuves et statut proposé de A conservés. |
| 4, B2B proactif | Démonstration avant contact proposée par le LLM scripté ; email absent, aucun dispatch. |
| 5, audience/affiliation | Payeur différent de l'utilisateur représentable, rémunération citée et sourcée. |
| 6, masse IA | Faible commission unitaire et volume restent envisageables ; coûts inconnus, aucun cash inventé. |
| 7, commerce | Signal qualifié ; publicité, fournisseur, paiement et plateforme manquants ne suppriment pas l'intérêt conceptuel. |
| 8, A informative | Continuation ciblée et reprise après borne de cycles restent validation ; aucun pivot forcé. |
| 9, A épuisée | Discovery → validation → discovery dans les trois cycles et le même plafond ; pause modèle brute conservée. |
| 10, non-substitution | A mieux classée avec capacité absente ; B exécutable ne la remplace pas pour exécution. |
| 11, nouvelle preuve | Test intégré #120 de preuve canonique et reclassement de B conservé. |
| 12, rien de convaincant | Discovery peut faire pause sans stratégie forcée ; reprise ultérieure en discovery. |

Recovery supplémentaire : interruption avant soumission de la synthèse, après les deux
acquisitions terminées. Le checkpoint conserve plan/résultats/intention/collect_complete.
Après reap/reprise, seuls determination et deux revues sont appelés ; aucune replanification,
recherche ou navigation rejouée. Les results restent identiques, la tâche finit done.
Le flag factuel resumed_collection empêche cette récupération de lancer une nouvelle collecte
à la place du travail manquant. Les régressions #119 (done_degraded/synthesis_unavailable,
fallback JSON, coûts, racine de run, avec/sans checkpoint) passent également.

Trois contrôles supplémentaires protègent browse public : cookies enregistrés ignorés,
task boundary réellement lu dans le worker, refus si l'autorité ne peut être lue ou si
la session a déjà lu un compte. Les protections d'adresses privées restent actives.

Quatre régressions structurelles (1/2/3/9) exécutées sur une archive de la base exacte échouent
comme attendu. Les échecs montrent l'absence de collecte/projection/intention nouvelle,
pas une mesure de qualité du modèle. Les tests historiques adaptés concernent les libellés,
l'allowlist browse public et la projection ; les preuves/cash/permissions ne sont pas affaiblis.
Les anciens tests de recovery mono-voie fixent target=1 ; le nouveau recovery vérifie target=2.

## Portée de la décision

Deux fichiers de production seulement : `octopus/supervisor.py` et `agents/runtime.py`.
Pas de modification de Foundation, llm/#119, strategy_separation, acquisition, economy,
ToolRegistry, routing, providers, modèles ou permissions. Aucun schéma DB nouveau.
La collecte reste publique et anonyme, bornée aux observations ; aucune expérience active
ou acquisition réelle de capacité n'est autorisée par cette boucle.

Limites : les acquisitions peuvent échouer, le modèle peut rester voisin d'une niche,
ou ne produire aucun signal qualifiable ; target=2 est un objectif, pas un gate de diversité.
Le budget peut arrêter la découverte avant deux signaux. Une permission réelle garde sa
frontière humaine. Le verdict de pause ne constitue pas une preuve d'épuisement global.
Le coût et la qualité du vrai LLM sont inconnus ; seul un prochain essai autorisé pourra
les établir. Aucun vrai provider, run économique, dépense, compte, contact, permission,
acquisition de capacité ou merge n'a été déclenché ici. Seuls commit/PR GitHub demandés
sont publiés, sans déploiement manuel ni publication business.

## Résultats de validation

- Base exacte, suite complète : **1 893 passed / 97 skipped**, 63,73 s.
- Patch, tests ciblés pursuit/business_signal/learning/recovery/strategy_separation/
  capability_acquisition/runtime/gateway/compute_finance/economy/strategy/web_guard/
  Workbench/stack : **807 passed**, 22,17 s.
- Patch, suite complète : **1 908 passed / 97 skipped**, 63,00 s.
- Comparaison des identifiants JUnit : les 97 skipped sont exactement les mêmes ;
  aucun nouvel échec ou skip. Différence : quinze cas supplémentaires.
- `git diff --check` passe. Diff des fichiers protégés : vide.

Commandes : `python -m pytest -o addopts='' -q --junitxml=RESULTATS.xml`, dans chacun
des deux checkouts ; pour la sélection ciblée, les fichiers du périmètre ci-dessus.
Transport LLM et Web simulés, DataRoot temporaire, fake transport fail-closed.

**READY FOR REAL BROAD DISCOVERY** — aptitude structurelle à un prochain essai autorisé,
pas affirmation de réussite qualitative ou économique de cet essai. Aucun run réel lancé.
