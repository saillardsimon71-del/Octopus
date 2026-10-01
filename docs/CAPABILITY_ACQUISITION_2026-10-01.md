# Capability gaps et acquisition de capacités — 2026-10-01

Verdict technique : **Mécanisme démontré hors ligne avec des doubles locaux. Aucun connecteur réel
construit, aucune capacité réelle acquise, aucune preuve économique apportée.**

Base validée : PR #116, `bd8a09de84f37b4bb4c086d0a24a1eedcd3c8f06` (séparation stratégie ↔ capacités).
Ce chantier ne refait ni #114 (stabilisation, reprise, frontières humaines), ni #115 (apprentissage
économique cumulatif), ni #116 (séparation stricte). Il les prolonge d'un cran : que faire quand la
stratégie retenue exige une capacité absente.

## Problème traité

« J'ai retenu une stratégie économiquement intéressante. Elle nécessite une ou plusieurs capacités
que je ne possède pas. Que faut-il faire pour devenir capable de l'exécuter ? »

Chaîne implémentée :

```text
STRATÉGIE RETENUE (#116, inchangée)
  → CAPACITÉS REQUISES (required_capabilities, jamais une déclaration de disponibilité)
  → INVENTAIRE ACTUEL (recalculé : registres réels + politique du caller)
  → CAPABILITY GAPS (information opérationnelle, pas un échec stratégique)
  → OPTIONS D'ACQUISITION (déterministes, bornées)
  → COÛT / RISQUE / VALEUR (pondérations explicites, sunk costs exclus)
  → DÉCISION (acquire | defer | reject | human_required)
  → CONSTRUCTION / CONNEXION / DEMANDE HUMAINE (tâche durable, jamais dans l'étude)
  → VALIDATION (source déterministe uniquement)
  → INVENTAIRE MIS À JOUR (recalculé, jamais mémorisé)
```

## Ce qui est réutilisé, sans second registre

| Besoin | Mécanisme existant réutilisé |
|---|---|
| Autorité sur les outils | `agents.runtime.TOOLS` / `agents.tool_registry.ToolRegistry` |
| Autorité sur les effets de canal | `octopus.actions.registered_executors` |
| Autorité sur les ressources et leurs besoins humains | `octopus.resources` (+ `octopus.resource_probes`) |
| Classement stratégique et exécutabilité | `octopus.strategy_separation` (#116), non modifié |
| Indisponibilité temporaire du navigateur | `strategy_separation.browser_unavailable_tools` |
| Persistance des écarts et décisions d'étude | `strategy_evidence` (`nature=computed`) liée à l'hypothèse, comme l'annotation d'exécutabilité |
| Exécution durable d'une acquisition | file `tasks` + `worker` : clef d'idempotence, bail, `ctx.memo`, reprises |
| Frontière humaine | `ctx.ask_human` / `human_requests` (même mécanisme que `resources.acquire`) |
| Décision canonique d'acquisition | objet `decision` de `octopus.strategy` |
| Vocabulaire des frontières humaines | `resources.HUMAN_NEEDS` (login, oauth, 2fa, captcha, kyc, signature, bank_validation, legal, payment_method) |

**Aucune nouvelle table, aucune nouvelle base, aucun nouveau moteur de workflow, aucune migration.**
`octopus/capabilities.py` reste gelé et n'est pas promu autorité ; il n'est pas appelé par ce chantier.
`remember`/`recall` ne sont jamais une source d'autorité : un apprentissage mémorisé ne prouve pas
qu'une capacité existe.

## Modèle retenu

### A. Exigence de capacité

`required_capabilities` d'une stratégie proposée (#116). Identifiants normalisés
(`phone_call`, `email:send`, `browser_interaction`…). Une liste incomplète ou illisible reste
`not_established` : aucun écart n'est inventé à partir d'exigences tronquées.

### B. État de capacité (`CAPABILITY_STATES`)

`available`, `missing`, `temporarily_unavailable`, `permission_denied`, `human_required`,
`not_established` — mêmes distinctions sémantiques que #116, recalculées à chaque lecture.
Trois faits sont séparés, jamais confondus :

- `capable` : une cible réelle (outil, exécuteur, ressource sondée) est enregistrée ;
- `authorized` : cette cible est dans l'ensemble autorisé du caller ;
- `available` : capable **et** autorisé **et** non temporairement indisponible.

**CAPABLE ≠ AUTORISÉ.** Une capacité techniquement acquise hors de l'ensemble autorisé reste non
exécutable ; ce module n'élargit jamais `allowed_execution`.

Le cycle de vie d'acquisition (`ACQUISITION_STATES` : `none`, `planned`, `in_progress`,
`validation_required`, `acquired`, `failed`, `rejected`, `deferred`, `human_required`, `not_needed`)
est relu depuis la tâche durable. Il ne prouve jamais la disponibilité : si une capacité acquise
disparaît des registres, l'état redevient `missing` avec `stale_acquisition=true`.

### C. Options d'acquisition (`OPTION_KINDS`)

Produites déterministement, sans découverte ni installation :

- `already_present` — cible réelle déjà enregistrée : il manque une **autorisation**, pas une
  construction (jamais élargie ici) ;
- `local_build` — constructeur local explicitement enregistré (`register_builder`) ;
- `local_adapter` — ressource déclarée disponible mais sans exécuteur : adaptateur à construire ;
- `human_frontier` — vraie frontière humaine (needs de la définition connue ou de la ressource) ;
- `none` — exigences inconnues : rien n'est inventé, la décision est `defer`.

### D. Décision d'acquisition

Valeur économique potentielle de la stratégie retenue (critère dominant dans l'ordre
`cash_received > margin > recurrence > autonomy > growth`, rang économique, nombre de preuves liées,
réutilisabilité de la capacité) moins le coût de l'option (risque, complexité, minutes humaines,
coût LLM). Pondérations explicites et lisibles dans le module (`CRITERION_SCORE`, `RISK_SCORE`,
`ACQUIRE_THRESHOLD`…) : ce sont des pondérations de décision interne, **pas des mesures économiques**.

Règles d'échec sûr, dans l'ordre :

1. stratégie économiquement invalidée → `reject` (aucune acquisition automatique) ;
2. aucune option connue → `defer` (exigences inconnues, rien n'est inventé) ;
3. frontière argent ou juridique (`payment_method`, `legal`, `kyc`, `signature`, `bank_validation`)
   → `human_required`, et **aucun double local ne peut la lever** ;
4. risque critique → `human_required` ;
5. coût financier inconnu → `defer` (inconnu n'est ni zéro ni autorisé) ; coût financier positif →
   `human_required` (enveloppe et autorisation humaines, `octopus.economy` reste seul ledger) ;
6. option non locale ou non sûre → `defer` ;
7. coût élevé sans aucune preuve économique liée → `reject` ;
8. sinon `acquire` si le net atteint le seuil, `defer` en deçà, `reject` si le coût dépasse la valeur.

**Les sunk costs sont enregistrés pour le compte rendu et exclus du calcul** : un coût déjà dépensé
ne favorise jamais une acquisition et ne relance jamais une acquisition rejetée.

### E. Validation

Une capacité n'est confirmée que par une source déterministe :

- exécuteur réellement enregistré (`actions.registered_executors`) ;
- outil réellement enregistré (`agents.runtime.TOOLS`) ;
- test contractuel enregistré (`register_validator`) renvoyant une source vérifiable ;
- sonde de ressource réussie (`resources.check`, opt-in `--probe`, jamais implicite).

Une déclaration de modèle est ignorée (#116) ; une déclaration de constructeur l'est aussi : un
builder qui annonce avoir réussi sans rien enregistrer produit `failed`, jamais `acquired`.

## Auto-construction, bornée

`register_builder(capability, build, …)` enregistre un constructeur **local, sûr et déterministe**.
Aucun constructeur n'est fourni en production : le registre est vide par défaut, rien n'est découvert.
La démonstration passe par un double local dans les tests (`dummy_echo` → exécuteur factice
`dummy:echo`, sonde contractuelle, inventaire recalculé, capacité `available`).

La tâche durable `capability.acquire` (`octopus/builtin_handlers.py`) applique ses portes dans
l'ordre : capacité déjà confirmée → stratégie économiquement invalidée → décision non exécutable →
frontière humaine → constructeur enregistré → construction **mémoïsée** → validation séparée →
preuve de validation et décision canonique persistées. Elle est rejouable : après un crash entre
construction et validation, la reprise ne reconstruit pas et confirme par la source réelle.

Ce chantier n'est pas un agent développeur général : il représente le plan, ses portes, sa
validation et ses doubles. Il ne construit aucun connecteur réel.

## Frontières humaines

Une demande humaine n'est ouverte que pour une vraie frontière, avec le vocabulaire existant
(`capability:<capacité>:<need>`). Elle ne l'est jamais pour un timeout, un DNS, un endpoint cassé,
du JSON invalide, ni pour installer une capacité qu'OCTOPUS peut construire et tester localement.
Un échec technique de construction termine en `failed` sans demande humaine.

L'étude d'acquisition lancée par `pursuit` **n'ouvre aucune demande humaine, ne met aucune tâche en
file et n'exécute rien** : elle calcule, persiste et rend compte. Démarrer une acquisition est un
acte explicite (CLI ou appelant), jamais une conséquence automatique d'un écart.

## Lecture et pilotage

```bash
python -m octopus capability state [CAPACITÉ …] [--allowed search,browse] [--business X] [--json]
python -m octopus capability gaps BUSINESS --objective N
python -m octopus capability validate CAPACITÉ [--probe]
python -m octopus capability acquire BUSINESS CAPACITÉ [--objective N] [--hypothesis N] [--by human]
```

`state` et `gaps` sont en lecture seule. `acquire` met en file une acquisition **déjà décidée** par
une étude persistée ; sans étude, il refuse plutôt que d'inventer. Le Workbench n'est pas modifié :
la vue compacte est dans la sortie de tâche (`capability_acquisition`) et l'étape
`pursuit.capability_acquisition`.

## Fichiers

- nouveau : `octopus/capability_acquisition.py` (mécanisme), `tests/test_capability_acquisition.py`,
  ce document ;
- modifiés : `octopus/supervisor.py` (étude bornée après l'annotation #116, contexte de raisonnement,
  vue compacte en sortie), `octopus/builtin_handlers.py` (handler `capability.acquire`),
  `octopus/__main__.py` (CLI `capability`) ;
- inchangés : `docs/FOUNDATION.md`, `octopus/strategy_separation.py`, `octopus/capabilities.py`,
  `agents/tool_registry.py`, `agents/web_guard.py`, `octopus/economy.py`, `octopus/actions.py`,
  `octopus/resources.py`, `octopus/journal.py` (aucune migration), PURSUIT_TOOLS, GUI.

## Validé par des tests exécutables, hors réseau

49 tests déterministes (`tests/test_capability_acquisition.py`) couvrent les 18 cas exigés :
capacité disponible (aucun écart), capacité absente (écart créé, stratégie conservée), exigences
mixtes (seul le vrai écart), panne temporaire (≠ absence), permission refusée (≠ acquisition
technique), frontière humaine conservée, déclaration de disponibilité ignorée, option peu coûteuse
et réutilisable préférée, acquisition coûteuse sans preuve rejetée, A meilleure avec écart retenue
face à B exécutable, acquisition réussie via double local, acquisition échouée, reprise après crash,
reprises répétées (idempotence), capacité acquise redevenue indisponible (inventaire recalculé),
invalidation économique (acquisition non poursuivie), sunk costs sans effet, aucune stratégie retenue
(aucun écart inventé). S'y ajoutent : CAPABLE ≠ AUTORISÉ, frontière argent/juridique non substituable
par un double local, option payante jamais auto-acquise, échec technique jamais transformé en demande
humaine, concurrence (6 planifications simultanées → une seule annotation), study inchangée à la
reprise, étude modifiée ajoutée sans réécrire la précédente, arrêt pendant la mission (pause et
annulation), intégration `pursuit` (aucun effet, aucune demande, aucune substitution), CLI.

Suites voisines exécutées : `test_strategy_separation.py`, `test_pursuit_learning.py`,
`test_pursuit_recovery.py`, `test_capabilities.py`, `test_resources.py`, `test_actions.py`,
`test_strategy.py`, `test_autonomous_loop.py`, `test_second_business_loop.py`, `test_tasks_worker.py`,
`test_journal.py`, `test_workbench_v2.py` — **367 passés, 0 échec**.

Suite complète applicable dans la sandbox (Debian, Python 3.11.2, hors réseau) :
**1699 passés, 98 ignorés, 8 échecs**. Les 8 échecs sont `tests/test_agnes.py` et
`tests/test_agnes_production.py` : `ffprobe/ffmpeg not available`. Ils sont **reproduits à
l'identique sur la base `bd8a09d` sans aucune modification de ce chantier** (vérifié par
`git stash`) : c'est une limite de l'environnement, pas une régression. Les 4 modules GUI
(`test_gui.py`, `test_gui_smoke.py`, `test_gui_intelligence.py`, `test_agnes_gui_missions.py`) ne
sont pas collectables faute de `tkinter` dans cette sandbox, comme documenté pour #114.

## Limites restantes

- Aucun connecteur réel : `phone_call`, `sms_send`, `whatsapp_message`, `linkedin_publish`,
  `payment_receive`, `youtube_publish`, `tiktok_publish` restent `missing`. Le mécanisme dit ce
  qu'ils exigent, ce qu'ils coûteraient et si cela vaut la peine ; il ne les construit pas.
- Les coûts financiers des définitions connues sont des **estimations** (`cost_nature: estimated`),
  séparées des faits ; une exigence inconnue donne un coût `None`, jamais zéro.
- Les pondérations de décision sont internes et déterministes ; elles ne mesurent aucune valeur
  commerciale réelle. Aucun paiement client n'est prouvé par ce chantier.
- La réutilisabilité est comptée dans la comparaison bornée de l'objectif courant (≤ 12 propositions),
  pas sur tout l'historique.
- L'auto-construction reste limitée à des constructeurs locaux enregistrés explicitement ; il n'y a
  ni génération de code, ni découverte MCP, ni installation privilégiée.
- SiteQuiVend reste exclu : ni preuve de marché, ni traction, ni avantage, ni justification d'une
  capacité.

## Confirmations

Aucun vrai run autonome lancé. Aucun achat. Aucun provider réel (gratuit ou payant) appelé.
Aucun compte externe créé. Aucune publication, aucun message, aucun appel téléphonique.
Aucune permission, `web_guard`, finance safety ou budget élargi. `docs/FOUNDATION.md` inchangé.
Aucun merge. PR en brouillon, basée exactement sur
`bd8a09de84f37b4bb4c086d0a24a1eedcd3c8f06`. Coût réel de cette session : 0 USD (transports LLM
simulés par les fixtures de test existantes).
