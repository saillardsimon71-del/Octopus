# Prochaines étapes

État canonique: `docs/CURRENT_STATE.md`. Vision: `docs/VISION.md`.
Protocole économique: `docs/HANDOFF_WORK.md`.
Chantier constructeur actuel: `docs/migrations/FINAL_READINESS_ANTI_CONTAMINATION.md`.

## P0 - Préparation technique avant la première activité

1. Checkpoint E acquis: `057fc6e`; routeur E5: `dbf30d2`. Conserver la revue avant transfert produit.
2. Consommer le résultat de validation finale consigné dans `CURRENT_STATE`, sans relancer le baseline.
3. E5 terminée `inconclusive`: annonce acquise mais fermée, arrêt LLM gratuit sur 429.
   Ne pas relancer la mission. Correction de la vue BROWSE validée par 225 tests ciblés;
   exécuter la validation complète finale du nouvel état après checkpoint.
   Le prochain test économique proposé dans `CURRENT_STATE` reste soumis à revue humaine.
4. Google seul via DDGS n'a pas fourni de résultat au probe; ne pas annoncer sa disponibilité.
5. Réutiliser les décisions P0/P1 de la matrice Hermes, sans relancer l'audit historique.
6. Extraire les commits produit relus hors de la branche du constructeur, puis mettre à jour la PR
   produit sans y inclure `.codex/`, les scripts Astra ou leurs tests.
7. Faire séparément le smoke test Agnes réel autorisé par l'opérateur. Ne pas le lancer dans les tests
   ou dans le chantier Astra.

## P0 - Première expérience économique supervisée

Après validation technique, suivre le démarrage neutre de `docs/HANDOFF_WORK.md`:

1. définir avec l'humain un objectif neutre et des limites, dans un business neuf;
2. chercher une opportunité testable via SEARCH/BROWSE, sans reprendre E5 ni un ancien pilote;
3. qualifier seulement les sources acquises et consigner les inconnues, éventuellement `inconclusive`;
4. soumettre le prochain test à la revue humaine avant contact, dépense ou action externe;
5. si une expérience est autorisée, enregistrer séparément livraison, encaissement, acceptation,
   utilisation, coûts et minutes humaines, puis lire `economy outcome` et décider.

Le CSV, Podalux, la vidéo, les artisans et l'accessibilité sont des contextes historiques,
pas des objectifs hérités. Un compte connecté ou un site existant ne choisit pas l'activité.

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
