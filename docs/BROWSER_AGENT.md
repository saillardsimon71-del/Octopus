# Espace de travail navigateur OCTOPUS (backend Hermes)

Hermes fournit l'infrastructure d'interaction ; OCTOPUS garde l'intelligence, les objectifs, la
planification, les décisions, la mémoire, l'économie et l'orchestration. Il n'y a ni second
cerveau, ni second superviseur, ni seconde mémoire.

```
objectif (strategy) -> Superviseur (supervisor.tick) -> tâche durable supervisor.objective_work
  -> Worker -> run_mission (ORBIT planifie, un rôle exécute en ReAct)
  -> outils browser_* du registre -> octopus.browser_workspace (politiques OCTOPUS)
  -> agents.agent_browser (couche de commande Hermes) -> CLI agent-browser 0.26.0 -> Chromium
       tout le trafic Chromium -> web_guard.GuardProxy (anti-exfiltration, taint des comptes)
  -> observation (arbre d'accessibilité, refs @eN) -> décision du modèle -> action
  -> vérification sur la page réelle -> channel_actions + preuve observée + task_step
  -> action suivante décidée par le modèle -> évaluation du superviseur (verified_browser_actions)
```

## Ce qui vient de Hermes (`59004a62356f3a4697ab0fe8ad5086d2b405e2a6`)

Hermes ne pilote pas Playwright directement : `tools/browser_tool*.py` pilote le CLI natif
`agent-browser` (vercel-labs, Apache-2.0, binaire Rust multiplateforme dont `win32-x64.exe`, version
et sha256 figés dans `pm/lock.json`), qui pilote Chromium par CDP. OCTOPUS réutilise ce backend tel
quel et adapte (sans importer Hermes, dont ces modules dépendent de `hermes_cli`, du PM, du bot
desktop, des providers cloud et du secret scope) :

- le contrat des outils : `navigate` avec observation, `snapshot`, `click/type/press/scroll/back`
  par refs `@eN` issues de l'arbre d'accessibilité, sessions nommées persistantes ;
- l'exécution du CLI (`agents/agent_browser.py`) : sorties dans des fichiers temporaires (le démon
  hérite des descripteurs), drapeaux Windows `CREATE_NO_WINDOW` + `STARTF_USESTDHANDLES`,
  contournement des shims `.cmd` (`eval --base64`, `batch` sur stdin), interprétation JSON stricte,
  socket dir court, environnement enfant assaini ;
- troncature de l'arbre aux limites de ligne et motifs de masquage des secrets (`agent/redact.py`).

Écarts voulus : aucune installation implicite (script explicite), aucune relance automatique d'une
commande, trafic forcé à travers le proxy de garde, arbre complet élagué au lieu du mode compact
`-c` (qui supprime les éléments de liste purement textuels : constaté dans le laboratoire, le
modèle ne voyait pas la liste « Mes demandes »), observation renvoyée après chaque interaction.

Computer Use Hermes (`tools/computer_use/`, `cua-driver` en MCP) reste `defer` : contrôle du bureau,
driver externe, permissions d'accessibilité de l'OS, API privées macOS. Aucun parcours web ne l'exige ;
ses concepts utiles sont repris : ref périmée refusée (« absente du dernier snapshot ») et verdict
structuré de l'effet (`effect.status`).

## Outils du runtime

| Outil | Nature | Permission |
|---|---|---|
| `browser_navigate(url)`, `browser_snapshot(full?)`, `browser_scroll`, `browser_back` | lecture | garde web |
| `browser_click(ref)` sur un lien simple | navigation | garde web |
| `browser_type`, `browser_select`, `browser_check`, `browser_upload` (uniquement depuis `browser_files/<business>/outbox/`) | édition de formulaire | canal `act` |
| `browser_click(ref, expect?)` bouton/validation, `browser_press(Enter…)` | action à effet, registrée | canal `act` |
| `browser_verify(text, action_id?)` | constat sur la page réelle | — |
| `browser_download(ref, filename)` | fichier vers `agents/data/browser_files/<business>/inbox/<tâche>/` | lien simple : garde web ; sinon canal `act` |

Un canal autorisé est un `economic_channel` `active`, accès `act` (accordé uniquement par un humain),
capacité `browser_workspace`, dont le `locator` couvre la page (même origine et préfixe ; `https`
obligatoire hors laboratoire déclaré). L'humain l'ouvre ainsi :

```
python -m octopus economy channel <business> website "Portail X" --locator https://portail.example/ --capabilities browser_workspace
python -m octopus economy access <business> <id> --status active --access act
```

Toujours réservés à l'humain : champs mot de passe / 2FA / paiement / IBAN, saisie d'un secret, URL
contenant un secret. Plafonds par tâche : 300 commandes, 30 actions à effet.

## Reprise sans répétition aveugle

Une action à effet est écrite `proposed` dans `channel_actions` AVANT l'exécution, avec une empreinte
(canal, page, cible rôle+libellé, empreinte des valeurs saisies sur la page) et le texte de la page.
Puis : `verified` (texte `expect` absent avant, présent après ; preuve `observed` créée), `executed`
(pas d'`expect`), `failed` (erreur connue avant tout clic : refaisable) ou `ambiguous` (délai, arrêt,
erreur inconnue). À la réouverture de la tâche, `proposed` devient `ambiguous`. Même empreinte :

- dans la tâche : `verified/executed` -> `already_done` sans rien exécuter ; `ambiguous` -> refus ;
- dans une autre tâche : `proposed/ambiguous/executed` -> refus jusqu'à `browser_verify` ou humain.

Le modèle voit `reprise` (dernière URL, actions ambiguës) dans sa première observation. Le point de
reprise (`task_step browser.workspace`) rouvre la dernière page si l'IA observe avant de naviguer.
Le superviseur transforme une action ambiguë non levée en frontière humaine :

```
python -m octopus browser actions <business> [--status ambiguous]
python -m octopus browser resolve <business> <id> executed|not_executed
```

Limite connue (prudente) : deux envois différents par la même page sans saisie sur cette page, dans
la même tâche, ont la même empreinte ; le second reçoit `already_done`. Une nouvelle tâche le permet.

## Installation et validation (Windows, Linux, macOS)

```
python scripts/install_agent_browser.py      # binaire agent-browser 0.26.0 vérifié (sha256) -> agents/data/bin
python -m playwright install chromium        # Chromium partagé avec BrowserTool
python -m octopus browser doctor --smoke     # vraie page locale, écriture sans canal refusée
python -m pytest -q tests/test_agent_browser_backend.py tests/test_browser_workspace.py tests/test_browser_workspace_e2e.py tests/test_browser_integration.py
```

`OCTOPUS_AGENT_BROWSER`, `OCTOPUS_CHROMIUM` et `OCTOPUS_BROWSER_ARGS` (arguments Chromium séparés par
des virgules) permettent un emplacement ou un environnement particulier. `OCTOPUS_BROWSER_LAB_ORIGINS`
déclare des applications locales explicitement autorisées (tests, outils internes) : sans elle, tout
hôte local ou privé est refusé, y compris au niveau du proxy.

`tests/test_browser_workspace_e2e.py` (Chromium réel) : cycle superviseur complet sur une
application locale dont l'ordre des étapes, des champs et des liens varie ; worker tué dans un
processus séparé pendant l'envoi puis reprise sans second envoi ; refus sans canal ; filtrage réseau.
Le modèle y est remplacé par un décideur d'observation déterministe (aucun appel LLM ni réseau) ;
aucun compte réel, aucune publication, aucun paiement.
