# Design — Salad Wan Worker V1

**Date : 19/09/2026**  
**Statut : design de reprise, pas encore implémenté.**

## Décision

Pour le premier canary Salad, ne pas reconstruire WanGP sur Salad immédiatement.

Utiliser comme référence le chemin officiel Salad **Wan 2.2 TI2V-5B + Kelpie** :

- worker Wan officiel/recette ;
- queue de jobs ;
- stockage S3-compatible ;
- un worker par GPU ;
- support de scale-to-zero.

Référence actuelle :

https://docs.salad.com/container-engine/reference/recipes/wan-5b

WanGP MCP reste utile pour :

- modèles supplémentaires ;
- environnement local ;
- futur worker custom ;
- compatibilité avec l'écosystème WanGP.

Mais il ne doit pas retarder la mesure économique du premier worker cloud.

---

# 1. Pourquoi Kelpie est adapté à OCTOPUS

La recette Salad actuelle fournit exactement les primitives recherchées :

```text
queue
→ worker stateless
→ Wan 2.2 TI2V-5B
→ output MP4
→ S3-compatible
```

Elle cible les GPU consumer ~24 GB+ et un worker par node.

Elle sépare aussi très bien :

- control-plane ;
- queue ;
- worker ;
- storage.

C'est compatible avec l'architecture OCTOPUS.

---

# 2. Point critique : ne PAS dépendre du scale-to-zero 5 minutes

La recette Kelpie documente un autoscaler qui évalue les règles toutes les ~5 minutes.

Pour une cible :

```text
GPU cost / vidéo < $0.01
```

c'est beaucoup trop lent comme seul mécanisme de coupure.

Exemple conceptuel :

```text
job fini
→ 4 min 30 d'idle
→ prochain tick autoscaler
```

Le coût idle peut dépasser le coût d'inférence.

## Décision

L'autoscaler Salad est un **filet de secours**.

La coupure normale doit être :

```text
dernier job du batch terminé
→ upload confirmé
→ queue OCTOPUS vide
→ stop container group immédiatement
```

Le watchdog OCTOPUS reste la deuxième barrière.

---

# 3. Architecture cible

```text
                        OCTOPUS
                           │
                    Media Batch Planner
                           │
           combien de clips / vidéos finales ?
                           │
                           ▼
                    ComputeBroker
                   provider + GPU live
                           │
                           ▼
              GuardedComputeManager
                 réserve hard cap
                 + allowance USD
                           │
                           ▼
                   SaladWorkerPool
             ensure group / start / stop
                           │
                    ┌──────┴──────┐
                    │             │
                    ▼             ▼
               Kelpie queue   watchdog
                    │
                    ▼
             Wan 2.2 TI2V-5B
              1 worker / GPU
                    │
                    ▼
              S3/R2 outputs
                    │
                    ▼
               ResultCollector
                    │
                    ▼
          octopus.media.library
                    │
                    ▼
        FORGE / montage vidéo final
```

Salad génère des **plans source**.

FORGE reste responsable de l'assemblage final :

- narration ;
- sous-titres ;
- montage ;
- audio ;
- QC ;
- CTA.

---

# 4. Container group : persistant en configuration, éphémère en compute

Le bon objet à conserver n'est pas une machine.

C'est une **configuration de pool**.

Exemple :

```text
octopus-wan5b-5090-batch-v1
octopus-wan5b-4090-batch-v1
octopus-wan5b-3090-batch-v1
```

Chaque groupe peut être stoppé lorsqu'il n'y a aucun job.

Avantage :

- pas besoin de recréer toute la configuration à chaque vidéo ;
- pas de machine persistante ;
- credentials et probes restent attachés au groupe ;
- le broker peut choisir un autre groupe/GPU au batch suivant.

Pour le premier canary, un groupe unique suffit.

---

# 5. Image OCI

## V1

Partir de l'image/worker officiel de la recette Wan 5B ou d'une image construite à partir de cette recette avec versions épinglées.

Référence immuable :

```text
image repository
image digest SHA256
model revision
worker revision
Python
PyTorch
CUDA runtime
```

Jamais :

```text
latest
git clone main au boot
pip install sans version
```

## Modèles dans l'image ou runtime ?

Salad ne facture pas :

- allocation ;
- image download ;
- cold start.

Le temps **après que l'application est running** est facturé.

Donc, économiquement, il est très intéressant de tester des poids intégrés aux layers OCI :

```text
grosse image
→ pull non facturé
→ model load facturé
```

contre :

```text
petite image
→ app running
→ download poids
→ temps potentiellement facturé
```

Ne pas décider par intuition.

Le benchmark doit mesurer les deux stratégies si la recette actuelle ne bake pas déjà les poids.

---

# 6. Contrat de job

Le job doit être indépendant du provider.

Proposition :

```json
{
  "schema_version": "1",
  "job_id": "clip-<sha256>",
  "batch_id": "batch-...",
  "business": "podalux",
  "reservation_id": 123,
  "model_profile": "wan2.2-ti2v-5b-v1",
  "prompt": "...",
  "width": 1280,
  "height": 704,
  "frames": 121,
  "steps": 50,
  "seed": 42,
  "input_uri": null,
  "output_prefix": "media/<job_id>/"
}
```

Ne jamais mettre dans le job :

- clé Salad ;
- secret S3 ;
- token fournisseur ;
- allowance secret.

---

# 7. Identité et idempotence

`job_id` doit être un hash des paramètres qui changent le rendu :

```text
model digest
prompt
input digest
width/height
frames
steps
sampler
guidance
seed
```

Avant de calculer :

```text
S3 manifest COMPLETED existe ?
  oui → reuse
  non → queue
```

Le worker doit également vérifier avant upload final.

Le système doit tolérer **at-least-once delivery** sans payer deux générations lorsque le premier résultat existe déjà.

---

# 8. Manifest de résultat

```json
{
  "schema_version": "1",
  "job_id": "...",
  "status": "COMPLETED",
  "model_profile": "...",
  "model_digest": "...",
  "gpu_class": "rtx_5090",
  "provider": "salad",
  "attempt": 1,
  "started_at": 0,
  "model_ready_at": 0,
  "inference_started_at": 0,
  "inference_finished_at": 0,
  "uploaded_at": 0,
  "output_uri": "s3://...",
  "sha256": "...",
  "metrics": {
    "model_load_s": 0,
    "inference_s": 0,
    "upload_s": 0
  }
}
```

Ces mesures alimentent le futur benchmark store.

---

# 9. États OCTOPUS

```text
PLANNED
  ↓
RESERVED
  ↓
POOL_STARTING
  ↓
WORKER_READY
  ↓
QUEUED
  ↓
RUNNING
  ↓
UPLOADING
  ↓
COMPLETED
  ↓
POOL_STOPPING
  ↓
CLOSED
```

États exceptionnels :

```text
AMBIGUOUS_SUBMISSION
PREEMPTED
FAILED
TRIPPED
CANCELLED
```

Une interruption Salad Lowest ne doit pas transformer un job en « échec définitif ».

Le job reste dans la queue avec la même identité.

---

# 10. Readiness

Le worker n'est READY que lorsque :

- CUDA visible ;
- GPU correspond au profil attendu ;
- PyTorch CUDA fonctionne ;
- modèle accessible ;
- modèle chargé ;
- S3/R2 accessible en écriture ;
- queue accessible.

Ne pas faire une génération 5 secondes complète comme healthcheck de chaque boot.

Le premier vrai job du batch sert de canary économique.

---

# 11. Préemption

Lowest/Batch est interruptible.

Architecture exigée :

```text
job dans queue externe
+ input externe
+ output externe
+ état control-plane externe
```

Donc si le node disparaît :

```text
node A disparaît
→ pas de résultat COMPLETED
→ job reste/redevient disponible
→ node B
→ même job_id
```

Aucun état essentiel uniquement sur `/tmp`.

---

# 12. Politique d'arrêt

Le worker doit envoyer heartbeat/progress à OCTOPUS.

Valeurs V1 recommandées à benchmarker :

```text
watchdog sweep       5 s
idle stop normal     10–30 s
hard financial trip  calculé depuis hard_cap
max batch lifetime   explicite
```

Le défaut global actuel `idle_timeout_s=300` est volontairement générique et trop large pour optimiser un worker vidéo très bon marché.

Le caller vidéo doit demander un timeout d'idle plus agressif.

---

# 13. Réservation financière d'un batch

Le breaker ne doit pas raisonner « un clip = une vidéo finale » si une vidéo utilise plusieurs clips.

Le planner doit annoncer :

```text
final_videos_planned
clips_planned
estimated_total_gpu_seconds
```

La réservation est bornée par :

```text
final_videos_planned × $0.01
batch hard cap
daily caps
allowance
```

Exemple :

```text
10 vidéos finales
3 clips Wan / vidéo
30 clips à produire

hard cap unité = 10 × $0.01 = $0.10
```

Le broker choisit un GPU dont le runtime prévu tient dans cette enveloppe.

---

# 14. API / modules à construire

Proposition, sans obligation de nom :

```text
octopus/media/salad_wan.py
    SaladWanJobClient
    submit/status/cancel/result

octopus/compute_pool.py
    SaladWorkerPool
    ensure/start/stop/status

octopus/compute_benchmarks.py
    runtime historique par workload/GPU

octopus/media/handlers.py
    backend salad_wan
```

Ne pas mettre la logique financière dans `salad_wan.py`.

---

# 15. Canary live

But : vérifier le lifecycle, pas « faire la meilleure vidéo du monde ».

Conditions :

- auto-recharge Salad désactivée ;
- petit solde ;
- allowance OCTOPUS dédiée ;
- hard global cap très bas ;
- un seul groupe ;
- un seul job ;
- watchdog réellement séparé ;
- sortie externalisée.

Séquence :

```text
quote
→ reserve
→ start group
→ READY
→ submit one cheap canonical job
→ receive output
→ stop group immediately
→ read provider/account cost
→ settle reservation
→ verify ledger
```

Test volontaire :

pendant un second canary, tuer le processus worker/control-plane et vérifier que le watchdog stoppe la ressource.

---

# 16. Ce que Work doit éviter

- intégrer WanGP complet avant le premier benchmark Salad ;
- utiliser l'autoscaler 5 min comme arrêt principal ;
- créer un container group différent pour chaque clip ;
- stocker les outputs seulement sur le node ;
- télécharger des poids arbitraires à chaque boot sans mesure ;
- lancer plusieurs GPU avant d'avoir un canary unitaire ;
- augmenter l'allowance pour « voir si ça passe ».

Le premier objectif est : **une boucle compute prouvée, bornée et mesurée**.
