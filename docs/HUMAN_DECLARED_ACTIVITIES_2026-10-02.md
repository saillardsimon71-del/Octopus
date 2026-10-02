# Activités déclarées par l'humain — 2026-10-02

Base exacte : `dc0430cc67a058aaac1898688a0544b0de2db2ac` (#121).
Audit et design établis avant modification de production.

## Audit — réponses aux huit questions

1. `octopus.businesses.Business` représente déjà une activité. Son TOML sous
   `OCTOPUS_HOME/businesses/<id>/business.toml` est le registre canonique à réutiliser.
   Aucun besoin d'une table activities ni d'un registre supplémentaire.
2. `octopus` n'est pas créé comme déclaration TOML par pursuit : c'est son DEFAULT_BUSINESS.
   Les objets/tasks/runs créés sous cet identifiant constituent son historique système.
3. start/queue/pause/run/reconcile de pursuit codent cet identifiant en dur ; le corps de
   mission, la stratégie et l'économie utilisent déjà ctx.business.
4. Généraliser ces entrées, la CLI pursue et les boutons du Workbench ; fournir le terrain
   déclaré comme données au prompt. Conserver les paramètres par défaut pour l'historique.
5. Strategy, liens, preuves, hypothèses, expériences, reviews, tasks, demandes humaines,
   canaux, ledger, allowances, coûts LLM et schedules portent déjà business. Le worker
   exécute le business de la tâche ; les contrôles de liens refusent les traversées.
6. Les six vues V2 lisent déjà read_snapshot(selected_business). Mais les activités sans
   objets restent invisibles, octopus est caché, les boutons démarrent toujours octopus,
   et le changement de sélection laisse l'ancien snapshot visible jusqu'au rafraîchissement.
   Les coûts/actions de processus et certains inventaires doivent également expliciter leur scope.
7. Moteur, ToolRegistry, handlers moteur, executors installés, catalogue/provider et worker
   sont partagés. Les ressources sans business sont globales ; celles attribuées à un business
   doivent rester locales. resources_status et les faits des études de capacité lisaient tout.
   Les canaux/permissions/allowances ne sont pas partagés par cette mission.
8. Création locale d'un TOML atomique via businesses, puis rediscovery/adapter UI existant.
   WorkspaceRegistry est déjà une métadonnée GUI legacy : ne pas y créer un deuxième objet
   économique ni écrire la nouvelle activité dans workspaces.json.

## Design minimal

Activité UI = Business interne. Formulaire nom/description seulement, ID généré sûr et
stable après persistance, collision réservée par création exclusive du dossier. Texte sérialisé
comme chaînes TOML, handlers=[], aucun budget/allowance/permission/executor dérivé du texte.
Créer n'appelle ni journal économique, ni LLM, ni worker, ni navigateur.

Démarrer explicitement utilise le pursuit commun avec business sélectionné ; objectif
persistant réutilisable dans ce business, finalité économique dans le terrain déclaré.
La description borne le domaine, pas segment/offre/prix/canal/test ; elle n'accorde aucun droit.
Budget pursuit 0.20 USD, économique externe 0 EUR, outils et trois cycles inchangés.

Vue portefeuille : déclarations même non démarrées et activités historiques, état, dernière
activité, objectif, demandes humaines, coûts LLM et ledger séparé par devise/nature/catégorie.
La sélection efface immédiatement le snapshot précédent ; réponses asynchrones obsolètes
ignorées. Les callbacks de démarrage capturent le business au clic.

octopus reste l'entrée secondaire Discovery autonome/historique. #121 reste intact comme
primitive de recherche dans le domaine d'une activité et en découverte autonome.
Les six vues principales sont locales ou portefeuille ; les services moteur sont clairement
globaux. Les anciennes vues avancées restent legacy, sans promesse d'isolation supplémentaire.

## Compatibilité, preuves et limites

Pas de migration DB ni modification de Foundation, #119, providers, modèles ou finance safety.
La saisie est une donnée, pas une autorisation. La conformité sémantique du raisonnement au
terrain déclaré dépend du modèle ; les limites d'exécution restent déterministes dans le code.
Le parcours pursuit reste observation/analyse : cette mission n'ajoute pas de canal d'expérience
commerciale. Le premier euro exigera les capacités/autorisations réelles correspondantes.

La limite `budget_daily_usd` reste une borne de consommation LLM historique, pas une
allowance de dépense externe ; elle n'est ni saisie ni accordée par le formulaire. Le cap
pursuit reste 0.20 USD par démarrage borné, avec la comptabilité de lignée existante pendant
les cycles/reprises techniques. Plusieurs activités ne constituent pas un plafond de
portefeuille nouvellement partagé : aucun agrégateur de budgets n'a été ajouté.

Les observations Navigateur sont isolées lorsqu'elles sont liées à une tâche/business.
Le moteur navigateur et les profils techniques restent partagés ; pursuit conserve la
frontière public-only. Les vues avancées legacy ne sont pas devenues des silos supplémentaires.
L'atomicité du TOML couvre les lecteurs et collisions ; ce n'est pas une transaction
distribuée ni une garantie contre un acteur local privilégié modifiant simultanément le disque.

## Validation reproductible — aucun provider réel

Les nouveaux tests ont d'abord échoué sur la base exacte (19 cas initiaux, création absente),
avant toute modification de production. Le transport LLM et les acquisitions Web sont simulés ;
les tests intégrés conservent le vrai supervisor, worker, runtime et gateway. Les assertions sur
des stratégies simulées vérifient la transmission du terrain et les contrats, pas l'intelligence
ou la qualité stratégique d'un modèle réel.

| Scénario | Preuve exécutée |
| --- | --- |
| A — création | TOML valide, rediscovery, adapter existant et redémarrage réel du Workbench. |
| B — ID | Accents, espaces, slash, `..`, emoji et noms réservés ; collision forcée et huit créations concurrentes ; refus d'un registre symlink hors DataRoot. |
| C — aucun auto-run | Aucun transport, tâche, run, objectif, canal, allowance ou ledger créé ; formulaire Tk sans lancement de subprocess. |
| D — démarrage | Objectif créé/réutilisé dans le business choisi ; callback lancé après changement de sélection conserve l'activité cliquée. |
| E — isolation | Historique API-cost d'A et d'octopus absent de tous les appels de B, agents inclus ; objets d'A inchangés. |
| F — stratégie | Terrain transmis, moyens libres ; sortie simulée de stratégie conservée sans template de marché imposé. |
| G — capacité | Email absent : rang économique 1 conservé, exécution non autorisée ; aucune acquisition exécutée. |
| H — ledger | Encaissements simulés A/B, apport distinct, dépense et coûts LLM locaux ; agrégation portefeuille par devise sans confondre apports et clients. |
| I — recovery | Interruption de B avant synthèse, reprise sur le même DataRoot temporaire ; deux acquisitions réutilisées, collecte non répétée, tâche A inchangée. |
| J — switch | Six vues Tk réelles et tests headless ; données d'A effacées avant affichage de B, réponses asynchrones périmées ignorées. |
| K — ReadOnly | Backend création/start/pause/run refusés ; boutons natifs désactivés ; consultation sans création de journal. |
| L — historique | octopus lisible comme entrée secondaire, non injecté dans B. |
| M — discovery | 15 tests #121 inchangés fonctionnellement ; discovery/validation/recovery et budgets restent opérationnels. |
| N — permissions | Cap 0.20, public-only et outils inchangés, aucun canal/allowance/action externe créé. |
| O — saisie | Description hostile conservée comme texte ; aucun handler, chemin, permission, budget ou executor issu de la saisie ; caractères de contrôle et Unicode relus. |

31 nouveaux cas backend/headless et 3 nouveaux cas Tk. Un ancien test de visibilité change
d'attendu : octopus est maintenant visible comme entrée secondaire, au lieu d'être caché.
Le fixture de capture de prompt renseigne désormais le business historique, exigé par la
vérification anti-reprise croisée ; aucune mesure #121 n'est relâchée.

| Validation | Résultat |
| --- | --- |
| Ciblée : registre, Workbench, pursuit, stratégie, économie, ledger, capacités, tâches/worker, gateway, finance, recovery, runtime et stack | **835 passed** en 27.74 s. |
| Base exacte, suite complète sans affichage | **1908 passed, 97 skipped** en 65.81 s. |
| Base exacte, suite complète avec Tk natif | **1918 passed, 96 skipped** en 83.99 s. |
| Patch, suite complète avec Tk natif | **1952 passed, 96 skipped** en 88.00 s. |

Les 96 tests ignorés ont exactement les mêmes identifiants sur les deux suites comparables.
Aucun nouvel échec. L'affichage Tk isolé a vérifié le formulaire réel, le restart, les six vues,
ReadOnly, les rafraîchissements, le focus et les tailles de fenêtre existantes.
Il a révélé puis validé la correction d'un accès au champ Missions avant sa création pendant
le chargement d'une nouvelle sélection. Les rapports pytest/JUnit sont conservés dans le
workspace de validation ; la suite ciblée utilise les fichiers existants concernés et
`tests/test_human_declared_activities.py`, la suite Tk active également `tests/test_gui_smoke.py`.

## Périmètre livré et décision

Production : `octopus/businesses.py`, `octopus/supervisor.py`, `octopus/__main__.py`,
`octopus/resources.py`, `octopus/capability_acquisition.py`, `agents/runtime.py`,
`agents/gui/workspaces.py`, `agents/gui/workbench_v2_data.py`, `agents/gui/workbench_v2.py`.
Les changements de ressources/capacités filtrent la visibilité, sans ajouter une permission
ni changer l'autorité des executors. Foundation, strategy_separation, gateway/#119,
catalogue de modèles, ledger et finance safety ne sont pas modifiés.

Une activité humaine peut être ajoutée, retrouvée après restart et démarrée explicitement
dans sa propre continuité. La validation n'est pas une preuve de revenus, de qualité LLM
ou de livraison commerciale. Prochaine observation utile après revue humaine : le premier
choix stratégique d'une activité déclarée sur un DataRoot autorisé, sous les limites existantes.

Aucun provider réel, run économique réel, dépense, contact, compte, acquisition, permission
élargie ou merge n'a été exécuté. Seules les créations dans des DataRoots temporaires de tests
et la publication Git/PR draft expressément demandée font partie de cette intervention.
