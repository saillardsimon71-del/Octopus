# Installation locale OCTOPUS — Windows cloud-first

Cette configuration garde le poste local léger : **contrôle OCTOPUS + GUI + navigateur + OmniRoute**. Les modèles vidéo lourds, MiniMax H3, Remotion/FFmpeg et le TTS cloud s'exécutent hors de la machine quand `PODALUX_VIDEO_RENDERER=cloud`.

## 0. Bootstrap recommandé

Depuis PowerShell à la racine du dépôt :

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup-local.ps1
```

Le script est idempotent : il crée `.venv` si nécessaire, installe `requirements-local.txt`, installe Chromium Playwright, prépare les variables non secrètes cloud-first et démarre/réutilise OmniRoute lorsque Docker Desktop est disponible. Il ne stocke aucun secret.

## 1. Préparer Python

Utiliser Python 3.11+ :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-local.txt
```

## 2. Installer Chromium Playwright

```powershell
python -m playwright install chromium
```

Le navigateur intégré utilise Chromium Playwright, pas le navigateur Edge personnel. Le profil persistant des comptes est stocké sous `agents/data/browser_profile`.

## 3. Démarrer OmniRoute

Docker Desktop doit être démarré. OmniRoute expose actuellement son proxy OpenAI-compatible sous `/v1` sur le port `20128`. Le bind sur `127.0.0.1` évite aussi une exposition réseau locale involontaire.

```powershell
docker pull diegosouzapw/omniroute:latest
docker run -d --name omniroute --restart unless-stopped --stop-timeout 40 -p 127.0.0.1:20128:20128 -v omniroute-data:/app/data diegosouzapw/omniroute:latest
docker ps
```

Pour OCTOPUS, les valeurs par défaut sont :

```powershell
[Environment]::SetEnvironmentVariable("OMNIROUTE_ENABLED", "1", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_MODEL", "auto/free", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_API_KEY", "<CLE_RUNTIME>", "User")
```

OmniRoute documente le modèle `auto` et ses variantes `auto/...`; OCTOPUS conserve `auto/free` pour privilégier le pool gratuit lorsque cette variante est disponible dans l'instance.

La clé ne doit pas être mise dans GitHub, un test, un commit ou un fichier de configuration versionné.

Fermer et rouvrir PowerShell après modification des variables utilisateur.

## 4. Préparer le rendu vidéo cloud

Le cycle économique est cloud-first par défaut.

```powershell
[Environment]::SetEnvironmentVariable("PODALUX_VIDEO_RENDERER", "cloud", "User")
[Environment]::SetEnvironmentVariable("PODALUX_RUNPOD_ENDPOINT_ID", "<RUNPOD_ENDPOINT>", "User")
[Environment]::SetEnvironmentVariable("PODALUX_RUNPOD_API_TOKEN", "<RUNPOD_TOKEN>", "User")
```

Pour MiniMax H3, le backend dédié utilise :

```text
OCTOPUS_MINIMAX_H3_ENDPOINT_ID
OCTOPUS_MINIMAX_H3_API_TOKEN
```

Le PC ne doit pas télécharger les poids H3.

## 5. TTS du worker cloud

Le worker vidéo ne lance pas Chatterbox sur le poste local. Il attend une URL TTS configurée côté worker :

```text
CHATTERBOX_URL=https://<endpoint-cloud>/v1/audio/speech
```

La même recette audio historique reste utilisée : voix, mix, bed, SFX et captions sont produits dans le worker.

## 6. Diagnostic automatique

Avant un vrai cycle :

```powershell
.\.venv\Scripts\python.exe -m agents.run doctor
```

En cloud-first, le diagnostic doit vérifier :

- Python de contrôle ;
- Playwright + Chromium ;
- SQLite / journal OCTOPUS ;
- OmniRoute + `/v1/models` ;
- identifiants RunPod ;
- absence de verrou de production gênant.

Les dépendances locales de rendu/Chatterbox sont informatives et non bloquantes lorsque `PODALUX_VIDEO_RENDERER=cloud`.

## 7. Lancer la GUI

```powershell
.\.venv\Scripts\python.exe run_gui.py
```

Le bouton **Ouvrir le navigateur** lance le profil Chromium persistant. La connexion aux comptes est faite une fois par l'humain ; les actions sensibles (login, 2FA, CAPTCHA, confirmation) passent par un handoff humain.

Le bouton **Worker** lance la file OCTOPUS. La file utilise SQLite avec leases, retries, idempotence et `task_steps` pour éviter de rejouer des étapes coûteuses.

## 8. Tests avant le premier cycle

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_omniroute.py tests/test_minimax_h3_cloud.py tests/test_browser_integration.py tests/test_doctor.py tests/test_gateway.py tests/test_cycle_logic.py
```

Pour les tests de navigateur réel, Chromium doit être installé. Pour un test purement hors-réseau, la suite réseau utilise des doubles d'interception.

## 9. Sécurité / comptes

Le navigateur intégré sépare les usages :

```text
page publique
    → contexte éphémère
    → pas de cookies de comptes

page de compte autorisé
    → profil persistant
    → lecture contrôlée
    → pas de navigation publique après entrée dans le contexte compte
```

Les requêtes actives (`fetch`, `xhr`, WebSocket, EventSource, beacon) sont surveillées afin qu'une page d'un compte ne puisse pas utiliser le navigateur comme relais vers un domaine public non autorisé.

## 10. Retour temporaire au local historique

Le rendu local reste disponible pour diagnostic uniquement :

```powershell
[Environment]::SetEnvironmentVariable("PODALUX_VIDEO_RENDERER", "local", "User")
```

Dans ce cas le `doctor` exigera les dépendances locales correspondantes, notamment FFmpeg, Remotion, Chromium et Chatterbox. MiniMax H3 n'est jamais rendu localement par cette configuration.


## Voix (TTS_CHAIN)

La narration passe par `tools/tts_providers.py`. `TTS_CHAIN` (defaut `azure,cloudflare,chatterbox,piper`)
essaie les fournisseurs dans l'ordre et garde le premier qui repond ; le fournisseur reellement utilise
par segment est ecrit dans `out/<offer>/audio/tts_report.json`.

| Fournisseur | Variables | Gratuit |
|---|---|---|
| azure | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `AZURE_TTS_VOICE` (defaut `fr-FR-VivienneMultilingualNeural`) | palier F0 : 500 000 caracteres/mois, voix neuronales |
| cloudflare | `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `CF_TTS_MODEL` (defaut `@cf/myshell-ai/melotts`) | 10 000 neurones/jour |
| chatterbox | `CHATTERBOX_URL` (serveur local, ou `hf-space:<owner/space>` + `HF_TOKEN`) | local, ou quota ZeroGPU du Space |
| piper | `PIPER_VOICE` (defaut `fr_FR-siwis-medium`), `PIPER_DATA_DIR`, `PIPER_LENGTH_SCALE` | illimite, CPU, sans compte |

`piper` est le plancher : tant qu'il est installe (`pip install piper-tts`), aucun quota ne peut
arreter un cycle. Mesure du 17/09/2026 (2 vCPU) : 7,4 s de voix synthetises en 1,8 s.
