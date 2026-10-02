# Liberté du raisonnement économique — audit du 2026-10-02

Base exacte #122 : `251d1de2d5a450356c11cde0a230f883e21c3f2d`.
Audit et régressions établis avant modification de production. Aucun DataRoot/log du vrai
run #122 n'est fourni ; la cause de son blocage est reconstruite depuis le code et le
symptôme déclaré. Le lien causal exact avec les requêtes réelles reste une inférence.

## Cartographie préalable

A = sécurité ; B = vérité/provenance ; C = bornes de ressources ; D = contrat logiciel ;
E = préférence cognitive ; F = expérience/heuristique ; G = hardcode métier.

| Famille | Classe | Décision |
| --- | --- | --- |
| ToolRegistry, allowed_tools, Web public sans cookies, anti-exfiltration, droits externes | A | Conserver strictement. |
| Ledger/allowances/compute safety, budgets LLM pré-appel, timeouts, trois cycles | A/C | Conserver strictement, mêmes valeurs/provider/model. |
| Ownership business, liens, journal, checkpoints, idempotence, reprise | A/B/D | Conserver. |
| Acquisition réelle, dates/URL outil, citations littérales si fournies | B | Conserver sans imposer buyer/pain/money ni une seule page. |
| Nature des propositions et absence de promotion en cash/fait réel | B | Calculée par le code : analyse sourcée = inférence ; sans source = hypothèse. |
| JSON plan/action/détermination, rôle outil, types, bornes de stockage | C/D | Conserver ; candidat facultatif, énoncé minimal. |
| Neuf signal_type, treize champs exigés, quatre citations obligatoires, rejets de secteurs/pages | E/G | Supprimer du gate et du prompt. |
| Target, combinaison indépendante forcée, replan multi-signaux | E/F | Retirer du parcours ; ancien paramètre ignoré pour compatibilité. |
| Revue automatique par signal, cinq classes d'actionnabilité | E/F | Retirer ; coûts et données historiques conservés. Ne pas renommer une nouvelle mesure en ancienne preuve. |
| Search/browse lockstep, selectors heuristiques | F | Déjà opt-in et absent de pursuit ; conserver seulement l'expérience explicitement demandée, retirer l'upgrade automatique du selector. |
| Discovery/validation, retour discovery imposé après pause | E/F | Intentions légères, choix explicite facultatif du modèle ; respecter sa pause. |
| Liste obligatoire de dimensions économiques dans le schema stratégies | E | Retirer du prompt ; énoncé suffit, autres champs facultatifs. |
| Classement par critères prioritaires pour observer | E | Préserver l'ordre/rang du modèle ; garder classification/autorisation d'exécution déterministes. |
| Interdiction lexicale de nouvelle stratégie SiteQuiVend | G | Retirer du normaliseur et de la relecture d'annotations ; isolation par ownership, pas mot interdit. |
| Filtrage lexical de contexte dans learning_context | G | Retirer ; garder toutes les vérifications SQL d'ownership, liens, nature et validité des preuves. Aucun partage de mémoire entre activités. |
| Pause forcée sur pensée ressemblant à une hypothèse invalidée | E/B | Retirer la pause cognitive ; garder l'état invalidated et l'interdiction d'exécuter/promouvoir sans preuve nouvelle liée. |
| Capabilities, acquisition studies, scoring acquisition | A/D/F | Les faits/droits exécutables et études existantes restent ; aucune acquisition nouvelle. Le scorer d'étude ne choisit pas les marchés et ne borne pas la représentation d'une stratégie. |
| Source publique bloquée vs demande humaine | A/B/D | Classifier les métadonnées d'acquisition ; aucun refus de garde-fou réel ne devient une erreur technique. |
| Workbench | D | Projection historique des objets, aucune nouvelle taxonomie ni UI. |

## Régressions sur la base

14 nouveaux cas : **8 failed, 6 passed**. Ils reproduisent le rejet d'une idée atypique,
la combinaison de trois sources, l'exigence de citations, le replan/review automatique,
le classement prescriptif et le faux blocage humain sur métadonnées publiques.
Base complète hors affichage : **1939 passed, 97 skipped** (141.20 s).
Mesure avec mêmes sources, état et réponses simulées : contrat 1957 caractères,
rappel sous-agent 2313 ; 10 appels simulés, dont 2 revues ; entrées cumulées 49391 caractères.
Ce n'est pas un benchmark d'intelligence LLM.

## A–C. Suppressions et protections

Six fichiers de production changent : `agents/runtime.py`, `agents/browser.py`,
`agents/task_handlers.py`, `octopus/supervisor.py`, `octopus/strategy.py` et
`octopus/strategy_separation.py`. Aucune nouvelle architecture, taxonomie ou heuristique
de choix de marché. Foundation, gateway LLM, catalogue modèles, finance et ToolRegistry
ne changent pas.

Supprimés : enum de signal, champs métier obligatoires, quatre citations, liste de rejets
économiques, quota, indépendance forcée, replan automatique, revue/classification automatique,
upgrade automatique de selector, checklist de comparaison, veto lexical, retour discovery
imposé après une pause et pause automatique sur une idée réfutée.

Conservés parce qu'ils protègent des conséquences réelles : budgets pré-appel, ledger,
allowances, compute safety, permissions/actions, public-only, secrets et anti-exfiltration,
ownership, isolation, journal, checkpoints, idempotence, timeouts, provenance et absence de
cash inventé. Plafond pursuit **0.20 USD**, **3 cycles**, **6 étapes par agent / 120 s**,
mêmes outils exposés, profils et modèles. Une capacité pensée n'est pas une capacité acquise.
Une stratégie invalidée reste invalidée ; la recherche de faits supplémentaires peut continuer.

## D–E. Contrats et consommateurs

`business_signals` conserve son nom pour compatibilité de lecture. Il est désormais une
liste facultative de pistes, pas une preuve d'actionnabilité. Un seul champ est obligatoire
par candidat : un `statement` non vide. Les anciennes formes avec `evidence_summary` et
`evidence_url` restent projetables. Les champs métier historiques peuvent rester du texte
libre ; un `signal_type` inconnu ne provoque aucun rejet.

```json
{
  "rapport": "Analyse libre ; faits, interprétations et inconnues distingués.",
  "determination": {
    "action": "continue",
    "reason": "Une incertitude mérite une observation supplémentaire.",
    "next_goal": "Recherche choisie par le modèle.",
    "permission": ""
  },
  "business_signals": [
    {"statement": "Hypothèse encore incomplète", "sources": []}
  ]
}
```

Les `sources` facultatives ont la forme `{url, quote?}` : URL effectivement acquise par
le runtime, extrait facultatif littéral dans **sa** capture. Un candidat peut rapprocher
plusieurs acquisitions. Les dates et URL finales viennent de l'outil. Sans source,
le code produit `nature=hypothesis` ; avec source, `nature=inferred`, même si le modèle
prétend `observed`. Une source ne prouve ni l'interprétation ni un encaissement.
URL inventée, extrait inventé, capture bloquée/échouée, provenance incohérente restent rejetés.
Le seuil de longueur de 100 caractères ne disqualifie plus une information courte réelle ;
les captures restent non vides et soumises aux contrôles techniques/provenance.

Le wrapper `rapport` + `determination` et ses quatre chaînes restent nécessaires aux
consommateurs actuels ; les trois actions sont des états logiciels. `strategies` et
`intent` sont facultatifs. Une stratégie avec seulement `statement` reste représentable,
y compris avec un moyen absent. Le classement d'observation respecte le rang/ordre du
modèle. L'autorisation d'exécution reste indépendante et déterministe.

Les anciennes lignes et revues ne sont ni migrées ni réécrites. Pour les nouvelles sorties,
`source_linked_candidate_count` compte uniquement les références validées, sans seuil ni
champ `success`. Il ne reprend pas le sens de `qualified_business_signal_count` ni de
`actionable_business_signal_count`. `business_signal_review_status=not_requested` indique
la suppression de la revue automatique. Workbench conserve sa projection historique ;
aucun chantier UI.

## F. Sources bloquées et reprise

Cause reproduite : une acquisition publique pouvait retourner `page.blocked/error/status`
sans `refused`. Le runtime traitait comme techniques surtout des préfixes de refus ;
le superviseur pouvait donc accepter la demande humaine formulée ensuite par le modèle.
Le vrai log #122 étant absent, attribuer ce chemin au run déclaré reste une inférence.

Le runtime marque désormais cette classe d'échec depuis les métadonnées d'acquisition ;
le superviseur sait également relire les anciennes captures. Une panne publique constatée
ne crée pas de droit humain à elle seule. Les vrais refus de politique, outils interdits et
budget atteint gardent priorité ; les métadonnées absentes restent conservatrices.
Les deux libellés du garde-fou de navigation sont explicitement préservés, y compris
une interception Chromium causant `ERR_BLOCKED_BY_CLIENT`. Aucune règle propre à un site.

Une reprise explicite peut réconcilier une ancienne demande technique prouvée obsolète,
uniquement pour le même business/objectif/tâche/question. Le nouveau test reprend **le même memo**,
réutilise la détermination déjà conservée et n'appelle pas le collecteur. Les tests de reprise
distincts avec `synthesis_unavailable` demandent seulement la synthèse manquante et réutilisent
les sous-tâches terminées. Aucun faux accord humain ni extension de permissions.

## G. Lockstep et phases

`search_browse_lockstep` était déjà opt-in, absent du parcours pursuit. Il reste disponible
uniquement comme expérience explicitement activée. L'upgrade automatique du selector
économique disparaît. La découverte libre peut lire avant de rechercher, reformuler,
abandonner une source, agréger ou conclure dans l'ordre du modèle. Pas de quota/source
interdite ni d'instruction d'éviter les forums.

Discovery/validation deviennent des intentions légères. Le modèle peut proposer un changement
d'intention ; sinon l'intention courante est conservée. Sa pause n'est plus changée par le
code en continuation/discovery. Les bornes de ressources restent applicables dans les deux phases.

## H. Mesures avant/après

Scénario identique de `test_pursuit_real_discovery` : même état initial sans historique,
deux sources, même plan et réponses simulées. Capture des messages envoyés au gateway,
sans provider réel. Les entrées cumulées comprennent système et user à chaque appel ;
elles ne décrivent pas la taille d'un unique prompt de cold start.

| Mesure | Base #122 | Après |
| --- | ---: | ---: |
| Contrat principal, caractères | 1 957 | 333 |
| Rappel de contrat sous-agent, caractères | 2 313 | 333 |
| Planner, entrées cumulées | 6 404 | 3 073 |
| Six appels sous-agents, entrées cumulées | 30 599 | 18 263 |
| Synthèse, entrées cumulées | 9 401 | 5 232 |
| Revues, entrées cumulées | 2 987 | 0 |
| Total caractères entrants | 49 391 | 26 568 |
| Tokens approximatifs, caractères / 4 | 12 348 | 6 642 |
| Appels simulés | 10 | 8 |
| Champs obligatoires par candidat | 13 | 1 |
| Enum métier de type/classification | 9 types + 5 classes | 0 |
| Critères cognitifs de qualification | 6 dimensions + type autorisé | 0 |
| Quota de pistes pursuit | 2 | Aucun |
| Citations obligatoires par piste | 4, même acquisition | 0 |

Réduction cumulée de **46.2 %** des caractères entrants. Les six dimensions désignent
acteur, besoin, source, argent/urgence, canal, offre/test du contrat ancien ; leur exigence
conjointe est supprimée. La provenance technique de toute source **citée** reste contrôlée.
Les règles de source cognitives (une acquisition unique, quatre citations, indépendance,
liste de rejets métier) disparaissent ; aucun comptage ne mélange ces règles avec les
guards anti-exfiltration/HTTP/acquisition conservés. Les deux passages techniques reproduits
(absence de marqueur runtime, transfert d'une permission textuelle) sont couverts ; cela
ne prétend pas dénombrer toutes les pannes Web possibles.

## I–K. Validation

20 nouveaux cas couvrent les libertés A–J demandées, les captures courtes, la pause du
modèle, le classement, les garde-fous de navigation, le maintien d'une hypothèse invalidée
et la reprise sans recollecte. Ils testent des possibilités, pas une bonne réponse business.
26 anciens cas de revue automatique retirée sont remplacés par trois cas vérifiant
l'absence de replan/revue et de coût additionnel. Les autres anciens tests sont adaptés
au contrat minimal ; faux faits/provenance, finance, droits et isolation restent testés.

Validation finale hors provider :

- **885 passed, 1 skipped**, 88.65 s : pursuit, discovery, stratégie, capability, gateway,
  runtime, worker, recovery, finance, Workbench, multi-business et stack integration.
- **1933 passed, 97 skipped**, 172.46 s : suite complète applicable, contre
  **1939 passed, 97 skipped** sur la base exacte. Les identités des 97 skips sont identiques.
  Baisse nette de six tests : 26 anciens cas retirés, 20 nouveaux cas ajoutés.
- **20 passed**, 2.76 s : nouveau fichier de libertés/frontières seul.
- `git diff --check` passe. Fichiers des frontières Foundation/gateway/finance/ToolRegistry
  et acquisition de capacités inchangés par rapport au SHA de base.

Les tests utilisent DataRoots temporaires et transports simulés. Workbench est testé en
projection hors affichage ; ses tests natifs sans DISPLAY restent ignorés comme sur la base.
Aucun vrai provider, run économique, contact, message, compte, acquisition de capacité,
dépense, permission accordée ou merge. La seule publication autorisée est la branche/PR draft.

## N. Limites

Une baisse structurelle du contexte et des veto n'est pas une preuve de meilleure qualité
LLM ni de succès commercial. Aucun provider réel, vrai run ou DataRoot réel n'est utilisé.
Le diagnostic causal du run déclaré et la qualité de ses prochaines décisions restent à observer.

Le comparateur historique de stratégies reste pour ses consommateurs d'exécution ; pursuit
utilise le mode observation qui respecte l'ordre du modèle. Les heuristiques d'études de
capacité existantes restent sans acquérir de droits ni choisir les marchés. La mémoire
pertinente, les plafonds de contexte/stockage, le format textuel des acquisitions et les
références strictes de l'ancien champ facultatif `hypothesis` restent des limites techniques.
Les candidats/stratégies libres permettent néanmoins une première idée partielle.
