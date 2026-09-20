# Audit du cerveau LLM / OmniRoute — 2026-09-19

## Résumé exécutif

Le cerveau LLM d'OCTOPUS est déjà une architecture en couches cohérente :

```text
ORBIT / agents
    ↓
agents/runtime.py             boucle ReAct + outils
    ↓
agents/deepseek.py            compatibilité historique
    ↓
octopus/llm.py                routage, budgets, journal, fallback
    ↓
octopus/catalog.py            profils + overlay OmniRoute
    ↓
OmniRoute                     routage aval dynamique
    ↓
LLM réellement exécuté
```

La bonne direction n'est **pas** de réécrire ce système. Il faut transformer la passerelle actuelle en véritable **LLMBroker observable et fail-closed**.

Les deux problèmes prioritaires sont :

1. le profil OCTOPUS `zero_cost` ne peut pas encore **prouver** quel modèle/fournisseur OmniRoute a réellement utilisé ni son coût réel ;
2. les wrappers historiques JSON/vision valident la sortie **après** `octopus.llm`, ce qui court-circuite le fallback sur sortie invalide.

## Périmètre vérifié

Code relu :

- `agents/runtime.py`
- `agents/deepseek.py`
- `agents/agents.py`
- `agents/doctor.py`
- `octopus/llm.py`
- `octopus/catalog.py`
- `octopus/config/catalog.json`
- `octopus/bench.py`
- `octopus/pricing.py`
- `octopus/report.py`
- `tests/test_gateway.py`
- `tests/test_omniroute.py`
- `docs/OMNIROUTE_SETUP.md`

Documentation OmniRoute re-vérifiée le 19/09/2026 :

- Auto-Combo : https://github.com/diegosouzapw/OmniRoute/blob/main/docs/routing/AUTO-COMBO.md
- API : https://github.com/diegosouzapw/OmniRoute/blob/main/docs/reference/API_REFERENCE.md

## 1. Ce qui est déjà bon

### 1.1 Un point d'entrée LLM central existe

`octopus.llm.complete()` fournit déjà :

- profils de coût ;
- sélection de candidats ;
- filtrage par capacités ;
- filtrage de confidentialité ;
- estimation pré-appel ;
- budget de run ;
- budget journalier global ;
- budget journalier business ;
- fallback réseau ;
- fallback sur sortie invalide **si** un validateur lui est fourni ;
- journalisation tokens / coût / durée ;
- justification du choix ;
- preuves issues du banc.

Ce n'est donc pas une simple fonction API : c'est déjà le noyau d'un broker.

### 1.2 Le catalogue sépare tâche, modèle et profil

`octopus/config/catalog.json` ne demande pas directement un fournisseur dans les agents. Les tâches portent des besoins :

```text
podalux.write_job
podalux.qc_vision
podalux.arbitrate
agent.react_step
agent.plan
agent.synthesize
...
```

et les profils déterminent les classes de coût autorisées.

Cette abstraction est à conserver.

### 1.3 OmniRoute est injecté dynamiquement

`octopus/catalog.py::_overlay_omniroute()` ajoute le provider et le modèle virtuel sans polluer le catalogue statique.

Quand OmniRoute est activé et qu'aucun profil explicite n'est imposé :

```text
legacy → zero_cost
```

C'est un bon comportement par défaut.

### 1.4 Le banc existe réellement

`octopus/bench.py` sait mesurer :

- taux de réussite ;
- score ;
- latence p50 ;
- coût moyen ;
- erreurs ;
- stabilité entre répétitions.

Le journal utilise ensuite ces preuves pour déclarer un modèle alternatif éligible.

La pièce manquante n'est donc pas « créer des benchmarks » mais **les utiliser pour choisir**, pas uniquement pour autoriser.

---

# 2. Findings prioritaires

## P0-LLM-1 — `zero_cost` n'est pas attesté de bout en bout

### État actuel

OCTOPUS injecte :

```text
model_id    = omniroute/auto-free
api_model   = auto/best-free
cost_class  = free_quota
price       = absent
```

La passerelle OCTOPUS enregistre donc :

```text
provider = omniroute
model    = omniroute/auto-free
cost     = 0
```

mais elle ne sait pas quel modèle aval a réellement servi la requête.

OmniRoute construit les routes `auto/*` à partir des connexions actives. Sa documentation actuelle indique aussi que certains filtres category/tier sont **fail-open** : s'ils ne trouvent aucun candidat correspondant, le pool général peut être repris.

Cela ne signifie pas que `auto/best-free` va forcément payer. Cela signifie qu'OCTOPUS n'a aujourd'hui **aucune preuve indépendante** permettant d'affirmer que « zero_cost = exactement 0 $ » lorsque des connexions payantes coexistent dans OmniRoute.

### Pourquoi c'est P0

Le nom `zero_cost` est une promesse financière.

Une promesse financière doit être fail-closed, pas simplement fondée sur le nom d'un modèle virtuel.

### Cible

OCTOPUS doit connaître au minimum après chaque réponse :

```text
requested_route
resolved_provider
resolved_model
actual_or_attested_cost
request_id
```

et refuser/alerter si la route réelle viole la politique.

### Recommandation

Pour la V1 :

**Option la plus sûre : isoler un pool OmniRoute strictement gratuit pour OCTOPUS.**

```text
OCTOPUS zero_cost key / instance
        ↓
OmniRoute
        ↓
connexions gratuites uniquement
```

Ne pas compter uniquement sur un suffixe `:free` comme frontière de sécurité.

Ensuite, récupérer l'identité réellement routée. La documentation/les issues OmniRoute indiquent que les métadonnées de réponse peuvent exposer le modèle réellement exécuté ; il faut le vérifier sur la version locale et le tester avant de l'utiliser comme attestation.

## P0-LLM-2 — Le fallback « sortie invalide » est contourné par les wrappers historiques

`octopus.llm.complete()` sait faire :

```text
modèle A
→ sortie invalide
→ validate() échoue
→ modèle B
```

et `tests/test_gateway.py::test_invalid_output_falls_back` le couvre.

Mais `agents/deepseek.call_json()` fait actuellement :

```text
llm.complete(json_mode=True)
→ retourne texte considéré OK
→ regex JSON dans agents/deepseek.py
→ json.loads
→ exception hors du gateway
```

Donc si OmniRoute renvoie du JSON cassé, le gateway a déjà enregistré l'appel comme `ok` et ne teste pas le candidat suivant.

Même problème conceptuel pour `vision()` et plusieurs validateurs métier appelés après le wrapper.

### Cible

Le parsing/validateur doit être passé à `llm.complete(validate=...)`.

Exemple cible :

```text
call_json
→ llm.complete(..., json_mode=True, validate=llm.parse_json)
→ Completion.data
```

Pour `CONVERT` :

```text
validate = parse_json + validate_job
```

Pour le QC :

```text
validate = parse_json + validate_verdict
```

Ainsi le fallback devient réel.

---

## P1-LLM-3 — Le modèle virtuel OmniRoute sur-déclare ses capacités

L'overlay actuel lui donne :

```text
json
vision
tools
reasoning_effort
```

alors que le backend réellement sélectionné varie.

Le protocole OpenAI-compatible traduit beaucoup de différences, mais « route virtuelle capable » n'est pas équivalent à « chaque candidat aval supporte correctement la capacité ».

### Cible

Déclarer plusieurs routes virtuelles par intention :

```text
omniroute/general-free
omniroute/reasoning-free
omniroute/vision-free
omniroute/coding-free
```

et les mapper à des routes OmniRoute adaptées, par exemple les catégories documentées `chat`, `reasoning`, `multimodal`, `coding`.

Le mapping exact doit être détecté via `/v1/models` de **l'instance locale**, pas codé aveuglément à partir d'une documentation distante.

## P1-LLM-4 — Le transport jette l'identité réelle du modèle

`_transport()` renvoie :

```python
(text, Usage)
```

Il perd :

- `response.model` ;
- éventuels headers OmniRoute ;
- request id ;
- métadonnées de cache ;
- coût/route si exposés par le gateway.

### Cible

Créer un résultat de transport structuré :

```python
TransportResult(
    text,
    usage,
    requested_model,
    resolved_model,
    resolved_provider,
    request_id,
    cache_status,
    provider_cost_usd,
)
```

Puis enregistrer ces champs dans `llm_calls`.

## P1-LLM-5 — Deux sources de vérité pour le profil par défaut

La logique existe à deux endroits :

- `Catalog.default_profile`
- `agents.deepseek._default_profile()`

Le wrapper passe ensuite explicitement le profil à `llm.complete`.

Aujourd'hui les deux sont alignés. À terme ils peuvent diverger.

### Cible

Une seule résolution du profil dans `octopus.llm` / `catalog`.

Le wrapper legacy ne devrait fournir un profil que si le caller le demande explicitement.

## P1-LLM-6 — `OCTOPUS=off` désactive toutes les protections modernes

C'est volontaire et testé :

```text
OCTOPUS=off
→ direct DeepSeek
→ pas de journal OCTOPUS
→ pas de budget gateway
```

C'était utile pendant la migration.

C'est dangereux comme simple variable d'environnement dans un runtime de production.

### Cible

Conserver le kill-switch pour développement, mais demander une seconde opt-in explicite :

```text
OCTOPUS=off
OCTOPUS_ALLOW_LEGACY_DIRECT=1
```

ou rendre ce mode inaccessible depuis les entrées de production.

## P1-LLM-7 — Les LLM payants ne passent pas par les allowances économiques

Les appels LLM payants sont protégés par :

- budget run ;
- budget journalier ;
- budget business.

Ils ne créent pas de `spend_request` dans `octopus.economy`.

Donc la règle « aucune dépense sans allowance humaine/policy » n'est pas uniforme.

### Cible

Si `cost_class == paid` :

```text
estimate
→ réserve une allowance
→ appel
→ settle coût réel
→ libère le reliquat
```

Ne pas imposer cette mécanique aux routes réellement gratuites/locales.

## P2-LLM-8 — Le benchmark autorise, mais ne choisit pas le meilleur candidat

Le banc calcule score, latence et coût.

Le gateway sélectionne encore essentiellement **le premier candidat éligible dans une liste statique**.

### Cible

Faire du candidat un score :

```text
quality
latency
reliability
cost
privacy
quota headroom
```

Pour OmniRoute, le plus simple peut être de benchmarker les **routes OmniRoute** elles-mêmes, puis laisser OmniRoute sélectionner son upstream.

Exemple :

```text
agent.plan
  route reasoning-free → 96% pass, 2.1s
  route general-free   → 88% pass, 1.0s

→ reasoning-free
```

## P2-LLM-9 — Le catalogue statique mélange configuration et données temporelles

Les quotas/prix/provider notes évoluent.

### Cible

Séparer :

```text
catalog statique
  = tâches, privacy, capacités requises, politiques

discovery runtime
  = modèles disponibles, contextes, prix/quota, état de santé
```

OmniRoute expose aujourd'hui `/v1/models`, `/api/pricing` et de l'historique d'usage ; ces interfaces peuvent alimenter un snapshot, mais elles doivent rester des données observées et datées.

---

# 3. Architecture cible : LLMBroker V2

```text
Agent / ORBIT
    │
    ▼
LLMTaskRequest
 task
 privacy
 needs
 max_output
 cost_policy
 business
    │
    ▼
LLMPolicy
    │
    ├─ sensitive → local only
    ├─ zero_cost → attested free only
    └─ paid → allowance + budget
    │
    ▼
LLMBroker
    │
    ├─ historique bench
    ├─ santé
    ├─ latence
    ├─ coût
    └─ quota
    │
    ▼
Transport
    │
    ▼
ResolvedLLMCall
 requested route
 actual model
 usage
 actual/attested cost
    │
    ▼
validator
  ├─ OK → journal
  └─ KO → candidat suivant
```

Le runtime agentique ne change pas.

---

# 4. Batches d'implémentation recommandés pour Work

## Batch L1 — rendre le fallback JSON réel

Fichiers probables :

- `agents/deepseek.py`
- `octopus/llm.py`
- `agents/agents.py`
- `tests/test_gateway.py`

Done si :

- JSON invalide d'OmniRoute déclenche le candidat suivant ;
- verdict QC invalide peut fallback ;
- le premier appel est journalisé `invalid`, pas `ok` ;
- aucune régression legacy.

## Batch L2 — capturer la route OmniRoute réelle

Done si le journal contient :

```text
requested_model
resolved_model
resolved_provider
request_id
```

avec tests hors-réseau simulant les métadonnées OmniRoute.

## Batch L3 — sécuriser `zero_cost`

Done si :

- un provider payant connecté ne peut pas être utilisé silencieusement ;
- un résultat dont le coût ne peut pas être attesté est refusé ou marqué non conforme ;
- panne du pool gratuit = échec explicite, pas dépense.

## Batch L4 — routes par capacité

Done si :

- vision n'utilise qu'une route multimodale vérifiée ;
- planification/arbitrage utilise une route reasoning vérifiée ;
- général utilise une route simple ;
- l'instance locale `/v1/models` est la source de disponibilité.

## Batch L5 — paid LLM → économie

Done si tout LLM payant a un `spend_request` et un settlement réel.

## Batch L6 — broker mesuré

Ajouter une sélection fondée sur les données du bench au lieu de l'ordre statique.

---

# 5. Ce qu'il ne faut pas faire

- ne pas supprimer `agents/deepseek.py` brutalement ;
- ne pas renommer toute la couche maintenant ;
- ne pas mettre un modèle précis dans les prompts des agents ;
- ne pas considérer `localhost` comme « local data » : OmniRoute retransmet ;
- ne pas faire confiance au mot `free` comme preuve comptable ;
- ne pas ajouter un second routeur concurrent d'OmniRoute.

La cible est : **OCTOPUS décide de la politique ; OmniRoute exécute le routage aval.**
