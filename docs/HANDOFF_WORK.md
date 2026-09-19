# Handoff — reprise propre dans Work

Ce document est conçu pour reprendre OCTOPUS sans repartir de zéro ni suivre un ancien état périmé.

## Contexte

Branche active de référence :

```text
feat/salad-compute-provider
```

PR : **#2**, encore en Draft.

Lire d'abord :

1. `docs/CURRENT_STATE.md`
2. `docs/ACCEPTANCE_GATES.md`
3. `docs/audits/LLM_BRAIN_AUDIT_2026-09-19.md`
4. `docs/audits/PAID_PATHS_AUDIT_2026-09-19.md`
5. `docs/design/SALAD_WAN_WORKER_V1.md`
6. `docs/benchmarks/GPU_COST_BENCHMARK_PLAN.md`
7. `docs/COMPUTE_GPU.md`
8. `NEXT_STEPS.md`
9. seulement ensuite les runbooks techniques utiles.

Les documents sous `docs/archive/` sont historiques.

## Mission immédiate

La prochaine session vise **G1 + G2** de `docs/ACCEPTANCE_GATES.md` avant tout canary réel.

Deux P0 ont été identifiés pendant l'audit pré-Work :

1. rendre le compute GPU **impossible à contourner** ;
2. rendre le profil LLM `zero_cost` **attestable de bout en bout**, et faire passer les validateurs JSON/vision dans `octopus.llm` afin que le fallback fonctionne réellement.

Le canary Salad vient seulement après ces deux frontières.

### Étape A — vérifier l'état réel

Avant toute modification :

```text
git status
git branch --show-current
git log -10 --oneline
git diff
```

Vérifier que la branche attendue est active et que l'arbre local n'a pas de travail utilisateur non intégré.

Comparer avec GitHub si nécessaire. Ne jamais écraser un worktree local inconnu.

### Étape B — cartographier les bypasses

Chercher tous les chemins capables de créer/lancer une ressource payante :

```text
provider.create(
SaladClient(
GPUAIClient(
RunPod
cloud renderer
subprocess / worker GPU
media.video_generate
WanGP / Wan2GP
```

Classer chaque chemin :

- gratuit/local ;
- payant mais déjà gardé ;
- payant et contournant le breaker ;
- inconnu.

**Critère de done : aucun nouveau compute GPU payant ne peut démarrer sans réservation persistante et allowance.**

### Étape C — imposer le chemin unique

Le chemin cible est :

```text
caller
  → ComputeBroker.select()
  → FinancialCircuitBreaker.reserve()
  → GuardedComputeManager.provision()
  → provider.create()
```

Ne pas dupliquer la logique de budget dans les callers.

Les providers restent des adaptateurs techniques ; la politique financière vit au-dessus.

### Étape D — watchdog indépendant

`ops/compute_watchdog.py` existe.

À vérifier / compléter :

- lancement séparé du worker vidéo ;
- redémarrage automatique du watchdog lui-même ;
- accès au même SQLite ;
- credentials provider ;
- sweep toutes les 5 s par défaut ;
- réconciliation après reboot ;
- arrêt idempotent ;
- logs exploitables.

Le watchdog ne doit pas dépendre du processus qui produit la vidéo.

### Étape E — tests avant argent réel

Minimum :

```bash
python -m pytest -q \
  tests/test_compute_finance.py \
  tests/test_compute_salad.py \
  tests/test_compute_broker.py \
  tests/test_compute_gpuai.py
```

Puis la suite `video-foundation` / suites touchées par le changement.

Ajouter des tests si un nouveau chemin payant est branché.

### Étape F — canary Salad

Seulement après les étapes précédentes.

Conditions :

- auto-recharge désactivée côté compte si disponible ;
- allowance OCTOPUS minuscule ;
- un seul job ;
- workload court et déterministe ;
- GPU économique ;
- watchdog réellement actif ;
- observer création → running → génération → stop → rapprochement coût.

Le premier canary sert à tester la **facturation et l'arrêt**, pas la qualité finale.

### Étape G — benchmark coût/vidéo

Comparer le même workload, mêmes paramètres, mêmes poids.

Enregistrer au minimum :

- provider ;
- GPU ;
- prix/h ;
- temps de provisionnement ;
- temps de téléchargement/cache ;
- temps d'inférence ;
- temps total facturé ;
- vidéos terminées ;
- coût réel ;
- coût réel/vidéo ;
- erreurs/interruption.

Le broker devra ensuite optimiser le **coût par vidéo**, pas le prix horaire.

## Contraintes à préserver

- pas de modification directe de `main` ;
- pas de secret versionné ;
- pas de dépense sans allowance ;
- pas de retry de création payante ambiguë ;
- pas de nouveau ledger financier ;
- pas de données business inventées ;
- pas de suppression de fonctionnalités historiques sans migration/test ;
- garder les changements petits et testables.

## Fin de session attendue

Avant d'arrêter Work :

1. tests exécutés ;
2. état Git propre ou explicitement documenté ;
3. `docs/CURRENT_STATE.md` mis à jour si l'état a changé ;
4. `NEXT_STEPS.md` réduit aux vraies prochaines actions ;
5. aucun ancien fichier d'état créé à la racine ;
6. PR mise à jour avec ce qui est réellement vérifié.


## Pack d'analyse préparé avant Work

Les cinq travaux préparatoires demandés sont terminés et versionnés :

- audit cerveau LLM : `docs/audits/LLM_BRAIN_AUDIT_2026-09-19.md` ;
- audit dépenses : `docs/audits/PAID_PATHS_AUDIT_2026-09-19.md` ;
- design worker Salad : `docs/design/SALAD_WAN_WORKER_V1.md` ;
- protocole benchmark : `docs/benchmarks/GPU_COST_BENCHMARK_PLAN.md` ;
- critères binaires : `docs/ACCEPTANCE_GATES.md`.

Ne refaire ces audits que si le code a changé matériellement. Utiliser les findings pour coder directement.
