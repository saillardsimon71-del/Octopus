# CODEX_START — Point d'entrée développement

Ce fichier est volontairement court. Les détails sont dans les documents liés.

## 1. Commencer ici

Avant toute modification :

```bash
git status
git branch --show-current
git log -10 --oneline
git diff
```

Branche de référence actuelle :

```text
feat/salad-compute-provider
```

PR actuelle :

```text
#2 — Draft
```

Ne jamais travailler directement sur `main`.

Si le worktree local contient des modifications inconnues, les préserver et les examiner avant toute action.

## 2. Lire

Ordre minimum :

1. `AGENTS.md`
2. `docs/VISION.md`
3. `docs/CURRENT_STATE.md`
4. `docs/ACCEPTANCE_GATES.md`
5. `NEXT_STEPS.md`
6. audits/designs spécifiques à la mission.

Pour le chantier actuel, lire aussi :

- `docs/audits/LLM_BRAIN_AUDIT_2026-09-19.md`
- `docs/audits/PAID_PATHS_AUDIT_2026-09-19.md`
- `docs/COMPUTE_GPU.md`

## 3. Mission courante

Priorité :

```text
G1 — cerveau LLM
+
G2 — frontière financière
```

Avant le canary Salad.

### G1

- rendre le fallback JSON/validation effectif dans `octopus.llm` ;
- connaître le provider/modèle réellement résolu derrière OmniRoute ;
- garantir que `zero_cost` reste réellement gratuit ;
- aucun fallback LLM payant implicite ;
- DeepSeek payant n'est pas une dépendance normale.

### G2

- empêcher les bypasses directs Salad/GPU.ai ;
- imposer `GuardedComputeManager` au compute provisionné ;
- créer une abstraction de settlement pour RunPod/H3 metered ;
- conserver un seul ledger ;
- rendre le watchdog réellement indépendant.

## 4. Ce qui a déjà été fait

Ne pas refaire sans preuve de divergence :

- audit architecture LLM ;
- audit chemins payants ;
- design worker Salad Wan ;
- protocole benchmark GPU ;
- gates d'acceptation ;
- compute broker ;
- provider Salad ;
- breaker financier ;
- watchdog initial.

## 5. Tests

Toujours commencer par les tests directement touchés.

Compute minimum :

```bash
python -m pytest -q \
  tests/test_compute_finance.py \
  tests/test_compute_salad.py \
  tests/test_compute_broker.py \
  tests/test_compute_gpuai.py
```

Pour G1, exécuter aussi les suites gateway/OmniRoute concernées.

Puis exécuter les suites transversales affectées.

Aucun test ne doit lancer une ressource payante réelle.

## 6. Discipline

Une session doit avoir :

```text
gate visée
preuve manquante
modifications
tests
résultat
```

Ne pas annoncer DONE si une preuve de gate manque.

Utiliser :

```text
PARTIAL — preuve manquante : ...
```

## 7. Fin de session

Avant d'arrêter :

- état Git connu ;
- tests réellement exécutés ;
- commits cohérents ;
- `docs/CURRENT_STATE.md` mis à jour si nécessaire ;
- `NEXT_STEPS.md` mis à jour si nécessaire ;
- PR mise à jour si le périmètre réel change.

## Prompt minimal pour une nouvelle session Codex

```text
Lis intégralement AGENTS.md puis docs/CODEX_START.md.
Vérifie l'état Git réel.
Travaille sur la branche prévue.
Exécute la mission courante jusqu'aux critères de docs/ACCEPTANCE_GATES.md.
Ne refais pas les audits déjà versionnés.
Ne marque jamais une gate DONE sans preuve et tests.
```
