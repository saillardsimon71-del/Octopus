# OCTOPUS — Centre de travail GUI

La fenêtre principale est le cockpit du control-plane. Elle regroupe l'observation et les commandes sans déplacer la logique métier hors des modules existants.

## Navigation

- **Cockpit** : vue d'ensemble, activité récente, agents et actions rapides.
- **Missions** : objectif ORBIT, file `octopus.tasks`, worker et suivi.
- **Agents** : activité récente de SOUT, CONVERT, FORGE, GROWTH, LEDGER et ORBIT.
- **Production** : offres, cycle, Studio vidéo, résultats et QC.
- **Humain** : demandes de validation/login/2FA/CAPTCHA ou questions bloquantes.
- **Navigateur** : URL observée, captures et ouverture du navigateur visible.
- **Système** : diagnostic cloud-first et pont Orca optionnel.

## Principes UX

La GUI ne lance pas de rendu lourd dans le thread principal. Les cycles, missions, workers et diagnostics sont lancés via les mécanismes existants ; les observations sont relues périodiquement depuis SQLite et l'état journalisé.

Les actions dangereuses restent protégées par les contrats existants : le bouton de publication reste un dry-run, le navigateur reste soumis à `web_guard`, et le pont Orca est opt-in.

## Lancement

```powershell
python run_gui.py
```
