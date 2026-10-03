# Catalogue OpenRouter et qualification

## Audit avant modification

Base vérifiée : PR #134, `fix/browser-cognitive-routing`,
`b77f8d41c25630572fcfc406a705e87d23b7664a`.

- `octopus/config/catalog.json` déclare sept fournisseurs, deux modèles
  OpenRouter nommés en dur et plusieurs modèles de fournisseurs retirés.
- `octopus/catalog.py` injecte OmniRoute, des pools opaques et des listes
  spécifiques par tâche. Le profil par défaut dépend de cette injection.
- `octopus/llm.py` dispose déjà du transport commun, du journal, des budgets,
  des cooldowns et de la qualification `browser.trajectory/browser-v1`.
  Le profil `economical` dispense cependant OpenRouter de preuve générale.
- `octopus/browser_bench.py` exige une sélection explicite de modèles et
  borne les trajectoires à douze étapes, mais ne borne pas les requêtes.
- Les diagnostics, le bootstrap Windows, les tests et les runbooks présentent
  encore le proxy retiré comme un prérequis.

Le correctif reste dans ces composants canoniques : deux fournisseurs actifs,
découverte de métadonnées, cache local, sélection par besoin, preuves distinctes
de la découverte et plafond de requêtes au niveau du transport. Le CLI Kilo
de l'atelier de développement latéral n'est pas le fournisseur Kilo Gateway.
Les lignes historiques du journal restent lisibles sans réactiver leurs routes.

La suite de référence a été exécutée avant modification dans un checkout
isolé. Ses résultats sont conservés pour la comparaison finale. Aucun appel
d'inférence, compte réel ou DataRoot économique n'a été utilisé.

## État livré

Le runtime n'expose que `openrouter` et `deepseek`. Le catalogue Git conserve
DeepSeek Flash et V4 Pro, leurs paramètres, prix et politiques existants. Les
routes OpenRouter sont construites dans `octopus/catalog.py`, à partir d'un GET
de `https://openrouter.ai/api/v1/models`. Aucun modèle OpenRouter n'est nommé
dans le code de production. Le CLI Kilo de l'atelier latéral reste disponible.

Les métadonnées publiques ont été consultées le 03/10/2026 pour vérifier le
schéma, sans clé ni inférence. L'observation ponctuelle contenait 466 entrées,
dont 17 à prix nuls et 8 avec entrée image. Ces nombres ne sont ni des quotas,
ni une configuration, ni une qualification. Ils peuvent changer.

### Données, cache et identité

Chaque modèle découvert conserve `api_model` (ID exact auteur/modèle),
`canonical_slug`, `name`, `pricing`, `context_length`, `input_modalities`,
`output_modalities`, `supported_parameters`, capacités dérivées, méthodes
structurées, `source`, `fetched_at` et `evidence_identity`. Son ID OCTOPUS est
`openrouter/` suivi de l'ID API exact, sans renommage. Sa clé de preuve générale
est `<ID OCTOPUS>@<canonical_slug>` ; la clé browser ajoute `browser.model:`.

Le cache est `OCTOPUS_HOME/data/openrouter-models.json`, version 1, avec source,
date et modèles normalisés. Écriture temporaire dans le même dossier, flush,
fsync puis remplacement atomique. Aucun header, secret, prompt ou réponse LLM
n'y est enregistré. La lecture revalide les données plutôt que faire confiance
aux drapeaux de gratuité ou capacités précédemment stockés.

- TTL : 6 heures. Cache périmé encore utilisable jusqu'à 24 heures incluses,
  explicitement indiqué `stale`. Au-delà, aucune route OpenRouter issue de ce
  cache n'est proposée.
- Échec réseau, JSON non exploitable ou échec d'écriture : dernier cache sain
  conservé, erreur réduite à son type et éventuel code HTTP. Sans cache sain,
  aucun candidat gratuit inventé. DeepSeek reste soumis à sa politique payante.
- Refresh automatique si cache absent/périmé ; backoff de 300 secondes après
  échec, dans le processus courant. `models --refresh` est une demande explicite
  qui peut réessayer immédiatement. Le cache persiste après redémarrage ; ce
  backoff en mémoire ne persiste pas. Aucun HTTP à chaque inférence avec un
  cache frais.
- GET borné à 15 secondes, 8 Mio et 4 096 entrées. Métadonnées invalides ignorées
  individuellement ; payload entièrement inexploitable refusé. Un payload valide
  sans modèle gratuit peut remplacer le cache par une liste vide.
- Un modèle disparu cesse d'être candidat au prochain refresh réussi. Une
  indisponibilité conserve uniquement la dernière observation, jusqu'à sa borne
  d'âge. Le changement de `canonical_slug` crée une nouvelle identité sans
  transférer l'ancienne qualification. L'identité est figée dans la justification
  de l'appel et utilisée par le benchmark, même si le cache change pendant l'appel.

### Gratuité, capacités et routage

`pricing.prompt` et `pricing.completion` sont obligatoires. Chaque prix présent,
y compris image, requête, raisonnement interne, cache ou champ supplémentaire,
doit être numérique, fini, non négatif et exactement zéro. Booléens, null,
NaN, infini, prix absent obligatoire ou non nul sont refusés. Le suffixe `:free`
n'est jamais une preuve. Une clé image optionnelle absente n'est pas inventée :
l'API observée n'en fournissait pas pour les modèles gratuits multimodaux.
Cette règle porte sur les prix déclarés par l'API, pas sur une garantie externe
de facturation ou de confidentialité.

Chaque requête OpenRouter impose `provider.max_price.prompt=0` et
`provider.max_price.completion=0`. Une réponse n'est admise que si son coût
provider est observé, fini et exactement zéro, avec fournisseur amont et modèle
résolu égal à l'ID API attendu ou son canonique. Coût inconnu, réponse payante
ou identité opaque : résultat bloqué, journal conservé et fournisseur suspendu.
Le contrôle après réponse ne peut annuler une facturation fautive du fournisseur.
Le crédit de 10 USD n'est jamais lu comme une allowance à consommer.

Vision vient de `image` dans `architecture.input_modalities`; tools de `tools`
dans `supported_parameters`; JSON schema de `structured_outputs`, JSON object
de `response_format`; reasoning de `reasoning` ou `include_reasoning`. Le nom
commercial n'intervient pas. La méthode textuelle conserve une validation JSON
locale, sans prétendre que le provider accepte un schéma. Une méthode schema
sans schéma ni un tool call sans outils déclarés ne sont envoyés.

Les tâches canoniques déclarent leurs besoins. Les candidats dynamiques doivent
les couvrir et respecter les classes de coût du profil. Une image ajoute vision
aux besoins à l'admission. Les tâches sensibles ne reçoivent aucun nouveau candidat
cloud. Le profil normal est `zero_cost`; seul un profil humain explicite permettant
de payer rend DeepSeek admissible, dans les budgets existants. `economical` conserve
deux routes gratuites, trois requêtes gratuites et DeepSeek comme seule voie payante.

Le benchmark général enregistre les preuves sur l'identité canonique. Les profils
exigeant une preuve demandent cinq essais, 90 % et moins de 60 jours ; `economical`
n'exempte plus OpenRouter et peut utiliser les tâches d'évaluation correspondantes.
Le profil `bench` amorce explicitement cette évaluation, sans fabriquer de preuve.
Les profils historiques qui n'exigent pas de preuve générale gardent leur politique
explicite. Aucun n'exempte `browser.react_step` de sa preuve browser.

### Browser et budget de requêtes

`browser.react_step` exige toujours `browser.trajectory/browser-v1`, y compris
DeepSeek, les pins, les baselines et les profils bench/legacy. Les dix scénarios
doivent être couverts, le score moyen atteindre 90 %, les cinq scénarios critiques
réussir intégralement, les preuves avoir moins de 14 jours et la suite être complète.
La stagnation en exécution est une observation journalisée, sans score cognitif automatique.
Le protocole browser accepte une action claire ou un rapport libre ; JSON natif n'est pas requis.
Le benchmark `browser.bench_step` est l'amorçage explicite sur des pages locales.
Les pools `openrouter/free` et routes automatiques de l'ancien catalogue sont exclus.

`models --browser-candidates` filtre vision et affiche `TECHNICAL`, avec
`browser=not-benchmarked`, `unqualified` ou `qualified` séparément. La disponibilité
signifie clé présente et candidat catalogué ; elle ne prouve ni quota disponible
ni compétence. Source, nombre, âge du cache et dernière erreur sont visibles.

`browser benchmark` exige une shortlist non vide sans doublon. `--max-requests`
est un plafond global strictement positif, indépendant de `--max-cost`. Une
réservation intervient immédiatement avant chaque transport, y compris une
alternative structurée ; les retries automatiques du SDK sont désactivés en bench.
Une borne atteinte interrompt la suite, laisse une preuve négative `incomplete`
et ne qualifie pas le modèle. Un rerun échoué ne conserve pas un ancien score
qualifiant. Aucun second modèle n'est lancé après épuisement du plafond global.

Le préflight affiche modèles, dix scénarios, répétitions, trajectoires, étapes,
borne des alternatives structurées, plafond de requêtes et plafond monétaire.
Pour un modèle, deux répétitions et douze étapes : **20 trajectoires et 240
décisions au maximum**. Avec JSON object puis texte, au maximum **480 requêtes
HTTP** sans plafond global ; avec texte seul, 240. Le préflight calcule ce nombre
depuis les méthodes réellement admissibles. Avec `--max-requests 240`, la borne
de transport reste 240, même si la suite doit finir incomplete.

Les résultats conservent scénario, identité résolue, score, réussite, steps,
calls, tokens, latence et coût. Le résumé affiche les objectifs terminés et
`cost_per_completed_objective_usd` : coût total divisé par objectifs terminés,
ou null si aucun n'est terminé. CSV, matrice, `llm_calls` et `bench_results`
restent dans les composants existants ; pas de nouvelle DB ni ledger.

### Interruptions de transport et raisonnement browser

Un 429 peut déclencher un seul retry du même appel, avec les mêmes messages,
le même modèle et la même trajectoire. Le cooldown annoncé est respecté si
son délai est fini et inférieur ou égal à 60 secondes. Un délai supérieur
interrompt la suite sans attendre ni raccourcir le cooldown. Chaque transport
consomme le plafond initial, alternatives structurées et retry inclus ; aucun
retry SDK n'est activé. `transport_requests_upper_bound` inclut cette possibilité
et reste borné par `max_requests`.

Une erreur persistante, un candidat inéligible ou un backend indisponible produit
`INCOMPLETE_INFRA` et arrête la suite. Les scénarios suivants sont exportés
`NOT_RUN`, avec scores absents, sans ajout de faux essais au journal. Les réponses
Un format illisible ou une action ambiguë deviennent des observations FORMAT/PROTOCOL
récupérables. L'objectif non atteint reste distinct d'une panne du runtime. Les résultats
exportés distinguent `evaluated`, `failed`, `incomplete_infra` et `not_run`.

Le schéma SQLite historique exige des nombres pour `passed` et `score` : les
lignes d'interruption conservent ces champs techniques à zéro mais portent
`checks.benchmark_incomplete=true` et leur statut explicite. La qualification
les exclut du score cognitif et reste refusée pour cette tentative interrompue.
Les anciennes lignes `RateLimitError`/`NoEligibleModel`, notamment le run 58,
sont également exclues, sans modifier l'historique ni réutiliser une ancienne
qualification. Le score est inconnu si aucun scénario n'a été évalué.

Les métadonnées de raisonnement OpenRouter sont conservées et revalidées dans
le cache. Pour `browser.bench_step` et `browser.react_step`, la gateway choisit
`reasoning.effort=low` uniquement si cet effort est annoncé, ou si
`supported_efforts=null` indique que tous les efforts sont acceptés. Une liste
absente ou incompatible ne déclenche aucun réglage inventé. Le benchmark et le
contrôleur utilisent la même politique, journalisée avec l'appel. Les autres
tâches gardent leur défaut. Ce réglage répond à une observation réelle : sous
le défaut `xhigh`, Qwen a consommé 1 190 à 1 200 tokens de raisonnement dans
trois réponses limitées à 1 200 tokens, sans JSON exploitable. Le contrat des
efforts et le budget partagé sont décrits dans la
[documentation OpenRouter](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Audit final : 32 réponses

| # | Réponse |
|---|---|
| 1 | Seuls deux modèles OpenRouter étaient déclarés dans le catalogue Git ; aucune découverte API n'ajoutait la population réelle. |
| 2 | `octopus/config/catalog.json`, IDs `openrouter/qwen3.8-27b-free` et `openrouter/dots-3-free`; listes et exemption supplémentaires dans `catalog.py` et `llm.py`. Ces noms sont historiques. |
| 3 | Ollama, LM Studio, Groq, Gemini, Cerebras, Kilo Gateway et injection OmniRoute. |
| 4 | Lecture du journal, anciennes classes de coût et IDs des rapports ; CLI Kilo de développement, consommateur actuel distinct de Kilo Gateway. Aucun ancien fournisseur redevient exécutable. |
| 5 | Les métadonnées de l'API OpenRouter, revalidées et datées ; le cache est une observation bornée de cette source. |
| 6 | GET `https://openrouter.ai/api/v1/models`. |
| 7 | `OCTOPUS_HOME/data/openrouter-models.json`, pas le catalogue Git ni une nouvelle DB. |
| 8 | 6 h ; usage stale borné à 24 h ; backoff automatique 300 s dans le processus. |
| 9 | Cache sain conservé et état/error visibles ; après 24 h ou sans cache, aucun OpenRouter inventé. Pas de fallback payant implicite. |
| 10 | Prompt/completion obligatoires et tous les prix présents finis exactement nuls ; jamais le suffixe ni le nom. |
| 11 | Routes payantes exclues du catalogue, prix plafonnés à zéro dans la requête, coût et identité vérifiés après réponse. Le crédit n'autorise aucun achat. |
| 12 | `image` dans `architecture.input_modalities`. |
| 13 | `tools` dans `supported_parameters`. |
| 14 | `structured_outputs` pour schema et `response_format` pour JSON object ; outil structuré si tools, validation locale séparée. |
| 15 | Besoins de la tâche couverts, classe de coût autorisée, disponibilité, privacy, cooldowns et autres admissions existantes. |
| 16 | Preuves récentes de la tâche/évaluation correspondante pour les profils qui les exigent ; preuve browser-v1 séparée pour le pilotage browser. |
| 17 | Les métadonnées décrivent un contrat technique ; seules les trajectoires mesurent navigation, vision, récupération et non-stagnation. |
| 18 | Clé `browser.model:<ID OCTOPUS>@<canonical_slug>`, identité résolue vérifiée et snapshot de la justification conservé pendant l'appel. |
| 19 | Retiré des candidats au refresh réussi ; anciens journaux conservés. Pendant une panne, dernière observation stale explicitement bornée. |
| 20 | Nouveau candidat technique, aucune preuve automatique, aucune compétence déclarée avant évaluation. |
| 21 | NON. Nouvelle identité canonique, aucune ancienne preuve de qualification transférée. |
| 22 | NON. Les pools opaques/automatiques sont exclus ; une réputation de pool ne devient pas preuve d'un modèle. |
| 23 | NON. DeepSeek est soumis à browser-v1 dans tous les profils, pins et baselines. |
| 24 | 2 : OpenRouter et DeepSeek. |
| 25 | Réservation globale avant chaque transport, alternatives incluses, aucun retry SDK en bench ; arrêt et preuve incomplete à la borne. |
| 26 | 240 décisions : 1 x 10 x 2 x 12 ; jusqu'à 480 transports avec deux méthodes admissibles. Le plafond explicite prime. |
| 27 | NON. `models` affiche seulement les deux fournisseurs et les gratuits dynamiques. |
| 28 | OUI. Pas de migration/destruction du journal ; les anciens IDs restent lisibles mais inéligibles. |
| 29 | NON. Foundation et orchestration ne sont pas modifiées. |
| 30 | NON. Aucune permission ni mandat élargi. Les fixtures locales conservent les frontières existantes. |
| 31 | NON. Aucun changement au ledger, schéma, allowances ou provisionnement compute. Le budget gateway gratuit n'exclut plus un appel sans coût lorsque l'enveloppe payante est épuisée. |
| 32 | NON. Aucun accès au DataRoot économique, Fiverr, profil Chrome réel ou mandat du compte. |

## Validation et limites

Les fixtures remplacent métadonnées et transport ; leurs preuves sont synthétiques
et isolées. Elles ne qualifient aucun modèle réel. Les contrôles portent sur prix,
capacités, cache et redaction, identité changeante/disparue, preuves générales et
browser dans tous les profils, transports structurés, quotas et coûts. La dernière
sélection de nouveaux cas, bornes et worker compte 76 succès, aucun skip. Les
fixtures pursuit/recovery, la capture UTF-8 et le rerun Tk ciblé comptent ensuite
71 succès, aucun skip ; les scénarios de récupération fournissent explicitement
leurs preuves générales et isolent la disponibilité browser sans réseau.
Les
autres tests gateway, cooldowns, multimodal, doctor et frontières partagées sont
repris dans la suite complète et les workflows existants.

La référence exécutée sur ce Windows au SHA #134 exact compte 2 231 succès,
28 échecs et 10 skips (816,038 s), différente de l'environnement publié de #134
(2 160 succès, 97 skips). Douze échecs de référence concernent la gateway
economical ; quinze les fixtures pursuit/recovery/reasoning ; un la création
d'un symlink Windows sans privilège. Les dix skips sont deux faux binaires POSIX,
sept E2E browser sans backend et un smoke foundation sans Hermes/Chromium.
Les résultats après patch, la comparaison exacte des identités/reasons de skips
et la CI du head publié sont consignés dans la PR, sans transformer un résultat
attendu en succès observé. Vercel était déjà rouge sur #134 ; il reste hors périmètre.

Les limites externes restent explicites : prix et identités observés auprès du
provider, disponibilité réelle/quota non prouvés par `models`, cache stale borné,
backoff non persistant, conditions de données à vérifier avant envoi autorisé,
qualification réelle et observation Windows à obtenir selon le protocole suivant.

## Protocole Windows après publication de la PR

Ce protocole est à exécuter ultérieurement par l'opérateur. Aucun benchmark LLM
réel ni smoke de compte n'a été exécuté pendant le correctif. Arrêter d'abord
worker, runs et Workbench. La nouvelle PR est empilée sur la branche réelle
`fix/browser-cognitive-routing` de #134, dont le SHA exact a été vérifié ; la
branche `feat/browser-cognitive-routing` mentionnée dans la mission n'existe pas
sur origin. Aucun merge de main.

1. Checkout détaché du head exact publié de la nouvelle PR, dans un nouveau
   worktree propre. Le head est résolu par GitHub et comparé au remote ; conserver
   la valeur affichée avec le rapport de qualification. Ces commandes ne copient
   ni ne recréent le DataRoot ou le profil du compte.

```powershell
$qualificationRepo = 'C:\Users\saill\Projects\video-factory'
$qualificationTree = 'C:\Users\saill\Projects\Octopus-openrouter-qualification'
git -C $qualificationRepo fetch origin codex/openrouter-dynamic-free
if ($LASTEXITCODE -ne 0) { throw 'Fetch échoué' }
$qualificationHead = gh -R saillardsimon71-del/Octopus pr view codex/openrouter-dynamic-free --json headRefOid --jq .headRefOid
if ($LASTEXITCODE -ne 0) { throw 'Head PR indisponible' }
$qualificationFetched = git -C $qualificationRepo rev-parse FETCH_HEAD
if ($qualificationHead -ne $qualificationFetched) { throw 'Head PR différent du remote' }
git -C $qualificationRepo worktree add --detach $qualificationTree $qualificationHead
if ($LASTEXITCODE -ne 0) { throw 'Checkout détaché échoué' }
Set-Location $qualificationTree
git rev-parse HEAD
git status --short
```

2. Utiliser exactement le même Python et le même DataRoot économique. Les clés
   passent par le mécanisme secret existant ; ne pas les afficher.

```powershell
$qualificationPython = 'C:\Users\saill\Projects\video-factory\.venv\Scripts\python.exe'
$qualificationDataRoot = 'C:\Users\saill\AppData\Local\OCTOPUS\economic-cold-e9de3eb2907c4bb6898b2c19b7897693'
$env:OCTOPUS_HOME = $qualificationDataRoot
$env:OCTOPUS_DB = Join-Path $qualificationDataRoot 'data\octopus.db'
$env:PODALUX_ROOT = $qualificationTree
$env:OCTOPUS_PROFILE = 'zero_cost'
& $qualificationPython -m octopus browser doctor --smoke
if ($LASTEXITCODE -ne 0) { throw 'Smoke navigateur local échoué : arrêter' }
& $qualificationPython -m octopus models --refresh
if ($LASTEXITCODE -ne 0) { throw 'Refresh catalogue échoué : arrêter' }
& $qualificationPython -m octopus models
& $qualificationPython -m octopus models --browser-candidates
```

3. Vérifier Flash, V4 Pro et les gratuits OpenRouter présents, aucun ancien
   fournisseur. Ne pas ouvrir Fiverr. Choisir **un seul** candidat gratuit fixe
   dans la liste TECHNICAL vision pour cette première évaluation. Ne pas
   sélectionner tous les modèles et ne pas utiliser un pool. Saisir son ID exact
   imprimé par le CLI ; le modèle choisi devra lui-même acquérir sa preuve.

```powershell
$qualificationModel = Read-Host 'ID OCTOPUS exact du candidat gratuit choisi'
if (-not $qualificationModel.StartsWith('openrouter/')) { throw 'Choisir un candidat OpenRouter fixe' }
& $qualificationPython -m octopus browser benchmark --models $qualificationModel --repeats 2 --max-cost 0 --max-requests 240
$qualificationExit = $LASTEXITCODE
& $qualificationPython -m octopus models --browser-candidates
```

4. Examiner le préflight, les 20 trajectoires attendues et les sorties CSV/JSON :
   résultats par scénario, modèle/fournisseur résolus, vision réelle, recovery,
   invalid_args, stale_refs, score, steps, calls, tokens, latence, coût nul et
   coût par objectif terminé. Vérifier absence de stagnation et qualification
   `browser-v1` réellement acquise. Aucune preuve synthétique des tests ne doit
   être importée. Si la suite est interrompue, le candidat reste inéligible ;
   un nouveau plafond ou candidat est un choix explicite après inspection,
   jamais une hausse automatique ni un retry payant. Un exit non nul exige
   l'examen des résultats et n'autorise pas le smoke compte.

5. Seulement après au moins un modèle réellement qualifié, reprendre le smoke
   du même compte `fiverr-main`, même session et mandat `owned_account/read`,
   sans nouveau login. Le profil d'une mission explicitement autorisée peut
   conserver sa politique, mais DeepSeek aussi exige browser-v1. Donner un but
   libre d'observation des services, commandes, évaluations, solde, performance,
   messages et restrictions. Aucune instruction de clic, URL interne donnée,
   obligation de screenshot ou tutoriel de plateforme.

6. Constater les informations effectivement acquises, les inconnus, la
   récupération et l'absence de boucle/invention ; contrôler `llm_calls`,
   `browser.controller`, `task_steps`, preuves et `execution_status`. Conserver
   les frontières read et les effets externes existants. Une qualification
   technique ou une suite verte ne constitue pas un résultat économique.
