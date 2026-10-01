# Stabilisation OCTOPUS — PR #114 — 2026-10-01

Verdict technique : **READY PENDING WINDOWS SMOKE CHECK**. Le DataRoot Windows
réel n'a pas été accessible ici. Cela ne constitue pas une panne d'OCTOPUS.
Le contrôle ci-dessous reste nécessaire avant d'affirmer sa reprise effective.

## Commits et portée

Départ : `57cbc803e0650ec16e5d522391231ce81fa17826`.
Code validé : `b99c8d982f6227409fc05f9f53edf49b1e173dc3` ; le commit documentaire
suivant ne modifie pas ce code. Le SHA de tête final figure dans le rapport de session.

| Commit poussé | Changement |
|---|---|
| `65751b28dc557ce1eeb5139f720754304a02520c` | Récupération technique, demandes obsolètes, checkpoints, affichage Workbench |
| `bd6e474e0b572c0d1051fe6bea81779ec74e666b` | Reprise générique, coûts historiques, migrations concurrentes, contrôle sur copie |
| `b99c8d982f6227409fc05f9f53edf49b1e173dc3` | Tests hors réseau, prérequis de plateforme explicites, ffmpeg en CI |

Fichiers modifiés pendant cette stabilisation :

- Runtime et affichage : `agents/runtime.py`, `agents/tool_registry.py`,
  `agents/gui/workbench_v2.py`, `agents/gui/workbench_v2_data.py`.
- Persistance et supervision : `octopus/journal.py`, `octopus/supervisor.py`.
- Contrôle Windows : `scripts/check_pursuit_resume.py`.
- Tests : `tests/test_pursuit_recovery.py`, `tests/test_browser_workspace.py`,
  `tests/test_journal.py`, `tests/test_web_acquire.py`,
  `tests/test_astra_constructor.py`, `tests/test_astra_thin_harness.py`.
- CI/documentation : `.github/workflows/python-foundation.yml`,
  `docs/FIRST_START.md`, le présent rapport.

PR toujours en brouillon ; aucune fusion. Foundation et web_guard inchangés.
Aucune permission élargie, aucune action économique externe, aucun run autonome
réel, aucun Agnes réel. **Coût réel supplémentaire de cette session : 0 USD.**
Les appels LLM de validation ont tous utilisé des transports simulés.

## VALIDÉ dans cet environnement

Les refus connus de source ou d'entrée invalide ne deviennent plus automatiquement
une permission : `.local`, adresses privées, DNS, URL/schéma, paramètres manquants
ou mal typés, outil inconnu, JSON invalide, 429, timeout et fournisseur indisponible.
Les hôtes locaux restent interdits. Une source publique alternative reste possible
dans les bornes existantes ; les observations acquises sont conservées.

Les vrais refus de politique, outils connus interdits, paiement/publication,
budget explicitement épuisé et résultats d'action ambigus conservent leur frontière.
Les refus inconnus restent conservateurs. Login et consentement ne confèrent
aucun accès : abandonner une source ne permet pas de franchir cette barrière.

La réconciliation des anciennes `pursuit.permission` exige une preuve persistée
du caractère technique, le texte historique exact et l'absence de refus réel dans
la même mission. Elle n'est effectuée qu'à la reprise explicite : statut `cancelled`,
événement d'audit, aucune réponse humaine inventée. Une autre demande réelle
maintient la tâche en attente. Le résultat brut, les preuves et les coûts restent
conservés. La consultation seule affiche « Reprise technique disponible » sans écrire.

Le runtime conserve progressivement le plan, les sous-tâches terminées et les
étapes partielles. Après synthèse dégradée, seule la synthèse manquante est reprise ;
après crash, les étapes acquises sont réinjectées et consomment toujours la borne
d'étapes. Cela s'applique à la poursuite autonome et au superviseur générique.
Les coûts des reprises sont calculés par les liens parent/enfant persistés, même
si les anciens champs `root_id`/`root_run_id` ont été mal enregistrés ; aucune ligne
de coût historique n'est réécrite. Les migrations SQLite sont sérialisées, relisent
la version sous verrou et annulent le schéma partiel en cas d'échec.

Workbench : motif technique distinct d'une autorisation, cause réelle de pause,
coût cognitif corrigé, état issu de la base. Aucune refonte graphique.

## VALIDÉ par CI/tests

Suite locale complète : **1 589 réussis, 97 ignorés, aucun échec**, en 48,02 s.
Commande : `python -m pytest -o addopts='' -q` avec `requirements-local.txt`.
Compilation des modules modifiés et `git diff --check` réussis.
Les tests ignorés concernent PowerShell, Docker, navigateurs réels et une collection
optionnelle ; ils ne constituent pas une validation Windows.

Tests déterministes : DataRoots temporaires, cycle public avec source invalide puis
alternative, arguments invalides, 404/DNS/local, JSON et structured-output 400,
429, timeout, provider indisponible, navigateur fermé puis rouvert, action ambiguë,
vraie permission, pause/reprise, crash/restart, synthèse sans recollecte, coûts anciens,
réconciliation idempotente et demandes mixtes. Huit processus concurrents testent
les migrations d'une base neuve et V8. Le contrôle Windows est testé en processus
séparé avec empreintes SQLite/WAL/SHM inchangées et preuves/coûts conservés.

Le routage réel et les contrats sont exercés derrière un transport HTTP simulé.
La politique structurée existante reste : deux routes gratuites, trois requêtes
gratuites au maximum, seconde méthode du même modèle uniquement pour incompatibilité,
un repair JSON local puis validation. Aucun retry transport caché dans `economical`.
DeepSeek reste le seul repli payant autorisé, sous plafond. OpenRouter reste `:free`
avec prix maximum zéro et attestation modèle/provider/coût. Groq et OmniRoute gardent
leurs critères de qualification et d'attestation. Cooldowns, Retry-After, réputation
et budgets restent journalisés et couverts par les tests du gateway.
Une indisponibilité persistante s'arrête après trois cycles ; un plafond explicitement
atteint demande toujours une décision humaine. Le plafond initial par lancement/reprise
reste celui du parcours existant : 0,20 USD cognitif, 0 EUR économique externe.

CI GitHub du code validé :

- [python-foundation, run 36893678280](https://github.com/saillardsimon71-del/Octopus/actions/runs/36893678280) :
  `contract-and-worker` et `local-browser-and-control-plane` réussis, y compris
  installation Chromium, tests ciblés et suite Python complète.
- [Compute finance safety, run 36893678255](https://github.com/saillardsimon71-del/Octopus/actions/runs/36893678255) : succès.
- Statut Vercel : échec déjà présent au SHA de départ, encore présent sur cette
  branche ; il n'est pas une preuve de panne du runtime local OCTOPUS.

La CI historique du SHA de départ avait 87 échecs de prérequis : 79 PowerShell,
8 ffmpeg/ffprobe. Les tests PowerShell sont désormais ignorés si l'exécutable manque,
et ffmpeg est installé dans le job Ubuntu. Les assertions Windows restent identiques.

## À CONFIRMER SUR WINDOWS

DataRoot attendu : `%LOCALAPPDATA%\OCTOPUS\first-real-run-20261001`.
**État exact non inspecté ici** : objectif courant, tâches, demandes anciennes,
preuves, coûts, cooldowns, sessions et piste d'affiliation restent à lire sur la copie.
`api.octopus.local` n'était pas présent dans le code/contexte versionné de départ.
Une origine modèle ou un ancien contexte persisté est plausible, pas démontrée.
Le script rapporte les mentions persistées sans autoriser cet hôte.

Au prochain clic « Démarrer / reprendre OCTOPUS », l'objectif admissible existant
est réutilisé. Les seules demandes techniques prouvées sont réconciliées ; les vraies,
mixtes ou inclassables demeurent visibles et bloquantes. Une tâche réconciliée garde
sa détermination acquise, puis le superviseur décide dans les bornes restantes.
Un objectif en pause peut ouvrir un nouveau lancement borné avec le plafond existant.
La prédiction exacte pour ce DataRoot est donnée par `next_work` et
`pending_after_copy_resume` du script, avant toute vraie reprise.

Limites restantes : rendu/focus/scroll et session visible du Workbench Windows,
disponibilité réelle des providers et navigateur installé. Chromium n'a pas pu être
téléchargé ici ; sa fermeture/reprise a donc été vérifiée par simulation déterministe.
La qualité stratégique et un résultat économique réel ne sont pas prouvés par ces tests.
La borne de 120 s reste coopérative. Un crash entre retour d'outil et checkpoint peut
encore répéter une lecture publique ; entre retour HTTP LLM et journalisation, le coût
externe peut nécessiter une réconciliation. Aucune action externe n'est rejouée pour
réparer une soumission ambiguë. Ces limites ne justifient pas une nouvelle architecture.

## Smoke-check minimal au retour

Fermer le Workbench précédent avant ce contrôle, pour obtenir une copie stable.
Ce bloc retrouve le worktree existant de la branche ; il ne crée pas un nouveau dépôt.

```powershell
$ErrorActionPreference = 'Stop'
$mainRepo = 'C:\Users\saill\Projects\video-factory'
$blocks = ((git -C $mainRepo worktree list --porcelain) -join "`n") -split "`n`n"
$repos = @($blocks | Where-Object { $_ -match '(?m)^branch refs/heads/codex/foundation-realignment\r?$' } |
    ForEach-Object { ($_ -split "`n")[0].Substring(9).Trim() })
if ($repos.Count -ne 1) { throw 'Worktree PR #114 introuvable ou ambigu.' }
Set-Location -LiteralPath $repos[0]
if (git status --porcelain) { throw 'Modifications locales à préserver avant mise à jour.' }
git fetch origin codex/foundation-realignment
if ($LASTEXITCODE -ne 0) { throw 'Fetch échoué.' }
git merge --ff-only origin/codex/foundation-realignment
if ($LASTEXITCODE -ne 0) { throw 'Mise à jour non fast-forward.' }
$sha = (git rev-parse HEAD).Trim()
$remote = (git rev-parse origin/codex/foundation-realignment).Trim()
if ($sha -ne $remote) { throw 'SHA local différent de la branche distante.' }
Write-Host "SHA installé : $sha (comparer au SHA final du rapport de session)"
$python = Join-Path $mainRepo '.venv\Scripts\python.exe'
$dataRoot = Join-Path $env:LOCALAPPDATA 'OCTOPUS\first-real-run-20261001'
& $python .\scripts\check_pursuit_resume.py --data-root $dataRoot
if ($LASTEXITCODE -ne 0) { throw 'Contrôle sur copie non validé.' }
.\scripts\start-workbench.ps1 -DataRoot $dataRoot -Python $python -ReadOnly
```

Vérifier `integrity: "ok"`, `source_hashes_unchanged`, `evidence_preserved`,
`llm_cost_preserved`, `existing_objective_reused: true`, l'ID/résumé de l'objectif,
les demandes maintenues et `next_work`. Si plusieurs objectifs sont possibles,
relancer avec `--objective ID` pour l'objectif attendu. La simulation s'exécute
uniquement sur une copie temporaire ; aucun worker, service, navigateur ni LLM démarre.

En lecture seule : vérifier le motif de pause, la différence entre demande technique
et autorisation, les coûts/routes et les anciennes observations. Fermer cette fenêtre.
Après ces vérifications, pour la reprise explicitement choisie par l'opérateur :

```powershell
$browser = 'C:\Users\saill\Projects\Octopus-browser-test\agents\data\bin\agent-browser-win32-x64.exe'
if (Test-Path -LiteralPath $browser) { $env:OCTOPUS_AGENT_BROWSER = $browser }
.\scripts\start-workbench.ps1 -DataRoot $dataRoot -Python $python
```

Le lancement normal ouvre le Workbench ; le clic humain « Démarrer / reprendre OCTOPUS »
déclenche la reprise. Contrôler que l'ID d'objectif reste le même, la session Chromium
est visible lorsqu'ouverte, le budget/route sont affichés et aucune vraie demande
d'autorisation n'a disparu. Aucun run réel de ce smoke-check n'a été lancé pendant
la stabilisation.
