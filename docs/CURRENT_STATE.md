# État actuel OCTOPUS - 2026-09-27

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

1. Mesurer maintenant SEARCH/BROWSE sur une source pertinente pour la mission économique,
   puis faire relire les citations. Le probe technique Python ne valide pas le marché.
2. Google peut ne pas répondre: conserver les erreurs observables et les autres moteurs;
   ne pas inventer un résultat ni déclencher un fallback payant.
3. Faire la revue avant transfert des commits E dans la branche produit.
4. Le smoke test Agnes réel reste à faire séparément si la première activité utilise la vidéo.
5. Les prochains commits produit doivent être extraits de la branche constructeur sans y inclure
   l'infrastructure Astra.

## Vérité économique

### Observation E5 lancée le 2026-09-27

Une seule mission réelle a été lancée depuis `dbf30d2` via `runtime.run_mission`,
business `cycle_0`, sous un run journalisé `phase_e5_observation`. Profil `zero_cost`,
plafond LLM partagé de 0 USD, allowlist `search,browse`, six étapes au maximum par
sous-agent, cible d'un signal et sélection `business_signal_relevance` avec acquisition
SEARCH/BROWSE couplée. Le goal demande au plus deux tâches de recherche; cette demande
est une consigne au planner, dont la limite déterministe reste cinq tâches.
Aucun contact, achat, publication ou test commercial ultérieur n'est autorisé.

À ce checkpoint documentaire, la sortie finale n'est pas encore disponible. Ne pas
relancer la mission: reprendre son résultat dans
`cache/astra-relay/phase-e5-mission.json` et son diagnostic dans
`cache/astra-relay/phase-e5-mission.log`. Le journal existant conserve les runs et appels.
Examiner ensuite les acquisitions, citations, rejets, coût et inconnues avant de statuer.
Un lancement n'est ni une qualification de signal ni une validation économique.

Le dépôt ne contient toujours aucune preuve de paiement commercial, de livraison acceptée ou
d'utilisation client pour la première activité. Tests verts, commits, agents et missions ne sont
pas des preuves de marché.

Le golden path opérationnel reste `docs/HANDOFF_WORK.md`. La phase E prépare techniquement ce
parcours; elle ne remplace pas l'expérience client ni la décision humaine.
