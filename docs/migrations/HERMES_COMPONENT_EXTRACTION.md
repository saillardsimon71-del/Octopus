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
- policy/approval OCTOPUS autour des actions à conséquences.

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
