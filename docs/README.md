# Documentation OCTOPUS

La documentation est organisée pour distinguer **état courant**, **runbooks techniques** et **historique**.

## Contexte durable — lire en priorité

| Fichier | Rôle |
|---|---|
| `../AGENTS.md` | constitution du projet et règles globales pour agents |
| `VISION.md` | pourquoi OCTOPUS existe et où il va |
| `CURRENT_STATE.md` | source de vérité de l'état actuel |
| `migrations/CODEX_START_2026-09-27.md` | routeur du chantier Astra phase E |
| `HANDOFF_WORK.md` | protocole de la première expérience économique supervisée |
| `EVIDENCE_ACCEPTANCE.md` | frontière gouvernée, gate technique et limites de preuve |
| `ACCEPTANCE_GATES.md` | critères binaires de progression et définition de la V1 |
| `../NEXT_STEPS.md` | prochaines actions priorisées |
| `../CLAUDE.md` | compatibilité / règles pour agents historiques |

## Installation / exploitation

- `LOCAL_SETUP.md` - poste Windows et control-plane;
- `OMNIROUTE_SETUP.md` - gateway LLM;
- `RESOURCES.md` - inventaire de ressources réelles;
- `GUI.md` - Workbench;
- `ORCA_INTEGRATION.md` - pont de développement optionnel.

`CLOUD_RENDERER_RUNBOOK.md`, `RUNPOD_SETUP.md` et `GENERATION_VIDEO.md` décrivent l'ancien moteur
vidéo retiré en phase B. Ils sont conservés pour l'historique et ne constituent plus le chemin
d'exécution vidéo. Le remplacement actuel est le service Agnes externe pinné décrit dans
`migrations/AGNES_VIDEO_REPLACEMENT.md`.

## Audits / designs techniques — conditionnels, pas roadmap active

- `audits/LLM_BRAIN_AUDIT_2026-09-19.md` — cerveau LLM / OmniRoute ;
- `audits/PAID_PATHS_AUDIT_2026-09-19.md` — chemins payants et gaps de settlement ;
- `design/SALAD_WAN_WORKER_V1.md` — worker Wan Salad cible ;
- `benchmarks/GPU_COST_BENCHMARK_PLAN.md` — protocole $/vidéo.

`COMPUTE_GPU.md` est un runbook technique conservé. Aucun canary GPU n'est requis pour le pilote
manuel. `NIGHT_SHIFT.md` et `PYTHON_CANARY.md` documentent l'atelier latéral, pas le chemin économique.

## Conception / plans spécialisés

Les autres fichiers de `docs/` décrivent des briques précises. Un plan daté n'est pas automatiquement un état courant. Les documents à statut historique portent désormais un bandeau en tête.

`docs/superpowers/` contient notamment des plans expérimentaux de développement GPU/Qwen ; ils ne définissent pas la plateforme compute de production.

## Historique

`archive/` contient les anciens audits, prompts de mission et rapports de sessions.

Ils sont conservés pour la traçabilité mais peuvent contenir :

- anciennes branches ;
- anciennes décisions techniques ;
- numéros de schéma obsolètes ;
- chemins locaux ;
- états CI périmés ;
- tâches déjà terminées.

Toujours préférer `CURRENT_STATE.md`.
