# OCTOPUS / Podalux — usine à Shorts cloud-first

OCTOPUS est le contrôleur/orchestrateur de l'usine Podalux : sélection d'offre, rédaction, production, QC, arbitrage et distribution. La machine locale reste légère ; les traitements vidéo lourds sont externalisés.

## Architecture actuelle

```text
                         ┌─────────────────────┐
                         │       OCTOPUS       │
                         │ control-plane / DB  │
                         └──────────┬──────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
          LLM / agents          Navigateur            Vidéo
              │                     │                     │
        octopus.llm           Playwright          VideoService
              │                + web_guard               │
         OmniRoute                  │              RunPod Serverless
              │                     │                     │
         auto/free             comptes / web       FORGE cloud worker
                                                    │
                                             TTS + Remotion +
                                             FFmpeg + QC
```

## Agents

```text
SOUT → CONVERT → FORGE → GROWTH → LEDGER → ORBIT
```

- **SOUT** : recherche / veille / sélection d'offre
- **CONVERT** : offre, script, CTA, job vidéo
- **FORGE** : production déterministe
- **GROWTH** : QC visuel / distribution
- **LEDGER** : métriques, coûts, go/no-go
- **ORBIT** : coordination, arbitrage, synthèse

`agents/runtime.py` est le runtime ReAct commun et reste volontairement stable pendant la migration vidéo.

## LLM : OmniRoute

Le gateway local recommandé est [OmniRoute](https://github.com/diegosouzapw/OmniRoute). OCTOPUS injecte dynamiquement un modèle virtuel :

```text
omniroute/auto-free → auto/free
```

Lorsque `OMNIROUTE_ENABLED=1`, les agents utilisent par défaut le profil `zero_cost`. Ce profil n'autorise aucun modèle `paid` et ne doit jamais basculer automatiquement vers DeepSeek payant.

Configuration locale :

```text
OMNIROUTE_ENABLED=1
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
OMNIROUTE_MODEL=auto/free
OMNIROUTE_API_KEY=<secret>
```

La clé est uniquement une variable d'environnement. Ne jamais la committer.

OmniRoute fournit un endpoint OpenAI-compatible ainsi que des routes audio/vision selon les providers connectés ; le provider réellement choisi dépend de la configuration du gateway. Voir la [documentation API](https://github.com/diegosouzapw/OmniRoute/wiki/API-Reference) et le [guide free tiers](https://github.com/diegosouzapw/OmniRoute/wiki/User-Guide).

## Développement assisté : Orca (optionnel)

[Orca](https://github.com/stablyai/orca) peut superviser les tâches de **développement du dépôt** — par exemple un audit, une correction ciblée ou une revue dans un workspace agentique. Il n'est pas une dépendance des cycles Podalux et ne remplace pas `octopus.tasks`, RunPod ou le runtime métier.

Le pont `agents/orca.py` utilise uniquement le CLI public Orca et reste désactivé par défaut :

```text
OCTOPUS_ORCA_ENABLED=1
OCTOPUS_ORCA_CLI=orca
```

Le flux exposé est `Run → Task → Worker`, avec un `run_id` transmis explicitement entre les appels CLI pour rester indépendant de l'état de session du terminal. Voir `docs/ORCA_INTEGRATION.md`.

## Vidéo

### Cycle Podalux

`agents/cycle.py` choisit le rendu cloud par défaut. Le pipeline métier historique FORGE n'est pas dupliqué : le worker cloud exécute les étapes existantes TTS → Remotion → FFmpeg → QC dans un workspace isolé.

### MiniMax H3

MiniMax H3 est **cloud-only** dans cette configuration. Le client construit un workflow ComfyUI H3, puis l'envoie à un endpoint RunPod séparé.

Le workflow respecte le modèle H3 actuel : 24 fps, grille temporelle `17n+5`, canvas natif autour de `768x1344`, sampler multi-step et nœuds H3/VAEs dédiés. Les templates/node sources officiels ComfyUI sont la référence du contrat.

Le poste local ne télécharge pas les poids H3.

## Stockage et idempotence

Le worker publie les artefacts dans un backend `filesystem` pour les tests ou `s3` / S3-compatible en cloud :

```text
<offer_id>/<job_id>/final.mp4
<offer_id>/<job_id>/qc_metrics.json
<offer_id>/<job_id>/frames/*
<offer_id>/<job_id>/logs/*
<offer_id>/<job_id>/manifest.json
```

L'identifiant vidéo est déterministe (`stable_job_id`). Le store d'état local mémorise également l'identifiant du job distant. Une soumission ambiguë sans `remote_id` n'est **jamais** resoumise automatiquement, afin d'éviter un double rendu facturé.

RunPod Serverless utilise le modèle asynchrone `POST /run` puis `GET /status/{id}` ; le projet conserve cette identité jusque dans le manifest. Voir la [documentation RunPod](https://docs.runpod.io/).

## Navigateur intégré

Le navigateur agentique est Playwright/Chromium :

- web public en contexte éphémère ;
- comptes autorisés en profil persistant ;
- Service Workers bloqués sur les contextes surveillés ;
- `browserContext.route()` pour les requêtes du contexte et `browserContext.route_web_socket()` pour les WebSockets ;
- navigations, redirects, `fetch`, XHR, WebSocket, EventSource et beacon soumis au garde ;
- après lecture d'un compte, la sortie vers une page publique est refusée dans la même session ;
- login, 2FA, CAPTCHA et confirmations passent par `handoff()` humain.

Les tests de sécurité couvrent notamment l'exfiltration `fetch` pendant le chargement d'une page de compte et la création d'un WebSocket public après lecture d'un compte.

## Installation locale légère

Voir **`docs/LOCAL_SETUP.md`** et **`docs/OMNIROUTE_SETUP.md`**.

Le fichier `requirements-local.txt` contient uniquement les dépendances du control-plane :

```text
openai
requests
customtkinter
Pillow
playwright
numpy
pytest
```

Préparer Chromium :

```powershell
python -m playwright install chromium
```

Docker Desktop + OmniRoute sont requis pour le routage LLM local. MiniMax H3, TTS lourd et rendu vidéo restent cloud-first.

## Diagnostic

Avant tout cycle :

```powershell
python -m agents.run doctor
```

Puis :

```powershell
python run_gui.py
```

Le doctor distingue les prérequis obligatoires du control-plane de ceux du renderer local historique.

## Tests CI / hors réseau

```powershell
python -m pytest -q tests/test_omniroute.py tests/test_minimax_h3_cloud.py tests/test_browser_integration.py tests/test_doctor.py tests/test_gateway.py tests/test_cycle_logic.py tests/test_orca.py
```

Les tests vidéo et H3 ne téléchargent pas les gros modèles ; les tests navigateur utilisent des doubles réseau et peuvent également démarrer Chromium lorsqu'il est disponible.

## Publication

Le chemin de publication reste un dry-run tant que les validations humaines et intégrations externes ne sont pas explicitement activées.

## Documentation de reprise

- `docs/LOCAL_SETUP.md` : installation Windows actuelle
- `docs/OMNIROUTE_SETUP.md` : gateway OmniRoute
- `docs/ORCA_INTEGRATION.md` : pont Orca optionnel pour le développement
- `agents/README.md` : roster, coordination, navigateur, vidéo
- `BRIEF-DEEPSEEK.md` : historique de conception et critères qualité
- `AUDIT.md` : audits historiques
