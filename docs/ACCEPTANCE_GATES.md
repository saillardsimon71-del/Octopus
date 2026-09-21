# Critères d'acceptation OCTOPUS

## Deux questions distinctes

1. **Technique** : le changement respecte-t-il son contrat, ses tests et les frontières gouvernées ?
2. **Économique** : le travail a-t-il été livré, payé, accepté/utilisé et à quel coût humain/variable ?

Un `ACCEPTED`, `task done` ou `evaluate_experiment=supports` ne répond jamais seul à la deuxième.
Les anciens pourcentages de progression et l'ordre obligatoire GPU → vidéo → marché sont retirés :
ils ne mesuraient pas la preuve économique et imposaient des dépendances inutiles au pilote manuel.

## G0 — Travail examinable

- HEAD/main, branche, arbre et origine des observations connus.
- Changements limités, tests réellement exécutés et diff relu.
- Aucune donnée client ou secret commité ; revue avant toute fusion.
- Les documents courants ne contredisent pas le code vérifié.

## G1/G2 — Permissions et argent

Ces protections survivent, mais une expérience sans LLM/compute n'attend pas un benchmark GPU.

- LLM gratuits par défaut, attestation du pool avant usage ; aucun fallback payant implicite.
- Accès `act` accordé par un humain ; knowledge n'accorde pas de droits.
- Dépenses explicites et bornées dans l'unique ledger/allowances.
- Compute provisionné via `GuardedComputeManager`, watchdog indépendant.
- RunPod/H3 legacy restent sous opt-in explicite et allowance ; pas de nouveau paid path.
- Une soumission ambiguë ne doit pas être rejouée automatiquement comme si elle avait échoué.
- Tests hors ligne ne prouvent ni gratuité réelle d'un fournisseur ni disponibilité live.

## G3–G5 — Runbooks compute/vidéo conditionnels, gelés

Lifecycle, crash/stop, benchmark et coût/vidéo restent à prouver avant un usage industriel vidéo.
Les protocoles sont `COMPUTE_GPU.md` et `benchmarks/GPU_COST_BENCHMARK_PLAN.md`.
Ils ne sont **ni la définition de V1 ni des prérequis du service d'enrichissement supervisé**.
Aucun canary payant à lancer pour faire avancer cette session.

## G6 — Effet externe constaté

- Destinataire/canal et autorité d'agir vérifiés par l'humain.
- Trace de l'action, identité stable, résultat de transport et ambiguïtés conservés.
- Preuve de livraison distincte d'un simple « message envoyé ».
- Refus/opt-out respectés par l'opérateur du pilote.

Les preuves HTTPBin/SMTP consignées dans l'historique prouvent le transport, pas une vente.
Leur source historique n'a pas été rejouée lors de cette intervention.

## G7 — Première boucle économique réelle

```text
besoin observable → expérience bornée → travail → livraison
 → paiement / acceptation / usage distincts
 → coûts + minutes humaines + inconnues → décision
```

Requis pour revendiquer un premier résultat commercial :

- besoin et accord client réels, lot identifié ;
- livraison constatée avec référence consultable ;
- encaissement client vérifié, pas facture seule, apport, promesse ou transport réussi ;
- acceptation/utilisation constatée ou explicitement inconnue ;
- coûts variables, devises et temps humain enregistrés ; coûts manquants signalés ;
- revue de la contribution complète avant toute affirmation de rentabilité ;
- décision humaine motivée, persistée et liée aux preuves.

Une expérience peut légitimement s'arrêter sans paiement. Sa valeur d'apprentissage n'est pas
du chiffre d'affaires. Une échéance sans mesure reste `inconclusive`, pas un fait négatif.

## G8 — V1 utilisable, pas autonomie universelle

Une personne peut suivre ce parcours avec `HANDOFF_WORK.md`, reprendre les états et comprendre
les inconnues. Le rapport `economy outcome` ne crée pas de faits et ne décide pas à sa place.
La réduction du temps humain n'est affirmée qu'après deux mesures comparables avant/après.
Pas d'exigence artificielle de 50 jobs média pour un service qui ne génère aucune vidéo.

## Contrat technique et promotion

```text
observation → tâche ciblée → contrat immuable → candidat
 → tests → evidence → ACCEPTED / REJECTED / UNCERTAIN
 → revue humaine → promotion → retour à la même mission
```

Le contrôleur possède `tests`, `git` et `artifact`; le probe ne peut pas les écraser.
Le probe Tk lit encore un attribut fourni par le candidat dans son processus : ce n'est pas
une inspection indépendante de qualité visuelle. Un hash prouve l'identité, pas la vérité.
Un contrat qui mesure le mauvais objectif peut passer ; toujours revoir le résultat réel.
Limites et noyau gouverné : `EVIDENCE_ACCEPTANCE.md`.

## Règle de clôture

Rapporter commandes exactes, résultats et environnement. CI configurée ≠ CI exécutée ;
tests verts ≠ bon produit. Si une preuve manque : **PARTIAL — preuve manquante : ...**
Ne jamais compenser un manque de preuve externe par davantage d'infrastructure.