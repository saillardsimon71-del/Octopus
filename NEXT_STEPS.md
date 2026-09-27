# Prochaines étapes

État canonique: `docs/CURRENT_STATE.md`. Vision: `docs/VISION.md`.
Protocole économique: `docs/HANDOFF_WORK.md`.
Chantier constructeur actuel: `docs/migrations/CODEX_START_2026-09-27.md`.

## P0 - Préparation technique avant la première activité

1. Faire relire et checkpoint par l'hôte le diff E sur `prep/astra-local-orchestration`.
2. Consommer le résultat de validation finale consigné dans `CURRENT_STATE`, sans relancer le baseline.
3. Prochaine observation: SEARCH -> BROWSE -> citations sur une source pertinente au besoin client,
   avec revue humaine. Le probe Python réussi ne constitue aucune preuve économique.
4. Google seul via DDGS n'a pas fourni de résultat au probe; ne pas annoncer sa disponibilité.
5. Réutiliser les décisions P0/P1 de la matrice Hermes, sans relancer l'audit historique.
6. Extraire les commits produit relus hors de la branche du constructeur, puis mettre à jour la PR
   produit sans y inclure `.codex/`, les scripts Astra ou leurs tests.
7. Faire séparément le smoke test Agnes réel autorisé par l'opérateur. Ne pas le lancer dans les tests
   ou dans le chantier Astra.

## P0 - Première expérience économique supervisée

Après validation technique, suivre `docs/HANDOFF_WORK.md`:

1. vérifier l'offre sur quelques références réellement incomplètes et une source fabricant accessible;
2. créer une expérience bornée: au plus cinq contacts revus, sept jours et quatre heures humaines;
3. n'effectuer aucun contact, dépense ou action externe sans validation humaine explicite;
4. si commande, produire et vérifier un lot de 20 références puis livrer le CSV sourcé;
5. enregistrer séparément livraison, encaissement, acceptation, utilisation, coûts et minutes humaines;
6. lire `python -m octopus economy outcome BUSINESS EXPERIMENT`, puis enregistrer une décision.

Le dépôt ne prouve encore aucun paiement commercial. Ne pas inventer un client, un résultat ou une
preuve pour compléter le rapport.

## P1 - Après observation

- Répéter le travail seulement si l'intérêt et l'économie sont soutenables.
- Sinon changer l'offre ou arrêter; absence de données signifie améliorer la mesure.
- Identifier la phase qui consomme le plus de minutes ou produit le plus de corrections.
- Faire une seule amélioration reliée à cette preuve et mesurer avant/après sur le même périmètre.

## Gelé sans besoin démontré

- nouveau cerveau, planner, routeur LLM, mémoire ou UI Hermes;
- discovery MCP générique sans consommateur;
- campagnes automatisées, CRM, connecteurs e-commerce et nouvelle surface Web;
- nouveau moteur vidéo, provider GPU ou benchmark sans scénario réel;
- généralisation d'une capability, d'un scheduler ou de skills sans obstacle observé.
