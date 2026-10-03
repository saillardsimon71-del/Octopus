# Audit browser — base #133

Audit historique de la PR #134. Le catalogue, les fournisseurs et le protocole
courants sont décrits dans [OPENROUTER_CATALOG.md](OPENROUTER_CATALOG.md).

Base exacte : `78c1641d803a0c3d9cdeca93e8123b41f588c968`.

## Phase 0, avant patch

### FACTS OBSERVED (trace fournie par l'opérateur)

Task 16 `resources.account_work`, run 53, mission 54, agent 55 SOUT,
profil economical, compte fiverr-main, mandat owned_account/read.
Planification : dots-3-free. Six agent.react_step : dots-3-free, status ok,
aucune erreur API, coût rapporté zéro. Synthèse : dots-3-free. Mission incomplete.
Le fichier de mission contient cette séquence minimale ; ce ne sont pas des
résultats SQLite directement observés dans cet environnement :

| Décision | Outil / arguments fournis | Résultat intermédiaire complet |
|---|---|---|
| 1 | browser_navigate https://www.fiverr.com/ | Non disponible |
| 2 | browser_click e34, expect Dashboard page loaded, channel_id fiverr_dashboard, effect navigate | Non disponible |
| 3 | browser_click e34, expect Dashboard page loaded | Non disponible |
| 4 | browser_snapshot full=true | Non disponible |
| 5 | browser_click e33, expect Dashboard page loaded | Non disponible |
| 6 | browser_navigate https://www.fiverr.com/dashboard | Page finale hors objectif selon l'opérateur |

Le snapshot décrit expose utilisateur/avatar, Inbox, Dashboard, Buying/Selling,
paramètres et Sign out. Aucune capture demandée. Le mapping exact e33/e34 à
chaque décision n'est pas disponible : aucun libellé ne leur est attribué ici.

### CODE-RECONSTRUCTED

`resources.account_task` fixe six étapes et 120 secondes et force economical.
`run_mission` planifie via agent.plan ; SOUT/action est mappé à agent.react_step.
`build_prompts` expose les outils disponibles, demande une décision JSON et fournit
le but ; les observations précédentes reviennent via `_tool_result_view`.
En economical, `_react_prompt_context` tronque l'historique après dix messages.
La sélection `_rank_economical` utilise octopus.json, pas la qualité navigateur.
Moins de deux échantillons restent admis ; status ok mesure parsing/validation et
réponse technique. `_ineligibility` dispense OpenRouter de preuve sous economical.
Aucun changement de modèle n'est déclenché par stagnation ; seul un rappel texte
intervient après répétition. Le fallback est technique, pas cognitif.
La mauvaise valeur channel_id est rejetée par ToolRegistry avant dispatch ; le
résultat de validation n'entre pas dans status LLM, qui peut donc rester ok.
La valeur effect=navigate est interdite dans Workspace, mais pas dans le schéma.
`expect` sur un lien simple l'envoie vers `_effect`, susceptible de demander une
autorité d'effet alors que l'intention du modèle paraît être une navigation.
On ne peut pas affirmer quel refus a réellement eu lieu sans l'observation persistée.
La boucle for s'arrête à six décisions ; la synthèse dispose de résultats compacts
mais le handoff perd execution_status et toutes les actions click.

Finding additionnel : #133 annonce screenshot dans resources.account_task, mais
`builtin_handlers.account_work` reconstruit allowed_tools SANS browser_screenshot.
La disponibilité historique alléguée par le smoke ne peut donc être confirmée
pour ce chemin exact. Le patch doit réparer cette divergence réelle.

### NOT AVAILABLE

Aucune copie actuelle du DataRoot Task 16 ni accès au PC Windows. Les deux anciennes
bases locales disponibles ne contiennent pas Task 16. Prompts intégraux, résultats
intermédiaires, refs exactes, bench_results et configuration/catalogue runtime réels
ne sont pas disponibles. Les noms de clés du dashboard OpenRouter ne permettent
pas de déduire laquelle est utilisée : le code direct lit OPENROUTER_API_KEY,
OmniRoute sa propre clé. Aucun secret ni empreinte de clé n'est extrait.
Aucune reconstruction n'est présentée comme texte historique exact. Les 6,43 M
tokens et 0,03 USD sont ceux du dashboard fourni, pas du seul runtime OCTOPUS.

Cause vérifiable : compétence de format admise comme substitut à compétence
interactive ; absence d'évaluation et de repli sur non-progression. L'absence de
trace intégrale est un finding d'observabilité, pas un blocage du correctif.

### Prompts historiques : fragments reconstruits depuis le code

Source exclusive : `agents/runtime.py:build_prompts` au head de base ci-dessus.
Le squelette non conversationnel est déterministe :

```text
Tu es l'agent {role} du groupe {group}. {role_desc}
Poursuis l'objectif en utilisant les outils disponibles.
À chaque étape, choisis UNE action.
Outils disponibles : {tools_desc(allowed_tools)}
{freshness_context}{proof_rule}
Réponds TOUJOURS en JSON : soit {"tool": "<nom>", "args": {...}}
pour agir, soit {"final": "<réponse>"} quand l'objectif est atteint.
Premier message : Objectif : {goal}
```

Ce fragment est **reconstruit depuis le code**, pas un journal observé. Les branches
mémoire et terrain, les valeurs group/business/freshness, le texte exact du but
transmis au sous-agent par le planificateur, les mandats et les outils présents à
cet instant ne sont pas tous récupérables. Même avec l'input conceptuel fourni,
on ne peut donc pas restituer le prompt complet exact de chaque appel.
Les retours sont construits par `_tool_result_view`, puis insérés dans la
conversation ; leur contenu historique exact reste NOT AVAILABLE.
Les décisions fournies demeurent le cas de régression Task 16, sans inventer les
observations manquantes ni attribuer rétroactivement un score de benchmark à Dots.

## Architecture livrée

| Point | #133 | Patch |
|---|---|---|
| Micro-décision interactive | agent.react_step / octopus.json | browser.react_step / browser.trajectory |
| Qualification | JSON et santé technique, exemptions de preuve | dix scénarios browser récents, aucune exemption |
| Bootstrap | modèle disponible | commande séparée browser benchmark sur fixtures |
| Historique browser | compacté sous economical | conversation complète et captures par références |
| Stagnation | rappel textuel | détecteur générique et un remplacement borné |
| Identité | route catalogue | preuve du modèle sous-jacent, contrôle après résolution |
| Budget compte | 6 steps / 120 s | 8 initiaux / 300 s, quanta de 4, plafond 20 |
| Synthèse | statut et clicks perdus au handoff | statut, actions, traces et preuves transmis |
| Observation UI | arbre accessibility | arbre + href/expanded/disabled/checked/haspopup |
| Journal cerveau | données techniques dispersées | événements browser.controller sanitizés |

La gateway existante reste l'unique routeur. Aucun nouveau framework, panneau
Workbench, DB, ledger, mandat, critique multi-agent ou logique par plateforme.
Les missions génériques restent sur leurs tasks existants. Si une mission mixte
choisit pour la première fois une intention browser, cette intention est soumise
au contrôleur qualifié avant tout dispatch ; cela peut ajouter un appel de passage.
Le vérificateur sémantique des comptes utilise aussi la qualification browser.
Le fast-path de marqueur explicitement configuré et HumanConnection sont conservés.

## Benchmark exécutable et mesure

`python -m octopus browser benchmark --models "<IDs fixes explicitement sélectionnés>" --repeats 2 --max-cost 0 --max-requests 240`

Les modèles fixes gratuits OpenRouter et DeepSeek direct peuvent concourir selon
leurs capacités et la politique humaine. `browser.bench_step` est un task de
bootstrap explicitement pincé en profil bench ; il ne nécessite pas une preuve
browser préalable. `browser.react_step` exige cette preuve dans TOUS les profils,
y compris bench et legacy. Sans candidat qualifié : incomplete explicite ; aucun
contrôleur gratuit médiocre choisi silencieusement. Un benchmark payant exige
`--allow-paid` et son plafond explicite ; aucun n'a été exécuté pendant ce chantier.
Le plafond du benchmark ne réintroduit aucun plafond monétaire pursuit.

| Scénario | Ce qui est mesuré |
|---|---|
| affordance | recherche métier, avatar et lien direct alternatif observé |
| dynamic_menu | révéler puis exploiter des contrôles nouveaux |
| recovery | quitter une section sans information utile |
| ambiguous_dom | contrôles de même libellé, disposition expliquée par canvas |
| sufficient_dom | information textuelle suffisante, capture facultative |
| stale_refs | ref invalidée, nouvelle observation puis récupération |
| invalid_args | rejet structuré injecté, récupération |
| multi_screen | objectif nécessitant plusieurs écrans |
| language_layout | autre langue et disposition |
| vision | code de preuve présent uniquement dans les pixels du canvas |

Les pages HTTP locales ont des destinations et codes opaques aléatoires. Aucune
route compte réel, aucun effet externe. Le modèle écrit chaque prochaine action.
Le Workspace, les schémas, la gateway et le chargeur PNG de production sont utilisés.
Les seuls contrôles scriptés sont la vérité de fixture, l'injection d'erreurs et
le score. Plusieurs chemins peuvent réussir ; aucune séquence exacte imposée.

Score : 0,60 objectif atteint + code correctement rapporté ; 0,15 taux d'outils
valides ; 0,10 récupération ; 0,10 efficacité (au plus 9 actions) ; 0,05 perception
visuelle lorsqu'elle est nécessaire. Au plus 12 décisions, arrêt après cinq
observations sans nouveauté. Pour passer : score >= 0,90 ET objectif, preuve,
récupération, absence d'outil interdit, capture effective dans les deux scénarios
visuels. Demander une capture lorsque le DOM suffit n'est pas sanctionné en soi.
Le code du canvas doit figurer dans le rapport : une image jointe seule ne suffit pas.

Persistance : `bench_results`, task `browser.trajectory`, version `browser-v1`,
clé `browser.model:<identité sous-jacente>`, run/item/répétition/score/checks/coût.
Le runner produit les CSV et matrices existants et `cost_per_completed_objective_usd` ;
sans objectif achevé cette valeur est inconnue (`None`), pas zéro.
Les échecs API restent dans llm_calls ; les échecs de trajectoire restent distincts.
Une résolution inconnue ou mélangeant plusieurs modèles ne qualifie aucune route.

Qualification : dernier run de suite seulement, âge <= 14 jours, couverture des
dix scénarios, moyenne des taux de réussite par scénario >= 90 %, 100 % pour
vision/DOM ambigu/recovery/invalid_args/stale_refs, aucun outil interdit.
Deux stagnations mesurées récentes après cette suite bloquent la qualification ;
un nouveau benchmark peut la rétablir. Aucun `status=ok` ne crée de preuve browser.
Les tests ne préchargent des preuves synthétiques que dans leurs bases temporaires.

## Continuation, erreurs et honnêteté

Stagnation : deux erreurs consécutives, trois actions identiques sans nouveauté,
cycle d'observation A-B-A-B, ou trois tours sans nouvelle observation. Les hashes
normalisent le changement de refs ; URL/texte/capture contribuent à la nouveauté.
Cette nouveauté est un signal de continuation, **pas une preuve d'objectif atteint**.
Un changement d'horloge/animation peut donc encore produire de faux progrès ; le
plafond final reste nécessaire et cette limite ne doit pas être cachée.

Une seule escalade : modèle précédent exclu, candidat qualifié de qualité mesurée
strictement supérieure. Conversation et captures conservées, même scope/Workspace,
aucun reset navigateur ni répétition automatique d'effet. Détecteur réinitialisé
pour donner au remplaçant sa chance ; l'historique et les exclusions sont repris
après checkpoint. Sans candidat supérieur, arrêt incomplete ; deux candidats ayant
le même score ne sont pas présentés artificiellement comme une amélioration.
Le fallback technique provider/JSON existant reste indépendant.

Le budget initial du compte est huit actions. À sa limite, deux observations
nouvelles sur les quatre derniers tours, ou une escalade récente, accordent quatre
actions supplémentaires. Plafond général min(24, quantum initial + 12), donc 20
pour account_task. Les tests multi-écrans et de plafond vérifient la continuation
utile et la terminaison, sans prétendre que ces valeurs sont optimales en réel.

L'interface browser indique types et paramètres optionnels. `channel_id` disparaît
des descriptions LLM ; Workspace dérive le canal business/compte/page. Le paramètre
numérique reste accepté pour compatibilité avec les callers historiques, sans
créer d'autorité supplémentaire. Effect = contact|publish|edit ou omis ; ref = @eN ;
URL HTTP(S) absolue sans identifiants ; enums direction/state, types stricts et
champs inconnus refusés. Les erreurs techniques sont des observations structurées,
pas une demande humaine. Les JSON/action contracts invalides peuvent être rejetés
plus tôt dans la gateway : ce fallback technique est conservé.

Les href des liens et propriétés ARIA sont lus via batch pour un maximum de 40
refs, avec fallback sur l'arbre si le backend ne les expose pas. Un lien simple
avec `expect` reste une navigation read. Un contrôle de disclosure ARIA observé
peut ouvrir le menu sous read, sauf surface sensible/à effet ; cette classification
est une frontière d'autorité, elle ne choisit aucune destination pour le modèle.
Navigation directe autorisée ; provenance observed_or_historical ou
model_supplied_unobserved tracée, sans transformer une URL devinée en fait observé.

Le modèle choisit snapshot ou capture ; aucune capture automatique. Chaque image
présente dans l'historique est chargée depuis sa référence contrôlée vers la requête
provider, rendant vision obligatoire y compris à l'escalade. Les protections image
#133, intégrité, scope, mandat vivant et absence de base64 dans SQLite sont conservées.
La capacité screenshot omise par builtin_handlers.account_work est rétablie.

Un final browser exige objective_status, missing et preuves référencées à un step
(quote textuelle réellement présente ou hash d'image). Une finalisation sans preuve,
avec information manquante ou quote inventée devient incomplete. La complétion reste
`model_claim` ; une référence d'image est une inférence et ne valide pas mécaniquement
la vérité de toute conclusion. La synthèse reçoit les actions et execution_status.
Si l'exécution est incomplète, son récit optimiste est remplacé par le constat
incomplete déterministe et les observations partielles. Pas de critique ajouté.

## Observabilité future sanitizée

`events:browser.controller` et le dernier checkpoint `task_steps:browser.controller`
contiennent modèle catalogue/requested/resolved/provider, qualification et raison,
step/outil, arguments admis en allowlist, indicateur erreur de schéma, état technique
outil, URL avant/après, hashes observation avant/après, capture demandée et image
réellement présente dans la requête, progrès/stagnation, escalade/exclusions,
limites d'étapes, coût et total d'étapes au final. Les détails de chaque tentative
provider restent dans llm_calls, reliés par call_id.

Ni prompt intégral, ni observation brute, ni texte de formulaire/expect/filename,
ni valeur de touche arbitraire, ni credentials/query/fragment URL, ni image base64
ne sont ajoutés à ces traces. Les enums invalides sont remplacés par un marqueur.
Les mécanismes de redaction de secrets existants restent actifs. Les observations
et références de captures déjà persistées par #133 gardent leur format et leurs
contrôles. Les futurs traces suffisent à reconstruire la chaîne de décisions et
les transitions observées, sans prétendre conserver chaque contenu intermédiaire.

## Audit final : 25 réponses

1. Dots a tenu les six décisions parce que le task était agent.react_step sous
   economical : ranking générique et routes gratuites admises. Son classement
   historique exact ne peut pas être calculé sans bench_results/config runtime.
2. Le code consultait octopus.json et succès/latences récents ; absence de preuves
   suffisantes n'empêchait pas cette sélection. Les scores historiques sont inconnus.
3. JSON valide/API ok ne mesure ni affordances, récupération, vision ni objectif.
4. browser.react_step contrôle maintenant la boucle ; aucun modèle fixe imposé.
5. Preuve browser puis contraintes catalogue/provider/capacités/politique ; ranking
   des qualifiés gratuit/local avant paid, prix minimal puis fiabilité et qualité.
6. Dix scénarios, >=90 % global, cinq compétences critiques à 100 %, âge <=14 jours.
7. La compétence est un prérequis ; le coût classe ensuite les candidats admissibles.
   Santé provider/cooldowns/vision et politiques paid restent éliminatoires.
8. Un gratuit qualifié passe avant un paid équivalent ; pas de preuve fictive au bootstrap.
9. Candidat paid qualifié si politique autorisée ; zero_cost ne devient pas paid.
   Aucun qualifié : incomplete et benchmark explicite à exécuter.
10. Erreurs/répétitions/cycles/absence de nouveauté, sans décision métier codée.
11. Même Workspace/scope/conversation/observations/captures ; exclusions durables.
12. Une escalade ; stagnation suivante ou absence de meilleur candidat : arrêt propre.
13. Preuve par modèle sous-jacent et vérification requested/resolved avant retour ;
    auto-pool inconnu non qualifié, même après JSON valide.
14. Rejet strict avant dispatch, erreur structurée dans le contexte ; correction
    possible sans waiting_human. Rejets gateway restent des invalidités techniques.
15. channel_id retiré des descriptions browser LLM, types et enums rendus explicites.
16. Non : Workspace connaît scope/business/resource/page et dérive le canal ;
    compatibilité numérique conservée, ambiguïté réelle toujours refusée.
17. Liste elements allowlist avec href et attributs ARIA observés ; aucune URL fabriquée.
18. 8 initiaux, continuation par 4 sur nouveauté/escalade, maximum 20 et 300 secondes.
19. Capture choisie, vraie image provider et modèle vision qualifié obligatoire ensuite.
20. DOM suffisant peut réussir sans capture : le benchmark ne sanctionne pas cette voie.
21. Final grounded et missing ; statut/actions au handoff ; incomplete prévaut sur récit.
22. Déterminismes ajoutés : qualification/scoring (mesure), identité (provenance),
    schémas/canal (API et autorité), metadata ARIA (perception), hashes/seuils/bornes
    (gestion d'exécution), allowlist (confidentialité), grounding/statut (preuve).
    Aucun de ces contrôles ne choisit où cliquer ni quelle action métier entreprendre.
23. Aucune règle Fiverr dans le code de production ; seules la trace et ce protocole
    mentionnent la plateforme. Les fixtures sont génériques et à routes opaques.
24. mandates.py inchangé ; _guard/_channel/_effect/_edit inchangés par comparaison AST.
    Classification read corrigée et testée ; révocation/effets sensibles conservés.
25. Profil Fiverr et DataRoot Windows jamais ouverts/modifiés ; aucun compte/login,
    message/publication/paiement/provider réel ni merge pendant ce chantier.

## Fichiers et validation

Production : agents/agent_browser.py (batch), agents/deepseek.py (task/provenance),
agents/runtime.py (boucle/handoff/final), agents/tool_registry.py (API),
octopus/__main__.py (CLI), octopus/browser_workspace.py (metadata/trace/stagnation),
octopus/builtin_handlers.py (screenshot), octopus/catalog.py (task/candidats),
octopus/llm.py (qualification/ranking), octopus/resources.py (compte/verifier),
octopus/browser_bench.py (fixtures/runner).
Tests nouveaux : browser_evidence.py, test_browser_cognitive_routing.py,
test_browser_trajectory_bench.py. Adaptations de fakes existants : workspace,
visual_account_autonomy, native_human_login, delegated_resource_hub, pursuit_recovery,
foundation_start, octopus_stack_integration. Aucun skip nouveau intentionnel.

Validation finale locale : **2 160 passed, 97 skipped**, en 132,88 secondes.
Base #133 : **2 094 passed, 97 skipped** ; +66 cas, mêmes identités de skips
(comparaison des XML, aucun ajout/retrait). Dernière sélection routing :
**50 passed**. Diff whitespace vérifié propre. CI et head exact consignés
dans la PR après publication ; ne pas confondre CI attendue et CI observée.
Les scénarios hors ligne utilisent un backend et des décisions simulés ; les PNG
sont réellement décodés dans les requêtes simulées, avec trois images conservées
à l'escalade et absence de base64 SQLite. Cela valide le câblage et le scorer,
**aucune compétence de modèle réel**. agent-browser 0.26.0 est disponible, Chromium
est absent ici. Les E2E backend existants restent des skips explicitement comparés
à #133. Aucun appel LLM réel, consommation provider de ce chantier : zéro.
Foundation, supervisor/pursuit, journal/DB schema, ledger et finance non modifiés ;
HumanConnection, verify_account_page, capture_page et image_part inchangés par AST.

## Prochain smoke Windows — à exécuter après qualification réelle

1. Arrêter worker/run/Workbench ; fetch de la branche de la PR puis switch detached
   sur le head exact publié. Ne pas merger. Reprendre exactement les mêmes chemins
   Python et DataRoot utilisés au smoke #133, sans recréer/importer/copier de profil.
2. Vérifier backend via `python -m octopus browser doctor --smoke` sur page locale.
   Charger la configuration de routes déjà autorisées sans exposer les clés.
3. Dans cet environnement, lancer le benchmark gratuit ci-dessus sur les modèles
   configurés compatibles. Deux répétitions donnent 20 trajectoires par candidat.
   Vérifier les résultats réels, resolved identity, couverture/vision et qualification.
   Ne pas injecter les preuves synthétiques des tests. Sans candidat qualifié, garder
   incomplete ; choisir explicitement un autre candidat à benchmarker.
4. Conserver le compte fiverr-main, la session et le mandat owned_account/read
   existants. Aucun nouveau login. La qualification s'applique aussi au vérificateur
   sémantique : ne pas le lancer avant que son contrôleur soit disponible.
5. Mission libre : observer services, commandes, évaluations, solde, performance,
   messages et restrictions ; les absences doivent être dites inconnues. Ne donner
   ni clics, ni route interne, ni consigne de capture ni tutoriel de plateforme.
6. Vérifier events browser.controller, llm_calls, task_steps, execution_status,
   preuves et coût. Si stagnation, une escalade supérieure observable, même session
   et aucune répétition d'effet ambigu ; sinon arrêt incomplete honnête.
7. Acceptance : informations utiles réellement acquises, affordances comprises,
   récupération, capture pertinente si nécessaire, absence de boucle/invention,
   mandat read respecté. Une suite verte n'est pas cette preuve Windows.

Verdict de livraison : **PARTIAL — qualification réelle des modèles et smoke
Windows restant à exécuter ; Chromium absent dans cet environnement.**
Le défaut structurel est corrigé et les tests hors ligne valident la mécanique.
Aucun modèle réel n'est déclaré compétent sur la seule base de ces tests.
