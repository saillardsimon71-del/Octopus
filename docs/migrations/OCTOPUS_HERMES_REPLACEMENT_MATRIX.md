# Matrice OCTOPUS ↔ Hermes — remplacement concret

Date initiale: 2026-09-25
État révisé: 2026-09-27

But: identifier les remplacements qui réduisent réellement la dette. Un composant Hermes n'est accepté que s'il supprime une implémentation maison ou apporte une capacité absente.

## État au démarrage de la phase E

| Composant | État réel | Décision à prendre en phase E |
|---|---|---|
| Registry d'outils | intégré et testé | conserver, vérifier son raccordement aux policies |
| Capability / permissions | modèles OCTOPUS existants, raccordement registry incomplet | intégrer seulement la frontière d'exécution nécessaire |
| Computer use | absent | intégrer uniquement si un parcours actuel le requiert |
| MCP générique | absent | créer une frontière minimale seulement avec un premier consommateur |
| Evidence / vérification | preuves économiques et gate de sources présentes | vérifier portée, fraîcheur et lien action/résultat sans seconde base |
| Retry / cooldown | présent pour LLM, dispersé ailleurs | extraire seulement si SEARCH/BROWSE/computer le justifient |
| Scheduler | queue durable présente, threads ciblés restants | conserver ou remplacer selon consommateurs observés |
| Worker / lifecycle | queue OCTOPUS durable conservée | n'extraire que des primitives manquantes et testables |
| Mémoire / skills | non intégrés | différer sans workflow économique répété et prouvé |

La phase E doit produire une décision actuelle `integrate`, `keep_octopus`, `defer` ou `reject`
pour chaque ligne P0/P1. Elle doit implémenter entièrement les décisions `integrate`; elle ne doit
pas ajouter un composant pour compléter mécaniquement la liste Hermes.

## 1. Registry d'outils

### Décisions E1/E2 du 2026-09-27 (code pinné inspecté)

Hermes vérifié à `59004a62356f3a4697ab0fe8ad5086d2b405e2a6` dans
`cache/upstreams/hermes-agent`. Cette table remplace les intentions historiques ci-dessous.

| Besoin OCTOPUS | Composant actuel | Composant Hermes disponible | Décision |
|---|---|---|---|
| SEARCH Web indépendant de Bing, Google compris | `agents/search.py`, RSS et Brave/Tavily | `plugins/web/ddgs/provider.py`, `_search_worker.py`, dépendance `ddgs==9.16.0` | `integrate`: adapter recherche seule, normalisation et isolation avec délai global; pas de parser Google/Bing maison |
| Métarecherche auto-hébergée | aucune instance configurée | `plugins/web/searxng/provider.py`, endpoint JSON | `defer`: DDGS couvre le consommateur actuel sans déployer un service |
| MCP pour acquisition | aucun serveur autorisé/configuré | `plugins/web/keyless_mcp.py`, `tools/mcp_tool_transport.py`, `mcp_tool_errors.py` | `defer`: DDGS suffit ici; pas de discovery ni de ring de services activé implicitement |
| BROWSE public et récupération de pages | `browser.acquire_public_page`: HTTP, Playwright, extraction PDF sous politique de coût | `tools/web_tools_extract.py`, `web_tools_rescue.py`, `agent/browser_provider.py` | `keep_octopus`: acquisition datée, garde-fous URL/comptes et citations déjà consommés; cloud/rescue n'enlèveraient pas ces obligations |
| Disponibilité et normalisation SEARCH | table `PROVIDERS`, enveloppe six champs | `DDGSWebSearchProvider.is_available`, `plugins/web/_common.py` | `integrate`: disponibilité locale sans réseau, résultat adapté une seule fois au contrat existant |
| Registry, toolsets et permissions | `agents.tool_registry`, allowlist, policies des handlers | `tools/registry.py` | `keep_octopus`: SEARCH est déjà raccordé; pas de nouveau toolset, discovery ou promotion de `capabilities.py` |
| Annulation et timeout natif SEARCH | appels HTTP bornés mais pas de worker DDGS | `_run_ddgs_search_bounded`, `_terminate_and_reap` | `integrate`: enfant jetable, arrêt humain OCTOPUS propagé, secrets exclus de son environnement |
| Erreurs, retry et cooldown | erreurs par provider, cooldown LLM et retry worker existants | `agent/error_classifier.py`, `retry_utils.py`, `fallback_cooldown.py` | `keep_octopus`: upstream orienté failover LLM; aucun retry de soumission ni nouvelle taxonomie nécessaire pour ce GET/search borné |
| Computer-use | BROWSE HTTP/Playwright | `tools/computer_use/permissions.py`, backend cua MCP | `defer`: aucun obstacle desktop démontré; ne pas confondre disponibilité du driver et permission OCTOPUS |
| Qualification et fraîcheur | `PublicPageRecord.fetched_at`, gate de citations, journal strategy | `agent/verification_evidence.py` (ledger de vérification de code) | `keep_octopus`: autre domaine et seconde DB; acquisition != preuve économique |
| Scheduler | queue durable et watchdog existants | `agent/periodic_scheduler.py` | `keep_octopus`: aucun blocage actuel justifiant un scheduler supplémentaire |
| Lifecycle | worker/tasks, scopes d'annulation | `agent/subagent_lifecycle.py` | `keep_octopus`: contrats dépendants de la délégation Hermes, pas nécessaires au worker SEARCH jetable |
| Skills | pas de procédure économique répétée prouvée | système de skills Hermes | `defer`: aucun consommateur actuel |
| Cerveau, mémoire générale et UI | identité, orchestration et preuves OCTOPUS | loop/planner/mémoire/UI Hermes | `reject`: autorités parallèles hors mandat |

Frontière minimale: registry OCTOPUS -> SEARCH -> adapter DDGS dérivé de Hermes ->
enveloppe OCTOPUS -> BROWSE -> qualification. Les moteurs DDGS restent dans la dépendance,
pas dans OCTOPUS. Le choix de moteurs Web exclut les backends encyclopédiques du mode `auto`
de DDGS pour préserver la politique business existante. Une URL découverte n'est pas acquise.

OCTOPUS actuel:
- `agents/runtime.py::TOOLS`
- `agents/runtime.py::_validate_tool_args`
- descriptions sérialisées par `tools_desc`
- règles/outils dispersés entre runtime, agents/tools.py, browser, stratégie.

Hermes:
- `tools/registry.py`
- entrée unique = nom, toolset, schema, handler, check_fn, requires_env, async, description, max result size.
- disponibilité sondée;
- erreurs de tool bornées au point de dispatch.

Décision:
- REMPLACER progressivement la table `TOOLS` par un registry dédié OCTOPUS inspiré de Hermes.
- Conserver les handlers existants.
- Ne pas importer plugin discovery complet lors de la première tranche.

État 2026-09-27: TERMINÉ pour la tranche registry. `agents.tool_registry.ToolRegistry` est la source
unique de description, validation, allowlist et dispatch. Les probes de disponibilité, toolsets et
discovery ne sont pas intégrés.

Critère:
- aucun double registry;
- `allowed_tools` continue à fonctionner;
- validation schema centralisée;
- tests H3 inchangés.

## 2. Modèle de capability / permissions

OCTOPUS actuel:
- `octopus/capabilities.py`
- modèle déjà propre: kind, risk, cost, secrets, OAuth/KYC, human approval;
- `octopus/browser_actions.py` contient des contraintes déterministes fortes.

Hermes:
- tool guardrails / approvals / scopes.

Décision:
- CONSERVER `octopus/capabilities.py` comme source de vérité.
- IMPORTER les patterns d'exécution/approval Hermes autour des tools, pas leur modèle identitaire.
- Le registry demande une CapabilityEvaluation avant side effect.

Critère:
- pas de second système de risk levels;
- toute action side-effect passe par la policy OCTOPUS.

## 3. Computer use

OCTOPUS actuel:
- Playwright/browser orienté web;
- `octopus/browser_actions.py` sait soumettre des formulaires web sous contraintes;
- pas de contrôle générique d'applications desktop.

Hermes:
- `tools/computer_use/`
- abstraction backend;
- cua-driver via MCP;
- AX tree;
- targeting fenêtre/processus;
- captures;
- input background;
- action verdict;
- stale snapshot protection.

Décision:
- AJOUTER une capability `computer` sur le modèle OCTOPUS existant.
- Backend initial = cua-driver/MCP.
- Ne pas remplacer le browser web par computer-use: ce sont deux capacités différentes.

Critère:
- `computer.capture`, `computer.click`, `computer.type`, `computer.press` derrière un adapter;
- aucune dépendance au loop Hermes.

## 4. MCP

OCTOPUS actuel:
- MCP déjà dépendance dans `requirements-local.txt`;
- certaines briques spécifiques MCP existent (ex. ancien WangP vidéo);
- pas encore de client capability MCP générique central.

Hermes:
- frontière MCP standard;
- exposition volontaire d'un sous-ensemble de tools.

Décision:
- CRÉER un adapter/client MCP générique minimal.
- Le registry OCTOPUS peut annoncer des tools MCP après probe.
- cua-driver devient le premier backend réel validant cette frontière.

Critère:
- un nouveau serveur MCP peut être branché sans modifier `agents/runtime.py`.

## 5. Evidence / vérification

OCTOPUS actuel:
- `octopus/journal.py` possède déjà le journal économique, strategy_evidence, résultats de bench;
- `agents/runtime.py::_verified_browse_pages` + #94 impose preuve réelle pour business signals.

Hermes:
- `agent/verification_evidence.py`
- ledger de vérification distinct de l'exécution;
- portée targeted/full;
- fraîcheur;
- état courant référencé par événements;
- ne promeut pas une preuve ciblée en garantie globale.

Décision:
- NE PAS importer une deuxième base SQLite.
- PORTER le modèle de statut/portée/fraîcheur dans le journal OCTOPUS.
- Généraliser l'idée à toute action externe: executed != observed != verified.

Critère:
- une action économique peut référencer un evidence record;
- les déclarations de succès ont un scope/freshness explicite.

## 6. Retry / cooldown / erreurs

OCTOPUS actuel:
- `octopus/llm.py` gère déjà:
  - 429;
  - Retry-After;
  - cooldown par modèle;
  - cooldown par provider;
  - erreurs de connexion;
  - fallback de modèle;
  - InvalidOutput/NoEligibleModel.
- worker gère retries de tâches.

Hermes:
- classification d'erreurs et cooldowns plus généraux, au-delà du LLM.

Décision:
- CONSERVER le code LLM OCTOPUS.
- EXTRAIRE seulement une taxonomie d'erreur/tool retry générique si elle permet de supprimer du code SEARCH/BROWSE/computer.
- surtout pas remplacer OmniRoute/catalog par Hermes.

## 7. Scheduler

OCTOPUS actuel:
- schedules persistants dans journal/tasks;
- workers et watchers utilisent encore des threads ciblés (ex. bridge cancel).

Hermes:
- `agent/periodic_scheduler.py`
- scheduler process-wide unique;
- callbacks courts;
- non-overlap par handle;
- cancellation.

Décision:
- CANDIDAT DE REMPLACEMENT pour heartbeats/watchdogs internes.
- NE PAS remplacer la file persistante de tâches/schedules économiques.

## 8. Worker / handlers

OCTOPUS actuel:
- `octopus/worker.py` = queue persistante, lease, retry, budget, human wait, memo.
- c'est une primitive économique utile.

Hermes:
- subagent lifecycle / delegates.

Décision:
- CONSERVER worker/tasks OCTOPUS.
- éventuellement reprendre les primitives heartbeat/lifecycle pour les exécutions parallèles.
- ne pas remplacer la queue persistante.

## 9. Mémoire / skills

OCTOPUS actuel:
- journal + stratégie + evidence;
- pas besoin d'une mémoire conversationnelle générale pour la finalité économique.

Hermes:
- memory/session search;
- skills auto-améliorés.

Décision:
- mémoire Hermes: NE PAS PORTER maintenant.
- skills: futur candidat pour procédures économiques validées, jamais pour remplacer les faits/journal.

## 10. Vidéo

OCTOPUS actuel:
- `octopus/media`
- `octopus/video`
- Remotion
- RunPod
- TTS/B-roll
- video worker.

Hermes:
- non pertinent.

Nouvelle cible:
- moteur externe pinné `lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b`;
- service indépendant derrière un adapter HTTP OCTOPUS minimal;
- aucune dépendance au core.

Décision:
- SUPPRIMER ancien moteur selon `VIDEO_ENGINE_REMOVAL.md`.

## Ordre de la phase E

1. reproduire et corriger les blocages opérationnels, SEARCH en premier;
2. vérifier le parcours économique complet et ses frontières;
3. statuer sur MCP, computer-use, capabilities et vérification à partir de consommateurs réels;
4. intégrer les composants nécessaires avec tests, sans autorité parallèle;
5. examiner error taxonomy, scheduler et lifecycle seulement si un défaut actuel les justifie;
6. exécuter la suite complète et actualiser cette matrice avec les décisions finales.

## Mesure de succès

La migration est réussie si le nombre de lignes/branches spéciales diminue, pas si le nombre de fonctionnalités ou d'abstractions augmente.
