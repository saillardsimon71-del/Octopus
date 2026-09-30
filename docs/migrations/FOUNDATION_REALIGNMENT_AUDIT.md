# Audit descriptif du réalignement OCTOPUS - 2026-09-30

## Statut et source manquante

Audit seulement. Aucun changement du moteur, de la GUI, des permissions ou de la
Foundation. Aucune architecture de remplacement n'est choisie.

L'opérateur a précisé pendant cette session que la Foundation normative est le
message utilisateur original de sa conversation ChatGPT des 13-14 septembre 2026,
sous le titre `FOUNDATION EXACTE`. Le document commence par `# OCTOPUS` et contient
les sections Identity, Finality, Autonomy, Starting environment, Real world,
Construction, Non-prescription et Starting point.

**Ce texte intégral n'a pas été retrouvé.** La mission de réalignement n'en est pas
une retranscription. Les titres de sections et la citation partielle ne suffisent
pas à reconstituer son contenu. L'opérateur demande explicitement de suspendre les
modifications qui supposeraient de l'inventer, la compléter ou la réinterpréter.

Recherches effectuées : fichiers suivis du dépôt, historique accessible de toutes
les références Git locales pour les formulations distinctives, pièces jointes
Codex locales, documents locaux sous Documents/Codex et Downloads pour
`FOUNDATION EXACTE` et `espace des réalisations admissibles`. Aucun outil accessible
dans cette session ne fournit l'historique des conversations ChatGPT. Cette recherche
n'établit pas que le message n'existe plus, seulement qu'il n'est pas accessible ici.

La suite nécessite le **message utilisateur original complet**. Après sa réception,
le conserver verbatim dans un document versionné, identifier sa provenance, puis
résoudre explicitement les contradictions documentaires avant les corrections.

## Base Git vérifiée

Remote : https://github.com/saillardsimon71-del/Octopus

| Référence | HEAD vérifié | État / base |
|---|---|---|
| `main` local | `5301a27b8f400041e38eef8b2f0afd73a7b23e6f` | En retard sur le remote |
| `main` remote | `ae4d98dc9692aa10ba15051381a36809e25377df` | Vérifié par `ls-remote` |
| PR #109 | `c92e4a0ac7d2a5bcac9438dbac4481c440d4cab0` | Ouverte, base `arena/01a0f13b-octopus` |
| PR #110 | `90fbac637339205b406579a9fb42dff26c60d692` | Ouverte, base #109 |
| PR #111 | `51ca8aac7ff3c08cd59e45ad2b06b3541dec7da8` | Ouverte, base #110 |
| PR #112 | `7cd0c6eca353b79d0bb6e743ff8689e3e5a9167f` | Ouverte, base #111 |
| PR #113 | `1d434bbe510dd7af1bb2da127e8480220805f038` | Ouverte, base #112 |

Le checkout initial `prep/astra-local-orchestration` était propre à
`61fa0b2d07007e9d098ca07c2b4aa2e7b94e206b`, avec l'outillage du constructeur.
Une branche indépendante `codex/foundation-realignment` a été créée depuis #113
dans `C:\Users\saill\Projects\Octopus-foundation-realignment`.
Les worktrees historiques et `main` ne sont pas modifiés.

## Contradictions documentaires constatées

- `docs/VISION.md` à `a0333f70a1d49c46b20533855cbdd1a249651f4f`
  décrit la création et l'exploitation d'activités avec intervention décroissante,
  mais prescrit aussi une verticale de contenu prioritaire.
- La version de `docs/VISION.md` dans #113 demande un atelier économique supervisé,
  attribue les objectifs à l'humain, déprécie l'autonomie générique et propose un
  premier pilote CSV. `AGENTS.md` et le routeur de phase E reprennent l'atelier supervisé.
- `docs/HANDOFF_WORK.md` impose un objectif humain et un business neuf pour le
  démarrage neutre. Il qualifie cependant le pilote CSV d'exemple historique.
- La mission actuelle demande une détermination économique sans activité imposée.

Aucun de ces documents n'est déclaré ici équivalent à la Foundation manquante.
Le décalage observable concerne déjà le mandat actuel, indépendamment du texte
normatif qui reste nécessaire pour décider du réalignement complet.

## Matrice provisoire

La première colonne reprend les exigences explicites de la **mission actuelle**,
pas une prétendue lecture de la Foundation complète. Les corrections sont des
points à instruire après réception de la source, pas des décisions d'architecture.

| Exigence du mandat, à confronter à la Foundation | Comportement actuel | Écart constaté | Correction à instruire | Validation requise |
|---|---|---|---|---|
| Commencer sans activité | `supervisor.bootstrap` crée un tick ; `tick` parcourt uniquement les objectifs actifs | Base vide : zéro objectif examiné, aucun travail déterminé | Définir l'entrée de poursuite de la finalité à partir du texte exact | Scénario 1 : base vide, travail réel ou obstacle explicite |
| Mission humaine libre | ORBIT accepte un goal libre ; `plan_work` le délègue au runtime | `_missions` et `_create_mission` imposent business autorisé et vidéo Agnes | Exposer le parcours générique existant, une fois son contrat fixé | Scénario 2 : mission sans Agnes ni business préexistant |
| Ressources sans stratégie prescrite | `ToolRegistry` décrit les outils ; les handlers appliquent les permissions ; `goal_text` décrit les canaux autorisés | L'entrée GUI impose Agnes ; présence dans le registre ne prouve pas disponibilité live | Distinguer sélection, disponibilité et autorisation dans le parcours retenu | Scénario 3 : choix non vidéo et usage vidéo autorisé |
| Zéro dépense initiale et limites explicites | Les tâches génériques planifiées par défaut portent `profile=None`, `allowed_tools=None`, `budget_usd=None` ; durée 900 s et six étapes par agent | Le point d'entrée ne fixe pas lui-même le contrat demandé de zéro dépense ; les politiques sous-jacentes restent applicables | Fixer et conserver les limites sur le parcours initial et ses reprises | Scénarios 4 et 8 : refus avant effet, aucune route payante implicite |
| Expliquer raisons, savoir et inconnues | Le runtime produit plan, résultats et synthèse ; `work_output` projette surtout statut, compteurs, citations et frontière | La projection consommable par la GUI ne contient pas la synthèse ; le résultat brut est conservé par `ctx.memo` | Exposer les résultats persistés avec leur nature et leurs limites | Scénario 5 : distinction inférence, source acquise, preuve et revenu |
| Évaluer sans inventer un résultat | `decide` clôt sur critère mesuré, réessaie sinon, suspend à épuisement ou attend une ressource | Les critères supportés comptent acquisitions/actions/fichiers ; ils ne mesurent pas la finalité économique globale | Définir ce qui doit être évalué au démarrage sans confondre métrique technique et résultat économique | Scénario 5 : tâche terminée sans succès économique déclaré |
| Interruption et reprise contrôlées | Queue durable, mémo de mission, limites de tentatives ; Agnes conserve une identité de génération et traite l'ambiguïté | Aucun contrôle GUI dédié à la poursuite de la finalité ; l'arrêt Worker hérité vise les tâches en cours globalement | Déterminer le périmètre du contrôle avant de réutiliser ces mécanismes | Scénario 6 : reprise sans doublon ni reprise d'un ancien effet |
| Deux modes lisibles dans le Workbench | L'accueil affiche l'atelier supervisé ; sans activité il demande un canal Agnes ; `mission_state` attend un MP4 même pour une tâche générique terminée | Finalité, mode autonome et mission humaine ne sont pas distingués | Adapter libellés, commandes et affichage des résultats au contrat normatif reçu | Scénarios 1, 2 et 5 ; contrôle visuel Windows aux deux tailles demandées |
| Consultation sans mutation | `read_snapshot` ouvre SQLite en lecture seule ; la GUI dispose de `OCTOPUS_WORKBENCH_READONLY` | Contrat présent ; les futures commandes doivent le préserver | Garder cette frontière lors du réalignement | Scénario 7 : aucun objectif, tick, effet ni écriture à l'ouverture |

## Causes dans le code

1. `octopus/supervisor.py:334`, `:652`, `:688` : le démarrage amorce la
   supervision d'objectifs déjà créés. Il ne détermine pas un premier objectif
   à partir d'une finalité. L'exécution n'exige pas un fichier business déclaré,
   mais la stratégie et les tâches exigent un identifiant de scope `business`.
   Cela ne prouve pas l'existence d'une entreprise réelle.
2. `agents/gui/workbench_v2.py:270`, `:343`, `:437`, `:448` : l'accueil,
   le formulaire et la validation font du parcours vidéo l'unique nouvelle mission.
   Le runtime générique existe pourtant dans `agents/runtime.py:1588` et le
   Supervisor l'utilise à `octopus/supervisor.py:383`.
3. `octopus/supervisor.py:475` : la projection du résultat perd le raisonnement
   de la synthèse pour ses consommateurs. Ce constat ne signifie pas que la
   mémoïsation ou les traces runtime ont perdu le résultat brut.
4. `agents/gui/workbench_v2_data.py:127` : l'état d'une tâche générique terminée
   est présenté comme une preuve MP4 restant à confirmer.

Ces constats identifient les raccordements manquants. Ils ne démontrent pas qu'un
simple changement de prompt suffirait à réaliser l'autonomie décrite par la source
normative encore absente.

## Vérifications exécutées

Sur la base #113 dans le nouveau worktree :

```text
python -m pytest -q tests/test_autonomous_loop.py tests/test_workbench_v2.py tests/test_agnes_gui_missions.py --tb=short -o addopts=''
24 passed in 7.23s
```

Interpréteur utilisé : environnement Python existant du checkout constructeur.
Les fixtures isolent les bases et remplacent le transport LLM. Aucun appel LLM,
génération payante ou contact commercial réel n'a été lancé.

Reproduction supplémentaire dans un répertoire temporaire neuf, en fixant
`OCTOPUS_HOME`, `OCTOPUS_DB` et `PODALUX_ROOT` avant tout import :

```text
bootstrap(), puis tick(businesses=["octopus"])
objectives=[] ; checked=0 ; tasks=["supervisor.tick"]

Création explicite d'un objectif libre, activation, puis tick :
kind="supervisor.objective_work"
profile=null ; allowed_tools=null ; budget_usd=null
max_steps=6 ; max_duration_s=900.0
```

Cette reproduction ne lance pas le travail planifié. Elle démontre le blocage du
démarrage vide et la planification générique, pas la disponibilité d'un modèle live,
une détermination autonome de stratégie ou un résultat commercial.

Non exécutés : suite complète, huit scénarios du futur comportement, validation
visuelle d'une GUI corrigée. Le code n'a pas été modifié. Aucun fichier du DataRoot
historique `C:\Users\saill\Projects\Octopus-agnes-test`, MP4, preuve ou secret
local n'a été modifié. La réussite Agnes et son hash fournis par l'opérateur restent
une référence historique ; aucune nouvelle vérification de ce MP4 n'est revendiquée.

## Reprise du chantier

1. Obtenir le texte original complet, sans reconstruction ni paraphrase.
2. Le versionner avec sa provenance et une désignation normative explicite.
3. Compléter la matrice en citant ses exigences exactes ; résoudre les contradictions.
4. Seulement alors décider les corrections du moteur et des deux modes de la GUI,
   puis implémenter et exécuter les huit scénarios isolés et les contrôles visuels.
5. Livrer la procédure de premier démarrage avec zéro dépense, adaptée au code
   effectivement validé. Aucune procédure de lancement autonome réel n'est proposée
   comme fonctionnelle à ce stade.

La PR d'audit est préparatoire et doit rester en brouillon tant que la Foundation
manque et que les corrections demandées ne sont pas implémentées. Aucun merge.
