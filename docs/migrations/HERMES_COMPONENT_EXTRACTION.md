# Extraction Hermes → OCTOPUS

Date: 2026-09-25  
OCTOPUS base: `ae4d98dc9692aa10ba15051381a36809e25377df`  
Hermes upstream pin: `NousResearch/hermes-agent@59004a62356f3a4697ab0fe8ad5086d2b405e2a6`

## Principe

Hermes n'est PAS le nouveau cerveau d'OCTOPUS.

OCTOPUS conserve:
- sa finalité économique;
- son journal économique;
- ses expériences;
- sa qualification de preuves;
- ses décisions d'allocation;
- ses contraintes de coût et de conséquences.

Hermes sert de banque de composants techniques déjà éprouvés.

Architecture cible:

```
                  OCTOPUS
     economic reasoning / experiments / state
                     |
              capability boundary
                     |
      +--------------+---------------+
      |              |               |
  Hermes-derived   MCP tools      OCTOPUS-native
  infrastructure   external       economics
```

## Composants à extraire en priorité

### P0 — Computer Use

Upstream:
- `tools/computer_use_tool.py`
- `tools/computer_use/`
- backend `cua-driver` via MCP

Valeur:
- contrôle desktop Windows/macOS/Linux;
- accessibility tree;
- ciblage fenêtre/processus;
- capture;
- actions en arrière-plan;
- verdict structuré sur l'effet des actions;
- détection d'état/snapshot périmé.

Cible OCTOPUS:
- une capability stable `computer`;
- aucune dépendance au loop Hermes;
- backend initial: cua-driver/MCP;
- policy/approval OCTOPUS autour des actions à conséquences;
- préserver les propriétés du pin Hermes: MCP/stdio, environnement enfant assaini pour ne pas transmettre les secrets provider, télémétrie cua-driver désactivée par défaut;
- aucun auto-installer cua-driver déclenché implicitement par OCTOPUS.

Décision: PORTER/ADAPTER.

### P0 — Tool registry / toolsets

Upstream:
- `tools/registry.py`

Concepts à reprendre:
- une seule source de vérité par outil;
- schema + handler + disponibilité + dépendances + toolset;
- checks de disponibilité;
- résultats d'erreur bornés;
- découverte contrôlée des outils;
- pas de listes parallèles dispersées.

Cible OCTOPUS:
- remplacer progressivement les registres ad hoc, pas le raisonnement;
- garder les règles économiques/permissions hors du registry.

Décision: ADAPTER, pas copier aveuglément tout le discovery/plugin system.

#### Tranche D — registre uniquement (2026-09-26)

`agents/tool_registry.py::ToolRegistry` remplace la table passive et les
fonctions de description/validation de `agents/runtime.py`. `runtime.TOOLS`
est l'unique instance, compatible avec les consommateurs existants du mapping
`nom -> desc/params/fn`. Les anciens noms de fonctions référencent ses méthodes.
`dispatch(nom, args, allowed_tools)` renvoie `(refus, résultat)` ; il applique
le filtre de mission et le schéma avant le handler, sans retry. Les exceptions
sont limitées à 2048 caractères ; l'annulation humaine remonte intacte.
Les résultats normaux restent intacts, avec leur vue de contexte bornée existante.

Le dialecte de paramètres existant est conservé (optionnels, unions, IDs,
champs supplémentaires tolérés) ; un type de schéma inconnu ne valide plus
arbitrairement une valeur. Les 17 handlers et leurs politiques restent en place.
La disponibilité reste vérifiée par les handlers : cette tranche n'ajoute ni
probes, ni toolsets, ni discovery, ni dépendance runtime Hermes. Elle ne promeut
pas `capabilities.py` en autorité, conformément à la constitution actuelle.

Réduction de complexité : le runtime ne possède plus sa propre séquence de
filtrage/validation/exécution ; le registre porte ce contrat unique. L'extraction
ajoute un module et 31 lignes de production nettes : le gain porte sur la
localisation du contrat, pas sur le volume de code ni sur un gain économique.
Le pattern vient du pin Hermes indiqué ci-dessus ; l'implémentation reprend le
code OCTOPUS existant, sans copie substantielle de code upstream.
Les tests H3 existants sont conservés ; les nouveaux cas vérifient le refus avant
effet, l'unicité du registre, les résultats intacts, les erreurs bornées et l'arrêt.

Validation exécutée dans le sandbox Astra : 383 tests ciblés passent
(`cache/astra-relay/phase-d-targeted.log`). L'unique suite complète termine avec
1181 succès, 8 ignorés, 31 échecs (`cache/astra-relay/phase-d-full.log`, code 1).
Les échecs concernent `test_dev_worker` (21), `test_night_shift` (1),
`test_promotion` (8) et `test_stop_memory` (1). Le log montre des clones Git
bloqués par `sh.exe: couldn't create signal pipe, Win32 error 5` ; le test
d'arrêt dépasse sa limite de 15 secondes (22,08 s), sans cause établie ici.
Après le checkpoint `954a7e62a1655e1102c4739a7a8820e02c08e78d`, le host a
relancé la suite complète hors sandbox avec `.venv/Scripts/python.exe -m pytest
-q --tb=short`. Elle termine avec le code `0`; le journal est conservé dans
`cache/astra-relay/phase-d-host-full.log`. Les 31 échecs sont donc propres au
sandbox Astra et ne se reproduisent pas sur le checkpoint exact. Aucune promotion
vers `main` n'est effectuée par cette validation.

#### Fiabilité des missions — contrat D, checkpoint avant relay (2026-09-26)

Incident observé dans `data/octopus.db`, tâches durables 71 et 72 (lecture seule).
La tâche 71 avait `budget_usd=0`, cinq sous-tâches sans étape, mais un rapport
`synthesis_status=validated` et une evidence inférée 74. Au diagnostic, cette
preuve était déjà `retracted` ; aucune donnée historique n'a été modifiée.
La tâche 72 avait huit SEARCH et zéro BROWSE ; les traces contiennent notamment
une collision sémantique avec une marque et une offre/chiffres sans acquisition.
Elle finit `done_degraded` après épuisement des routes gratuites (429).

Causes et correction bornée dans le runtime existant :
- `coût >= plafond` coupait le ReAct avant le premier appel à zéro sur zéro.
  Le coupe-circuit historique utilise désormais `>` ; le gateway conserve son
  contrôle préalable `coût cumulé + estimation du candidat > plafond`.
- Une erreur LLM était capturée puis rendue comme `(max steps atteint)` ; les
  agents exposent maintenant `execution_status` et `execution_error`. Un arrêt
  pour budget, annulation ou indisponibilité LLM coupe la mission, conserve les
  résultats et produit `synthesis_status=degraded`, sans nouvelle synthèse ni
  exécution des rôles restants. Les bugs de programmation remontent.
- Plan vide, absence de travail après arrêt et rapport vide/non textuel ne
  produisent plus de synthèse validée. Une limite d'étapes avec travail acquis
  peut encore produire une synthèse partielle ; l'exécution est `incomplete`.
- Le handler durable `orbit.mission` active par défaut le contrat existant
  `business_signal_focus`. Il réutilise le gate acquisition + citations d'une
  même page et la politique SEARCH business existants. Le runtime générique
  garde son défaut historique. Aucun mot, requête, fournisseur de recherche ou
  enchaînement SEARCH/BROWSE n'est imposé par cette correction.
- Le handler expose `opportunity_status` : `source_supported`, `inconclusive`
  ou `not_evaluated` pour une analyse explicitement hors qualification.
  Une preuve de rapport automatique exige exécution `completed`, synthèse
  valide et signal qualifié ; elle reste `inferred`, jamais preuve de revenu
  ou d'acceptation client. Les acquisitions complètes restent dans la sortie
  durable, même si la trace diagnostique compacte n'est pas demandée.

`synthesis_status=validated` reste un statut technique du rapport. Les citations
littérales ne démontrent pas la pertinence sémantique ; une revue humaine reste
nécessaire. L'échec du reviewer conserve son statut séparé et ne fabrique aucune
classification. Les propositions non sourcées ne deviennent pas des signaux
qualifiés. Aucun nouveau planner, journal ou moteur d'evidence n'est ajouté.

**Deux budgets distincts.** `tasks.budget_usd` est le plafond des appels LLM,
partagé par les runs imbriqués ; un run enfant n'ajoute plus son plafond implicite
historique de 1 USD lorsqu'un budget parent existe. Ce champ n'accorde aucune
allowance économique et ne sélectionne aucun profil. Les dépenses externes
restent contrôlées par `economy`/`actions`, et SEARCH payant reste refusé.
`zero_cost` interdit toujours tout fallback payant. Pour l'autorisation humaine
courante (zéro dépense externe, au plus 2 USD de LLM), la combinaison explicite
est `profile=flash_fallback`, `budget_usd=2` et l'allowlist
`search,browse,economy_status,resources_status`. Le profil existant tente les
routes gratuites puis DeepSeek Flash via le gateway, sans raccourci fournisseur.
Aucune mission n'a été mise en file ni exécutée avec cette autorisation.
Le plafond est contrôlé avant chaque appel sur les coûts journalisés et
l'estimation existante ; ceci n'est pas une réconciliation de facture fournisseur.
Une nouvelle mission est une nouvelle enveloppe, pas une prolongation implicite.

Validation ciblée exécutée : **330 tests passent** en 54,28 s ; log
`cache/astra-relay/mission-contract-targeted.log`. `git diff --check` passe.

Oracle payé entièrement simulé : deux sous-agents coûtent chacun 0,70 USD dans
une enveloppe commune de 2 USD ; le troisième est bloqué avant transport. Les
oracles zéro coût, absence d'evidence, arrêt du pool, limites de prompt, gate de
sources et permissions sont conservés. La validation complète finale reste à
exécuter après délégation et revue Step ; ne pas réutiliser le baseline comme
preuve de validation de ces changements.

Ticket mécanique prévu, uniquement `octopus/strategy_cli.py` : ajouter
`--llm-budget-usd` comme alias de `--budget-usd`, destination inchangée
`budget_usd`, et clarifier l'aide (plafond LLM distinct de toute dépense externe ;
profil explicite nécessaire pour le payant). Aucun changement de défaut, de
validation, de catalogue, d'allowlist, de permission ou de handler dans ce ticket.
Oracle de non-régression : `tests/test_strategy.py`, en particulier le passage
explicite de `zero_cost/0` et `flash_fallback/2` sans modification de l'allowlist.
Astra vérifiera aussi les deux orthographes et les valeurs invalides à la revue.

### P0 — MCP boundary

Upstream:
- `agent/transports/hermes_tools_mcp_server.py`
- intégration MCP Hermes

Concept:
- exposer un sous-ensemble volontaire de capacités par une frontière standard;
- le runtime appelant ne dépend pas de l'implémentation interne.

Cible OCTOPUS:
- client MCP générique;
- adapters pour cua-driver et autres capacités;
- possibilité future d'exposer certaines capacités OCTOPUS par MCP.

Décision: PORTER LE PATTERN.

### P0 — Tool guardrails / consequence control

Upstream:
- `agent/tool_guardrails.py`
- garde-fous computer-use
- scopes/approvals Hermes

Cible OCTOPUS:
- préserver le principe: CONSTRAIN CONSEQUENCES, NOT INTELLIGENCE;
- policies déterministes autour des side effects;
- distinction read-only / reversible / external-side-effect;
- fail closed sur permission inconnue.

Décision: EXTRAIRE LES PRIMITIVES/PATTERNS.

### P0 — Verification evidence

Upstream:
- `agent/verification_evidence.py`

Concept:
```
action -> observation -> evidence -> verified state
```

Hermes distingue le fait qu'une action a été exécutée du fait que son résultat a été vérifié.

Cible OCTOPUS:
- généraliser au-delà de #94;
- ledger de preuves par action/expérience;
- scope, timestamp, source, statut, artefact;
- ne jamais promouvoir une preuve ciblée en garantie globale.

Décision: PORTER LE MODÈLE, l'aligner sur le journal OCTOPUS.

### P1 — Retry / cooldown / error classification

Upstream à étudier:
- `agent/error_classifier.py`
- `agent/retry_utils.py`
- `agent/fallback_cooldown.py`
- credential/provider cooldowns

Cible:
- 403/429/provider unavailable;
- distinguer erreur permanente, transitoire, auth, rate limit, blocage;
- empêcher les retries aveugles;
- cooldown partagé et observable.

Décision: EXTRAIRE après comparaison avec `octopus/llm.py` et SEARCH/BROWSE.

### P1 — Periodic scheduler

Upstream:
- `agent/periodic_scheduler.py`

Intérêt:
- un scheduler process-wide;
- pas un thread dormant par agent/watchdog;
- callbacks non chevauchants;
- cancellation propre;
- un callback bloqué ne bloque pas les autres.

Cible:
- heartbeats;
- watchers;
- maintenance;
- futures activités autonomes récurrentes.

Décision: PORTER/ADAPTER.

### P1 — Skills

Upstream:
- skill system Hermes;
- standard agentskills.io.

Cible:
- mémoire procédurale de workflows économiques réellement réussis;
- aucune création automatique de skill avant preuve répétée.

Décision: ÉTUDIER/PORTER PLUS TARD.

### P1 — Subagent lifecycle

Upstream:
- `agent/subagent_lifecycle.py`
- délégation/heartbeat/isolation.

Cible:
- récupérer lifecycle/annulation/heartbeat;
- ne pas importer l'orchestrateur cognitif Hermes.

Décision: EXTRAIRE LES PRIMITIVES.

### P2 — Secrets/Vault, cron/gateway, memory

Utiles à terme:
- secret scopes / vault;
- cron;
- Telegram/Discord/etc.;
- mémoire et session search.

Ne pas prioriser avant les capacités P0.

## Ce qu'on ne doit pas importer

- agent loop Hermes;
- persona/system prompt Hermes;
- planificateur Hermes;
- mémoire utilisateur complète;
- router LLM complet;
- UI desktop Hermes;
- orchestration Hermes entière.

Raison: deux cerveaux/orchestrateurs concurrents et dilution de l'identité économique d'OCTOPUS.

## Licence

Hermes est MIT.

Si du code substantiel est copié ou dérivé:
- conserver la notice copyright Nous Research;
- conserver la permission/licence MIT applicable;
- documenter le commit upstream d'origine.

Ne pas vendoriser du code avant d'avoir défini la frontière cible.
