# État actuel d'OCTOPUS

**Date de mise à jour : 21 septembre 2026**  
**Document de référence pour la reprise.**

## 1. Git / intégration

Branche de référence : main.

HEAD vérifié au moment de cette mise à jour :

~~~text
951d398272ee9e13c1c3c1bf7775de97a5f41fb0
~~~

Ce HEAD contient la fusion de la PR **#38 — businesses as fourth supervised Python canary surface**.

Aucune PR n'était ouverte juste après cette fusion.

Les protections GitHub restent actives : les PR récentes ont exigé trois checks avant merge :

- targeted-tests
- contract-and-worker
- local-browser-and-control-plane

Le statut Vercel externe peut échouer sur une limite de build ; il n'est pas un check requis OCTOPUS.

## 2. Vision

OCTOPUS est un moteur local d'activités autonomes orienté vers des résultats économiques observés :

~~~text
ressources réelles
→ objectifs / hypothèses / expériences
→ agents + outils contrôlés
→ action dans le monde réel
→ preuves / métriques observées
→ ledger / décision / réinvestissement
→ nouveau cycle
~~~

La contrainte économique principale reste le **cash réellement encaissé**, puis marge, récurrence, profit et autonomie.

## 3. Frontières déjà établies

- LLM : routage centralisé, profil normal free-first / zero-cost, aucun fallback payant implicite.
- Finance : fail-closed ; compute metered, TTS/search distants et futurs connecteurs doivent déclarer leur coût et respecter les allowances.
- Développement autonome : worker isolé, worktree dédié, tests déterministes, commits bornés, Kilo/Step 3.7 Flash free.
- Night shift : rapports persistants, reprise, promotion gate et validation Git.
- Python canary : Docker imposé, baseline oracle obligatoire, AST guard, preflight strict, 1 fichier source par ticket, rayon de diff borné, fallback déclaratif interdit.

## 4. Canaris Python — état réel

Trois surfaces ont déjà été exécutées avec succès en conditions réelles puis promues :

1. octopus/capabilities.py → oracle tests/test_capabilities.py
2. octopus/resources.py → oracle tests/test_resources.py
3. octopus/connectors.py → oracle tests/test_connectors.py

Le canari réel connectors.py a passé :

- run 20260921-121758-e06c18
- policy python_canary
- Docker octopus-test-sandbox:py311
- 1 ticket, 1 tentative, 1 fichier modifié
- AST guard + preflight strict
- aucun fallback
- promotion Git vérifiée
- PR #37 mergée

La quatrième surface est maintenant enregistrée sur main :

4. octopus/businesses.py → oracle tests/test_businesses.py

Plan :

~~~text
octopus/config/night_shift_python_businesses_canary_v1.json
~~~

**Preuve encore manquante : le vrai run local du canari businesses.**

## 5. Seuil de sortie de la phase “plomberie”

La plomberie générale n'a pas vocation à devenir un projet sans fin.

Le seuil choisi est :

~~~text
businesses.py canary réel
→ backlog_complete
→ promotion git_verified
→ diff limité à octopus/businesses.py
→ oracle identique
→ PR de promotion verte et mergée
~~~

Si ces conditions passent sans défaut structurel nouveau, la phase de généralisation du self-development est considérée **suffisante pour V1**.

Il ne faut pas ajouter mécaniquement actions.py, economy.py, strategy.py, compute_finance.py, etc. comme canaris juste pour augmenter un compteur. Ces zones plus sensibles seront modifiées lorsqu'un besoin produit/business concret l'exige, avec leurs propres tests et frontières.

## 6. Prochaine phase — preuves économiques

Après le dernier canari businesses, la priorité quitte la plomberie et revient aux gates produit :

### G6 — canal réel / publication / action

Construire au moins un executor réel, idempotent, avec source externe persistée.

### G7 — boucle économique réelle

Faire une expérience complète :

~~~text
objectif
→ hypothèse
→ expérience
→ action réelle
→ mesure observée
→ ledger
→ evaluate_experiment
→ décision
→ prochaine action
~~~

### Cash-in

Le premier jalon commercial n'est pas “plus d'autonomie interne” mais :

~~~text
au moins 1 euro réellement encaissé
→ source vérifiable
→ ledger observed
→ attribution à un business / canal / expérience
~~~

## 7. Ce qui reste hors de cette preuve

- G1 live : attestation du pool OmniRoute free-only.
- G3/G4/G5 : compute GPU live, benchmark et coût réel vidéo.
- G6/G7 : action/publication et expérience économique réellement fermée.
- G8 : V1 exploitable de bout en bout.

Ces sujets doivent être traités selon leur valeur économique réelle, pas seulement selon l'ordre historique des travaux.

## 8. Point de reprise immédiat

Sur le PC Windows :

~~~powershell
$WT = 'C:\Users\saill\.codex\worktrees\python-canary-prelaunch'
$PY = 'C:\Users\saill\Projects\video-factory\.venv\Scripts\python.exe'

Set-Location $WT

git fetch origin
git checkout --detach origin/main

git log -1 --oneline
git status --short

& $PY -m octopus night-resume

docker image inspect octopus-test-sandbox:py311 --format '{{.Id}}'

& $PY -m pytest -q `
  tests/test_night_shift.py `
  tests/test_dev_worker.py `
  tests/test_businesses.py

if ($LASTEXITCODE -ne 0) {
    throw "Tests locaux échoués - ne pas lancer le canari businesses."
}

& $PY -m octopus night-shift `
  --repo . `
  --plan octopus/config/night_shift_python_businesses_canary_v1.json `
  --hours 1 `
  --max-tasks 1 `
  --max-failures 1
~~~

Ensuite : promotion gate, diff, push de la branche de promotion, PR, CI, merge.

## 9. Règle de reprise

Toujours vérifier l'état Git réel avant modification. Les fichiers sous docs/archive/ sont historiques. Le dépôt et ses tests priment sur toute ancienne conversation.
