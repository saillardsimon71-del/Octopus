# Plan de benchmark économique GPU — OCTOPUS

**Date : 19/09/2026**  
**Objectif : choisir le provider/GPU qui minimise le coût réel par vidéo réussie, sans changer la qualité du workload.**

Ce protocole ne cherche pas « le GPU le plus rapide » ni « le moins cher à l'heure ». Il cherche :

```text
coût réel provider / nombre de vidéos finales réussies
```

Le benchmark doit être reproductible, borné financièrement et utilisable par `ComputeBroker`.

## 1. Métrique de décision

### Métrique principale

```text
gpu_cost_per_successful_final_video_usd
=
total_observed_gpu_cost_usd
/
successful_final_videos
```

### Cible

```text
TARGET  <= $0.008 par vidéo finale
HARD    <= $0.010 par vidéo finale
```

La cible à 0,8 centime laisse une marge pour le délai d'arrêt, les retries légitimes, les préemptions, les variations de runtime et l'overhead de session. Le hard cap à 1 centime reste le disjoncteur.

Métriques secondaires : coût/clip, secondes GPU facturables/clip, inférence, modèle chargé → résultat, délai job fini → ressource stoppée, taux de réussite, taux de préemption, coût perdu en échecs, overhead/batch.

## 2. Principe expérimental

Le GPU/provider est la variable. Tout le reste reste identique :

```text
même image OCI
même digest d'image
même modèle
même revision/digest des poids
même prompt
même seed
même résolution
même nombre de frames
même steps
même guidance/sampler
même input image si TI2V
même output contract
```

Il est interdit de comparer une 3090 sur un modèle 4 steps à une 5090 sur un modèle 50 steps : ce serait un benchmark de modèles, pas de compute.

## 3. Workload canonique V1

Pour le premier benchmark Salad, utiliser le worker Wan 2.2 TI2V-5B de la recette retenue.

Profil canonique à figer avant lancement :

```text
runtime_profile = wan22-ti2v-5b-benchmark-v1
model_revision  = <exacte>
image_digest    = sha256:<exact>
size            = 1280x704
frame_num       = 121
sample_steps    = 50
seed            = fixe
prompt          = fixture versionnée
input_image     = fixture versionnée ou null selon mode
```

Les valeurs exactes finales doivent correspondre au worker réellement utilisé. Les fixtures doivent être versionnées, par exemple sous :

```text
benchmarks/gpu/video-v1/
  workload.json
  prompt.txt
  input.png
  expected.json
```

Aucun prompt ne doit être changé à la main entre deux GPU.

## 4. Candidats

La liste est dynamique. Candidats initiaux intéressants :

```text
RTX 3090 24 GB
RTX 5090 Laptop 24 GB
RTX 4090 24 GB
RTX 5090 32 GB
```

Un candidat n'est testé que s'il est réellement disponible et respecte VRAM, prix max, compatibilité runtime et priorité. Les prix ne sont jamais codés en dur : chaque run commence par un quote live enregistré avec timestamp.

## 5. Phases du benchmark

### Phase 0 — lifecycle canary

Un seul GPU, un seul job. À prouver :

```text
quote
→ réservation financière
→ allowance
→ start
→ worker READY
→ submit
→ output externalisé
→ stop explicite
→ provider confirme l'arrêt
→ settlement
→ ledger
```

Puis un deuxième canary :

```text
start
→ job/worker actif
→ tuer volontairement control-plane ou worker
→ watchdog reprend
→ ressource stoppée
→ aucun second GPU créé
```

Aucun benchmark multi-GPU avant réussite de Phase 0.

### Phase 1 — screening cold

Un run froid par candidat :

```text
groupe arrêté
→ start
→ ready
→ 1 job canonique
→ result
→ stop immédiat
```

But : éliminer incompatibilités et GPU manifestement dominés. Un run préempté reste dans les données comme PREEMPTED.

### Phase 2 — répétitions des finalistes

Conserver au maximum 2–3 finalistes. Pour chacun :

- minimum 5 inférences réussies ;
- au moins 3 fixtures représentatives ;
- même distribution de workloads ;
- au moins un batch multi-job chaud.

Si la disponibilité empêche 5 runs comparables, le résultat reste `insufficient_evidence`.

### Phase 3 — warm batch

Tester plusieurs tailles de batch, par exemple 1, 5 et 10 jobs, sans redémarrage entre les jobs d'un batch.

Mesurer startup/model-load/idle-tail amortis par vidéo. Le GPU doit être arrêté dès que la vraie queue est vide.

### Phase 4 — interruption / reprise

Sur une priorité interruptible, vérifier qu'un job non terminé peut être repris avec la même identité, qu'un output COMPLETED n'est pas recalculé et que le coût de l'interruption est inclus.

## 6. Timings à enregistrer

```text
quote_at
reservation_at
provider_create_requested_at
provider_running_at
worker_ready_at
job_enqueued_at
job_started_at
model_ready_at
inference_started_at
inference_finished_at
upload_finished_at
job_completed_at
stop_requested_at
provider_stopped_at
settled_at
```

Dérivés :

```text
provision_s
running_to_ready_s
model_load_s
queue_wait_s
inference_s
upload_s
stop_latency_s
billable_wall_s
session_wall_s
```

Ne pas confondre cold start fournisseur non facturé et temps running facturé.

La source de vérité du coût final est le provider/billing lorsqu'elle est disponible. Le calcul `price_per_hour × billable_seconds / 3600` reste de nature `computed`, pas `observed`.

## 7. Schéma de benchmark cible

À implémenter dans SQLite :

```text
compute_benchmark_runs
  id
  ts
  provider
  accelerator
  priority
  region
  quoted_price_per_hour
  runtime_profile
  image_digest
  model_digest
  workload_hash
  batch_size
  attempt
  outcome
  interruption_reason
  provision_s
  ready_s
  model_load_s
  inference_s
  upload_s
  stop_latency_s
  billable_s
  computed_cost_usd
  observed_cost_usd
  cost_nature
  successful_outputs
  code_commit
  raw_metrics_json
```

Le benchmark devient obsolète si changent modèle, image, runtime, paramètres de qualité, provider, priorité ou génération majeure du worker.

## 8. Coût de la vidéo finale

Le benchmark du clip Wan ne suffit pas. OCTOPUS doit connaître le plan média final.

Exemple :

```text
vidéo finale
  3 clips Wan
  2 assets gratuits
  1 motion FFmpeg
```

Alors :

```text
estimated_gpu_cost_final_video
=
sum(estimated_cost des clips)
+
part amortie de la session
```

La réservation financière utilise le nombre de vidéos finales, pas seulement les clips. Dix vidéos finales avec 30 clips ont un hard cap GPU total de 10 × $0.01 = $0.10. Si l'estimation dépasse ce plafond, le batch est refusé avant création du GPU.

## 9. Règle de sélection

Un GPU doit avoir une preuve suffisante, un coût/output réussi, un taux d'échec acceptable, un runtime compatible et une disponibilité raisonnable.

```text
expected_cost_per_success
=
(total_cost_successes + total_cost_failures)
/
successful_outputs
```

Parmi les candidats éligibles sous politique de coût, sélectionner le minimum de `expected_cost_per_success`. La latence sert de départage sauf deadline explicite.

## 10. Dominance

Éliminer A si B est simultanément moins cher/output réussi, au moins aussi fiable, suffisamment disponible et compatible avec le même workload.

Conserver idéalement :

```text
primary
fallback
```

## 11. Budget du benchmark

Le benchmark est lui-même une expérience payante : allowance dédiée, batch cap dédié, global cap inchangé, nombre max de runs, arrêt anticipé si la preuve est déjà suffisante.

Ordre :

```text
canary
→ screening
→ élimination
→ répétition finalistes
```

Jamais quatre GPU × vingt essais « pour voir ».

## 12. Conditions de réussite

Le benchmark V1 est DONE seulement si :

1. workload versionné et identique ;
2. prix live de chaque run enregistré ;
3. coût final marqué `observed` ou `computed` ;
4. échecs/préemptions inclus ;
5. au moins deux candidats comparables, sauf indisponibilité documentée ;
6. primary + fallback choisis par règle déterministe ;
7. résultat injecté dans `ComputeBroker` ;
8. broker capable de refuser une vidéo/batch estimé hors budget ;
9. raw measurements auditables.

## 13. Résultat attendu

```text
Workload : wan22-ti2v-5b-benchmark-v1
Provider : ...
GPU      : ...
Evidence : N runs, X% success
Median   : ... s
p95      : ... s
Cost / successful clip      : ...
Expected GPU cost / video   : ...
Fallback : ...
Reason   : cheaper per successful video under hard cap
```

Aucun « meilleur GPU » ne doit être codé en dur sans cette preuve.
