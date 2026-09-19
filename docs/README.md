# Documentation OCTOPUS

La documentation est organisée pour distinguer **état courant**, **runbooks techniques** et **historique**.

## État courant — lire en priorité

| Fichier | Rôle |
|---|---|
| `CURRENT_STATE.md` | source de vérité de l'état actuel |
| `HANDOFF_WORK.md` | protocole exact pour reprendre dans Work/Codex |
| `COMPUTE_GPU.md` | architecture GPU, coûts, breaker et watchdog |
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
