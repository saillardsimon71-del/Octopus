# OCTOPUS

OCTOPUS est un **control-plane entrepreneurial multi-business**. Il orchestre des agents, des tâches durables, un navigateur contrôlé, une boucle économique persistante et plusieurs chemins de production média. Podalux est aujourd'hui son premier business réellement intégré.

> **Constitution / règles agents :** lire [AGENTS.md](AGENTS.md).  
> **Vision durable :** lire [docs/VISION.md](docs/VISION.md).  
> **État de référence :** lire [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md).  
> **Reprise Codex :** lire [docs/CODEX_START.md](docs/CODEX_START.md).  
> **Reprise Work :** lire [docs/HANDOFF_WORK.md](docs/HANDOFF_WORK.md).

## Architecture

```text
                         ┌──────────────────────┐
                         │       OCTOPUS        │
                         │ control-plane + DB   │
                         └──────────┬───────────┘
                                    │
             ┌──────────────────────┼───────────────────────┐
             │                      │                       │
      stratégie / économie      tâches / agents        média / vidéo
             │                      │                       │
  strategy + economy +         durable queue            VideoService
  resources + evidence         leases/retries                │
             │                      │                renderers / GPU jobs
             └──────────────┬───────┘                       │
                            │                         ComputeBroker
                         ORBIT                         + breaker
                            │                       Salad / GPU.ai
                      outils / browser                    │
                            │                         watchdog
                     Playwright guard
```

Les rôles historiques restent :

```text
SOUT → CONVERT → FORGE → GROWTH → LEDGER → ORBIT
```

La GUI Workbench est une surface de pilotage ; elle ne doit pas devenir un second moteur métier.

## Sources de vérité

- **Constitution du projet** : `AGENTS.md`
- **Vision / ligne directrice** : `docs/VISION.md`
- **État actuel vérifié** : `docs/CURRENT_STATE.md`
- **Critères de DONE** : `docs/ACCEPTANCE_GATES.md`
- **Reprise Codex** : `docs/CODEX_START.md`
- **Reprise Work** : `docs/HANDOFF_WORK.md`
- **Prochaines étapes** : `NEXT_STEPS.md`
- **Compute GPU et disjoncteur financier** : `docs/COMPUTE_GPU.md`
- **Index de la documentation** : `docs/README.md`

Les anciens rapports de session et prompts d'implémentation sont conservés sous `docs/archive/` et ne doivent pas être utilisés comme état courant.

## Invariants importants

1. **Pas de dépense GPU directe.** Tout nouveau chemin payant doit passer par `GuardedComputeManager` et le disjoncteur financier.
2. **Pas de dépense sans allowance.** Par défaut, une enveloppe USD explicite est requise avant toute réservation GPU.
3. **Pas de retry payant aveugle.** Une soumission ambiguë reste ambiguë jusqu'à réconciliation.
4. **SQLite est la source de vérité durable** pour tâches, stratégie, économie et réservations compute.
5. **Aucune donnée business inventée.** Revenus, clients, métriques ou preuves absentes restent absents.
6. **Aucun secret dans Git.** Les credentials restent dans les variables d'environnement / secrets runtime.
7. **`main` n'est pas un espace de travail.** Les changements passent par une branche et une PR avec CI verte.

## Compute GPU

La branche active de développement contient :

- `octopus/salad.py` — provider SaladCloud ;
- `octopus/gpuai.py` — provider GPU.ai + réconciliation/stop ;
- `octopus/compute_broker.py` — choix du provider/GPU ;
- `octopus/compute_finance.py` — réservations, hard caps, allowances, coût réel ;
- `ops/compute_watchdog.py` — watchdog indépendant ;
- `.github/workflows/compute-finance.yml` — tests de sécurité compute.

Les plafonds par défaut sont prudents : **$0.01/unité**, **$0.25/batch**, **$1/business/jour**, **$2 global/jour**. Ce sont des garde-fous OCTOPUS, pas une garantie contractuelle de facturation du fournisseur.

**Limite actuelle importante :** le pipeline vidéo réel n'est pas encore forcé à utiliser exclusivement `GuardedComputeManager`. Tant que ce verrouillage n'est pas terminé et qu'un canary live n'a pas été mesuré, la PR compute reste une fondation protégée, pas une autorisation de production industrielle.

## Démarrage local

Sous Windows :

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup-local.ps1
.\.venv\Scripts\python.exe -m agents.run doctor
.\.venv\Scripts\python.exe run_gui.py
```

Worker OCTOPUS :

```powershell
.\.venv\Scripts\python.exe -m octopus worker
```

Voir `docs/LOCAL_SETUP.md` pour les détails.

## Tests

Suite compute ciblée :

```bash
python -m pytest -q \
  tests/test_compute_finance.py \
  tests/test_compute_salad.py \
  tests/test_compute_broker.py \
  tests/test_compute_gpuai.py
```

Le workflow historique `video-foundation` couvre aussi le renderer, le control-plane, la stratégie, l'économie, la GUI et les tests multi-business.

## Développement

Avant de modifier l'architecture :

1. lire `AGENTS.md` ;
2. vérifier branche, status, log et diff ;
3. lire `docs/CURRENT_STATE.md` et `docs/ACCEPTANCE_GATES.md` ;
4. utiliser `docs/CODEX_START.md` en Codex ou `docs/HANDOFF_WORK.md` en Work ;
5. vérifier les tests réellement verts sur le HEAD courant ;
6. faire un changement petit, vérifiable et réversible ;
7. mettre à jour l'état documentaire si la réalité change.

Répartition recommandée :

```text
Chat  → décisions / architecture
Work  → audit / recherche / workflows multi-étapes
Codex → code / terminal / tests / Git
```
