# Matrice OCTOPUS ↔ Hermes — remplacement concret

Date: 2026-09-25

But: identifier les remplacements qui réduisent réellement la dette. Un composant Hermes n'est accepté que s'il supprime une implémentation maison ou apporte une capacité absente.

## 1. Registry d'outils

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

## Ordre de migration recommandé

1. retirer moteur vidéo;
2. remettre suite générale au vert;
3. introduire registry central sans changer les handlers;
4. introduire MCP générique;
5. brancher cua-driver;
6. brancher policy/capabilities;
7. généraliser evidence;
8. seulement ensuite examiner scheduler/error taxonomy.

## Mesure de succès

La migration est réussie si le nombre de lignes/branches spéciales diminue, pas si le nombre de fonctionnalités ou d'abstractions augmente.
