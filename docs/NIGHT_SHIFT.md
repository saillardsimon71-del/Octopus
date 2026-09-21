# Night shift autonome — canary V1

> **Atelier de maintenance optionnel, expansion gelée.** Les plans canary historiques ne sont
> pas la roadmap active. Utiliser seulement pour une modification motivée par une observation
> réelle ; conserver les limites et la promotion humaine. Le worker ordinaire ne charge plus
> `octopus.dev_worker` par défaut ; `night-shift` l'importe explicitement. Protocole économique
> et provenance par liens existants : `HANDOFF_WORK.md`.

Le mode `night-shift` exécute une petite file de tâches de développement sans surveillance, mais la V1 est volontairement **documentation-only**.

## Garanties de la V1

- Kilo/Step uniquement ;
- modèle configuré avec un identifiant `:free` ;
- fallback declarative désactivé ;
- maximum 5 tickets ;
- maximum 8 heures ;
- budget par ticket explicite (20 steps par défaut pour les revues docs, plafond 25 dans le runner) ;
- arrêt après 2 échecs consécutifs ;
- une allowlist exacte de fichiers modifiables par ticket ;
- `AGENTS.md` et `docs/ACCEPTANCE_GATES.md` interdits ;
- aucun fichier de test modifiable ;
- tests lancés avec les variables de type clé/token/secret retirées et un HOME temporaire ;
- `NO_CHANGE_NEEDED` est accepté uniquement si le ticket l'autorise explicitement et que Step le justifie ;
- kill switch local : créer `data/NIGHT_SHIFT_STOP` pour arrêter avant le ticket suivant ;
- aucun push ;
- aucun merge vers `main`.

Chaque ticket réussi produit d'abord un commit dans son worktree isolé. OCTOPUS fast-forward ensuite uniquement ce commit validé vers une branche locale dédiée :

```text
octopus/night-YYYYMMDD-HHMMSS-xxxxxx
```

La branche de départ et le dépôt de travail restent inchangés.

## Lancement

Depuis une racine Git propre et avec le Python de l'environnement OCTOPUS :

```powershell
python -m octopus night-shift --repo . --hours 8 --max-tasks 4
```

Pour vérifier le plan et les préconditions sans rien lancer :

```powershell
python -m octopus night-shift --repo . --dry-run
```

Le plan par défaut est `octopus/config/night_shift.json`.

Le rapport final est écrit sous :

```text
data/night-shift-reports/
```

## Pourquoi documentation-only au départ ?

Les tests exécutent du code du dépôt. Tant que les tests de code ne tournent pas dans un sandbox réseau/fichiersysteme suffisamment isolé, la première exécution sans surveillance ne doit pas autoriser un modèle à modifier du Python exécutable.

Après preuve de stabilité du canary, une V2 pourra ajouter un sandbox de tests et ouvrir des tickets de code bornés.
