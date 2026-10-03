# Runtime naturel sur #136

Base exacte : `9f0312231fa35d0e45ce2f0d54e73dca1221b5b0`, branche
`fix/browser-benchmark-infra`. Changement en place, sans runtime parallèle.

Le modèle choisit ses outils, ses observations et sa stratégie. Une action browser
ordinaire nécessite le nom de l'outil et sa cible, par exemple
`{"tool":"browser_click","ref":"@e12"}`. Une saisie ajoute `text`.
Le business, le compte, le workspace, l'URL courante et le canal sont résolus par
le contexte réel. Les anciens paramètres explicites restent acceptés et vérifiés.

Une seule action non ambiguë peut être extraite d'un texte, d'une enveloppe simple
ou d'un littéral JSON imparfait. Alias, casse, refs et nombres équivalents sont
normalisés. Deux actions, paramètres contradictoires ou clés répétées donnent une
observation de refus sans effet. Aucun contenu n'est exécuté comme du code.
La gateway browser utilise le format natif annoncé par le provider, avec fallback
textuel tolérant. Un modèle sans JSON natif reste utilisable directement en texte.
JSON schema et tool calls natifs restent disponibles aux tâches qui en ont besoin.

Une identité activée, connectée et configurée par l'humain pour ce business donne
l'autorité opérationnelle ordinaire. Read, contact, publish et edit ne nécessitent
plus de mandats distincts. Les périmètres publics confiés restent limités à leurs
sources observées. Le runtime ne classe plus les pages selon des signaux commerciaux
ni les boutons selon un enum contact/publish/edit.

Paiement/engagement financier réel et création de compte restent réservés à l'humain.
Secrets, sessions expirées, challenges et ressources hors contexte restent des
limites techniques : aucune permission supplémentaire ne peut les contourner.
Les protections réseau, isolation, journal avant effet, empreintes, limites physiques
et reprise des effets ambigus restent actives. L'empreinte du formulaire est conservée
après exécution pour empêcher une deuxième soumission, même sans `expect`.
La suppression irréversible de l'identité du compte reste hors du périmètre des
opérations ordinaires réversibles ; supprimer un brouillon reste autorisé.

Le modèle peut conclure par un rapport libre. Le runtime conserve sa provenance
d'observations, sans certifier la vérité du rapport (`model_claim`). Il ne remplace
plus le contrôleur ni ne modifie le nombre d'étapes selon une heuristique de progrès.
Une image entre dans le contexte seulement lorsque le modèle demande une capture.
La conversation browser conserve actions, observations et captures après reprise.

`protocol_status` distingue immediate, normalized, format et ambiguous. Les traces
et checks du benchmark conservent la couche de l'échec : cognition, format, protocole,
outil, provider, runtime, infrastructure ou indisponibilité temporaire. Une stagnation
live reste observable, sans devenir un score cognitif automatique.

Browser-v1 garde ses fixtures, scoring, scénarios critiques, identité résolue et seuils.
Le 429 garde le retry borné de #136 ; une interruption crée INCOMPLETE_INFRA puis
NOT_RUN, jamais une série de zéros cognitifs. Les champs historiques et anciennes
preuves restent lisibles. Les anciens enums d'effet et traces d'escalade n'activent
plus de décisions par verbe ni de changement de contrôleur.

Foundation et les modules finance ne sont pas modifiés. Deux appels du superviseur
Pursuit transmettent le résultat déjà disponible au classificateur technique : un
refus de format ou une réconciliation d'effet ne devient plus une permission humaine.
Cette exception précise a été documentée avant patch. Les deux appels
d'autorisation d'actions transmettent désormais le caractère financier même pour
un ancien canal `act`, avant d'entrer dans le code finance inchangé.

## Validation et limites observées

Suite Windows : 2 404 tests passent, zéro échec, dix skips identiques à #136
(mêmes tests et motifs). Les 226 tests ciblés de protocole, browser, routing et
runtime passent également. Les fixtures réelles utilisent le backend agent-browser
installé dans le DataRoot ; aucun compte réel n'a été ouvert.

Browser-v1 réel, mêmes dix scénarios, deux répétitions, scoring et seuils inchangés :

| Contrôleur | #136 | Après | Requêtes avant/après | Appels moyens par succès avant/après | Qualification après |
| --- | ---: | ---: | ---: | ---: | --- |
| Space Bunny | 19/20 | 18/20 | 112 / 119 | 5,26 / 5,28 | Non : ambiguous_dom 0/2 |
| Qwen | 16/20 | 14/20 | 132 / 109 | 6,31 / 5,86 | Non : plusieurs scénarios critiques |

Les runs finaux 74 et 75 évaluent chacun vingt trajectoires et coûtent 0 USD.
Space Bunny : quatre décisions normalisées, quatre ambiguïtés refusées puis
récupérées, zéro rejet de format. Qwen : deux normalisations, quatre rejets de
format, onze erreurs provider 429 récupérées par le retry borné du même appel.
Les rejets récupérés ne sont pas des échecs de trajectoire. Les métriques de
normalisation n'existaient pas dans #136 : leur valeur avant reste inconnue.

Les premiers runs 70/71 ont révélé une lecture d'URL sans session browser après
conclusion prématurée ; ils restent INCOMPLETE, sans faux scores. Le défaut a été
corrigé. Les runs 72/73 ont évalué un transport texte imposé ; les résultats n'ont
pas justifié de conserver cette contrainte. La version finale préfère le JSON
natif annoncé et conserve le texte comme possibilité, sans obligation de JSON natif.

Le contrat cognitif est plus court, mais l'amélioration des performances n'est pas
démontrée par ces petites séries. Le code de production compte 334 lignes ajoutées
et 316 supprimées (+18 net), principalement pour récupérer les formes imparfaites
sans exécuter une intention ambiguë. Aucun candidat ne satisfait la qualification
critique ; le smoke Fiverr n'a donc pas été exécuté.
