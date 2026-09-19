# Documentation OCTOPUS

La documentation est organisée pour distinguer **état courant**, **runbooks techniques** et **historique**.

## État courant — lire en priorité

| Fichier | Rôle |
|---|---|
| `CURRENT_STATE.md` | source de vérité de l'état actuel |
| `HANDOFF_WORK.md` | protocole exact pour reprendre dans Work/Codex |
| `COMPUTE_GPU.md` | architecture GPU, coûts, breaker et watchdog |
| `ACCEPTANCE_GATES.md` | critères binaires de progression et définition de la V1 |
| `../NEXT_STEPS.md` | prochaines actions priorisées |
| `../CLAUDE.md` | règles de reprise pour un agent de développement |

## Installation / exploitation

- `LOCAL_SETUP.md` — poste Windows / control-plane ;
- `CLOUD_RENDERER_RUNBOOK.md` — renderer vidéo cloud historique ;
- `RUNPOD_SETUP.md` — adapter RunPod existant, provider-spécifique ;
- `GENERATION_VIDEO.md` — WanGP/Wan2GP local ;
- `OMNIROUTE_SETUP.md` — gateway LLM ;
- `RESOURCES.md` — inventaire de ressources réelles ;
- `GUI.md` — Workbench ;
- `ORCA_INTEGRATION.md` — pont de développement optionnel.

## Audits / design actifs

- `audits/LLM_BRAIN_AUDIT_2026-09-19.md` — cerveau LLM / OmniRoute ;
- `audits/PAID_PATHS_AUDIT_2026-09-19.md` — chemins payants et gaps de settlement ;
- `design/SALAD_WAN_WORKER_V1.md` — worker Wan Salad cible ;
- `benchmarks/GPU_COST_BENCHMARK_PLAN.md` — protocole $/vidéo.

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
