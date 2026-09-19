# Audit des chemins potentiellement payants — 2026-09-19

## Objectif

Recenser les chemins capables de consommer de l'argent, un crédit cloud ou un quota valorisable.

Classification :

- **P0** : peut engager une dépense importante ou contourner un garde-fou ;
- **P1** : protégé partiellement, mais coût/settlement incomplet ;
- **P2** : micro-coût, quota ou chemin manuel.

Ce document distingue volontairement :

1. **compute provisionné** : Salad/GPU.ai, coût tant qu'une ressource tourne ;
2. **appel metered/serverless** : RunPod, H3, TTS/API, coût par job/appel ;
3. **LLM** : coût token ;
4. **stockage/egress** : coût indirect.

Une même abstraction de lifecycle ne convient pas aux quatre. En revanche, toutes doivent converger vers **une comptabilité économique unique**.

---

# Matrice

| Chemin | Code | Protection actuelle | Manque | Priorité |
|---|---|---|---|---|
| Salad Container Engine | `octopus/salad.py` | provider + idempotence | callers peuvent théoriquement appeler le provider directement | **P0** |
| GPU.ai instance | `octopus/gpuai.py` | lifecycle + stop | même risque de bypass | **P0** |
| Guarded compute | `octopus/compute_finance.py` | hard caps, allowance, watchdog | pas encore imposé partout | **P0** |
| OmniRoute `zero_cost` | `octopus/catalog.py`, `llm.py` | cost_class free | upstream réel/coût non attesté | **P0** |
| Direct DeepSeek legacy | `agents/deepseek.py` avec `OCTOPUS=off` | opt-out explicite | contourne journal/budget moderne | **P1/P0 runtime** |
| RunPod renderer final | `octopus/video/*` | allowance + estimate + anti-double-submit | pas de settlement réel, timeout n'arrête pas forcément le job | **P1** |
| MiniMax H3 RunPod | `octopus/media/handlers.py` | allowance par variante + état SUBMITTING | pas de settlement réel, cancel/timeout distant incomplet | **P1** |
| WanGP MCP distant | `octopus/media/wangp_mcp.py` | job id + polling | coût du GPU hôte non lié au job | **P1** si hôte payant |
| LLM payants | `octopus/llm.py` | budgets run/jour/business | pas d'allowance économie | **P1** |
| Azure TTS | `tools/tts_providers.py` | fallback si erreur | peut être metered selon compte, aucune gate | **P1/P2** |
| Cloudflare Workers AI | idem | fallback | peut être metered selon compte, aucune gate | **P1/P2** |
| HF Space / Chatterbox distant | idem | quota/erreur | coût éventuel du Space non modélisé | **P2** |
| Brave Search | `agents/search.py` | clé optionnelle | plan/quota non qualifié dans economy | **P2** |
| Tavily Search | idem | clé optionnelle | idem | **P2** |
| S3/R2 | `octopus/video/storage.py` | abstraction storage | stockage/egress non journalisé | **P2** |
| Channel executor futur | `octopus/actions.py` | accès act + spend_amount optionnel | executor pourrait coûter sans déclarer spend_amount | **P1 futur** |
| `agents._verify --live` | outil dev | gros warning + opt-in | appels réels + données | **P2 manuel** |
| `tools/deepseek_smoke.py` | outil dev | manuel | direct API hors gateway | **P2 manuel** |

---

# 1. P0 — compute provisionné contournable

Le nouveau chemin sécurisé est :

```text
ComputeBroker
→ FinancialCircuitBreaker.reserve
→ allowance USD
→ GuardedComputeManager.provision
→ provider.create
```

Mais `SaladClient.create()` et `GPUAIClient.create()` restent des méthodes publiques Python.

Il n'existe pas encore de preuve que tous les futurs callers utilisent exclusivement `GuardedComputeManager`.

## Cible

Ajouter une règle architecturale testable :

> Aucun module métier/handler ne peut appeler `.create()` sur un provider compute payant.

Seuls :

```text
octopus/compute_finance.py
tests/
provider internals
```

peuvent le faire.

### Test recommandé

Test AST ou scan déterministe des imports/appels interdits.

Il doit échouer si quelqu'un ajoute plus tard :

```python
SaladClient(...).create(...)
```

dans un handler métier.

---

# 2. P0 — OmniRoute peut masquer la nature financière de l'upstream

Voir `LLM_BRAIN_AUDIT_2026-09-19.md`.

OCTOPUS enregistre le modèle virtuel comme gratuit, pas la cible réellement exécutée.

Avant de prétendre « LLM = 0 $ », il faut attester le resolved target ou isoler un pool OmniRoute free-only.

---

# 3. P1 — RunPod renderer final : autorisation sans settlement

`octopus/video/renderers.py::economy_spend_gate()` appelle :

```text
economy.gate_paid_call(...)
```

avant chaque soumission.

Points positifs :

- estimation obligatoire par env ;
- aucune allowance → refus ;
- l'état `SUBMITTING` protège contre une double soumission ;
- retry terminal borné.

Mais `gate_paid_call` crée un `spend_request=authorized`.

Le renderer ne le marque ensuite ni :

- `executed` au coût réel ;
- ni `cancelled` lorsqu'aucun coût n'a été engagé.

Donc le système sait réserver, mais pas rapprocher.

## Deuxième risque

`CloudVideoClient.wait()` peut expirer côté client.

Cela ne prouve pas que le job distant a cessé.

Le client possède une primitive `cancel()`, mais le renderer normal n'effectue pas automatiquement un cancel distant à l'expiration et RunPodConfig ne configure pas ici de cancel URL.

## Cible

Créer une primitive commune pour appels metered :

```text
MeteredSpendLease
 reserve(estimate)
 mark_submitted(remote_id)
 settle(actual_cost)
 cancel_unspent()
 mark_ambiguous()
```

Ce n'est pas le même objet qu'une instance GPU.

---

# 4. P1 — MiniMax H3

Le chemin H3 est bien plus sûr qu'un retry naïf :

```text
allowance
→ état SUBMITTING sur disque
→ submit
→ remote_id
→ wait
```

Si le POST devient ambigu, aucun retry automatique.

Mais :

- le `spend_request` estimé n'est pas rapproché ;
- `wait()` timeout ne cancel pas le job distant ;
- une annulation OCTOPUS entre deux polls n'est pas propagée à RunPod dans ce client ;
- le coût réel par variante n'est pas inscrit dans le ledger.

Même cible : `MeteredSpendLease`.

---

# 5. P1 — WanGP MCP distant

Le client MCP lui-même ne crée pas de GPU.

Il peut toutefois pointer vers un WanGP qui tourne sur :

- Salad ;
- GPU.ai ;
- autre GPU loué.

Dans ce cas :

```text
job WanGP
≠
preuve que la ressource compute qui l'héberge est financièrement gardée
```

## Cible

Tout endpoint WanGP distant payant doit avoir un `compute_lease_id` ou une ressource déclarée indiquant :

```text
provider
reservation
resource_id
started_at
owner
```

Un job ne doit pas pouvoir utiliser un endpoint cloud « orphelin » sans ownership financier explicite.

---

# 6. P1 — LLM payants

`octopus.llm` protège déjà correctement contre beaucoup de dérives avec des budgets.

Mais les budgets LLM et les allowances économiques sont deux systèmes séparés.

## Cible

Conserver les limites LLM pour leur granularité, mais faire passer tout appel `cost_class=paid` par une réservation économique.

Le budget LLM dit :

> « ce run ne dépassera pas X »

L'allowance dit :

> « l'humain/policy a réellement autorisé cette dépense »

Les deux sont complémentaires.

---

# 7. P1/P2 — TTS cloud

La chaîne par défaut est :

```text
azure
→ cloudflare
→ chatterbox
→ piper
```

Le code suppose historiquement des free tiers, mais une clé valide peut appartenir à un compte payant.

## Risque

Présence d'une clé ≠ gratuité.

## Cible

Introduire une politique déclarative :

```text
TTS_PROVIDER_COST_CLASS
azure=free_quota|paid
cloudflare=free_quota|paid
...
```

ou déclarer ces services dans `octopus.resources`.

Si un provider est déclaré `paid`, le segment/batch doit obtenir une allowance.

Ne pas ralentir la V1 avec une comptabilité au caractère tant qu'aucun provider payant n'est utilisé.

---

# 8. P2 — search APIs

Brave/Tavily sont utilisés seulement si une clé est présente.

Même logique :

```text
clé ≠ garantie free
```

À court terme : ressource déclarée avec cost class.

À long terme : quota/billing observé.

---

# 9. P2 — stockage S3/R2

Le worker vidéo utilise S3/S3-compatible pour artefacts.

Les coûts probables :

- stockage ;
- PUT/GET ;
- egress.

Ils sont faibles par vidéo mais deviennent visibles à grande échelle.

## Cible

Ne pas bloquer chaque upload avec une allowance.

À la place :

- resource `storage` ;
- budget mensuel / journalier infrastructure ;
- import du coût observé fournisseur dans le ledger.

---

# 10. Channel actions

`octopus.actions.propose()` est bien conçu :

- canal actif ;
- accès `act` accordé ;
- executor enregistré ;
- `spend_amount` → allowance ;
- résultat avec source → preuve observée.

Mais le contrat fait confiance au caller pour déclarer `spend_amount`.

Quand de vrais executors payants seront ajoutés, le **connector** doit déclarer son coût ou sa classe, pas le modèle LLM.

---

# Architecture financière cible

```text
                          economy / ledger
                                │
              ┌─────────────────┼──────────────────┐
              │                 │                  │
              ▼                 ▼                  ▼
       TokenSpendLease    MeteredSpendLease    ComputeLease
            LLM             API/serverless       instance GPU
              │                 │                  │
        reserve/settle      reserve/settle    reserve/watch/stop
```

Une seule source de vérité : `spend_requests` + ledger.

Des lifecycles différents selon le type de dépense.

---

# Ordre de correction Work

## P0-A

Empêcher les bypasses Salad/GPU.ai.

## P0-B

Rendre `zero_cost` LLM attestable.

## P1-A

Créer `MeteredSpendLease` et migrer RunPod renderer + H3.

## P1-B

Relier LLM payants aux allowances.

## P1-C

Déclarer TTS/search payants comme ressources/cost classes.

## P2

Importer les coûts storage/infra observés à fréquence agrégée.

---

# Critère final

Une recherche SQL doit pouvoir répondre :

> « combien OCTOPUS a réellement engagé et dépensé aujourd'hui, par business et par catégorie ? »

sans addition manuelle de dashboards cloud.
