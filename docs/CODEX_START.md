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

### Worktree Codex

Si Codex travaille dans un worktree isolé en detached HEAD :

- ne pas modifier un autre worktree ;
- ne pas changer la branche du dépôt principal ;
- ne pas faire de `git switch` dans le répertoire principal utilisateur ;
- avant le premier commit, créer si nécessaire une branche de tâche basée exactement sur `feat/salad-compute-provider`.

Le dépôt principal utilisateur peut rester sur une autre branche. Ce n'est pas une erreur.


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

### Scope de CETTE session Codex

```text
G1 — cerveau LLM UNIQUEMENT
```

Ne pas commencer G2 après G1.

Quand le critère de sortie G1 de cette session est atteint :

1. arrêter les modifications ;
2. exécuter les tests nécessaires ;
3. produire le rapport compact ;
4. laisser G2 pour une session explicitement autorisée.

Le but est de préserver le quota agentique et d'éviter l'expansion spontanée de la mission.

### G1.1 — validation / fallback

Objectif :

- déplacer le parsing/validation JSON nécessaire au fallback dans `octopus.llm` ;
- faire de même pour les validateurs métier/vision lorsque leur échec doit déclencher un autre candidat ;
- journaliser correctement `ok/invalid/error/blocked`.

Critère de sortie :

```text
sortie invalide d'un candidat gratuit
→ tentative marquée invalid
→ candidat gratuit suivant
→ test prouvé
```

Puis tests ciblés et commit cohérent.

### G1.2 — attestation OmniRoute / zero_cost

Objectif :

- capturer la route demandée ;
- capturer le provider/modèle réellement résolu si OmniRoute l'expose ;
- conserver request id / usage / metadata utiles disponibles ;
- rendre `zero_cost` fail-closed ;
- aucun fallback payant implicite.

Inspecter l'API/runtime OmniRoute réellement disponible avant de supposer la forme des métadonnées.

Critère de sortie :

```text
route non attestable ou upstream payant sous zero_cost
→ refus / blocage
→ jamais compté comme coût certain = 0
```

Puis tests ciblés et commit cohérent.

### G1.3 — routage propre

Objectif :

- supprimer la duplication inutile de résolution du profil ;
- garder une seule source de vérité ;
- permettre des intentions/capacités distinctes quand utile : general, reasoning, vision, coding ;
- rester provider-neutral ;
- ne pas hardcoder arbitrairement une marque de modèle dans les callers métier.

Puis tests ciblés et commit cohérent.

### G1.4 — preuve finale G1

Seulement après stabilisation des lots précédents :

- exécuter les tests gateway/OmniRoute concernés ;
- exécuter les suites transversales réellement affectées ;
- comparer le résultat aux critères G1 de `docs/ACCEPTANCE_GATES.md`.

Si une preuve dépend d'un service live ou d'une capacité indisponible :

```text
PARTIAL — preuve manquante : ...
```

Ne pas lancer G2 pour utiliser le temps/quota restant.

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

## 5. Discipline de contexte / quota

Le quota agentique est une ressource rare.

Règles obligatoires pour cette session :

- ne jamais explorer tout le dépôt « pour comprendre » ;
- commencer par `rg`, `git grep`, Git et les symboles concernés ;
- ouvrir uniquement les fichiers nécessaires au lot courant ;
- ne pas relire un document déjà lu sauf nécessité ;
- ne jamais utiliser `docs/archive/` comme contexte de travail courant ;
- préférer les tests ciblés à la full suite pendant le développement ;
- lancer les suites larges seulement après stabilisation du lot ;
- ne pas produire de longs rapports intermédiaires ;
- ne pas réanalyser une conclusion déjà documentée si le code réel ne la contredit pas ;
- ne pas élargir spontanément la mission ;
- ne pas refactorer du code sain adjacent ;
- utiliser les diffs, tests et sorties du terminal comme preuves plutôt qu'une nouvelle analyse LLM ;
- arrêter la session après G1, même si du quota reste.

Principe :

```text
repo = mémoire
terminal/tests = preuve
LLM = décision quand nécessaire
```

## 6. Tests

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

## 7. Discipline

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

## 8. Fin de session

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
Vérifie l'état Git réel et respecte le worktree courant.
Exécute uniquement G1 selon l'ordre G1.1 → G1.4.
Ne refais pas les audits déjà versionnés.
Préserve le quota : contexte minimal, tests ciblés, aucun élargissement spontané.
Ne marque jamais une gate DONE sans preuve et tests.
Arrête-toi après le rapport compact G1 ; ne commence pas G2.
```
