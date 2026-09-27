# État actuel OCTOPUS - 2026-09-27

## Résumé

OCTOPUS est un atelier économique supervisé techniquement avancé, mais sans activité commerciale
prouvée dans le dépôt. Son noyau économique, ses tâches durables, ses garde-fous d'actions, son
journal et ses budgets existent. Le chantier Hermes + Agnes a supprimé l'ancien moteur vidéo,
ajouté une frontière Agnes minimale et renforcé le runtime de mission.

La première activité économique est prévue après une dernière phase de revue et correction. Le
blocage opérationnel actuellement démontré est SEARCH: la mission réelle 75 n'a acquis aucune
source parce que ses 12 recherches ont échoué sur le transport Bing/proxy.

## Réalité Git

- Référence GitHub `main`: `ae4d98dc9692aa10ba15051381a36809e25377df`.
- Branche locale du constructeur: `prep/astra-local-orchestration`.
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

Le registre est le seul composant directement adapté de Hermes à ce stade. MCP générique,
computer-use, raccordement complet capabilities/guardrails, portée/fraîcheur de vérification et
primitives génériques d'erreur restent à décider et, si nécessaires, à intégrer pendant la phase E.

## Validation disponible

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

1. SEARCH doit être diagnostiqué et réparé, puis le chemin vers BROWSE et les citations doit être
   vérifié.
2. Les composants Hermes restants doivent recevoir une décision actuelle fondée sur leurs
   consommateurs et les obstacles observés.
3. Une revue transversale doit vérifier permissions, budgets, reprise, idempotence, preuves et
   cohérence documentaire.
4. Le smoke test Agnes réel reste à faire séparément si la première activité utilise la vidéo.
5. Les prochains commits produit doivent être extraits de la branche constructeur sans y inclure
   l'infrastructure Astra.

## Vérité économique

Le dépôt ne contient toujours aucune preuve de paiement commercial, de livraison acceptée ou
d'utilisation client pour la première activité. Tests verts, commits, agents et missions ne sont
pas des preuves de marché.

Le golden path opérationnel reste `docs/HANDOFF_WORK.md`. La phase E prépare techniquement ce
parcours; elle ne remplace pas l'expérience client ni la décision humaine.
