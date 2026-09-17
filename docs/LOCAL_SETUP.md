# Installation locale OCTOPUS — Windows cloud-first

Cette configuration garde le poste local léger : **contrôle OCTOPUS + GUI + navigateur + OmniRoute**. Les modèles vidéo lourds, MiniMax H3, Remotion/FFmpeg et le TTS cloud s'exécutent hors de la machine quand `PODALUX_VIDEO_RENDERER=cloud`.

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

Docker Desktop doit être démarré :

```powershell
docker pull diegosouzapw/omniroute:latest
docker run -d --name omniroute --restart unless-stopped -p 20128:20128 -v omniroute-data:/app/data diegosouzapw/omniroute:latest
docker ps
```

Vérifier l'endpoint fourni par l'instance. Pour OCTOPUS, les valeurs par défaut sont :

```powershell
[Environment]::SetEnvironmentVariable("OMNIROUTE_ENABLED", "1", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/api/v1", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_MODEL", "auto/free", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_API_KEY", "<CLE_RUNTIME>", "User")
```

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
python -m agents.run doctor
```

En cloud-first, le diagnostic doit vérifier :

- Python de contrôle ;
- Playwright + Chromium ;
- SQLite / journal OCTOPUS ;
- OmniRoute + `/models` ;
- identifiants RunPod ;
- absence de verrou de production gênant.

Les dépendances locales de rendu/Chatterbox sont informatives et non bloquantes lorsque `PODALUX_VIDEO_RENDERER=cloud`.

## 7. Lancer la GUI

```powershell
python run_gui.py
```

Le bouton **Ouvrir le navigateur** lance le profil Chromium persistant. La connexion aux comptes est faite une fois par l'humain ; les actions sensibles (login, 2FA, CAPTCHA, confirmation) passent par un handoff humain.

Le bouton **Worker** lance la file OCTOPUS. La file utilise SQLite avec leases, retries, idempotence et `task_steps` pour éviter de rejouer des étapes coûteuses.

## 8. Tests avant le premier cycle

```powershell
python -m pytest -q tests/test_omniroute.py tests/test_minimax_h3_cloud.py tests/test_browser_integration.py tests/test_doctor.py tests/test_gateway.py tests/test_cycle_logic.py
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
