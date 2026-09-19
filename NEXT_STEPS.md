# Prochaines étapes

**Mis à jour : 19/09/2026 après audit pré-Work.**  
État : `docs/CURRENT_STATE.md`  
Critères de sortie : `docs/ACCEPTANCE_GATES.md`

## P0 — G1 cerveau LLM

- [x] G1.1 Faire passer parsing/validation JSON et vision dans `octopus.llm.complete(validate=...)` afin que les sorties invalides déclenchent réellement le fallback.
- [x] G1.2 Capturer et journaliser la route réellement résolue derrière OmniRoute.
- [x] G1.3 Rendre `zero_cost` fail-closed : attestation explicite du pool free-only, jamais simple confiance dans le nom `auto/best-free`.
- [x] G1.4 Éliminer la duplication de résolution de profil entre `agents/deepseek.py` et le catalogue.
- [x] G1.5 Verrouiller le bypass legacy direct derrière `OCTOPUS=off` et `OCTOPUS_ALLOW_LEGACY_DIRECT=1`.

Preuve live encore requise : vérifier sur l'instance OmniRoute free-only que les headers de modèle, provider, coût et request id sont présents et cohérents. Ne lancer aucun appel tant que le pool n'est pas configuré free-only.

Audit : `docs/audits/LLM_BRAIN_AUDIT_2026-09-19.md`.

## P0 — G2 frontière financière

1. Ajouter un test statique/AST interdisant les créations Salad/GPU.ai directes depuis les modules métier.
2. Forcer tout compute provisionné à passer par `GuardedComputeManager`.
3. Concevoir une `MeteredSpendLease` pour les jobs serverless/API.
4. Migrer RunPod renderer + MiniMax H3 vers réservation → soumission → settlement/cancel/ambiguous.
5. Rendre le watchdog réellement indépendant et redémarrable.

Audit : `docs/audits/PAID_PATHS_AUDIT_2026-09-19.md`.

## P0 — G3 canary Salad

Seulement après G1/G2 :

- petit solde ;
- pas d'auto-recharge ;
- allowance minuscule ;
- un worker ;
- un job canonique ;
- watchdog indépendant ;
- stop explicite immédiat après résultat ;
- crash test.

Design : `docs/design/SALAD_WAN_WORKER_V1.md`.

## P1 — G4 benchmark économique

Exécuter `docs/benchmarks/GPU_COST_BENCHMARK_PLAN.md` sur les GPU réellement disponibles.

Décision = **$/vidéo réussie**, pas $/h. Le résultat doit alimenter `ComputeBroker`.

## P1 — G5 coût GPU < 1 centime

Prouver sur 10 vidéos finales consécutives :

- moyenne <= $0.008 ;
- maximum normal <= $0.010 ;
- échecs/préemptions inclus ;
- 0 double génération facturable ;
- 0 GPU orphelin.

## P2 — G6/G7 monde réel

Après stabilisation du compute :

- executor de publication réel ;
- analytics observées ;
- expérience réelle ;
- revenu/coût dans ledger ;
- décision et learning au cycle suivant.

## Condition de merge de la PR compute

La PR reste Draft tant que G2/G3 ne sont pas prouvées live.

CI verte est nécessaire mais pas suffisante.
