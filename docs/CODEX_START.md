# Point d'entrée développement

## Vérifier, pas supposer

```bash
git branch --show-current
git rev-parse HEAD
git rev-parse main
git status --porcelain=v1
git log -10 --oneline
git diff
```

Vérifier aussi le main distant s'il est accessible, sans afficher de credentials.
La référence de cette intervention est documentée dans `CURRENT_STATE.md`, pas une branche GPU historique.
Préserver toute modification inconnue ; branche dédiée et revue avant fusion.

## Lire et choisir

1. `AGENTS.md` : règles durables.
2. `VISION.md` : choix market-first.
3. `CURRENT_STATE.md` : faits, tests et limites.
4. `HANDOFF_WORK.md` : unique protocole de première expérience.
5. `ACCEPTANCE_GATES.md` : preuves nécessaires, techniques ≠ commerciales.

Avant d'ajouter du code, demander : quelle observation justifie cette modification ?
Utiliser les preuves et liens existants. Ne pas refaire les audits ni créer un moteur parallèle.

## Vérifier et terminer

Tests ciblés après modification, puis suite complète si raisonnable :

```bash
python -m pytest -o addopts='' -q tests/test_economy.py tests/test_strategy.py tests/test_economy_act_cli.py
python -m pytest -o addopts='' -q
```

Aucun appel externe payant, aucun client fictif dans l'état réel.
Relire le diff contre la dette de complexité ; retirer ce qui ne sert pas le chemin principal.
Mettre à jour `CURRENT_STATE.md` et `NEXT_STEPS.md`, avec résultats exacts et preuves manquantes.