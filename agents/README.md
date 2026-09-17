# Groupe d'agents Podalux

Orchestrateur de 6 agents pilotés par un gateway LLM compatible OpenAI. En configuration normale, les appels passent par OmniRoute en `zero_cost` ; le rendu vidéo lourd est cloud-first.

## Roster

| Agent | Rôle | Responsabilité |
|---|---|---|
| **ORBIT** | CEO | planifie, coordonne, arbitre et synthétise |
| **GROWTH** | Acquisition / distribution | QC visuel + préparation distribution |
| **LEDGER** | Data / finance | rubric /35, coûts, go/no-go |
| **FORGE** | Production | TTS + Remotion + mux + QC technique |
| **CONVERT** | Monétisation | offre, prix, CTA, job vidéo |
| **SOUT** | Recherche | veille, sources, sélection d'offres |

## Cycle économique

```text
SOUT → CONVERT → FORGE → GROWTH → LEDGER → ORBIT
```

FORGE conserve le pipeline historique ; `agents/cycle.py` choisit désormais le renderer **cloud par défaut**. Le mode local reste disponible explicitement via `PODALUX_VIDEO_RENDERER=local`.

## Architecture LLM

```text
agents/deepseek.py
       ↓
  octopus.llm
       ↓
OmniRoute localhost
       ↓
 model virtuel auto/free
       ↓
 provider gratuit disponible
```

Le profil par défaut des agents devient `zero_cost` lorsque `OMNIROUTE_ENABLED=1`. Dans ce profil, un provider payant n'est jamais sélectionné automatiquement. Une panne du pool gratuit produit un échec explicite.

Variables locales :

```text
OMNIROUTE_ENABLED=1
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/api/v1
OMNIROUTE_MODEL=auto/free
OMNIROUTE_API_KEY=<secret runtime uniquement>
```

La clé ne doit jamais entrer dans Git.

## Vidéo et GPU

### Cycle Podalux

`agents/cycle.py` appelle `VideoService`. En cloud, un `VideoJob` autonome est envoyé au renderer distant et les artefacts/QC reviennent sous forme de manifest.

### MiniMax H3

MiniMax H3 est **cloud-only** pour cette configuration :

```text
media.video_generate
       ↓
MiniMaxH3RunPodClient
       ↓
RunPod Serverless / worker GPU
       ↓
ComfyUI H3
       ↓
vidéo + audio + artefacts
```

Le Studio GUI affiche H3 comme `MiniMax H3 [CLOUD RunPod — aucun téléchargement local]` et ne le sonde plus comme un modèle WanGP local.

### Worker cloud

`video_worker/` réutilise le pipeline existant :
- Chatterbox via `CHATTERBOX_URL` configurable ;
- Remotion/Chromium ;
- FFmpeg ;
- QC technique ;
- stockage objet + manifest.

Les modèles lourds ne sont pas installés par `requirements-local.txt`.

## Navigateur intégré

`agents/browser.py` utilise Playwright Chromium.

- profil persistant pour les comptes autorisés (`agents/data/browser_profile`) ;
- contexte éphémère pour le web public ;
- `service_workers="block"` afin que l'interception réseau reste observable ;
- redirections HTTP contrôlées saut par saut ;
- navigations et appels actifs (`fetch`, `xhr`, `websocket`, `eventsource`, `beacon`) soumis au `web_guard` ;
- lecture de compte suivie d'une restriction empêchant la sortie vers des domaines publics dans le même contexte ;
- `see()` capture la page et utilise le routage vision OCTOPUS ; compte = tâche sensible/local, page publique = `web.inspect_page`/OmniRoute quand disponible ;
- `handoff()` pour login, 2FA, CAPTCHA et validation humaine.

Tests : `tests/test_browser_integration.py` vérifie redirection tierce, navigation script et exfiltration `fetch`.

## Coordination des agents

`agents/runtime.py` reste le runtime ReAct partagé : un registre d'outils unique, une mémoire SQLite et un contexte de recherche partagé.

- `run_agent(role, goal)` : un rôle boucle en ReAct ;
- `run_mission(goal)` : ORBIT planifie 2 à 5 sous-tâches, délègue aux rôles et synthétise ;
- la file `octopus.tasks` gère leases, retries, idempotence, événements et demandes humaines ;
- `task_steps` mémorise les étapes coûteuses et évite de les rejouer après un retry ou une réponse humaine.

## Commandes utiles

```powershell
# Diagnostic de l'environnement local cloud-first
python -m agents.run doctor

# GUI
python run_gui.py

# Cycle
python -m agents.run cycle
python -m agents.run cycle --offer cash_devis_cgv01

# Navigateur
python -m agents.run browser https://example.com
python -m agents.run browse-open

# Missions
python -m agents.run mission "…"
python -m agents.run goal "…"
```

## Installation locale légère

Le poste de contrôle utilise `requirements-local.txt` : OpenAI-compatible client, requests, CustomTkinter, Pillow, Playwright, NumPy et pytest.

Après installation des paquets Python :

```powershell
python -m playwright install chromium
```

Docker Desktop + OmniRoute restent requis pour le routage LLM local. Pour le cycle vidéo cloud, `PODALUX_RUNPOD_ENDPOINT_ID` et `PODALUX_RUNPOD_API_TOKEN` doivent être présents.

## Garde-fous

- Secrets : variables d'environnement uniquement.
- `zero_cost` interdit les modèles payants.
- Publication et prospection restent dry-run.
- Verrou de cycle : un seul cycle économique à la fois.
- Annulation coopérative : checkpoints agents/worker + arrêt des sous-processus longs.
- Contrat vidéo : validation d'IDs/templates, rejet des secrets, manifest et artefacts bornés.
- Le diagnostic `python -m agents.run doctor` distingue ce qui est obligatoire localement du matériel cloud.
