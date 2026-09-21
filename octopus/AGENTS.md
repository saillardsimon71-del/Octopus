# octopus/AGENTS.md — Règles du noyau OCTOPUS

Ces instructions complètent le `AGENTS.md` racine pour tout travail dans `octopus/`.

## 1. Frontières du noyau

Le package `octopus` est le moteur partagé.

Ne pas introduire de logique spécifique à un nouveau business. Réutiliser les fonctions et objets
existants avant d'envisager un contrat générique ou un adapter supplémentaire.

Préserver les frontières :

```text
tasks      → orchestration durable
journal    → persistance / audit
strategy   → épistémologie / expériences / décisions
economy    → argent / allowances / ledger
resources  → ressources réelles
llm        → politiques et appels LLM
compute    → allocation de compute
media      → génération/artefacts
actions    → actions externes contrôlées
```

Éviter les dépendances circulaires et les duplications de responsabilité.

## 2. LLM

### Politique normale

```text
zero_cost uniquement
```

Un caller du noyau ne doit jamais sélectionner DeepSeek, Gemini, Groq ou autre fournisseur par marque sauf adapter/compatibilité.

Il demande :

- une tâche ;
- des capacités ;
- un niveau de confidentialité ;
- éventuellement une contrainte de latence/qualité.

Le gateway décide.

### Fallback

Les validateurs doivent être exécutés **dans** `octopus.llm` lorsque le fallback dépend de leur résultat.

Exemple :

```text
réponse JSON invalide
→ status invalid
→ candidat gratuit suivant
```

et non parsing tardif dans le caller.

### Observabilité

Journaliser autant que possible :

- route demandée ;
- provider/model réellement résolu ;
- usage ;
- latence ;
- coût ou nature du coût ;
- statut ;
- tentative.

Ne jamais transformer « route nommée free » en preuve financière sans attestation.

## 3. Économie

`octopus.economy` est l'unique système économique.

Tout nouveau mécanisme de coût doit finir dans :

- allowances ;
- spend requests ;
- ledger.

Les estimations et coûts observés doivent rester distincts.

### Compute provisionné

Chemin obligatoire :

```text
ComputeBroker
→ FinancialCircuitBreaker
→ GuardedComputeManager
→ provider
```

Aucun handler métier ne doit créer directement une instance Salad/GPU.ai.

### Appels metered/serverless

Ils ont un lifecycle différent du compute persistant.

Conserver les mécanismes existants. Seulement si une mission exige un chemin metered manquant,
son lifecycle doit couvrir :

```text
reserve
→ submitted
→ ambiguous | completed | cancelled
→ settle
```

sans dupliquer le ledger.

## 4. Persistance

Toute information nécessaire à la reprise après crash doit vivre hors mémoire volatile.

Éviter les états critiques uniquement dans :

- variables Python ;
- disque éphémère GPU ;
- fichiers temporaires non journalisés.

SQLite OCTOPUS est la source locale durable sauf quand un stockage externe explicitement versionné est requis.

## 5. Idempotence

Chaque action externe coûteuse ou non réversible doit avoir une identité stable.

Une erreur réseau après soumission ne signifie jamais :

```text
retry immédiatement
```

Elle signifie :

```text
AMBIGUOUS
→ reconcile
→ seulement ensuite décider
```

## 6. Tests

Pour chaque nouvelle frontière :

- happy path ;
- refus ;
- crash/restart ;
- idempotence ;
- timeout ambigu ;
- concurrence si budget/état partagé ;
- provider indisponible ;
- état persistant.

Les tests doivent utiliser fakes/mocks et ne jamais engager de dépenses réelles.

## 7. Compatibilité

Ne pas supprimer brutalement les wrappers historiques comme `agents/deepseek.py` ou les adapters vidéo.

Les migrer progressivement derrière les contrats modernes, avec tests de non-régression.

## 8. Changements d'architecture

Avant toute nouvelle abstraction, vérifier qu'une abstraction équivalente n'existe pas déjà.

Préférer :

```text
étendre proprement
```

à :

```text
créer un nouveau moteur parallèle
```

En particulier :

- pas de second ledger ;
- pas de second scheduler ;
- pas de second runtime agentique ;
- pas de second LLM gateway ;
- pas de logique de budget dans les providers.
