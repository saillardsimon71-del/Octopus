# Agnes — Smoke test Windows (première génération réelle)

> **Aucune génération réelle avec clé pendant la session Arena.** Ce smoke test est à exécuter sur ta machine Windows, hors Arena, une fois la clé configurée.

## Prérequis

- Pin récupéré: `cache/upstreams/agnes-video-generator` @ `a87162d6df73ffe72186838ca0ae9d461e68589b`
- Service Agnes lancé sur `http://127.0.0.1:8765` (voir `docs/AGNES_WINDOWS_SETUP.md`)
- `AGNES_API_KEY` configurée via méthode sécurisée (env var, fichier hors repo, jamais dans prompts/logs/Git/args/navigateur)
- Canal économique actif avec `act`

```powershell
# Vérifier pin
git -C cache/upstreams/agnes-video-generator rev-parse HEAD
# doit afficher a87162d6df73ffe72186838ca0ae9d461e68589b

# Vérifier service
curl http://127.0.0.1:8765/api/health
# {"ok": true, "service": "agnes-video-generator", ...}

# Vérifier ressource OCTOPUS
python -m octopus resources check agnes_video
python -m octopus status --business octopus
```

## Déclaration ressource (si pas déjà fait)

```powershell
# Dans resources.toml ajouter:
# [[resource]]
# key = "agnes_video"
# kind = "video_engine"
# label = "Agnes local 127.0.0.1:8765"
# locator = "http://127.0.0.1:8765"
# capabilities = ["video_generation","agnes_submit","agnes_stop"]
# probe = "agnes"

python -m octopus resources sync
python -m octopus resources check agnes_video
```

## Canal économique + autorisation humaine

```powershell
# Créer canal via Python (economy)
python -c "
from octopus import economy
cid = economy.add_channel('octopus', 'agnes_video', 'Local Agnes', created_by='human',
                          locator='http://127.0.0.1:8765',
                          capabilities=['agnes_submit','agnes_stop'])
print(f'channel #{cid}')
"
# Activer: via economy CLI
python -m octopus economy access octopus 1 --status active --access act
# ou via Python:
python -c "
from octopus import economy
economy.update_channel('octopus', 1, actor='human', status='active', access='act')
print('channel active act')
"
# Lister:
python -m octopus economy status octopus
```

## Smoke test réel — une seule génération courte, idempotente

```powershell
# 1. Enqueue via OCTOPUS (idempotency_key garantit reprise sans double génération)
# PowerShell: single quotes autour du JSON, double quotes à l'intérieur (pas d'échappement backslash)
$env:OCTOPUS_HOME=$PWD
python -m octopus enqueue octopus agnes.generate_video --input '{"prompt": "Une mer calme au coucher du soleil, plan large, 5 secondes", "idempotency_key": "smoke-2026-09-30"}'

# 2. Lancer worker (une fois)
python -m octopus worker --once

# 3. Vérifier génération locale
python -c "
from octopus import agnes_production, agnes
from pathlib import Path
gens = agnes_production.list_generations('octopus', limit=5)
print(gens[0])
g = gens[0]
print(agnes.verify_mp4(Path(g['output_path'])))
"

# 4. Via mission autonome — runtime démarre superviseur + worker (cycle complet)
python -c "
from octopus import strategy
oid = strategy.create('objective', 'octopus', 'Vidéo test Agnes', created_by='human',
                      statement='Produire une vidéo de 5s d une mer calme via Agnes et vérifier le livrable',
                      success_criteria='verified_video_count>=1')
strategy.transition('objective', oid, 'octopus', 'active', actor='human')
print(f'objective #{oid} active')
"
python -m octopus runtime --once --business octopus
# Vérifier état observable (pas de commande supervisor séparée):
python -m octopus status --business octopus
python -m octopus status --business octopus --json
```

## Vérification finale attendue

- Fichier MP4 présent dans `data/agnes_videos/octopus/...` ou `cache/upstreams/agnes-video-generator/.working_dir/.../final_video.mp4`
- `verify_mp4` → `verified=True`, SHA-256 présent
- Evidence dans `strategy_evidence` avec `source_ref` = chemin fichier, `observation` contient SHA-256
- Objectif clos uniquement après vérification physique (pas seulement HTTP `completed`)
- Aucune nouvelle génération aveugle en cas d'incertitude: `GET /api/tasks/{id}` pour réconcilier

## Commandes start/stop rappel

```powershell
# Setup (une fois)
powershell -ExecutionPolicy Bypass -File scripts/agnes_setup.ps1

# Lancer natif
powershell -ExecutionPolicy Bypass -File scripts/agnes_run.ps1

# Lancer Docker
powershell -ExecutionPolicy Bypass -File scripts/agnes_run.ps1 -Docker
# ou
docker compose -f ops/agnes/docker-compose.yml up -d

# Arrêter (préserve .working_dir/.agnes_config)
powershell -ExecutionPolicy Bypass -File scripts/agnes_stop.ps1
powershell -ExecutionPolicy Bypass -File scripts/agnes_stop.ps1 -Docker

# Logs (ne doivent jamais contenir la clé)
docker logs agnes-video --tail 100
```

## Sécurité checklist avant smoke test

- [ ] HOST=127.0.0.1 (jamais 0.0.0.0)
- [ ] Port bind 127.0.0.1:8765:8765 en Docker
- [ ] AGNES_API_KEY uniquement dans env du processus Agnes, jamais dans Git/prompts/logs/args/navigateur
- [ ] cache/upstreams/agnes-video-generator/.working_dir et .agnes_config préservés
- [ ] Aucun log ne contient la clé
- [ ] Canal agnes_video actif + act accordé par humain
- [ ] Tests avec faux service passent sans appel externe: `python -m pytest tests/test_agnes.py tests/test_agnes_production.py -v`
