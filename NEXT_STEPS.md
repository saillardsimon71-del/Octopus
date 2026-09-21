# Prochaines étapes

État vérifié : `docs/CURRENT_STATE.md`. Protocole : `docs/HANDOFF_WORK.md`.

## P0 — Une expérience économique supervisée

1. Vérifier l'offre sur quelques références réellement incomplètes et une source fabricant accessible.
2. Créer une expérience bornée : au plus cinq contacts revus, sept jours, plafond de temps humain
   de quatre heures ; aucun coût externe sans décision humaine explicite.
3. Contacter manuellement ou via le canal existant explicitement autorisé ; respecter refus et opt-out.
4. Si commande : exécuter un lot de 20 références, vérifier et livrer le CSV sourcé.
5. Enregistrer séparément livraison, encaissement, acceptation/utilisation, coûts et minutes humaines.
6. Lire `python -m octopus economy outcome BUSINESS EXPERIMENT`, puis enregistrer une décision.

Le dépôt ne prouve encore **aucun paiement commercial**. Ne pas inventer un client pour remplir le rapport.

## P1 — Seulement après cette observation

- Répéter le travail si intérêt réel et économie soutenable, sans généraliser.
- Sinon changer l'offre ou arrêter ; absence de données = améliorer la mesure, pas déclarer l'échec marché.
- Identifier la phase qui consomme le plus de minutes ou produit le plus de corrections.
- Une seule amélioration ciblée, reliée à la preuve existante ; mesurer avant/après sur le même périmètre.

## P2 — Gelé jusqu'à besoin démontré

- nouvelles surfaces canary, Model Lab, capability acquisition, MCP discovery ;
- refonte GUI / Web Control Plane ;
- benchmarks GPU, nouveaux providers et moteurs vidéo ;
- campagnes automatisées, CRM et connecteurs e-commerce ;
- extension des probes sans scénario de régression réel à couvrir.

Les protections existantes continuent d'être testées. G1 live et G3–G5 ne sont requis que si
l'expérience utilise effectivement un LLM ou du compute ; ils ne bloquent pas un pilote manuel.