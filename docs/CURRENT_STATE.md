# État actuel d'OCTOPUS

**Date de mise à jour : 19 septembre 2026**  
**Document de référence pour la reprise.**

Ce fichier décrit l'état réellement présent dans le dépôt. Les rapports plus anciens sont historiques et sont rangés sous `docs/archive/`.

## 1. Git / intégration

Branche de travail au moment de cette mise à jour :

```text
feat/salad-compute-provider
```

PR associée :

```text
#2 — feat: add cost-bounded Salad compute and GPU financial breaker
```

La PR est volontairement **Draft**. `main` n'a pas été modifié directement.

Le HEAD fonctionnel juste avant le commit de rangement documentaire est `555230d254bb9d3038392efe009e288c4851881b`.

Sur ce HEAD, GitHub Actions a vérifié :

- **Compute finance safety : success** ;
- **video-foundation : success** ;
- job `contract-and-worker` : success ;
- job `local-browser-and-control-plane` : success ;
- tests stratégie / économie / multi-business / journal : success.

Toujours re-vérifier la CI du HEAD courant après de nouveaux changements.

## 2. Vision actuelle

OCTOPUS n'est plus seulement une usine à Shorts.

La cible actuelle est un **moteur local de pilotage d'activités autonomes** :

```text
ressources réelles
      ↓
objectifs / hypothèses / expériences
      ↓
ORBIT + agents spécialisés
      ↓
outils / navigateur / média / compute
      ↓
résultats observés
      ↓
ledger / décisions / réinvestissement
      ↓
nouveau cycle
```

Podalux reste le premier business réellement intégré et sert de banc d'essai.

## 3. Ce qui est réellement implémenté

### Control-plane et persistance

- file de tâches durable SQLite ;
- leases, retries, idempotence et handoffs humains ;
- journal des runs et coûts LLM ;
- ressources réelles et probes ;
- stratégie persistante : objectifs, hypothèses, expériences, preuves, décisions, revues ;
- boucle économique : canaux, ledger multi-devise, allowances, spend requests, réinvestissement ;
- isolation multi-business ;
- GUI Workbench ;
- navigateur Playwright avec garde de contexte ;
- OmniRoute comme gateway LLM optionnel.

Le schéma du journal OCTOPUS est actuellement **v8**.

### Vidéo

Plusieurs briques coexistent encore :

- pipeline FORGE / Remotion / FFmpeg historique ;
- `VideoService` et renderer cloud ;
- adapter RunPod Serverless historique ;
- WanGP/Wan2GP local pour génération média ;
- MiniMax H3 cloud ;
- worker vidéo et stockage d'artefacts.

Le choix final de la plateforme GPU de génération n'est pas encore figé : le critère de décision est désormais **coût réel par vidéo**, pas prestige du GPU.

### Compute multi-provider

Implémenté sur la branche active :

- `SaladClient` ;
- lifecycle GPU.ai amélioré ;
- `ComputeBroker` ;
- `FinancialCircuitBreaker` ;
- `GuardedComputeManager` ;
- watchdog indépendant `ops/compute_watchdog.py` ;
- réservations persistantes ;
- rapprochement avec les allowances économiques ;
- coût réel par unité ;
- tests de crash/restart, idempotence et caps.

## 4. Disjoncteur financier GPU

Valeurs par défaut :

| Limite | Défaut |
|---|---:|
| coût max par vidéo/unité | $0.01 |
| coût max par batch | $0.25 |
| coût max par business/jour | $1.00 |
| coût GPU global/jour | $2.00 |
| watchdog | 5 s |
| allowance USD | obligatoire par défaut |

Principe :

```text
benchmark runtime
      ↓
quote provider
      ↓
réservation atomique du hard cap
      ↓
allowance économique vérifiée
      ↓
create provider
      ↓
watchdog + journal
      ↓
stop / finalisation / coût réel
```

Une création ambiguë après timeout n'est jamais resoumise automatiquement.

Sur Salad, `restart_policy=never` évite une boucle de redémarrage facturée si le worker plante.

## 5. Ce qui n'est PAS encore terminé

### P0 — obligatoire avant vrai usage payant

1. **Forcer tous les chemins GPU payants à passer par `GuardedComputeManager`.**
   Le breaker existe, mais du code plus ancien peut encore théoriquement appeler un provider sans lui.

2. **Déployer le watchdog comme processus réellement indépendant.**
   Le script existe ; il faut décider comment il vit en continu sur l'environnement d'exécution et vérifier sa reprise automatique.

3. **Faire un canary Salad réel très petit.**
   Pas d'auto-recharge. Allowance volontairement minuscule. Un seul workload connu.

4. **Mesurer le coût réel du même workload Wan sur plusieurs GPU.**
   3090 / 5090 Laptop / 4090 / 5090 selon disponibilité.

5. **Valider cold start + image + cache + arrêt.**
   Le prix horaire seul ne suffit pas.

### P1 — robustesse de production

- image OCI reproductible pour Wan/worker ;
- bootstrap déterministe après machine éphémère ;
- cache des poids et mesure de son impact ;
- queue batch et réutilisation d'un GPU déjà chaud ;
- coût réel par vidéo alimentant le broker ;
- politique de fallback provider ;
- tests de panne provider et terminaison lente.

### P2 — boucle business

- publication et analytics réels ;
- connecteurs de revenus / paiements / CRM ;
- retour des métriques réelles vers les expériences ;
- décision de réinvestissement fondée sur le cash observé.

## 6. Ce qu'il ne faut pas faire

- ne pas merger la PR compute uniquement parce que les tests unitaires passent ;
- ne pas donner un gros solde cloud avant le canary ;
- ne pas ajouter un deuxième ledger financier ;
- ne pas contourner `economy` / allowances ;
- ne pas mettre un retry générique autour de `provider.create()` ;
- ne pas relancer automatiquement un container GPU payant en boucle ;
- ne pas prendre un ancien handoff archivé comme état courant ;
- ne pas réécrire le runtime agentique sans régression démontrée.

## 7. Point de reprise recommandé

La prochaine session Work doit commencer par :

```text
1. vérifier branche + git status + HEAD
2. lire docs/CURRENT_STATE.md
3. lire docs/HANDOFF_WORK.md
4. cartographier TOUS les appels de création GPU/cloud payants
5. empêcher tout bypass du GuardedComputeManager
6. tester
7. concevoir le canary Salad à quelques centimes
8. seulement ensuite lancer un vrai benchmark
```

Voir `docs/HANDOFF_WORK.md` pour le protocole détaillé.
