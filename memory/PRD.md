# OCTOPUS — recalage économique supervisé

## Demande originale

« Lis et Applique prompt.md ». L'utilisateur a joint PROMPT.md :
https://customer-assets-cm19k8pv.emergentagent.net/job_cd7621ef-c6ce-4e53-bdf5-1aaba4ea5a62/artifacts/ti5uqwpt_PROMPT.md

Mandat : inspection forensique de main réel, décider/simplifier plutôt qu'ajouter de l'autonomie,
market first / automation second / generalization last, un golden path économique, task done
distinct des résultats client/argent, attention humaine mesurée, self-development latéral,
corrections de confiance locales, aucune nouvelle UI/framework/agent/CRM/DB ni réécriture.
Rapport demandé A–R, tests réels et chiffres de diff ; ne pas déclarer une réussite commerciale fictive.

## Choix et contraintes

- Dépôt confirmé dans /app. main local/distant = d2279f628703cb88ca1bf78fcb591389dcc9f764 à l'inspection.
- Arbre initial propre ; aucune DB utilisateur trouvée ; autres branches = historique seulement.
- L'utilisateur a explicitement autorisé les modifications dans l'espace courant malgré la limite
  de branche/commits plateforme ; sauvegarde ultérieure sur branche dédiée via Save to GitHub.
  Aucun push, commit manuel ni merge n'a été effectué.
- Maintenir l'architecture Python CLI/CustomTkinter/SQLite existante ; aucun serveur web requis.

## Décisions et changements

- Réutiliser strategy_evidence/strategy_links/ledger_entries, aucune table ou abstraction métier nouvelle.
- `economy outcome BUSINESS EXPERIMENT` : livraison, acceptation, usage, cash, contribution partielle,
  temps humain par phase, coûts estimés et inconnues ; ne pas inférer la rentabilité.
- Valeurs finies, mesures booléennes explicites, dernier constat et retrait, provenance par liens existants.
- Délai sans mesure = inconclusive ; budget zéro non consommé != échec ; unverified cash != zéro observé.
- `development.task` retiré du chargement ordinaire, chargement explicite/night-shift préservés.
- CLI économique ne charge plus les handlers vidéo ; imports reportés à la commande vidéo.
- Probe runtime/UI ne peut écraser les faits tests/Git/artefact du contrôleur.
- Création de canal act limitée à human, comme sa mise à jour.
- Documents canoniques réduits et alignés ; protocole supervisé dans docs/HANDOFF_WORK.md.
- Bug GUI bloquant révélé par suite : réponses Doctor/Orca tardives utilisent un widget détruit.
  Deux guards winfo_exists, régressions déterministes rouges avant correction puis vérifiées.

## Vérification

- Baseline ciblée : 228 tests.
- Premier lot modifié : 254 tests passés, 38.81s.
- Revue fonctionnelle : un test legacy refutes sans mesure corrigé intentionnellement, pas de changement
  de comportement pour obtenir du vert. Les autres remarques non bloquantes sont documentées.
- testing_agent : 195 ciblés passés ; 844 non-GUI passés, 4 skips ; collection GUI bloquée par libtk8.6.
- Correction environnement : customtkinter (déjà déclaré) installé par l'agent, libtk8.6 + ffmpeg ajoutés
  au conteneur de test, sans changer les dépendances ou tests produit pour masquer un défaut.
- Browser Chromium/Playwright installés, Xvfb utilisé. Suite sans exclusion initialement 870 passés,
  un échec GUI ; cas Doctor/Orca reproduits 2 échecs ciblés puis corrigés. Suite suivante 873 passés,
  un warning de GC Tk. Tentative de nettoyage de test inefficace retirée ; warning non masqué.
- Résultat final vérifié : 873 passés, 0 échec/skip, 1 warning Tk, 115.62 s ; git diff --check valide.

## Backlog

- P0 : lancer un pilote supervisé (20 références factuelles sourcées, hypothèse de prix ~99 EUR HT),
  au plus cinq contacts/sept jours/quatre heures humaines, puis décision fondée sur faits réels.
- P1 : identifier une phase réellement coûteuse, une seule amélioration liée à sa preuve, avant/après.
- P2 gelé : nouveau moteur e-commerce, CRM, campagne, GUI, Model Lab, capabilities et expansion GPU.

## Limites / prochaine tâche

La contribution est cash enregistré/classé uniquement, pas marge complète. Sources saisies à vérifier,
minutes manuelles, aucun gain de productivité ni revenu commercial prouvé. Acceptance n'est pas un
observateur indépendant du candidat ; revue humaine/promotion gouvernée conservées.
Rapport A–R : docs/CURRENT_STATE.md ; test credentials non applicables documentés.
Prochaine tâche produit : une expérience client réelle, pas une nouvelle phase d'architecture.
