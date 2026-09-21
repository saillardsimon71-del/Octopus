# AGENTS.md — Constitution OCTOPUS

## Identité et décision

OCTOPUS est un **atelier de travail économique supervisé**, pas une entreprise autonome prouvée.
Il aide un humain à tester un besoin réel, effectuer le travail, livrer, constater le paiement et
le résultat client, puis décider quoi améliorer. Un monolithe modulaire, maintenable par une personne.

**MARKET FIRST. AUTOMATION SECOND. GENERALIZATION LAST.**

```text
ONE REAL WORKFLOW → ONE OBSERVED BOTTLENECK → ONE TARGETED IMPROVEMENT
NO ECONOMIC SIGNAL → NO NEW GENERIC INFRASTRUCTURE
```

Un signal négatif ou une absence de réponse correctement observée peut justifier l'arrêt.
Un incident opérationnel peut justifier une correction avant tout revenu. Ni l'un ni l'autre
ne justifie de généraliser. Le marché choisit le travail ; l'humain gouverne les permissions.

## Mesures qui comptent

1. Argent client réellement encaissé, distinct des apports et promesses.
2. Livraison attestée, distincte de `task.status=done`.
3. Acceptation et utilisation client, chacune inconnue tant qu'elle n'est pas constatée.
4. Contribution économique, avec périmètre des coûts et devises explicites.
5. Minutes humaines par phase, coût variable et répétabilité.
6. Réduction mesurée du travail humain sur la même mission avant/après changement.

Tests verts, commits, emails envoyés, vidéos générées, agents et heures d'autonomie ne prouvent
aucun succès commercial. `supports` ne signifie que « cible de la métrique configurée atteinte ».
Une échéance sans mesure est `inconclusive`, pas un résultat client négatif inventé.

## Golden path et état canonique

`strategy` contient objectifs, hypothèses, expériences, preuves et décisions.
`tasks`/`worker` exécutent et reprennent le travail. `actions` garde les effets externes.
`economy` tient l'unique ledger et produit `economy outcome BUSINESS EXPERIMENT`.
`journal` conserve ces objets dans SQLite ; pas de nouvelle DB, CRM ou moteur de workflow.

Le travail initial peut être manuel et tracé par des preuves. Ne pas lancer ORBIT, un worker,
un navigateur ou un LLM pour simplement tenir les comptes d'un pilote.
Le protocole exécutable unique est [docs/HANDOFF_WORK.md](docs/HANDOFF_WORK.md).

## Frontières non négociables

- Aucune donnée client, livraison, performance, revenu ou disponibilité inventée.
- `observed` exige source et date ; une référence déclarée ne prouve pas à elle seule sa vérité.
- Les estimations restent séparées. Inconnu n'est ni zéro, ni échec, ni succès.
- Un seul ledger (`octopus.economy`) ; ne jamais additionner ses coûts et leur estimation LLM deux fois.
- Aucun effet externe sans canal autorisé ; accès `act` accordé par un humain, y compris à la création.
- Aucune dépense implicite : allowance, limites, journal et réconciliation.
- Compute provisionné via `GuardedComputeManager` ; watchdog indépendant conservé.
- Une erreur de soumission ambiguë n'autorise pas un retry payant aveugle.
- LLM **free-first, pas free-at-any-cost** : règles déterministes en code, `zero_cost` par défaut,
  modèle puissant payant seulement sur politique humaine explicite. Jamais de fallback payant implicite.
- Secrets hors Git et hors environnement du candidat ; pas d'installation autonome privilégiée.
- Les connaissances apprises ne changent ni autorisations, ni budgets, ni règles de preuve/promotion.
- Une evidence gate `ACCEPTED` est nécessaire pour un product ticket, jamais suffisante pour
  accepter le produit ou promouvoir sans revue humaine. Lire `docs/EVIDENCE_ACCEPTANCE.md`.

## Atelier de développement latéral

`development.task`, Kilo, sandbox, acceptance et promotion restent disponibles, hors du chargement
ordinaire des handlers de développement. `night-shift` est une entrée explicite, pas un objectif produit.
Avant une amélioration importante : enregistrer l'observation, lier la preuve à la tâche avec
`strategy link ... evidence ... task ... motivates`, puis comparer la même mission avant/après.
Les anciennes tâches sans provenance ne sont pas invalidées. Aucun graphe de bottlenecks supplémentaire.

Vidéo/compute, GUI, rôles historiques et inventaires sont conservés pour leurs consommateurs
actuels, **gelés pour l'expansion**. Le module `capabilities.py` n'a pas de consommateur runtime
identifié lors de cette intervention : ne pas le promouvoir au rang d'autorité.

## Dette de complexité

Avant chaque abstraction ou fichier : chercher un endroit canonique existant.
Un concept doit protéger une vraie frontière, exécuter une mission, éviter une perte, réduire un coût,
permettre la reprise, mesurer un résultat ou avoir un consommateur actuel. Sinon : geler ou retirer.
À valeur comparable : moins de concepts, fichiers, états, couplages et permissions implicites.

Pas de nouvelle GUI/Web UI, framework, agent, Model Lab, acquisition de capabilities, MCP discovery,
provider GPU, moteur vidéo, campagnes, CRM, microservices, message bus ou réécriture.

## Travail sur le dépôt

Lire ce fichier, `docs/VISION.md`, `docs/CURRENT_STATE.md`, puis `docs/HANDOFF_WORK.md`.
Git réel prime sur les snapshots documentaires. Les plans spécialisés ne fixent pas les priorités.
Vérifier branche, HEAD de main, arbre et historique avant modification ; préserver le travail inconnu.
Branche dédiée et revue avant fusion ; ne jamais merger main automatiquement.
Tester chaque changement puis les frontières partagées ; aucune ressource payante dans les tests.
Ne jamais annoncer vert sans exécution ni confondre validation technique et preuve économique.
Terminer par un diff relu, limites documentées et prochaine observation à obtenir, pas une nouvelle architecture.