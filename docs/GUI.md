# OCTOPUS — Centre de travail GUI

La fenêtre principale est le **Workbench** du control-plane. Elle sert de poste de travail quotidien : choisir un business, donner des objectifs, suivre les agents, produire les vidéos, traiter les handoffs et diagnostiquer le système sans déplacer la logique métier hors des modules existants.

## Concept Business Workspace

Le sélecteur **Business actif** fixe le contexte de travail. Un business contient uniquement de la métadonnée locale d'interface et un regroupement d'offres ; les données métier restent dans `jobs/`, `podalux.db` et les services existants.

Le registre est `agents/gui/workspaces.py` et se persiste dans `agents/data/workspaces.json`, qui n'est pas une configuration à versionner. Lorsque le fichier n'existe pas, les business sont déduits automatiquement du préfixe des `offer_id` présents dans `jobs/` et le registre peut ensuite être enrichi depuis la GUI.

Le contexte actif est mémorisé dans l'état SQLite sous `active_business`. `Tous les business` reste disponible pour les vues globales.

## Navigation

- **Cockpit** : vue d'ensemble, KPIs opérationnels, flux d'activité, agents et actions rapides.
- **Business** : portefeuille de business, offres rattachées et accès direct à Mission/Production.
- **Missions** : objectif ORBIT, filtres de file, worker et contexte business.
- **Agents** : charge récente, état et dernière activité de SOUT, CONVERT, FORGE, GROWTH, LEDGER et ORBIT.
- **Production** : offre active, cycle, Studio vidéo, ouverture du résultat et QC.
- **Humain** : demandes de validation/login/2FA/CAPTCHA ou questions bloquantes.
- **Navigateur** : navigation Chromium visible ou inspection contrôlée, URL et capture observée.
- **Système** : diagnostic cloud-first, journaux et accès au statut Orca.

## Commandes rapides

La barre de commande en bas accepte notamment `/business`, `/mission`, `/production`, `/agents`, `/human`, `/browser`, `/system` et `/doctor`. Un texte libre sans slash est envoyé à ORBIT ; `@SOUT ...` ou `@FORGE ...` cible directement le rôle demandé.

Les raccourcis `Ctrl+1` à `Ctrl+8` naviguent entre les pages.

## Principes UX

Les opérations longues restent hors du thread Tk principal. Les cycles, missions, workers, diagnostics et interactions CLI utilisent les mécanismes existants ; l'interface relit régulièrement SQLite et les journaux.

Le Workbench privilégie le **contexte actif** plutôt qu'une accumulation d'écrans séparés : sélectionner un business, observer son portefeuille, lancer une mission puis passer directement à sa production.

Les actions sensibles restent protégées : publication en dry-run, navigateur soumis à `web_guard`, MiniMax H3 cloud-only, idempotence RunPod, et Orca opt-in pour le développement.

## Lancement

```powershell
python run_gui.py
```

L'ancien import `agents.gui.app.main` reste compatible et ouvre le même Workbench.
