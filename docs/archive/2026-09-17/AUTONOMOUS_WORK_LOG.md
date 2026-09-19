# Autonomous work log

Session: 2026-09-17, branche `feat/autonomous-business-foundation` (HEAD de depart `7db3def`).
Regles tenues : rien n'est pousse, aucun commit, aucune action externe, aucune donnee utilisateur supprimee,
aucun appel LLM reel. La base reelle `data/octopus.db` est restee en version 4 (verifie en lecture seule) :
tous les essais CLI ont utilise une base temporaire (`OCTOPUS_HOME`/`OCTOPUS_DB` dans le scratchpad).

## File de taches

| ID | Objectif | Prio | Etat |
|---|---|---|---|
| T0 | Etat initial, baseline de tests sur worktree HEAD propre | B | VERIFIED |
| T1 | `agents.gui.strategy`/`workspaces` importables sans customtkinter | B | VERIFIED |
| T2 | Environnement prevu par le projet (`.venv` + requirements-local) | B | VERIFIED |
| T3 | Tests casses a HEAD : fuite d'environnement (registre Windows, renderer), tests obsoletes (H3 cloud-only), test executor | B | VERIFIED |
| T4 | Bug GUI : page "Systeme" introuvable (`_slug`) | B | VERIFIED |
| T5 | Blocage infini du garde WebSocket (`agents/browser.py`) | A/B | VERIFIED |
| T6 | Persistance strategique Phase 1 (migration v5, `octopus/strategy.py`) | C | VERIFIED |
| T7 | Evidence/provenance Phase 2 (`strategy_evidence`) | C | VERIFIED |
| T8 | Identite business : workspaces alignes sur les ids moteur | D | VERIFIED (sans smoke GUI) |
| T9 | Propagation du business : runs mission/agent et couts LLM | D | VERIFIED |
| T10 | Mission ORBIT generique `orbit.mission` avec contexte strategique | C/D | VERIFIED (LLM simule) |
| T11 | Revues planifiees via la file existante (`strategy.review`, sans LLM) | C | VERIFIED |
| T12 | CLI `python -m octopus strategy ...` | C | VERIFIED (base temporaire) |
| T13 | Test d'acceptation deuxieme business | C/D | VERIFIED |
| T14 | `.gitignore` (.venv) et workflow CI etendu (local, non pousse) | B | DONE, CI non observee |
| T15 | Reproduction des dependances du job CI "contract-and-worker" | B | VERIFIED |
| T16 | Durcissement runtime : plan ORBIT borne, `render_offer` reserve a Podalux, identite d'agent par business | E | VERIFIED |
| T17 | Audit couts LLM (mesures reelles, profils existants) | F | DONE (audit, pas de changement de routage) |
| T18 | QC ffmpeg en une passe (`tools/qc_metrics.py`) | G | VERIFIED (mesure locale) |
| T19 | Intelligence GUI : etat strategique persiste (lecture seule) | Phase 7 | VERIFIED (smoke isole) |
| T20 | Workbench impossible a ouvrir : crash premier lancement + melange pack/grid (3 pages + flux d'activite) | B | VERIFIED |
| T21 | Rafraichissement GUI arrete apres la page Missions | B | VERIFIED |
| T22 | Frontiere connecteurs (CRM, finance, social, messagerie, publication) sans donnees simulees | Phase 5 | VERIFIED |
| T23 | Preuve `model_inference` enregistree automatiquement a la fin d'une mission strategique | Phase 3 | VERIFIED |
| T24 | Documentation (CLAUDE.md, GUI.md, statut roadmap) et rapport de session | - | DONE |
| T25 | Formulaires GUI de creation/evolution des objets strategiques | Phase 7 | DEFERRED (clics reels non verifiables en autonomie) |
| T26 | Aide a la decision de reinvestissement | Phase 6 | BLOCKED (aucune source financiere reelle) |
| T27 | Avertissement pytest `Variable.__del__` apres le smoke GUI | - | DEFERRED (gc.collect essaye, sans effet, annule) |

## Entrees

### T0 - Etat initial
- Arbre propre, HEAD = branche GitHub la plus avancee. Pas de `.venv` alors que `setup-local.ps1` en cree une.
- Le `pytest` complet de l'audit precedent ne se terminait jamais : `test_account_page_cannot_open_public_websocket` bloque indefiniment (voir T5).
- Worktree HEAD propre (`git worktree add --detach <scratchpad>/head HEAD`) + `.venv` : 12 echecs pre-existants :
  `test_cycle_logic` (3), `test_stop_memory` (2), `test_doctor` (1), `test_gateway` (1), `test_gui` (1), `test_media_studio` (1), `test_media_wangp` (2), `test_video_executor` (1).
- Processus WanGP de l'utilisateur actifs (`wangp_bridge.py`), une tache `studio` en `running` dans la base reelle : non touches.

### T1 - Import GUI paresseux
- `agents/gui/__init__.py` chargeait `intelligence` (donc Tk) pour tout sous-module. Remplace par `__getattr__` (PEP 562), API publique identique.
- Verifie : `agents.gui.strategy` importable sans customtkinter ; `tests/test_gui_intelligence.py` passe.

### T2 - Environnement
- `py -3.11 -m venv .venv` + `pip install -r requirements-local.txt` (equivalent `setup-local.ps1` sans `setx` ni Chromium).
- Limite : Playwright 1.63 de `.venv` n'a pas son Chromium ; les tests navigateur y sont `skipped`. Ils ont ete executes avec le Python global (Playwright 1.60, Chromium 1223 present).

### T3 - Tests casses a HEAD (causes verifiees une par une)
- `test_cycle_logic` x3, `test_stop_memory` x2 : `agents/cycle.py` rend en cloud par defaut depuis `1f5ebc3`, les tests supposaient le rendu local et levaient `RunPod non configure`. Correctif : `tests/conftest.py` fixe `PODALUX_VIDEO_RENDERER=local` (un test ne vise jamais RunPod sans le demander). Code inchange.
- `test_gateway::test_legacy_missing_key_fails_before_any_call` : `octopus.llm.secret()` et `agents.config.api_key()` relisent le registre Windows ; la vraie cle de l'utilisateur fuyait dans le test. Correctif : `conftest` remplace `winreg` par un stub qui leve `OSError`. Effet secondaire utile : aucun test ne peut plus lire les secrets de la machine.
- `test_doctor::test_doctor_cloud_first...` : heritait de `OCTOPUS_PROFILE=legacy` du conftest, donc DeepSeek bloquant. Correctif : le test retire `OCTOPUS_PROFILE` (installation reelle).
- `test_media_wangp` x2, `test_media_studio` : tests anterieurs a la politique H3 cloud-only (`2968d71`, `7d47e52`). Mis a jour : H3 jamais auto-selectionne localement, libelle "CLOUD", garde-fou materiel teste sur un modele local (`t2v_1.3B`).
- `test_video_executor` : `"make_audio_chatterbox_full.py" in cmd` testait l'appartenance a la liste alors que la commande contient `tools/make_audio_chatterbox_full.py` depuis le premier commit ; le test ne pouvait pas passer. Correctif : recherche dans la commande jointe.

### T4 - Bug GUI "Systeme"
- `PodaluxWorkbench._slug("Système")` renvoyait `système` ; `_show_page` cherche `_page_système` alors que la methode est `_page_systeme` : ouvrir la page Systeme levait `AttributeError`. Correctif : remplacement de `è`. Test existant `test_navigation_labels_and_slugs` passe.
- Non verifie : clic reel dans la GUI.

### T5 - Blocage du garde WebSocket
- Diagnostic (faulthandler) : `websocket.close()` synchrone appele dans le handler `route_web_socket` attend la boucle Playwright dans laquelle il s'execute deja : blocage definitif du navigateur des qu'une page tente un WebSocket refuse. Le garde ne laissait rien passer, mais la session d'agent se figeait.
- Correctif `agents/browser.py` : fermeture planifiee sur la boucle en cours (`impl.close(code=1008)`), repli sur l'API publique si l'objet interne n'existe pas. Utilise un attribut prive de Playwright (`_impl_obj`) : a reverifier a chaque mise a jour majeure de Playwright.
- Verifie : `tests/test_browser_integration.py` 7 passes en 9-10 s (Python global) au lieu d'un blocage ; le WebSocket est ferme avec le code 1008 et journalise dans `blocked`.
- Non verifie : chemin WebSocket autorise (`connect_to_server`) en navigation reelle.

### T6/T7 - Persistance strategique et preuves
- Tables dans `octopus.db` (migration additive v5) : `strategy_objectives`, `strategy_hypotheses`, `strategy_experiments`, `strategy_decisions`, `strategy_reviews`, `strategy_evidence`, `strategy_links`.
- Decisions : ids entiers ; business = meme chaine que `tasks.business` ; `all` et vide refuses ; parents et liens jamais inter-business (verification aussi pour les liens vers `tasks`) ; transitions verifiees ; experience `completed` exige `outcome` ; seule `actor="human"` approuve une decision ; chaque ecriture emet `strategy.*` dans `events` dans la meme transaction ; aucune colonne de metrique.
- Preuves : nature obligatoire parmi `observed_fact`, `user_provided`, `model_inference`, `recommendation` ; un fait observe exige `source_ref` + `captured_at` ; `user_provided` reserve a `created_by="human"` ; preuve immuable (retrait uniquement).
- La persistance strategique ne depend pas du coupe-circuit `OCTOPUS=off`.

### T8 - Identite business
- `WorkspaceRegistry._derive_from_jobs` creait un business par prefixe d'offre (`cash`) alors que le moteur, les taches et le journal utilisent `podalux`. Desormais : activites declarees dans `businesses/*/business.toml` + offres de `jobs/` rattachees a `podalux`.
- Fichier `agents/data/workspaces.json` existant : conserve tel quel (pas de migration automatique) ; les activites moteur manquantes sont ajoutees en memoire. Sur cette machine le fichier n'existe pas.
- Non verifie : GUI reelle.

### T9 - Propagation du business
- Avant : `run_agent`/`run_mission` decores `@with_run("podalux", ...)` et `deepseek._complete` ecrivait `business="podalux"` : une tache `veille` executant ORBIT etait journalisee et facturee a Podalux (test ecrit, echoue sur HEAD).
- Apres : parametre `business` optionnel ; sinon business du run englobant (tache) ; sinon `podalux`. Couts LLM : business du run courant.
- `agents.run mission --business X` ; la GUI (Intelligence et barre de mission) transmet le business selectionne (pas pour `all`).

### T10/T11/T12/T13 - Mission, revues, CLI, deuxieme business
- `orbit.mission` (dans `agents/task_handlers.py`) : valide la chaine objectif/hypothese/experience du business de la tache, injecte un contexte deterministe (sans appel LLM supplementaire), relie les objets a la tache, marque le rapport `rapport_nature="model_inference"`. `podalux.mission` inchange.
- `strategy.review` (handler moteur) : revue calculee sans LLM, relie aux objectifs actifs, signale explicitement les donnees CRM/finance/social comme non connectees. Ponctuelle via `strategy.schedule_review` (tache differee + cle d'idempotence), recurrente via `python -m octopus schedule <business> strategy.review --every 604800`.
- CLI `python -m octopus strategy add|list|show|move|link|mission|review|snapshot` : essai reel en sous-processus sur base temporaire, mission annulee avant le worker (aucun appel LLM), revue executee par `worker --once`.
- `tests/test_second_business_loop.py` : business `atelier_test` parcourt workspace -> objectif -> hypothese -> experience -> `orbit.mission` via worker -> preuve -> resultat -> decision approuvee -> revue planifiee executee, sans chemin Podalux, sans fuite.

### T15 - Job CI "contract-and-worker"
- venv jetable avec exactement les dependances du job (`runpod`, `boto3`, `numpy`, `Pillow`, `pytest`) : a HEAD, seul `test_video_executor` echoue (bug de test, T3). Sur l'arbre de travail : 36 passes.
- Non verifie : job "local-browser-and-control-plane" sous Linux, et aucun run CI n'a ete observe (rien n'est pousse).

### T16 - Durcissement du runtime
- `_run_mission` : le plan ORBIT n'etait pas borne (le prompt demande 2 a 5 sous-taches, chaque sous-tache = une boucle ReAct payante potentielle). Borne a `MAX_PLAN_TASKS = 5`, elements non-objets ignores, troncature journalisee.
- `render_offer` : un agent d'un autre business pouvait lancer un cycle Podalux (rendu RunPod potentiellement payant). Refuse hors business `podalux` (message renvoye a l'agent).
- Prompt : "groupe Podalux" remplace par "groupe OCTOPUS (business X)" hors Podalux ; texte Podalux inchange (evals).
- Tests : `tests/test_business_propagation.py` (plan borne, refus, identite).

### T17 - Couts LLM (faits mesures)
- `data/octopus.db` : 0 appel LLM journalise. `agents/data/podalux.db` table `costs` : 289 appels, 0,1415 USD, tous le 2026-09-16.
- Postes principaux : ORBIT `action` (flash, 52 appels, 0,0193), CONVERT `redaction_job` (flash, 23, 0,016), ORBIT `planification` (v4-pro, 4, 0,0132 ; ~1 458 tokens de sortie), ORBIT `synthese` (v4-pro, 2, 0,010 ; ~4 228 tokens d'entree).
- Profils `zero_cost`, `low_cost`, `quality_first` deja implementes dans `octopus/config/catalog.json` ; `zero_cost` exclut la classe `paid` et ignore les pins (verifie dans `llm.complete`), defaut quand OmniRoute est actif.
- Constat : `web.inspect_page` (pages publiques, `agents/browser.py`) n'existe que dans la surcouche OmniRoute ; sans OmniRoute la vision publique finit en `NoEligibleModel` (repli texte) en `zero_cost`. Pas de depense cachee ; comportement a documenter.
- Hypothese non mesuree : la synthese ORBIT recoit les `steps` complets des sous-taches ; ne transmettre que les `final` reduirait l'entree du modele pro, effet qualite inconnu.

### T18 - QC ffmpeg en une passe
- Constat mesure : `qc_metrics.py` decodait 4 fois la video (LUFS, saturation, coupes, freezes), ~1,8 s par passe sur un rendu 1080x1920 de 19,5 s.
- Changement : `stream_metrics()` (un seul `-filter_complex`, branche audio seulement si une piste existe) ; anciennes fonctions gardees comme reference de test.
- Parite verifiee : 2 rendus reels (`out/cash_devis_cgv01`, `out/cash_impayes_relance01`) + video synthetique avec freeze et coupes + video sans audio : valeurs identiques ; JSON final identique hors chemins de frames.
- Gain mesure (machine locale, WanGP actif en fond) : 12,2 s -> 7,1 s et 14,5 s -> 8,0 s par QC complet. Gain sur le worker RunPod : non mesure.
- Test : `tests/test_qc_metrics_single_pass.py` (ignore si ffmpeg absent, donc ignore dans la CI actuelle).
- Hypotheses restantes : `extract_frames` (6 ffmpeg) et `ken_burns_motion` (8 ffmpeg) coutent ~4 s a eux deux ; regroupement possible mais changerait potentiellement la metrique de mouvement.

### T19/T20/T21 - GUI
- Smoke isole (`PODALUX_ROOT`/`OCTOPUS_HOME`/`OCTOPUS_DB` temporaires, fenetre reelle ouverte puis detruite) :
  - crash au premier lancement : `db.get_state` avant `db.init_db()` -> `no such table: state` ; corrige (init avant lecture) ;
  - crash a l'ouverture (page Cockpit) : `grid` dans une carte dont le titre est en `pack` ; memes defauts sur Missions et Navigateur, et flux d'activite ; reproduit sur worktree HEAD propre ; corrige par un cadre interne ;
  - `_refresh` levait `TclError` sur un libelle de la page Missions detruit, avant le `self.after` : plus aucun rafraichissement ensuite ; corrige.
- Carte "Etat strategique persiste" : business actif ou portefeuille ; capture verifiee visuellement.
- `tests/test_gui_smoke.py` : ouvre toutes les pages sur base vide et verifie que le rafraichissement continue ; echoue sur HEAD (pack/grid) et sans le correctif init_db (verifie par retour arriere temporaire). Ignore sans affichage (CI Linux).
- Non verifie : clics reels, donnees reelles, lancement effectif d'une mission depuis la GUI.

### T22/T23
- `octopus/connectors.py` : 5 domaines "non configure" par defaut ; une sonde en erreur ou incoherente reste indisponible ; ligne de synthese sans valeur metier, utilisee par les revues et la GUI.
- `orbit.mission` : si un contexte strategique est fourni, le rapport est enregistre comme preuve `model_inference` (confiance `low`, `source_ref=task#N`), liee a l'objet le plus precis, une seule fois par tache (`ctx.memo`).

### Resultats de tests (fin de session)
- `.venv/Scripts/python.exe -m pytest -o addopts="" -q --ignore=tests/test_browser_integration.py` : 402 passes, 1 skipped, 1 avertissement (T27).
- `python -m pytest -o addopts="" -q tests/test_browser_integration.py` (Python global) : 7 passes.
- venv jetable aux dependances du job CI contract : 36 passes.
