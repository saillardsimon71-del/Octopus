# Intégration Orca

OCTOPUS peut utiliser **Orca** comme couche optionnelle pour les tâches de développement et de maintenance du dépôt. Orca n'est pas requis pour exécuter un cycle Podalux et ne remplace ni `agents/runtime.py`, ni `octopus.tasks`, ni le pipeline vidéo cloud.

## Principe

```text
                         OCTOPUS
               business / vidéo / QC / cloud
                         │
                         │ tâches métier
                         ▼
                    octopus.tasks

Développement du code
                         │
                         ▼
                   Orca (optionnel)
                  Run → Task → Worker
                  ↙       ↓        ↘
               Claude   Codex    OpenCode
```

Orca apporte surtout le cycle de vie des workers de développement : Run durable, Task, Dispatch/Worker, supervision, messages et états `live` / `unverifiable` / `exited`. Le pont Octopus reprend uniquement cette interface via le CLI, sans dupliquer la base ou le moteur d'orchestration Orca.

## Activation

Par défaut le pont est désactivé. Installer Orca séparément puis définir :

```powershell
$env:OCTOPUS_ORCA_ENABLED="1"
# Optionnel si `orca` n'est pas dans PATH
$env:OCTOPUS_ORCA_CLI="C:\chemin\vers\orca.exe"
```

Aucune clé API Orca n'est stockée dans Git. Le pont utilise `subprocess` sans shell libre et transmet les objectifs/spécifications comme arguments distincts.

## Commandes

Voir l'état du CLI :

```powershell
python -m agents.run orca status
```

Créer un Run, une Task et lancer un worker de développement :

```powershell
python -m agents.run orca start `
  --objective "Maintenir le control-plane cloud-first" `
  --spec "Auditer puis corriger les tests du control-plane sans toucher au runtime métier." `
  --agent codex `
  --worktree current
```

La sortie de `start` contient le `run_id`. Il est nécessaire pour superviser ce Run depuis un autre processus CLI :

```powershell
python -m agents.run orca workers --run <run_id>
python -m agents.run orca check --run <run_id> --wait --timeout-ms 30000
```

## Règles d'intégration

- Orca reste **facultatif** : un poste sans Orca peut faire tourner Octopus normalement.
- Les tâches business Podalux ne sont pas déplacées dans Orca.
- Les rendus RunPod/H3 et les leases `octopus.tasks` restent sous l'autorité d'Octopus.
- Une perte de contact avec un worker Orca est `unverifiable`, jamais une preuve de sortie.
- Pour une tâche de développement, demander à Orca de travailler sur les fichiers explicitement visés et conserver les validations dans Octopus/CI.
- Le pont crée `Run → Task → Worker` en utilisant les commandes publiques documentées d'Orca et transmet explicitement le `run_id` entre les appels CLI ; il ne lit ni n'écrit la base SQLite interne d'Orca.
- Les arguments métier sont passés comme argv séparés ; aucune chaîne de commande libre n'est exécutée.
