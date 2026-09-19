# CLAUDE / agent handoff

Ce fichier donne les règles de reprise à un agent de développement. Il ne contient plus un ancien snapshot de branche.

## À lire en premier

1. `docs/CURRENT_STATE.md`
2. `docs/HANDOFF_WORK.md`
3. `NEXT_STEPS.md`
4. `docs/COMPUTE_GPU.md` si le travail touche le cloud/GPU.

Les fichiers sous `docs/archive/` sont historiques et ne doivent jamais être pris comme source de vérité courante.

## Vérification initiale obligatoire

Avant de modifier quoi que ce soit :

```bash
git status
git branch --show-current
git log -10 --oneline
git diff
```

Comparer l'état réel avec `docs/CURRENT_STATE.md`. Si le Git réel diffère, le Git gagne et la documentation doit être corrigée.

## Architecture à préserver

- `octopus.tasks` : queue durable / leases / reprises ;
- `octopus.journal` : source de vérité d'exécution ;
- `octopus.strategy` : stratégie persistante ;
- `octopus.economy` : ledger, allowances, dépenses ;
- `octopus.resources` : inventaire de ressources réelles ;
- `agents/runtime.py` : runtime agentique commun ;
- Workbench : surface de pilotage, pas moteur métier ;
- providers compute : adaptateurs techniques ;
- `GuardedComputeManager` : frontière obligatoire pour le compute GPU payant.

## Règles financières

- aucun compute GPU payant direct depuis un caller métier ;
- aucune dépense sans allowance quand la politique l'exige ;
- aucune resoumission aveugle après erreur ambiguë ;
- ne pas créer un deuxième ledger ;
- le coût estimé n'est jamais présenté comme coût observé ;
- conserver les preuves et la nature des chiffres.

## Règles de données

- ne jamais inventer revenu, client, conversion, analytics ou disponibilité ;
- les valeurs réelles doivent avoir une source/provenance ;
- ne jamais mettre un secret dans Git ;
- ne pas toucher aux données réelles utilisateur dans les tests ;
- utiliser les fixtures et DB temporaires prévues.

## Git

- ne pas développer directement sur `main` ;
- ne pas écraser un worktree inconnu ;
- garder des commits cohérents ;
- exécuter les tests ciblés puis les suites transverses touchées ;
- ne pas annoncer « vert » sans run récent ;
- mettre à jour `docs/CURRENT_STATE.md` quand une étape change l'état réel.

## Focus actuel

Le P0 est le compute :

1. trouver les bypasses de `GuardedComputeManager` ;
2. les supprimer ;
3. rendre le watchdog indépendant et redémarrable ;
4. préparer un canary Salad à très faible coût ;
5. benchmarker le coût réel/vidéo.

Ne pas partir sur une refonte générale d'OCTOPUS avant d'avoir terminé ce cycle.
