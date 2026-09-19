# Compute GPU — architecture, coûts et sécurité

Ce document décrit la couche compute actuelle. Il complète les runbooks vidéo historiques.

## Objectif

OCTOPUS doit pouvoir choisir un GPU cloud selon le **coût réel du travail produit**, tout en empêchant qu'un bug, un retry ou un crash vide le solde fournisseur.

Le principe n'est pas :

```text
GPU le plus puissant
```

mais :

```text
workload donné
→ coût total observé
→ coût / vidéo réussie
→ provider/GPU le plus économique sous contraintes
```

## Composants

### `octopus/compute.py`

Contrats communs :

- `ComputeRequest`
- `ComputeOffer`
- `ComputeOperation`
- `ComputeInstance`

### `octopus/salad.py`

Adapter SaladCloud :

- découverte classes GPU ;
- prix et disponibilité ;
- tier économique Batch/Lowest ;
- nom de container group déterministe ;
- création idempotente ;
- lecture après écriture ;
- stop/delete ;
- `restart_policy=never`.

Un timeout après POST n'entraîne jamais un deuxième POST aveugle.

### `octopus/gpuai.py`

Adapter GPU.ai :

- quote/create historique ;
- lecture d'opération ;
- lecture d'instance ;
- terminaison ;
- support de réconciliation après restart.

### `octopus/compute_broker.py`

Compare les offres.

Sans benchmark : peut comparer le prix horaire.

Avec benchmark : calcule :

```text
estimated_cost = price_per_hour × runtime_seconds / 3600
```

La production payante gardée exige un runtime benchmarké.

### `octopus/compute_finance.py`

Contient :

- `BudgetLimits`
- `FinancialCircuitBreaker`
- `GuardedComputeManager`

Le breaker réserve un budget **avant** l'appel provider.

## Hard caps par défaut

Variables :

```text
OCTOPUS_GPU_MAX_COST_PER_VIDEO_USD=0.01
OCTOPUS_GPU_MAX_BATCH_USD=0.25
OCTOPUS_GPU_MAX_BUSINESS_DAILY_USD=1.00
OCTOPUS_GPU_MAX_GLOBAL_DAILY_USD=2.00
OCTOPUS_GPU_MAX_RUNTIME_S=3600
OCTOPUS_GPU_IDLE_TIMEOUT_S=300
OCTOPUS_GPU_RESERVATION_TTL_S=120
OCTOPUS_GPU_WATCHDOG_INTERVAL_S=5
OCTOPUS_GPU_SHUTDOWN_MARGIN_PER_VIDEO_USD=0.001
OCTOPUS_GPU_REQUIRE_ALLOWANCE=1
```

Ces limites sont intentionnellement basses pour la phase de test.

### Important

Le plafond de $0.01 est un **garde-fou logiciel OCTOPUS**, pas une promesse que la facture du fournisseur ne pourra mathématiquement jamais dépasser $0.01.

Pourquoi :

- la facturation appartient au provider ;
- un arrêt réseau peut prendre du temps ;
- la granularité de facturation peut évoluer ;
- le watchdog a un intervalle ;
- une ressource peut être déjà engagée au moment d'une panne réseau.

OCTOPUS réserve une marge et coupe avant le plafond calculé, mais le vrai objectif doit être vérifié par canary et mesure fournisseur.

## Réservation atomique

Une réservation persistante est créée dans SQLite avant `provider.create()`.

Les réservations actives comptent à leur **hard cap**, pas à leur estimation moyenne.

Exemple :

```text
limite globale restante = $0.015

worker A réserve $0.010
worker B demande $0.010

→ B est refusé
```

Deux workers ne peuvent donc pas consommer simultanément le même budget théorique.

## Allowances

Par défaut, une réservation GPU exige également une allowance USD issue de `octopus.economy`.

Le breaker crée un `spend_request` lié à la réservation.

À la finalisation :

- coût 0 → request annulée ;
- coût > 0 → request exécutée au coût final ;
- écriture `gpu_compute` ajoutée au ledger.

Cela évite d'avoir deux systèmes financiers indépendants.

## Persistance

Tables ajoutées au schéma v8 :

```text
compute_reservations
compute_cost_events
compute_spend_links
```

États principaux :

```text
reserved
provisioning
running
ambiguous
stopping
closed
cancelled
tripped
```

## Idempotence / soumission ambiguë

Cas dangereux :

```text
POST create
→ provider reçoit la demande
→ réponse réseau perdue
```

Un retry aveugle peut créer deux ressources.

Politique OCTOPUS :

```text
timeout
→ état ambiguous/submitted_unverified
→ conserver le budget réservé
→ relire le provider
→ ne jamais créer une seconde ressource automatiquement
```

Salad utilise un nom de groupe dérivé de l'idempotency key, ce qui permet la relecture.

## Watchdog

`ops/compute_watchdog.py` est conçu pour tourner séparément du worker vidéo.

Il surveille :

- réservation jamais soumise ;
- coût calculé approchant du hard cap ;
- TTL ;
- inactivité ;
- opérations provider en attente ;
- terminaison asynchrone.

Commande :

```bash
python ops/compute_watchdog.py --once
python ops/compute_watchdog.py
```

Le script existe, mais son **déploiement comme service réellement indépendant** fait encore partie du P0 avant production.

## Salad et machines éphémères

Le compute doit être traité comme jetable.

Ne pas compter sur le disque local d'une instance comme mémoire durable.

Le design cible :

```text
image OCI versionnée
+ poids/cache récupérables
+ job depuis object storage / queue
+ résultat externalisé
+ container supprimable à tout moment
```

La décision entre installation à chaque boot et image préconstruite doit être prise sur mesure :

- cold-start ;
- fiabilité ;
- taille d'image ;
- cache disponible ;
- fréquence des jobs ;
- coût du temps de préparation.

## Critère économique

Une machine moins chère à l'heure peut coûter plus cher par vidéo.

Exemple :

```text
GPU A = $0.09/h, vidéo en 8 min  → $0.012
GPU B = $0.16/h, vidéo en 2 min  → $0.0053
```

Le broker doit donc apprendre sur des benchmarks réels.

## État de validation

Sur le HEAD fonctionnel `555230d` avant nettoyage documentaire :

- workflow `Compute finance safety` : vert ;
- workflow `video-foundation` : vert ;
- aucun appel GPU réel facturable dans les tests.

## Limite d'intégration actuelle

**Point critique :** le breaker existe mais tous les anciens chemins de génération GPU ne sont pas encore prouvés comme passant exclusivement par `GuardedComputeManager`.

Avant tout usage payant significatif :

1. cartographier les appels cloud/GPU ;
2. supprimer les bypasses ;
3. faire tourner le watchdog indépendamment ;
4. faire un canary à quelques centimes ;
5. mesurer ;
6. seulement ensuite augmenter les allowances.
