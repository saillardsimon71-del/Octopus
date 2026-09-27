# État actuel OCTOPUS - 2026-09-27

## Phase F — readiness au checkpoint

Verdict provisoire : **NOT READY**, validation complète du nouvel état encore attendue.
Le baseline hôte sur `ad95f22c9f82cadb2cf3a1b320e9b9a7b47676b3` a terminé avec exit=0
(`cache/astra-relay/baseline-pytest.log`) ; il ne valide pas les modifications suivantes.

Contaminations reproduites : une mission sans business ni run parent injectait l'identité
Podalux et une description affirmant des comptes connectés ; `NEXT_STEPS` prescrivait encore
le pilote CSV. Le défaut de mission est désormais `octopus`, les scopes explicites et parents
sont conservés, et le handler `podalux.mission` choisit explicitement son scope historique.
Le protocole distingue maintenant le démarrage neutre de son exemple CSV historique.

Inspection SQLite en lecture seule : le journal réel contient des objectifs actifs sous
`octopus` et `cycle_0`, ainsi que des expériences historiques en cours sous `octopus` et
`accessibility_outreach`. La base legacy contient 33 souvenirs et 37 handoffs. Rien n'a été
supprimé, déplacé ou activé. Le handler ORBIT ne joint un contexte stratégique que sur
références explicites ; les handoffs du runtime proviennent des sous-tâches de la mission
courante. Les déclarations de ressources ne sont pas automatiquement injectées en objectifs.

Canari déterministe : historique stratégique, mémoire, messages et ressources semés dans
des DB temporaires ; capture des prompts planner, agent et synthèse. Échec observé avant
correction pour le business omis, puis succès pour business omis, neuf et `octopus`.
Les cas de scope legacy explicite et hérité restent testés. Ce canari vérifie l'injection
de contexte, pas les choix d'un modèle live ni une preuve économique.

Validation exécutée sans réseau ni ressource payante :
- 320 tests réussis : prompt boundaries, runtime ReAct, business signal evidence,
  strategy, economy, actions et resources ;
- 197 tests réussis, 8 ignorés : prompt boundaries avec deux tests de scope supplémentaires,
  tasks/worker, SEARCH structuré/DDGS/coûts et browser integration (tests browser ignorés).
Ces groupes se recouvrent ; ne pas additionner leurs nombres comme des tests distincts.
Diff relu et `git diff --check` sans erreur.

Prochaine étape constructeur : checkpoint hôte, puis demander la suite complète via
`validation.json` dans un appel Astra frais, avant le verdict final. Aucun dry run lancé.
Le parcours proposé reste un business neuf, une mission neutre, SEARCH/BROWSE seulement,
politique `zero_cost`, citations acquises puis revue humaine. Les anciennes expériences
restent accessibles à un appel explicite d'economy/status/drive ou de mémoire : ce n'est
pas un parcours de cold start. Les entrées legacy agent/message restent historiques.
La disponibilité live du LLM gratuit (429 en E5) n'est pas démontrée par ces tests ; une
indisponibilité doit arrêter la mission sans fallback payant. Prochaine observation après
validation : source réellement acquise et signal qualifié, ou résultat `inconclusive`.

## Résumé

OCTOPUS est un atelier économique supervisé techniquement avancé, mais sans activité commerciale
prouvée dans le dépôt. Son noyau économique, ses tâches durables, ses garde-fous d'actions, son
journal et ses budgets existent. Le chantier Hermes + Agnes a supprimé l'ancien moteur vidéo,
ajouté une frontière Agnes minimale et renforcé le runtime de mission.

La mission réelle 75 reste inconclusive. Son blocage proxy a été reproduit hors OCTOPUS:
la sandbox sans réseau injecte HTTP_PROXY/HTTPS_PROXY/ALL_PROXY vers 127.0.0.1:9.
La configuration Astra autorise maintenant le réseau; aucune neutralisation des proxies
n'a été ajoutée à SEARCH. Un probe natif Codex avec profil workspace et réseau activé
confirme HTTP/HTTPS 200 et absence de ces proxies. L'autorisation reste limitée aux lectures.

La reprise E1/E2 adapte le provider DDGS de Hermes pinné, avec timeout global de 30 secondes,
annulation humaine et environnement enfant sans secrets provider. SEARCH utilise DDGS avant
les API sous politique de coût et les RSS historiques. Les moteurs et leur agrégation restent
dans `ddgs==9.16.0`, pas dans du code Google/Bing propre à OCTOPUS. BROWSE reste distinct.

Probe réel du 2026-09-27: SEARCH via registry a trouvé la documentation Python avec DDGS;
BROWSE a acquis `https://docs.python.org/3/tutorial/index.html` en HTTP 200,
méthode `http:html_main`, 6250 caractères, `usable=true`. Aucun LLM, effet métier ni
evidence stratégique créé. Google seul via DDGS a renvoyé `No results found`:
intégration présente, disponibilité effective Google non démontrée par ce probe.

## Réalité Git

- Référence locale `origin/main`: `ae4d98dc9692aa10ba15051381a36809e25377df`.
- `main` local: `5301a27b8f400041e38eef8b2f0afd73a7b23e6f`.
- Branche locale du constructeur: `prep/astra-local-orchestration`.
- Base de la reprise E: `b5032cae97947e9f83347f1564f0a5fab755b6ed`, arbre initial propre.
- Dernier checkpoint produit avant préparation de la phase E:
  `2952db4781ec8ad60b6958301a2ab48d388fdcd4`.
- Branche produit publiée: `feat/hermes-agnes-product`.
- HEAD produit publié: `57cd3e4b4cce4de5d9d60f9fb755668fe91e1a33`.
- Pull Request produit: GitHub #106 vers `main`.
- PR #105 reste séparée et contient l'environnement de construction Astra.

La branche du constructeur contient volontairement à la fois son infrastructure locale et les
checkpoints produit. Elle sert à construire, pas à fusionner telle quelle dans `main`. Les nouveaux
changements produit devront rester séparables puis être transférés dans la branche produit relue.

## Architecture active

```text
Humain: objectif, limites, permissions et revue
  -> strategy: objectifs, hypothèses, expériences, preuves et décisions
  -> tasks/worker: travail durable, reprise et attente humaine
  -> agents/runtime: missions, outils bornés et acquisition
  -> actions: effets externes autorisés et idempotents
  -> economy: allowances, demandes de dépense et ledger unique
  -> journal SQLite: état durable et audit
  -> economy outcome: livraison, client, cash, coûts, temps et inconnues
```

Frontières canoniques:

- `strategy`: état épistémique et décisions;
- `tasks` et `worker`: orchestration durable;
- `journal`: persistance locale;
- `economy`: argent, allowances et ledger;
- `actions`: effets externes contrôlés;
- `resources`: ressources réellement disponibles;
- `llm`: sélection, validation, coût et budget LLM;
- `compute`: allocation de compute;
- `agents/runtime`: orchestration de mission et utilisation des outils.

Il ne doit exister ni second ledger, ni second journal, ni second planner, ni second gateway LLM.

## État des migrations

### Phase B - ancien moteur vidéo

Terminée dans la branche produit:

- suppression de `octopus/video`, `octopus/media`, Remotion, video_worker et des outils associés;
- suppression des handlers, commandes et workflows exclusivement vidéo;
- conservation des primitives économiques, compute et historiques encore consommées;
- suite Python générale découplée des dépendances vidéo.

### Phase C - Agnes

Implémentée et testée:

- service Agnes externe et pinné, jamais recopié dans OCTOPUS;
- adapter HTTP loopback dans `octopus.agnes`;
- probe, statut, soumission simple, arrêt et référence vidéo;
- soumission et arrêt derrière `actions`, permissions, allowance et idempotence;
- réponses ambiguës sans retry aveugle;
- tests HTTP déterministes sans génération réelle.

Non encore validé: démarrage réel du service pinné, génération live, artefact final et coût observé.
Ce smoke test est réservé à une session séparée avec autorisation humaine.

### Phase D - Hermes et fiabilité des missions

Intégré:

- `ToolRegistry` unique inspiré du pattern Hermes;
- validation centralisée des paramètres et allowlist avant handler;
- erreurs bornées et annulation humaine préservée;
- statuts explicites d'exécution et de synthèse;
- arrêt propre sur budget, annulation ou indisponibilité LLM;
- budget LLM cumulé sur les runs imbriqués;
- qualification de signal exigeant SEARCH, BROWSE et citations acquises;
- aucune evidence stratégique automatique sans mission complétée et signal sourcé;
- statuts `source_supported`, `inconclusive` et `not_evaluated`;
- alias CLI `--llm-budget-usd`.

Le registre et le provider SEARCH DDGS sont adaptés de Hermes. La carte E1/E2 de
`OCTOPUS_HERMES_REPLACEMENT_MATRIX.md` statue sur les composants P0/P1: permissions,
acquisition, preuves, scheduler et lifecycle OCTOPUS conservés; SearXNG, MCP et computer-use
différés sans consommateur supplémentaire. Le modèle de capabilities n'est pas promu en autorité.

## Validation disponible

Sur le diff E de la branche constructeur, le 2026-09-27:

- baseline hôte de `b5032ca` réutilisé, sans réexécution;
- 191 tests ciblés passent en 43,56 s, dont frontières SEARCH/BROWSE, preuves,
  coûts, permissions, timeout natif, annulation et secrets du worker;
- suite complète finale exécutée une fois: **1268 passed en 203,96 s**, code 0;
  commande `.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts=''`,
  log `cache/astra-relay/phase-e-final-full.log`;
- diff relu, `git diff --check` passe; aucune suppression/relaxation de test valide;
- HTTP/HTTPS et SEARCH DDGS -> BROWSE validés aussi en sandbox native avec un profil
  workspace réseau activé: même page Python, 6250 caractères, HTTP 200, `usable=true`;
  aucun LLM ni écriture de données métier.

Les modifications E ont été commitées par l'hôte dans `057fc6e`, puis le routeur E5
dans `dbf30d2`. Elles ne sont pas fusionnées dans `main`.
Le changement `.codex/config.toml` appartient au constructeur et doit être exclu du
transfert produit. La lecture seule réseau est une limite du mandat, pas un filtre HTTP
implémenté par `network_access=true`. Cette validation ne prouve pas une opportunité client.

Sur la branche produit isolée:

- suite complète locale: 1246 tests passés;
- sélection migration: 341 tests passés;
- revue indépendante: aucun défaut Critical, Important ou Minor;
- CI GitHub OCTOPUS: `targeted-tests`, `contract-and-worker` et
  `local-browser-and-control-plane` réussis.

Le statut Vercel de la PR échoue parce qu'une intégration de déploiement reste attachée au dépôt
alors que la surface vidéo/Remotion a été supprimée. `main` n'est pas protégé par ce contrôle. Ce
statut n'est pas une preuve d'échec du noyau Python, mais l'intégration externe doit être nettoyée.

## Mission réelle 75

La tâche durable est `done`, avec:

- `execution_status=incomplete`;
- `synthesis_status=validated`;
- `opportunity_status=inconclusive`;
- 12 SEARCH, 0 BROWSE;
- 16 appels LLM, dont 14 réussis et 2 sorties JSON invalides;
- coût journalisé: 0 USD;
- aucune evidence stratégique créée.

Les 12 SEARCH ont échoué sur Bing/proxy. Les garde-fous ont correctement refusé de transformer le
rapport en opportunité prouvée. Ce test valide la persistance et les barrières de preuve, pas la
capacité actuelle à rechercher une opportunité.

## Freins connus avant la première activité

1. E5 a acquis une demande réelle, mais fermée: aucune piste actuelle retenue. Le prochain
   test proposé exige une demande ouverte et une revue humaine avant contact; ne pas relancer
   la mission automatiquement. Voir les citations et inconnues ci-dessous.
2. Google peut ne pas répondre: conserver les erreurs observables et les autres moteurs;
   ne pas inventer un résultat ni déclencher un fallback payant.
3. Faire la revue avant transfert des commits E dans la branche produit.
4. Le smoke test Agnes réel reste à faire séparément si la première activité utilise la vidéo.
5. Les prochains commits produit doivent être extraits de la branche constructeur sans y inclure
   l'infrastructure Astra.

## Vérité économique

### Observation E5 du 2026-09-27 — inconclusive

Une seule mission réelle a été lancée depuis `dbf30d2` via `runtime.run_mission`,
business `cycle_0`, sous un run journalisé `phase_e5_observation`. Profil `zero_cost`,
plafond LLM partagé de 0 USD, allowlist `search,browse`, six étapes au maximum par
sous-agent, cible d'un signal et sélection `business_signal_relevance` avec acquisition
SEARCH/BROWSE couplée. Le goal demande au plus deux tâches de recherche; cette demande
est une consigne au planner, dont la limite déterministe reste cinq tâches.
Aucun contact, achat, publication ou test commercial ultérieur n'est autorisé.

Résultat du run 328: `execution_status=llm_unavailable`, `synthesis_status=degraded`.
Les routes gratuites ont atteint des limites 429; aucun fallback payant déclenché.
Le journal contient 7 appels LLM (5 réussis, 2 erreurs), coût enregistré 0 USD.
Le statut technique `done` des runs ne signifie pas que la mission a réussi.
Sortie conservée: `cache/astra-relay/phase-e5-mission.json`; diagnostic:
`cache/astra-relay/phase-e5-mission.log`. Ne pas relancer cette mission automatiquement.

Quatre SEARCH via DDGS sans erreur et quatre BROWSE ont ouvert une seule page distincte:
[Spreadsheet Product Data Extraction](https://www.fr.freelancer.com/projects/data-cleansing/Spreadsheet-Product-Data-Extraction),
acquise à `2026-09-27T14:38:51.322535+00:00`, HTTP 200, méthode `http:html_body`,
27 950 caractères. Les moteurs individuels derrière DDGS ne sont pas attestés dans la sortie.
Extraits littéraux vérifiés dans cette acquisition:

- « I have multiple spreadsheets that hold product details scattered across different tabs and formats. »
- « Completion will be accepted when I receive the consolidated file, error-free and ready for immediate upload into our system. »
- « ₹100-400 INR / heure » et « Fermé ».

Cela soutient l'existence d'une demande publiée de consolidation de données produit,
avec budget annoncé; cela ne prouve ni paiement ni besoin encore disponible. Cette piste
est rejetée comme opportunité immédiate parce que l'annonce est fermée. Les autres liens
SEARCH n'ont pas été ouverts: aucune conclusion sur eux. Identité/accès à un acheteur actuel,
fichiers, volume, marge, prix acceptable et canal utilisable restent inconnus.
Aucun signal n'a été qualifié automatiquement; la synthèse et sa revue n'ont pas abouti.
Conclusion de revue: `inconclusive`, aucune piste retenue.

Obstacle observé et correction E5: le titre utile apparaissait au caractère 7 564,
après la limite de 6 000 caractères de la vue BROWSE. Le modèle ne voyait que les menus.
`runtime._tool_result_view` affiche maintenant une fenêtre littérale autour d'un titre
tardif, avec offset et indicateur de troncature. L'acquisition complète et le gate de
preuves restent inchangés. Relecture locale de la même acquisition: budget, statut fermé
et besoin visibles dès la fenêtre commençant au caractère 7 364. Aucun nouvel appel LLM.
Cette heuristique n'est pas un extracteur universel; sans titre trouvé, le préfixe est conservé.

Validation: 5 nouveaux cas échouaient avant correction; 225 tests ciblés passent en 11,86 s
(`cache/astra-relay/phase-e5-targeted.log`); diff relu et `git diff --check` réussi.
La suite complète de 1 268 tests précède cette correction et ne la valide pas.
La validation complète finale de ce nouvel état reste à exécuter après checkpoint.

Prochain test économique proposé, non exécuté: sur une demande encore ouverte, faire
valider humainement le destinataire, le canal et une offre de consolidation d'un petit lot
CSV avec critères d'acceptation et prix explicites. Aucun contact sans autorisation humaine;
ne pas extrapoler le budget INR de cette annonce fermée au pilote français.

Le dépôt ne contient toujours aucune preuve de paiement commercial, de livraison acceptée ou
d'utilisation client pour la première activité. Tests verts, commits, agents et missions ne sont
pas des preuves de marché.

Le golden path opérationnel reste `docs/HANDOFF_WORK.md`. La phase E prépare techniquement ce
parcours; elle ne remplace pas l'expérience client ni la décision humaine.
