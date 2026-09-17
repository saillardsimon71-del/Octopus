# OCTOPUS — Centre de travail GUI

La fenêtre principale est le cockpit du control-plane. Elle regroupe l'observation et les commandes sans déplacer la logique métier hors des modules existants.

## Navigation

- **Cockpit** : vue d'ensemble, activité récente, agents et actions rapides.
- **Business** : portefeuille de businesses, contexte actif et offres associées.
- **Intelligence** : boucle entrepreneuriale long-terme pilotée par ORBIT.
- **Missions** : objectifs ORBIT, file `octopus.tasks`, worker et suivi.
- **Agents** : activité récente de SOUT, CONVERT, FORGE, GROWTH, LEDGER et ORBIT.
- **Production** : offres, cycle, Studio vidéo, résultats et QC.
- **Humain** : demandes de validation/login/2FA/CAPTCHA ou questions bloquantes.
- **Navigateur** : URL observée, captures et ouverture du navigateur visible.
- **Système** : diagnostic cloud-first, journaux et pont Orca optionnel.

## Intelligence entrepreneuriale

La page **Intelligence** transforme la vision long-terme en missions explicites plutôt qu'en automatisations opaques. Pour le business actif, elle peut lancer avec ORBIT :

1. **Découvrir** — marchés, niches, problèmes et opportunités.
2. **Valider** — expériences simples, preuves et critères d'arrêt.
3. **Construire l'offre** — promesse, packaging, prix, preuve et CTA.
4. **Moteur contenu** — stratégie éditoriale et distribution multi-format.
5. **Construire le funnel** — contenu → conversation → qualification → offre, y compris les CTA à mot-clé lorsque les intégrations le permettent.
6. **Boucle client** — acquisition, onboarding, suivi, rétention, réactivation et expansion.
7. **Réinvestir** — règles de réallocation fondées sur les revenus/coûts réellement disponibles.
8. **Revue stratégique** — revue périodique, décisions, risques et prochains tests.

Les boutons de cette page utilisent le runtime de missions existant : ils ne constituent pas un second moteur d'agents.

## Ce qui est réellement connecté

Le cockpit peut déjà piloter les missions ORBIT, les tâches durables, la production vidéo cloud, le navigateur gardé, les handoffs humains et le pont Orca optionnel.

Le registre des businesses est une métadonnée locale d'interface. Il regroupe les offres connues et permet de changer de contexte sans dupliquer la base métier. Par défaut, les businesses existants peuvent être dérivés des IDs présents dans `jobs/`.

## Ce qui reste à brancher pour une autonomie business complète

Le dépôt ne possède pas encore une couche CRM structurée, une consolidation native des revenus/marges, ni les métriques propriétaires des plateformes sociales. La GUI n'invente pas ces données : elle prépare les missions qui pourront les exploiter dès que les sources seront connectées.

## Principes UX

La GUI ne lance pas de rendu lourd dans le thread principal. Les cycles, missions, workers, messages et commandes navigateur passent par les mécanismes existants ; les diagnostics et appels Orca sont traités en arrière-plan.

Les actions sensibles restent protégées par les contrats existants : publication = dry-run, navigateur = `web_guard`, rendu distant = idempotent, H3 = cloud-only, Orca = opt-in.

## Lancement

```powershell
python run_gui.py
```

Raccourcis : `Ctrl+1` à `Ctrl+8` pour les sections principales et `Ctrl+9` pour **Intelligence**.
