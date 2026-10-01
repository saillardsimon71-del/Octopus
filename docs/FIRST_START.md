# Premier démarrage OCTOPUS

État du chantier : 2026-10-01. Implémentation sur `codex/foundation-realignment`,
PR #114 en brouillon, basée sur la PR #113. Aucune fusion automatique.
La stabilisation et le contrôle du DataRoot existant sont décrits dans
[STABILIZATION_2026-10-01.md](STABILIZATION_2026-10-01.md).

## Référence et portée

[FOUNDATION.md](FOUNDATION.md) contient le texte intégral fourni directement par
l'opérateur pour ce chantier. C'est la nouvelle référence normative minimale.
Ce fichier ne prétend pas retranscrire le message historique "FOUNDATION EXACTE"
des 13-14 septembre, qui n'a pas été retrouvé. Les anciennes visions décrivent
des états historiques ; elles ne prescrivent ni marché, ni architecture.

Le bouton "Démarrer / reprendre OCTOPUS" part de la finalité, sans entreprise,
offre, marché ni mission vidéo. "Confier une mission" ajoute un objectif humain
au même moteur. Les limites du premier démarrage sont des choix d'implémentation
et d'autorisation, pas des clauses ajoutées à la Foundation.

## Parcours exécuté

`supervisor.start_pursuit` crée ou reprend un objectif dans `strategy`, sous le
contexte système `octopus` (aucune entreprise créée). Il utilise le handler
`supervisor.objective_work`, `tasks`, le worker et `agents.runtime.run_mission`
existants. Aucun second moteur, service ou journal n'est introduit.

Le modèle reçoit la Foundation, l'objectif, les ressources vérifiables, les
preuves et décisions conservées, le rapport et les observations précédents.
Il propose de continuer, de s'arrêter ou de demander une permission. Chaque
décision et sa raison sont persistées. Une continuation crée le travail suivant.
Après trois cycles au plus, une reprise humaine est nécessaire. Le délai de
120 secondes par cycle est coopératif : un appel déjà engagé peut dépasser ce
délai jusqu'à son timeout. Chaque rôle a au plus six étapes.

La reprise réutilise l'objectif et les tâches existants. Une demande humaine
conserve le résultat avant de suspendre le travail ; sa réponse ne rejoue pas
la mission et n'accorde aucune permission. Après un arrêt brutal, les baux du
worker sont réconciliés au démarrage. Le plan, les sous-tâches terminées et les
étapes d'observation sont enregistrés progressivement. Une reprise après crash
ou synthèse indisponible réutilise ces résultats ; une lecture interrompue
avant son checkpoint peut encore être répétée.
Les effets externes sont exclus de ce parcours initial.

Hermes fournit la navigation réelle, derrière les gardes existants. OCTOPUS
choisit quand s'en servir. "Navigateur" montre URL, objectif, date, dernière
action, résultat ou refus, état de session et aperçu textuel réellement acquis.
Le lanceur affiche aussi la même session Chromium lorsqu'elle est ouverte.
Il ne s'agit pas d'un second navigateur piloté par la GUI. Une session terminée
reste consultable comme observation datée, sans prétendre être encore active.

Agnes reste une capacité spécialisée dans les outils avancés et son parcours
autorisé existant. Aucune activité vidéo n'est créée au démarrage. Une mission
libre demandant une vidéo ne contourne pas la politique initiale : elle doit
s'arrêter, adapter sa stratégie ou demander l'autorisation nécessaire.

## Permissions de ce premier démarrage

- Budget économique externe : 0 EUR. Budget cognitif LLM séparé : profil `economical`,
  plafond initial de 0,20 USD pour les trois cycles au plus d'un démarrage ou d'une reprise,
  configurable avec
  `OCTOPUS_PURSUIT_LLM_BUDGET_USD` avant le démarrage.
- Recherche, consultation Web publique gratuite, état des ressources et comptes.
- Aucun envoi, contact commercial, publication, contrat, achat, paiement,
  génération vidéo ou installation autonome.
- Un canal déjà autorisé dans les données ne débloque pas d'autre outil ici.
- Deux routes gratuites et trois requêtes gratuites au plus par appel logique,
  puis DeepSeek seul sous plafond. Une incompatibilité structurée autorise une
  seconde méthode du même modèle. Sans route disponible, la panne technique
  reste bornée aux trois cycles puis l'objectif est mis en pause ; elle ne crée
  pas de demande de permission. Un plafond LLM explicitement atteint reste une
  vraie frontière humaine. Désactiver la passerelle bloque le démarrage, sans appel direct.
- Une réponse dans "Humain" ne modifie ni droits, ni budget. Les permissions
  spécialisées restent administrées par leurs mécanismes existants.

## Ouvrir sous Windows

Depuis la racine de cette branche, utiliser un dossier de données neuf. Conserver
le DataRoot historique Agnes séparé. L'interpréteur doit déjà contenir les
dépendances du projet. Exemple adapté à la machine de validation :

```powershell
$python = 'C:\Users\saill\Projects\video-factory\.venv\Scripts\python.exe'
$dataRoot = Join-Path $env:LOCALAPPDATA 'OCTOPUS\foundation-start'
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$env:OCTOPUS_AGENT_BROWSER = 'C:\Users\saill\Projects\Octopus-browser-test\agents\data\bin\agent-browser-win32-x64.exe'
.\scripts\start-workbench.ps1 -DataRoot $dataRoot -Python $python
```

Le binaire Hermes ci-dessus a été trouvé sur la machine de validation ; adapter
le chemin sur une autre machine. [BROWSER_AGENT.md](BROWSER_AGENT.md) décrit les
prérequis et le diagnostic existant. Le lancement n'installe rien.

Le lanceur isole journal, fichiers, journaux d'exécution et données auxiliaires
dans ce DataRoot. Le code et le catalogue LLM restent ceux de la branche.
`OCTOPUS_CATALOG`, s'il est défini, sélectionne le catalogue configuré par l'humain.
Ne pas confondre présence d'une clé et éligibilité d'un modèle gratuit : les règles
de prix, disponibilité, quotas et preuves de la passerelle continuent de s'appliquer.
La clé DeepSeek reste locale (`DEEPSEEK_API_KEY` ou variable utilisateur Windows).
Le routage, le coût et les refus sont visibles dans Activité ; ce budget LLM
n'accorde aucune allowance économique et n'autorise aucun effet externe.

1. Ouvrir la vue d'ensemble et cliquer sur "Démarrer / reprendre OCTOPUS".
2. Consulter Missions, Navigateur, Livrables et Activité pour suivre le travail.
3. Lire la raison d'arrêt ou la demande humaine. Ne pas assimiler le rapport à
   une preuve de demande, de paiement ou de performance économique.
4. Utiliser "Mettre en pause" sur l'objectif pour demander son arrêt, puis
   "Reprendre" pour un nouveau réexamen borné. Fermer la fenêtre ne constitue
   pas une demande de pause ; le processus déjà lancé termine sa borne.
5. Pour confier un objectif libre, décrire uniquement le résultat recherché dans
   Missions. Ce parcours utilise les mêmes limites et la même intelligence.

Pour consulter sans démarrer ni modifier les données :

```powershell
.\scripts\start-workbench.ps1 -DataRoot $dataRoot -Python $python -ReadOnly
```

Les bases et leurs transactions WAL sont copiées temporairement puis lues en
mémoire avec les écritures interdites. SQLite n'ouvre pas les fichiers source,
ce qui évite même la création de fichiers auxiliaires dans le DataRoot.
Les migrations et sondes Agnes sont désactivées ; démarrage et pause sont bloqués.
Les captures contrôlent les empreintes des fichiers d'un DataRoot au repos.
Si une base change pendant sa copie, la consultation réessaie puis signale
l'impossibilité d'obtenir cette vue au lieu de migrer ou modifier la source.

## Commandes équivalentes

Depuis la même racine, dans une session dédiée :

```powershell
$env:OCTOPUS_HOME = $dataRoot
$env:OCTOPUS_DB = Join-Path $dataRoot 'data\octopus.db'
$env:PODALUX_ROOT = (Get-Location).Path
Remove-Item Env:OCTOPUS_WORKBENCH_READONLY -ErrorAction SilentlyContinue
& $python -m octopus pursue
& $python -m octopus status
```

Autres entrées, à utiliser séparément selon l'objectif :

```powershell
& $python -m octopus pursue --goal 'Examine une possibilité économique à partir de sources publiques.'
& $python -m octopus pursue --objective 1 --pause
& $python -m octopus pursue --objective 1
```

Remplacer `1` par l'identifiant réellement affiché. La commande historique
`runtime` reste disponible pour ses objectifs explicites ; elle n'est pas le
nouveau démarrage neutre. Le lanceur `start-agnes-workbench.ps1` est conservé
comme alias compatible de `start-workbench.ps1`.

## Validation et limites

Validation Windows exécutée le 2026-10-01 : **223 tests réussis** dans la sélection
partagée ci-dessous (86 s), plus **7 scénarios de navigateur réel réussis**.
Aucun test ignoré dans ces exécutions. Le bouton principal a aussi lancé le vrai
processus CLI sur un DataRoot temporaire : création de l'objectif neutre et arrêt
explicite avec la passerelle désactivée, sans appel LLM. Les sept captures ont
été prises dans de vraies fenêtres Windows, sans erreur de callback.

| Exigence | Correction / constat vérifié |
|---|---|
| A. Partir de rien | Un objectif issu de la finalité, sans entreprise ni vidéo |
| B. Mission libre | Même runtime, origine humaine affichée, finalité conservée |
| C. Hermes | Vraie page locale et arbre d'accessibilité dans la GUI |
| D. Pas de vidéo par défaut | Allowlist de consultation, aucune génération créée |
| E. Agnes spécialisé | Parcours existant avec autorisation, backend simulé, MP4 vérifié |
| F. Permission manquante | Refus effectif, question durable, réponse sans extension de droits |
| G. Arrêt / reprise | Annulation bornée ; scénarios Hermes sans répétition d'effet ambigu |
| H. Vérité des résultats | Rapport distinct de preuve et résultat économique inconnu |
| I. Consultation | Écritures et démarrage refusés ; empreintes du DataRoot inchangées |

Commandes de validation, avec `$python` et le backend définis comme ci-dessus :

```powershell
& $python -m pytest -q tests/test_foundation_start.py tests/test_autonomous_loop.py tests/test_workbench_v2.py tests/test_agnes_gui_missions.py tests/test_agnes_production.py tests/test_browser_workspace.py tests/test_runtime_react.py tests/test_gateway.py tests/test_runtime_handler_discovery.py tests/test_runtime_status.py tests/test_journal.py --tb=short -o addopts=''
& $python -m pytest -q tests/test_browser_workspace_e2e.py --tb=short -o addopts=''
```

Les données historiques n'ont pas été ouvertes par les parcours d'écriture.
Le MP4 historique conserve son SHA-256
`bd87142a906a9c913b52794ade81c5da084ca0dc08d053713590718890a0fc41`.
Le checkout initial reste propre à `61fa0b2d07007e9d098ca07c2b4aa2e7b94e206b`.
Le diff et la syntaxe des deux lanceurs PowerShell ont été vérifiés.

Les scénarios sont dans `tests/test_foundation_start.py` : démarrage vide,
mission libre, continuation bornée, route gratuite absente, permission manquante,
pause/reprise sans effet externe, résultat économique inconnu, passerelle
désactivée, consultation et navigation Hermes réelle sur HTTP local.

Les tests partagés couvrent aussi Agnes avec backend simulé et fichier vérifié,
budgets, décisions, runtime, chargement des handlers et gardes navigateur.
Les scénarios de `tests/test_browser_workspace_e2e.py` exercent le vrai navigateur
sur un site local contrôlé. Le modèle y est remplacé par un décideur de test.

Les [captures Windows](validation/foundation-start/) montrent l'interface réelle
aux dimensions 1280x720 et 1488x960, sur des données temporaires. La page de
traduction est une fixture locale clairement identifiée. Les rapports sont
ceux du scénario contrôlé ; aucun client ni revenu réel n'est sous-entendu.

La qualité stratégique d'un modèle gratuit en situation réelle et une première
performance économique ne sont pas démontrées par ces tests. Aucun appel LLM
payant, contact commercial ou génération Agnes réelle n'est nécessaire à leur
exécution. Prochaine observation utile : sur le DataRoot neuf, vérifier soit
une première observation publique sourcée par la route gratuite disponible,
soit l'obstacle exact qui empêche cette observation.
