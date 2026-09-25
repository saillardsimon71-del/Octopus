# Handoff Codex — Migration Hermes + Agnes

Date: 2026-09-25

Branche de préparation active:
`prep/astra-local-orchestration`

Base documentaire précédente:
`prep/hermes-agnes-migration`

Bases:
- OCTOPUS main avant préparation: `ae4d98dc9692aa10ba15051381a36809e25377df`
- Hermes étudié: `NousResearch/hermes-agent@59004a62356f3a4697ab0fe8ad5086d2b405e2a6`

Point d'entrée unique:

`docs/migrations/CODEX_START_2026-09-25.md`

Ne pas relire tout le dépôt avant d'agir. Le fichier de départ impose l'ordre de lecture minimal et indique quand ouvrir les documents spécialisés.

## Orchestration de cette session

- GPT-6 Astra reste root, architecte, arbitre sécurité et reviewer final.
- Ne pas installer, charger ni réinstaller le package tiers `astra-flash-orchestrator`.
- Réutiliser uniquement la primitive OCTOPUS existante `development.task` lorsqu'une implémentation est suffisamment bornée.
- Le worker gratuit actuel est Step 3.7 via `octopus/dev_worker.py`; il n'est pas une autorité.
- Aucun fallback automatique vers un autre modèle gratuit ou payant. Si le worker échoue, si la preuve est ambiguë ou si Astra ne fait pas confiance au diff, Astra reprend le travail.
- Ne pas déléguer les décisions d'architecture, permissions, sécurité, preuves économiques ou trust boundaries.
- Pour les tâches déléguées: `allowed_paths` explicite, tests déterministes, `allow_declarative_fallback=false`, puis review Astra du diff réel avant intégration.

## Mission

Simplifier OCTOPUS en remplaçant les briques génériques maison par des primitives éprouvées inspirées/portées depuis Hermes lorsque cela réduit réellement le code et la dette, ET retirer l'ancien moteur vidéo pour installer le nouvel atelier Agnes autonome.

Ne pas importer Hermes entier.

## Ordre obligatoire

### Phase A — baseline
- vérifier état git;
- confirmer que la branche préparée est toujours basée sur le main courant; rebase/fast-forward seulement si main a réellement bougé;
- lancer suite actuelle;
- capturer les échecs préexistants.

### Phase B — retirer ancien moteur vidéo
Suivre `VIDEO_ENGINE_REMOVAL.md`.

Priorité:
1. débrancher le noyau;
2. tests;
3. supprimer arbre historique;
4. réparer imports/workflows/tests;
5. suite complète.

Le résultat doit être un OCTOPUS généraliste sans Remotion/RunPod/TTS/B-roll.

### Phase C — ajouter atelier Agnes
Créer:
`apps/agnes-video/index.html`

Suivre strictement le contrat fourni.
Pas de framework, CDN, build step ou backend.

Ajouter seulement les tests statiques/minimaux pertinents au repo:
- fichier unique;
- pas de CDN/import externe;
- endpoints/config attendus;
- queue/backoff/wakelock présents;
- syntaxe JS vérifiable si possible sans navigateur lourd.

Ne pas coupler Agnes au journal OCTOPUS pendant cette phase.

### Phase D — extraction Hermes P0
Ordre:
1. capability/tool registry;
2. client MCP générique;
3. computer-use via cua-driver;
4. guardrails de conséquences;
5. verification-evidence généralisée.

Pour chaque composant:
- comparer OCTOPUS actuel vs Hermes;
- préférer supprimer/remplacer plutôt qu'ajouter une seconde implémentation;
- préserver les interfaces économiques OCTOPUS;
- ajouter attribution MIT pour tout code substantiellement dérivé;
- pinner le commit Hermes source dans les commentaires/NOTICE appropriés.

### Phase E — P1 seulement si P0 propre
- retry/error/cooldown;
- periodic scheduler;
- lifecycle subagents.

Skills/memory/cron/gateway restent hors phase sauf besoin observé.

## Règles d'architecture

- OCTOPUS possède le raisonnement économique.
- Hermes fournit des primitives techniques.
- MCP est une frontière de capacité, pas un nouveau cerveau.
- Aucun double router LLM.
- Aucun double planner.
- Aucun double journal économique.
- Aucun nouveau framework sans suppression équivalente.
- CONSTRAIN CONSEQUENCES, NOT INTELLIGENCE.
- MARKET FIRST, AUTOMATION SECOND, GENERALIZATION LAST.

## Preuves attendues

Pour chaque remplacement:
- lignes/fichiers supprimés;
- lignes/fichiers ajoutés;
- tests;
- interface préservée;
- raison pour laquelle la nouvelle brique est plus simple/robuste.

## Critère final de la session

Un PR, non mergé sans ordre explicite, qui montre:
- ancien moteur vidéo supprimé;
- Agnes app présente et autonome;
- au moins la première tranche Hermes P0 réellement intégrée OU une preuve précise que son portage direct serait plus complexe que l'existant;
- CI verte;
- aucune régression H3/journal/worker/économie.
