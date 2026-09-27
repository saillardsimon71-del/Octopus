# CODEX START - 2026-09-27

Routeur d'exécution de la phase E: revue, correction, amélioration et alignement d'OCTOPUS.

## 1. Mandat humain

L'opérateur demande un chantier important avant la première activité économique:

1. terminer l'intégration des composants Hermes dont OCTOPUS a réellement besoin;
2. examiner OCTOPUS de bout en bout et identifier ce qui empêche son fonctionnement correct;
3. corriger les problèmes constatés, simplifier les duplications et aligner le produit avec sa vision;
4. valider le résultat par le code, les tests et des parcours locaux contrôlés;
5. mettre à jour l'état documentaire réel.

Ce chantier ne doit pas s'arrêter après un audit ou une liste de recommandations. Astra doit
implémenter les corrections autorisées, les tester et les relire. Il s'arrête seulement à une
frontière humaine explicite: secret ou compte manquant, dépense, action externe irréversible,
contact avec un tiers, choix produit réellement ambigu, ou test vidéo live réservé à l'opérateur.

## 2. Identité du produit

OCTOPUS reste un atelier économique supervisé. Sa boucle centrale est:

```text
besoin réel + limites humaines
  -> expérience bornée
  -> travail réel
  -> livraison vérifiable
  -> paiement et résultat client séparés
  -> coûts, temps humain et inconnues
  -> décision humaine
  -> obstacle observé
  -> amélioration ciblée
  -> nouvelle mesure sur le même travail
```

Hermes est une banque de composants techniques, pas le nouveau cerveau d'OCTOPUS.
Ne pas importer son agent loop, son planner, sa persona, son routeur LLM, sa mémoire générale,
son UI ou son orchestration complète. Ne pas créer de second ledger, journal, planner, scheduler,
runtime agentique, système de permissions ou autorité de preuve.

## 3. État de départ à vérifier, pas à supposer

Git réel prime sur ce document. Au démarrage, vérifier de façon compacte la branche, HEAD, le diff,
la filiation et les derniers commits. Ne pas refaire une exploration historique générale.

État préparé avant la phase E:

- les phases B, C et D existent dans la branche de construction locale;
- le moteur vidéo OCTOPUS historique est retiré;
- l'adapter Agnes local, borné et testé est présent;
- le registre d'outils inspiré de Hermes est intégré;
- les contrats de mission, preuve de source et budget LLM ont été durcis;
- la branche produit correspondante est publiée dans la PR GitHub #106;
- la suite produit isolée a passé 1246 tests localement;
- les trois contrôles GitHub propres à OCTOPUS de la PR #106 sont verts;
- la tâche réelle 75 a terminé sans preuve stratégique, car les 12 appels SEARCH ont échoué sur
  le transport Bing/proxy et aucun BROWSE n'a pu acquérir de source;
- le smoke test vidéo réel Agnes n'est pas inclus dans cette phase et sera réalisé séparément.

Le checkout du constructeur mélange volontairement l'infrastructure Astra et les commits produit
pour permettre le travail local. Cela n'autorise pas à fusionner cette infrastructure dans le code
produit. Garder les checkpoints produit cohérents et séparables. Ne jamais fusionner `main`.

## 4. Documents à lire une fois

Lire dans cet ordre, puis travailler dans le code réel:

1. `AGENTS.md`, déjà injecté;
2. ce fichier;
3. `docs/VISION.md`;
4. `docs/CURRENT_STATE.md`;
5. `NEXT_STEPS.md`;
6. `docs/HANDOFF_WORK.md` pour le parcours économique;
7. `docs/migrations/OCTOPUS_HERMES_REPLACEMENT_MATRIX.md`;
8. `docs/migrations/HERMES_COMPONENT_EXTRACTION.md`;
9. les `AGENTS.md` imbriqués seulement lorsqu'un fichier de leur portée est modifié.

Les pins upstream restent:

- Hermes: `NousResearch/hermes-agent@59004a62356f3a4697ab0fe8ad5086d2b405e2a6`;
- Agnes: `lcy362/agnes-video-generator@a87162d6df73ffe72186838ca0ae9d461e68589b`.

Les sources préparées sont sous `cache/upstreams/`. Lire seulement les fichiers upstream nécessaires
à une comparaison précise. Ne pas vendoriser un projet entier.

## 5. Ordre obligatoire du chantier

### E0 - Établir la réalité

- Consommer le baseline du superviseur sans le relancer.
- Vérifier que les documents ci-dessus concordent avec le code et Git.
- Construire une carte compacte des parcours réellement exécutables:
  stratégie, mission, outils, recherche/navigation, worker, actions, économie, preuves, reprise.
- Identifier les blocages par reproduction, test ou lecture du chemin concerné. Ne pas spéculer.

### E1 - Réparer les blocages opérationnels

Priorité immédiate: diagnostiquer puis corriger le transport SEARCH qui a empêché la tâche 75
d'acquérir une source. Vérifier SEARCH -> sélection -> BROWSE -> citation -> qualification de signal.

Pour chaque défaut:

1. écrire ou renforcer un oracle qui reproduit le problème;
2. observer l'échec attendu;
3. corriger au point canonique le plus simple;
4. exécuter les tests ciblés;
5. vérifier qu'aucune permission, dépense ou sémantique de preuve n'est affaiblie.

Un probe réseau en lecture seule et sans coût peut être utilisé si le diagnostic l'exige. Ne jamais
contacter un tiers, publier, acheter, créer un compte ou effectuer une génération vidéo live.

### E2 - Terminer le sous-ensemble Hermes nécessaire

Examiner chaque composant de la matrice et produire une décision fondée sur le code:

- `integrate`: remplace une duplication réelle ou comble une capacité nécessaire au parcours;
- `keep_octopus`: l'implémentation OCTOPUS est déjà plus simple et suffisante;
- `defer`: aucun consommateur ou obstacle actuel ne justifie le coût;
- `reject`: créerait une autorité parallèle ou contredirait la vision.

Une décision `integrate` doit être entièrement implémentée et testée dans cette phase. `defer` et
`reject` exigent une raison précise, pas une préférence générale.

Évaluer en priorité:

1. frontière MCP minimale et explicite;
2. computer-use derrière un adapter et les permissions OCTOPUS, seulement si un parcours actuel
   en a besoin;
3. raccordement effectif du registre aux évaluations de capability et aux garde-fous d'effets;
4. distinction exécuté, observé et vérifié avec portée et fraîcheur dans le journal existant;
5. classification d'erreurs, retry et cooldown génériques si cela supprime une duplication ou
   corrige SEARCH/BROWSE/computer-use;
6. scheduler ou lifecycle de sous-agent uniquement si un consommateur actuel démontre le besoin.

Ne pas intégrer skills, mémoire, vault, cron, messageries ou discovery générique simplement parce
que Hermes les contient.

### E3 - Revue d'alignement et simplification

Comparer le comportement réel à la vision et rechercher notamment:

- chemins annoncés mais inexécutables;
- statuts `done` qui pourraient être confondus avec livraison, paiement ou succès client;
- permissions implicites ou effets externes contournant `actions`;
- budgets LLM confondus avec allowances économiques;
- preuves sans source, portée ou fraîcheur suffisante;
- reprise après crash ou idempotence manquante sur un effet externe;
- registres, schedulers, gateways ou états parallèles;
- code mort et documents qui orientent vers un ancien produit;
- tests qui valident des mocks ou des succès de transport au lieu du résultat attendu.

Corriger ce qui freine le fonctionnement correct ou protège une frontière actuelle. Ne pas lancer
une réécriture, une nouvelle UI, un CRM, une campagne, un moteur vidéo, un microservice ou une
abstraction sans consommateur.

### E4 - Validation et vérité documentaire

- Exécuter les tests ciblés après chaque unité cohérente.
- Utiliser le relay Step seulement pour une tâche mécanique bornée dont le contrat et les tests sont
  déjà fixés. Astra reste propriétaire de l'architecture, des permissions et de la revue.
- Demander un checkpoint hôte après chaque unité cohérente testée.
- Exécuter la suite complète une fois sur l'état final.
- Relire le diff réel, les suppressions, les dépendances et les changements de permissions.
- Mettre à jour `CURRENT_STATE`, `NEXT_STEPS` et la matrice Hermes avec les résultats exécutés.

## 6. Contraintes de travail

- PowerShell uniquement.
- Git en lecture seule dans Astra. Le superviseur possède les écritures Git.
- Aucun fallback de modèle. Le constructeur reste sur `gpt-6-astra`.
- Aucune ressource payante dans les tests.
- Aucune donnée réelle utilisateur modifiée par les tests. Utiliser DB temporaires et fakes.
- Ne jamais masquer un échec en supprimant ou assouplissant un test valide.
- Ne pas charger de gros fichiers, logs complets, lockfiles ou diffs massifs dans le contexte.
- Préférer `rg`, des extraits bornés et les résumés Git.
- Une erreur ambiguë après soumission externe interdit tout retry aveugle.
- Les règles déterministes restent en code, pas dans un prompt LLM.

## 7. Définition de terminé

La phase E est terminée seulement si:

1. chaque composant Hermes P0 et P1 de la matrice porte une décision actuelle et justifiée;
2. chaque composant déclaré nécessaire est entièrement intégré, testé et relié à un consommateur;
3. les blocages reproductibles du parcours économique sont corrigés ou arrêtés sur une frontière
   humaine explicite avec diagnostic précis;
4. SEARCH et BROWSE ont des contrats testés qui permettent l'acquisition de sources sans fabriquer
   une preuve économique;
5. le golden path local fonctionne sans action externe irréversible;
6. permissions, budgets, journal, reprise, idempotence et preuves restent cohérents;
7. la suite complète finale est exécutée et son résultat exact est consigné;
8. le diff est relu et ne contient ni infrastructure spéculative ni autorité parallèle;
9. les documents canoniques décrivent le code réellement présent;
10. aucun merge vers `main`, test vidéo live, contact, paiement ou dépense n'a été effectué.

Un test vert ne prouve ni marché, ni paiement, ni satisfaction client. La phase livre un OCTOPUS
techniquement cohérent et prêt pour une activité supervisée, pas une entreprise autonome prouvée.
