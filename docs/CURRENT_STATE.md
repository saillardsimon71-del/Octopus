# Current-state notice — 2026-09-25

The detailed report below is an earlier intervention snapshot and contains historical priority language and an older baseline SHA.
For the current repository state, the protected reference is `main@ae4d98dc9692aa10ba15051381a36809e25377df` and the prepared maintenance branch is `prep/astra-local-orchestration`, based on that main with zero commits behind at preparation time.

The human operator has explicitly authorized the bounded 2026-09-25 maintenance window documented in `docs/migrations/CODEX_START_2026-09-25.md`: legacy-video removal, standalone Agnes replacement, then selective Hermes P0 replacement work. Statements below such as “the next work is a client experiment”, “freeze video/MCP”, or the old baseline SHA remain historical context and must not be used to override that current directive.

Economic truth rules, evidence semantics, permission boundaries and market-first doctrine remain unchanged.

---

**AVANT CETTE INTERVENTION, OCTOPUS ÉTAIT :**
un système de contrôle et d'exécution riche, issu de Podalux, avec de vraies protections et
une boucle stratégique/financière déjà présente, mais des priorités documentaires contradictoires
et aucune preuve commerciale disponible dans ce checkout.

**APRÈS CETTE INTERVENTION, OCTOPUS EST :**
un atelier économique supervisé au chemin explicite : besoin, expérience, travail, livraison,
encaissement/retour client distincts, coûts/temps, décision. Il n'est **pas** devenu une activité
rentable prouvée. Le prochain travail est une expérience client, pas une phase d'architecture.

## A. Thèse

Le dépôt n'avait pas besoin d'un nouveau moteur économique, mais d'une exploitation cohérente
de ses preuves et de son ledger. J'ai retenu le monolithe existant, maintenable sans ajouter
une couche « core » ni déplacer les modules. Market first n'implique pas de croire aveuglément
le premier paiement : livraison, acceptation, utilisation et économie doivent rester distinctes.
L'humain est un opérateur mesuré, pas un échec d'autonomie. Le self-development devient un outil
latéral déclenché par une observation. La complexité doit payer par un résultat ou une protection
actuels ; sinon elle est gelée. Le pilote e-commerce est une expérience réfutable, pas un business
définitif ni une raison de construire un moteur spécialisé.

## B. Réalité observée

- Main local, `origin/main` et main distant (`git ls-remote`) concordaient sur
  `d2279f628703cb88ca1bf78fcb591389dcc9f764` ; arbre initial propre.
- `CURRENT_STATE`/`HANDOFF_WORK` citaient encore `cd8a3b3`, `CODEX_START` une PR GPU Draft #2,
  tandis que l'historique avait déjà intégré actions HTTP/SMTP, product tickets et acceptance.
- API GitHub consultée en lecture : aucune PR ouverte, aucun check-run retourné pour ce HEAD.
  Les workflows existent ; aucune conclusion de CI distante verte n'en a été inventée.
- L'historique confirme la suppression antérieure de `core/` et `businesses/short_video/`
  (33 fichiers / 616 lignes selon commit/docs). Ce n'est **pas** une suppression de cette session.
- Les branches canary/capabilities/cleanup servent à comprendre l'historique, pas à remplacer main.
- Aucun journal SQLite utilisateur ni pièce commerciale trouvé dans /app. Cela signifie
  **absence de preuve disponible**, pas preuve qu'aucun client n'existe ailleurs.
- `strategy` stocke déjà objectifs, hypothèses, expériences, preuves, décisions, liens ;
  `economy` contient canaux, cash, imports CSV, allowances, verdicts et réinvestissement.
- Les missions ORBIT relient déjà les tâches à la stratégie et marquent leur rapport `inferred`.
  Le problème n'était donc pas une égalité générale `task done = argent` dans le code.
  Il manquait une lecture unifiée des résultats client et du temps humain, et la documentation
  donnait trop d'autorité aux succès de transport/compute et aux compteurs d'ingénierie.

### Architecture exécutée, pas seulement noms de modules

| Entrée / sous-système | Chemin constaté et décision |
|---|---|
| CLI économique | `__main__ → strategy_cli → strategy/economy → journal SQLite` ; sans worker/LLM |
| Tâches | `worker.load_handlers → businesses.handler_modules`, baux/reprise `tasks`, runs `journal` |
| Agents | `agents/task_handlers → runtime.run_mission/run_agent`, outils et gateway LLM ; cycle Podalux séparé encore consommé |
| Actions | `actions.propose → canal/autorisation/coût → browser_form ou SMTP configuré → evidence` |
| État historique | `agents/db.py` conserve messages/mémoire/verrous Podalux, distinct du journal durable OCTOPUS |
| Ressources/connecteurs | inventaire TOML, sondes et accès ; des consommateurs runtime réels, pas à supprimer |
| Capabilities | module typé testé et surface canary ; aucun consommateur runtime direct identifié |
| GUI | `run_gui → agents.gui`, CustomTkinter conservé ; pas de nouvelle UI |
| Vidéo/compute | Studio/WanGP, VideoService/renderers, broker/breaker/watchdog ; consommateurs actifs conservés |
| Développement | entrée explicite/night-shift → dev_worker/Kilo ou déclaratif → clone/sandbox/tests/gate → promotion humaine |
| Acceptance | contrat hashé, probe Tk important mais dépendant du candidat ; revue réelle toujours nécessaire |
| CI | workflows présents, dont suite Python complète ; vérification locale réelle détaillée ci-dessous |

**Hypothèse control plane >> economic plane : confirmée pour la priorité et les preuves disponibles,
pas comme absence de code économique.** La réutilisation était préférable à une nouvelle couche.

## C. Kill map

| Décision | Composants importants |
|---|---|
| KEEP | tasks/worker/journal, strategy, economy/ledger, actions, budgets, secrets/sandbox/promotion |
| SIMPLIFY | chemin CLI, rapport outcomes, documents canoniques et conventions de mesure |
| FREEZE | extension GUI/vidéo/GPU, nouveaux canaris, capabilities sans consommateur runtime |
| DEPRECATE | développement chargé par défaut ; autonomie/compteurs techniques comme preuve de progrès |
| REMOVE | imports média inutiles sur la CLI économique ; roadmap GPU obligatoire avant client ; pourcentages de progression non probants |
| LATER | automatisation de prospection et généralisation après répétition d'un travail acheté/utilisé |

## D. Golden path

```text
humain : besoin + limites
  → strategy : objectif / hypothèse / expérience bornée
  → travail manuel ou tasks/worker
  → action autorisée / livraison avec preuve
  → ledger : encaissement et coûts  +  evidence : client et minutes humaines
  → economy outcome : faits, provenance, inconnues
  → décision humaine : continuer / corriger / arrêter
       └ si obstacle mesuré → development.task → revue → même mission → nouvelle mesure
```

Protocole unique : [HANDOFF_WORK.md](HANDOFF_WORK.md). Aucune dépendance à quinze sous-systèmes
pour tenir le premier pilote ; les opérations manuelles restent légitimes et visibles.

## E. Changements : problème → décision → bénéfice → risque résiduel

1. **Faits dispersés** → preuves/ledger/liens existent → fonction `experiment_outcomes` et commande
   `economy outcome`, intégrées aux vues de résultats → lecture séparée travail/livraison/client/cash,
   temps par phase et références du ledger → saisie manuelle, coût complet et vérité des sources à revoir.
2. **Absence interprétée comme résultat** → échéance sans données `refutes`, cash unverified donnant
   zéro, budget zéro immédiatement consommé → `inconclusive`, valeur inconnue et dépense positive
   requise pour épuisement → pas de conclusion marché inventée → `supports` reste propre à la métrique.
3. **Conventions de preuve fragiles** → nombres non finis et addition de constats booléens possibles
   → validations et dernier constat observé, retrait existant réutilisé → minutes et états explicites
   → pas de dédoublonnage automatique de toute saisie manuelle ; sources/IDs à vérifier.
4. **Atelier au centre** → dev_worker dans les handlers par défaut → retiré du défaut, imports vidéo
   retardés dans la CLI → maintenance explicite, commande économique plus légère → un import explicite
   conserve l'enregistrement ; pas de désactivation rétroactive d'un processus déjà chargé.
5. **Faits candidat/contrôleur fusionnés** → probe pouvait écraser tests/Git/empreinte → refus de tout
   namespace autre que runtime/ui → intégrité des faits contrôleur → probe Tk toujours non indépendant.
6. **Création de canal plus permissive que mise à jour** → `act` pouvait être demandé à la création
   par un agent → même contrôle humain que lors d'une mise à jour → politique cohérente → API locale,
   chaîne `human` n'est pas une authentification ou un durcissement complet de la frontière hôte.
7. **Documents contradictoires** → vidéo/GPU déclarés passage obligé malgré phase économique ouverte
   → constitution, vision, gates, backlog et handoffs recadrés ; historiques signalés → une direction
   maintenable → les runbooks spécialisés conservés ne valent pas validations live.
8. **Callback GUI tardif** → suite réelle : Doctor écrivait dans un widget détruit après navigation,
   et Orca avait le même défaut → deux guards `winfo_exists`, sans refonte → le rafraîchissement
   continue après réponse tardive → tests déterministes reproduits rouges avant correction.

## F. Suppressions

Aucun fichier runtime supprimé. Une inscription `octopus.dev_worker` retirée du chargement par
défaut ; import groupé de cinq modules média déplacé hors de l'entrée économique ; import argparse
inutilisé retiré. Les suppressions de lignes les plus importantes sont documentaires, pas une
prétendue élimination massive de code. Chiffrage final en N.

## G. Ajouts justifiés

- Une projection dans `economy.py`, pas une nouvelle abstraction persistante. Les totaux cash et
  le statut de tâche existants ne pouvaient répondre à livraison/client/minutes ; les preuves et
  liens existants le peuvent, avec une agrégation explicite et quelques validations.
- Conventions de métriques (`delivery`, `customer_acceptance`, `customer_use`, `human_minutes:phase`)
  et catégories de ledger : pas d'entités prospect/order/payment nouvelles, pas de migration.
- Tests dans les fichiers existants. Aucun package/dependency produit supplémentaire.
- Les mémos et rapports générés par l'environnement de travail ne font pas partie du produit :
  ils ont été retirés avant revue. Les logs/XML de test restent locaux et ignorés par Git.

## H. Ce que j'ai refusé de construire

Moteur e-commerce, scraper générique, Shopify, CRM, campagne, nouveau rôle/agent, Model Lab,
capability acquisition, MCP discovery, Web Control Plane, refonte GUI, provider GPU, nouvelle DB,
Bottleneck Knowledge Graph, système de scoring de complexité et oracle visuel universel.

## I. Self-development

Disponible mais latéral. Preuve → lien `motivates` vers la tâche ; expérience → lien `improves`.
`improves` ne compte pas comme exécution du lot (`executed_by`). Aucun champ obligatoire imposé
aux anciennes tâches. Le goal documente observation/baseline/périmètre et mesure après retour sur
la même mission. Tests/gate/promotion humaine inchangés dans leur principe, pas d'auto-merge.

## J. Economic plane — ce qui est réellement utilisable demain

Créer le pilote, tenir prospect/offre/accord sous forme de preuves textuelles, produire manuellement,
consigner livraison/retour client, encaissements vérifiés, coûts et minutes ; lire un rapport,
évaluer une métrique et persister une décision. Le rapport n'effectue aucun envoi ni paiement.
La contribution n'est que le cash classé enregistré, pas une marge complète ni un calcul HT/TTC.
Pas de clients/prospects/revenus fictifs injectés dans un journal de production.

## K. Trust kernel

Droits d'agir ; dépenses/plafonds ; effets externes ; état durable/propriété d'exécution ; sémantique
des preuves ; modification/promotion/secrets. Responsabilités dans les modules existants, pas
nouveau package. Leur fermeture dépend de la DB, de l'hôte, du catalogue gouverné, des imports,
de l'image et de la CI. Voir [EVIDENCE_ACCEPTANCE.md](EVIDENCE_ACCEPTANCE.md) pour les limites.

## L. Tests et preuves

- Baseline : `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_economy.py
  tests/test_strategy.py tests/test_actions.py tests/test_acceptance.py tests/test_tasks_worker.py
  tests/test_dev_worker.py tests/test_businesses.py` → 228 tests passés.
- Premier diff : mêmes sept suites + `tests/test_economy_act_cli.py`, avec `-o addopts='' -q`
  → 254 passés, 38.81 s.
- Revue fonctionnelle indépendante : suite hors `test_gui.py`, un test encore attaché à l'ancien
  verdict sans mesure ; correction intentionnelle de son attente vers `inconclusive`, documentée.
- Agent de test : 195 ciblés passés ; 844 non-GUI passés, 4 skips ; suite complète bloquée par
  dépendance native Tk manquante. Aucun test retiré pour masquer ce problème.
- Installation environnement des dépendances déjà prévues : CustomTkinter, libtk8.6, ffmpeg, xauth.
  Première suite entière sous Xvfb : 859 passés, 1 module navigateur ignoré (Playwright absent), 114.04 s.
- Ajout vérifié du roundtrip CLI multi-processus, preuves/ledger/liens/reprise/rapport sans écriture :
  `python -m pytest -o addopts='' -q tests/test_economy_act_cli.py tests/test_agent_economy_tools.py
  tests/test_economy.py` → 37 passés, 8.70 s.
- Relecture finale : une preuve `computed` sans valeur doit pouvoir conserver l'inconnue pour les
  métriques réservées ; correction et quatre régressions testées, sans inventer un zéro.
- Navigateur Chromium/Playwright installés (dépendance déjà déclarée). Première suite sans skip :
  870 passés, 1 échec GUI intermittent. Reproduction déterministe Doctor/Orca :
  `xvfb-run -a python -m pytest -o addopts='' -q tests/test_gui_smoke.py -k late_background`
  → **2 échecs avant correction**, mêmes widgets détruits. Deux guards locaux corrigent les callbacks.
- Suites suivantes : 873 passés, un avertissement de finalisation Tk sur un thread non-GUI.
  Une tentative de nettoyage dans le test n'a pas éliminé l'avertissement : retirée, aucun filtre ajouté.

La dernière suite avec navigateur est vérifiée sans exclusion de tests :

```bash
xvfb-run -a python -m pytest -o addopts='' -q -rs tests/ --junitxml=/app/test_reports/pytest/pytest_results_final.xml
```

Résultat final : **873 passés, 0 échec, 0 ignoré, 1 avertissement Tk**, 115.62 s (XML vérifié).
Les traces XML/logs sont locales et non versionnées. Après publication de la branche, les workflows
GitHub `Compute finance safety` et `video-foundation` ont également terminé avec succès.
Aucun canary commercial, fournisseur LLM/GPU, paiement ou envoi externe réel n'a été exécuté.

## M. Git

Baseline canonique de cette intervention : `d2279f628703cb88ca1bf78fcb591389dcc9f764`.
Le travail a été publié sur `recalibrate/market-first-octopus` et ouvert en Pull Request #59
vers `main`. Les artefacts de session/environnement générés lors de la sauvegarde ont été retirés
avant revue. La branche reste séparée de `main` tant que la revue humaine n'est pas terminée.
La CI GitHub du HEAD revu est verte sur `Compute finance safety` et `video-foundation`.
La fusion n'est pas une preuve économique : elle ne doit intervenir qu'après revue du diff final.

## N. Complexité — faits, pas score

Périmètre : comparaison à `d2279f6`, hors logs/XML générés et métadonnées `.emergent/`.

| Périmètre | Fichiers | Lignes ajoutées | Lignes supprimées |
|---|---:|---:|---:|
| Runtime Python | 8 modifiés | 133 | 24 |
| Tests Python | 7 modifiés | 269 | 4 |
| Documentation canonique/historique | 15 modifiés | 840 | 1511 |
| Exclusion des logs/XML générés | 1 modifié | 2 | 0 |
| Total des fichiers déjà suivis | 31 modifiés | 1244 | 1539 |
Fichiers applicatifs ajoutés : **0**. Les artefacts de session/environnement ont été retirés avant revue.
Le diff final de la PR touche **31 fichiers** avec **1 246 ajouts / 1 539 suppressions** (net **−293 lignes**).
Concepts actifs retirés/dépréciés : self-development par défaut, infrastructure avant client,
pourcentages de progression et métrique technique prise seule comme preuve économique.
Zéro nouvelle table, runtime, agent, service, base ou intégration produit.

## O. Limites volontairement conservées

- Sources déclarées à vérifier, preuve Tk non indépendante, protection de chemins pas égale à
  fermeture complète des imports ; revue humaine, pas nouvel oracle universel.
- Coûts/temps incomplets possibles, contribution cash partielle, coût nul à justifier humainement,
  catégories historiques non reclassées automatiquement, saisie cash/preuves non idempotente.
- Ancienne DB Podalux et chemins vidéo multiples encore consommés ; supprimer serait plus risqué
  que les geler. Catalogue de modèles/politique encore couplé et donc gouverné.
- Le framework d'actions payantes personnalisé libère sur exception une autorisation ; aucune
  nouvelle utilisation payante avant résolution du cas ambigu. Le pilote n'en a pas besoin.
- Coûts des appels LLM estimés encore additionnés au budget USD dans l'évaluateur historique :
  ne pas les présenter comme facturation indépendante ni les doubler avec un coût déjà réglé.
- Projections répétées par expérience acceptables pour le pilote ; pas de cache/optimisation spéculative.
- Avertissement `Variable.__del__` de Tk multithread dans la suite complète, non masqué. Les parcours
  GUI et les régressions Doctor/Orca passent ; aucune affirmation de disparition de cet avertissement.

## P. Première expérience

Un lot de 20 références e-commerce françaises, quelques attributs factuels sourcés, prix hypothèse
~99 € HT, cinq contacts supervisés maximum, sept jours, quatre heures humaines. Une seule offre,
pas un business définitif. Observer commandes/refus, livraison, paiement, acceptation, usage,
minutes et coûts. Si aucun accord, pas de pipeline automatique à construire.

## Q. Next bottleneck rule

Après le pilote : relire les pièces et la décision. Si offre non désirée, arrêter/changer l'offre.
Si données absentes, mieux mesurer. Si travail acheté/utilisé mais coûteux, choisir **une** phase
dominante observée ; relier sa preuve à une amélioration bornée, puis comparer avant/après à
volume et qualité équivalents. Aucun gain ni répétabilité proclamé sans nouvelle observation.

## R. Do not build

**LA PROCHAINE CHOSE QU'IL NE FAUT SURTOUT PAS CONSTRUIRE EST :**
**un moteur générique d'enrichissement e-commerce.**