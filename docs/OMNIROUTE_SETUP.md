# OmniRoute — passerelle LLM locale

OCTOPUS garde son contrat OpenAI-compatible, mais les appels des agents passent par [OmniRoute](https://github.com/diegosouzapw/OmniRoute) lorsque `OMNIROUTE_ENABLED=1` (valeur par défaut).

## Architecture

```text
agents/runtime.py / agents/deepseek.py
        ↓
   octopus.llm
        ↓
OmniRoute local (Docker)
        ↓
   auto/free
        ↓
provider gratuit réellement connecté/disponible
```

Le catalogue OCTOPUS injecte à l'exécution le modèle virtuel `omniroute/auto-free`. Il est envoyé au gateway sous le nom `auto/free`, afin qu'OmniRoute fasse son propre routage sans que le code OCTOPUS fige un fournisseur gratuit précis.

## Installation locale Windows

Prérequis : Docker Desktop démarré. L'image OmniRoute est la seule grosse dépendance locale prévue pour la passerelle.

```powershell
docker pull diegosouzapw/omniroute:latest
docker run -d --name omniroute --restart unless-stopped -p 20128:20128 -v omniroute-data:/app/data diegosouzapw/omniroute:latest
docker ps
```

L'instance doit ensuite afficher/répondre sur l'endpoint local fourni par l'installation. Pour cette configuration OCTOPUS :

```text
OMNIROUTE_ENABLED=1
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/api/v1
OMNIROUTE_MODEL=auto/free
```

La clé ne va jamais dans Git. La définir uniquement dans l'environnement utilisateur Windows :

```powershell
[Environment]::SetEnvironmentVariable("OMNIROUTE_API_KEY", "<CLE_OMNIROUTE>", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_ENABLED", "1", "User")
[Environment]::SetEnvironmentVariable("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/api/v1", "User")
```

Fermer/réouvrir PowerShell après changement d'environnement.

## Vérification

```powershell
$headers = @{ Authorization = "Bearer $env:OMNIROUTE_API_KEY" }
Invoke-RestMethod -Uri "http://127.0.0.1:20128/api/v1/models" -Headers $headers
```

Puis dans OCTOPUS :

```powershell
python -m agents.run doctor
```

Le diagnostic contrôle désormais Playwright/Chromium, la base locale, OmniRoute et les credentials RunPod lorsque le rendu vidéo cloud est sélectionné.

## Coût / sécurité

`zero_cost` interdit les modèles `paid`. Une panne OmniRoute ou de tout son pool gratuit doit donc produire un échec explicite plutôt qu'un basculement implicite vers DeepSeek.

OmniRoute est une passerelle locale : le prompt peut néanmoins être transmis au provider final choisi par OmniRoute. Ne pas considérer `OMNIROUTE_BASE_URL=localhost` comme une garantie que les données restent sur la machine.

## Revenir temporairement au comportement historique

```text
OMNIROUTE_ENABLED=0
OCTOPUS_PROFILE=legacy
```

Ce mode réactive les appels directs historiques et doit rester exceptionnel si le quota DeepSeek est épuisé.
