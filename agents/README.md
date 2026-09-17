# Groupe d'agents Podalux

Orchestrateur de 6 agents pilotés par DeepSeek, qui produit des Shorts **sans intervention**.

## Roster

| Agent | Rôle | Modèle |
|---|---|---|
| **ORBIT** | CEO — arbitre, tient la barre qualité | `deepseek-v4-pro` (reasoning high) |
| **GROWTH** | Acquisition/Distribution — **propriétaire du QC SHIP+WARM** | `deepseek-flash` (vision) |
| **LEDGER** | Data/Finance — rubric /35, coûts, go/no-go | code + `deepseek-v4-pro` |
| **FORGE** | Production — rendu vidéo (Chatterbox + Remotion + mux) | déterministe (pas de LLM) |
| **CONVERT** | Monétisation — offre, prix, CTA, job.json | `deepseek-flash` |
| **SOUT** | Recherche — sélection d'offres, **veille web (navigateur)** | `deepseek-flash` |

## Cycle économique

```
SOUT → CONVERT → FORGE → GROWTH → LEDGER → ORBIT
(recherche) (monétisation) (rendu) (QC) (rubric/coût) (arbitrage)
```

## Commandes

```powershell
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')

python -m agents.run cycle              # SOUT choisit l'offre
python -m agents.run cycle --offer cash_devis_cgv01   # offre forcée
python -m agents.run status             # coûts + dernières décisions
python -m agents.run browser <url>      # teste le navigateur (navigation + vision)
python -m agents.run goal "…"           # objectif libre (1 agent ORBIT, boucle ReAct)
python -m agents.run mission "…"        # ORBIT planifie + délègue aux rôles (multi-agents)
python -m agents.run msg SOUT "…"       # message direct à un agent (@ROLE dans la GUI)
```

## Runtime général + missions multi-agents (Phase 7)

`agents/runtime.py` — les agents sont libres d'agir et d'apprendre via une boucle **ReAct**
(penser → agir → observer) sur un **registre d'outils partagés** :

| Outil | Effet |
|---|---|
| `search` | recherche web (Google News RSS + Wikipedia, ou Brave/Tavily si clé) |
| `browse` | ouvre une page et la décrit (texte + vision) |
| `render_offer` | produit la vidéo complète d'une offre |
| `qc` | métriques ffmpeg d'une offre |
| `ask_human` | demande confirmation/info à l'humain (non-bloquant en autonome) |
| `publish` | plan de publication (dry-run) |
| `send_message` | prospection (dry-run) |
| `remember` / `recall` | mémoire d'apprentissage persistée (SQLite) |

- `run_agent(role, goal)` — un rôle poursuit un objectif librement.
- `run_mission(goal)` — **ORBIT planifie** (décompose l'objectif en sous-tâches assignées à des
  rôles), **délègue** séquentiellement (chaque sous-tâche reçoit le contexte des précédentes),
  puis **synthétise** le rapport final.
- Garde anti-boucle : si un agent répète la même action sans progrès (CAPTCHA…), on lui demande
  de changer d'approche ou de répondre.

## Recherche web (anti-blocage)

DuckDuckGo et Bing HTML bloquent l'automatisation par CAPTCHA. On contourne avec des sources
bot-friendly, par ordre de priorité :

1. **Brave Search API** (`BRAVE_API_KEY`) — vraie recherche web, 2000 req/mois gratuites.
2. **Tavily API** (`TAVILY_API_KEY`) — recherche optimisée IA, 1000 crédits/mois gratuits.
3. **Google News RSS** — actualités (keyless, XML), idéal pour la veille.
4. **Wikipedia** — encyclopédie (keyless), idéal pour les concepts.

Sans clé, la veille fonctionne via Google News + Wikipedia (résultats pertinents en français).

## Navigateur (Phase 5)

`agents/browser.py` — Playwright **persistant** :

- **Profil persistant** (`agents/data/browser_profile`) : cookies/sessions conservés entre les runs.
- **Deux modes** : `headless=True` (veille, recherche) / `headless=False` (visible, passation humaine).
- **API** : `goto`, `snapshot` (texte), `links`, `click`, `type`, `wait_for`, `screenshot`, `download`, `see` (vision), `handoff`.
- **Vision** : `see()` envoie la capture à `deepseek-flash` → l'agent « voit » la page.
- **Veille** : `SOUT.research("sujet")` = recherche multi-sources + synthèse.

### Navigateur Chromium (profil persistant)

Le navigateur des agents est un **Chromium** (Playwright) avec un **profil persistant**
(`agents/data/browser_profile`) : les connexions aux comptes (Stripe, Reddit, X, Fiverr,
YouTube…) sont **conservées** entre les sessions.

- **Visible par défaut** (`headless=False`) : l'humain regarde, interagit et se connecte.
- **Connexion unique** : tu te connectes une fois dans la fenêtre, l'agent retrouve la session.
- Bouton **« 🌐 Ouvrir le navigateur »** (GUI) ou `python -m agents.run browse-open`.

⚠️ **ToS** : préférer les API officielles (YouTube Data API, Stripe API).
L'automatisation de logins est fragile et peut violer les CGU → `handoff()` pour login/2FA/captcha.

## Persistance (SQLite `agents/data/podalux.db`)

- `messages` — salon + fil par agent (@mentions)
- `costs` — chaque appel DeepSeek (agent, modèle, tokens, coût)
- `metrics` — rubric /35 par offre (équivalent `cash_metrics.csv`)
- `decisions` — audit des décisions (SOUT/CONVERT/GROWTH/LEDGER/ORBIT)

## Garde-fous

- **Secrets** : `DEEPSEEK_API_KEY` reste en variable d'environnement.
- **Shell** : liste blanche (`SHELL_WHITELIST`). Attention : `python`, `npx`, `curl` et `git` y figurent, la liste n'empêche donc pas l'exécution de code arbitraire. Ne jamais exposer `run_shell` à un LLM.
- **Coût** : budget par run (`CYCLE_BUDGET_USD`, sous-runs inclus) et plafond journalier, vérifiés avant chaque appel par la passerelle OCTOPUS (`octopus/README.md`). Chaque appel est journalisé dans `data/octopus.db` et dans la table `costs`.
- **Échecs bruyants** : toute étape de FORGE (TTS, rendu, mux, métriques) lève `StepError` sur code retour non nul, timeout (900 s) ou artefact absent/antérieur au début de l'étape. Sortie complète dans `out/<offre>/logs/<étape>.log`.
- **Un seul cycle à la fois** : verrou `run_lock` (table `state`, bail de 30 min renouvelé à chaque itération). Un second cycle (GUI, CLI, outil `render_offer`) est refusé sans rien modifier. `python -m agents.run status` affiche le détenteur.
- **Arrêt réel** : le bouton Arrêter horodate la demande. Cycles, missions et agents s'arrêtent au prochain point de contrôle (avant chaque appel LLM, entre les étapes de FORGE) ; une étape longue (TTS, rendu, ffmpeg) est interrompue en moins de 2 s et son arbre de processus tué. Un arrêt antérieur au lancement est ignoré.
- **Mémoire** : clés normalisées (casse, accents, séparateurs) ; `remember` écrit toujours pour l'agent qui tourne ; un `recall` sans résultat renvoie les clés connues.
- **SQLite** : `podalux.db` en WAL avec attente de 10 s sur verrou (GUI, cycle et agents écrivent en parallèle).
- **Journaux GUI** : chaque sous-processus lancé par la GUI écrit dans `agents/data/logs/` (200 derniers conservés).
- **Décisions en code** : LEDGER bloque la publication si un contrôle ffmpeg échoue (`MEDIA_GATES` : durée 18-35 s, 1080x1920, LUFS -16 à -12, aucun freeze). ORBIT applique sa règle écrite en code (`ORBIT_DECISION = "code"`, sans appel LLM) : `done` si GO, `iterate` s'il reste des itérations et que le défaut est corrigeable par le script, sinon `stop`. `PODALUX_ORBIT_DECISION=llm` rétablit l'arbitrage deepseek-v4-pro.
- **Sorties LLM validées** : notes du QC vision entières et bornées par axe, job de CONVERT à 7 segments dans l'ordre ; une relance, puis échec explicite.
- **Offre forcée** : pas d'appel à SOUT, angle pris dans le catalogue ; une offre hors catalogue est refusée.
- **Sortant** : rien ne sort de la machine (pas d'upload/email/achat) — à ajouter en `--dry-run`.
- **Tests** : `python -m pytest` (hors-ligne). `python -m agents._verify --live` fait des appels payants et écrit en base de production.

## Prérequis

- Serveur Chatterbox démarré (`127.0.0.1:4123`) — Phase 3.
- Remotion installé (`remotion/`).
- `playwright` installé + navigateurs (`%LOCALAPPDATA%\ms-playwright`).
- `DEEPSEEK_API_KEY` posée en variable d'environnement utilisateur.
