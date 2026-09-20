# AGENTS.md — Constitution du projet OCTOPUS

Ce fichier contient les règles durables qu'un agent de développement doit charger avant de travailler sur OCTOPUS.

Il décrit **pourquoi** le projet existe, ses invariants, son architecture mentale et la manière de décider. Les fichiers d'état et de handoff décrivent **où le projet en est aujourd'hui**.

## 1. Ce qu'est OCTOPUS

OCTOPUS est un **moteur d'activités économiques autonomes**.

Il ne doit pas être réduit à :

- une usine à vidéos ;
- un bot YouTube/TikTok ;
- un ensemble d'agents LLM ;
- une collection d'automatisations ;
- une démonstration technique.

La vision cible est :

```text
ressources réelles
      ↓
observation
      ↓
objectifs / hypothèses / expériences
      ↓
raisonnement / décision
      ↓
actions dans le monde réel
      ↓
résultats observés
      ↓
coûts / revenus / métriques
      ↓
learning
      ↓
prochaine décision
      ↓
réinvestissement sous contraintes
```

Les activités comme Podalux, des chaînes de contenu ou de futurs business sont des **clients/modules du moteur OCTOPUS**, pas le moteur lui-même.

## 2. Ligne directrice

Le développement doit optimiser, dans cet ordre :

1. **boucles fermées réelles** plutôt que nombre de fonctionnalités ;
2. **autonomie mesurable** plutôt qu'autonomie déclarée ;
3. **coût réel par résultat utile** plutôt que sophistication technique ;
4. **fiabilité et reprise** plutôt que vitesse de prototypage ;
5. **provider-neutral** plutôt que dépendance à un fournisseur ;
6. **preuves observées** plutôt que valeurs supposées ;
7. **progression verticale** plutôt qu'expansion prématurée.

Une fonctionnalité n'a de valeur que si elle aide OCTOPUS à observer, décider, agir, mesurer ou apprendre plus correctement.

## 3. Invariants non négociables

### 3.1 LLM gratuits en fonctionnement normal

Le mode normal d'OCTOPUS est :

```text
LLM gratuits uniquement
```

Le routage cible :

```text
ORBIT / agents
→ octopus.llm
→ politique zero_cost
→ OmniRoute
→ meilleur modèle GRATUIT adapté à la tâche
```

DeepSeek n'a aucun statut privilégié. Le fichier `agents/deepseek.py` est principalement une couche de compatibilité historique.

Si aucun modèle gratuit admissible n'est disponible :

```text
FAIL / WAIT / HUMAN
```

et jamais un fallback payant implicite.

Un LLM payant ne peut être utilisé que dans un mode exceptionnel explicitement activé par politique humaine. Ce n'est pas une priorité de la V1.

### 3.2 Fail-closed

Si OCTOPUS ne peut pas prouver :

- qu'une dépense est autorisée ;
- qu'un fournisseur est gratuit ;
- qu'une ressource est sous plafond ;
- qu'une donnée est réellement observée ;
- qu'une soumission distante a échoué ;

alors il doit **bloquer, attendre ou demander un humain**.

Il ne doit ni dépenser, ni inventer, ni retry aveuglément.

### 3.3 Une seule vérité économique

`octopus.economy` + ledger + allowances + spend requests forment la source financière commune.

Ne jamais créer un second ledger parallèle pour :

- compute ;
- vidéo ;
- LLM ;
- API ;
- stockage.

Les lifecycles peuvent varier, la comptabilité finale reste unifiée.

### 3.4 Provider-neutral

Les couches métier ne doivent pas dépendre de noms de fournisseurs.

Cible :

```text
LLM policy/broker  → OmniRoute / providers
ComputeBroker      → Salad / GPU.ai / futurs providers
media contract     → backends vidéo
channel contract   → plateformes externes
```

Un provider est un adaptateur, jamais la politique métier.

### 3.5 Monde réel seulement

Une donnée n'est `observed` que si OCTOPUS possède une provenance réelle.

Ne jamais inventer :

- revenu ;
- client ;
- conversion ;
- analytics ;
- disponibilité ;
- facture ;
- performance ;
- résultat d'expérience.

Une valeur non prouvée doit être marquée `unverified`, estimée ou absente.

### 3.6 Human boundaries

L'humain fixe les limites :

- budgets ;
- accès ;
- business autorisés ;
- actions sensibles ;
- règles de risque.

OCTOPUS doit prendre le maximum de décisions **à l'intérieur** de ces limites sans demander inutilement confirmation.

### 3.7 Pas d'expansion prématurée

Ne pas lancer 10 chaînes, 10 comptes ou 10 business tant qu'une boucle n'est pas prouvée.

Ordre :

```text
une verticale
→ preuve économique
→ fiabilité
→ duplication
→ portefeuille
```

## 4. Verticale prioritaire actuelle

Le prochain grand jalon n'est pas « ajouter plus d'agents ».

Il est de fermer cette boucle :

```text
ORBIT
↓
planification
↓
production vidéo
↓
compute GPU < 1 centime / vidéo
↓
publication réelle
↓
analytics observées
↓
objectif / conversion / revenu
↓
ledger
↓
learning
↓
nouvelle décision
```

Les gates dans `docs/ACCEPTANCE_GATES.md` définissent les preuves nécessaires.

## 5. Architecture mentale à préserver

### Cerveau / agents

- `agents/runtime.py` : boucle agentique / ReAct / outils ;
- `octopus/llm.py` : gateway/broker LLM, budgets, fallback, journal ;
- `octopus/catalog.py` : politiques, tâches, profils, routage ;
- OmniRoute : routage aval des modèles gratuits ;
- ORBIT : coordination et missions.

Ne pas réécrire le runtime agentique sans problème mesuré.

### Control-plane

- `octopus.tasks` : queue durable, leases, reprise ;
- `octopus.journal` : journal d'exécution et persistance ;
- `octopus.strategy` : objectifs, hypothèses, expériences, preuves, décisions ;
- `octopus.resources` : inventaire et état des ressources ;
- `octopus.economy` : ledger, allowances, dépenses, cash.

### Compute

- providers : adaptateurs techniques ;
- `ComputeBroker` : sélection ;
- `FinancialCircuitBreaker` : limites ;
- `GuardedComputeManager` : frontière de provisionnement payant ;
- watchdog : indépendant du worker producteur.

### Média

La vidéo est une activité du moteur, pas son centre architectural.

FORGE/Remotion/FFmpeg, Wan, H3, RunPod, Salad ou WanGP doivent rester interchangeables derrière des contrats propres.

## 6. Politique de coût

Le critère principal est :

```text
coût réel / résultat utile réussi
```

Pas :

- prix horaire seul ;
- vitesse seule ;
- qualité maximale indépendamment du coût.

Exemples :

- LLM → gratuit par défaut, qualité/latence par tâche ;
- GPU → $/vidéo réussie ;
- APIs → coût réel par action ;
- stockage → coût agrégé observé.

Pour la génération vidéo, objectif courant :

```text
target GPU moyen <= $0.008 / vidéo
hard normal       <= $0.010 / vidéo
```

Voir `docs/benchmarks/GPU_COST_BENCHMARK_PLAN.md`.

## 7. Règles Git / tests

- ne jamais développer directement sur `main` ;
- vérifier `git status`, branche, log et diff avant modification ;
- ne jamais écraser un worktree utilisateur inconnu ;
- changements petits, cohérents, réversibles ;
- tests ciblés après chaque lot ;
- suites transversales si une frontière partagée est touchée ;
- ne jamais annoncer « vert » sans exécution réelle ;
- ne jamais lancer de ressource payante dans les tests ;
- aucun secret dans Git.

## 8. Source de vérité documentaire

Lire dans cet ordre avant une tâche importante :

1. `AGENTS.md`
2. `docs/VISION.md`
3. `docs/CURRENT_STATE.md`
4. `docs/ACCEPTANCE_GATES.md`
5. `docs/CODEX_START.md` pour une session Codex
6. `docs/HANDOFF_WORK.md` pour une session Work
7. `NEXT_STEPS.md`
8. audits/designs spécialisés nécessaires à la mission.

Les fichiers sous `docs/archive/` sont historiques.

Si Git et la documentation divergent :

```text
Git réel > documentation
```

Corriger ensuite la documentation.

## 9. Définition de DONE

Ne jamais utiliser DONE parce que :

- le code compile ;
- un fichier existe ;
- une IA affirme avoir terminé ;
- un happy path passe.

Utiliser les gates de `docs/ACCEPTANCE_GATES.md`.

Si une preuve manque :

```text
PARTIAL — preuve manquante : ...
```

## 10. Répartition des environnements

### Chat

Utiliser pour :

- décisions ;
- architecture ;
- stratégie ;
- arbitrages ;
- préparation de prompts.

### Work

Utiliser pour :

- audits longs ;
- recherche ;
- workflows multi-étapes ;
- exploration croisée fichiers/apps/web.

### Codex

Utiliser prioritairement pour :

- code ;
- terminal ;
- tests ;
- refactors ;
- Git ;
- commits ;
- PRs.

Le dépôt doit contenir assez de contexte pour que Codex n'ait pas besoin de l'historique complet des conversations.

## 11. Règle finale

Ne pas optimiser OCTOPUS pour « sembler autonome ».

Optimiser OCTOPUS pour :

```text
agir réellement
à coût borné
avec preuves
et améliorer ses décisions
à partir du monde réel.
```
