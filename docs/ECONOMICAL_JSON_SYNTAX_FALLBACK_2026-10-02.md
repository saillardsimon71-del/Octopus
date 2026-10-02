# Fallback syntaxique JSON du profil economical — 2026-10-02

Base exacte : `15a0eafe8c046d10c5d6dfc38e105810268bc596`, PR #118.
Branche : `fix/economical-json-syntax-fallback`.

## Cause confirmée

`octopus.llm.complete` conservait au plus deux méthodes structurées sous `economical`.
Sans schéma/outils utilisables, DeepSeek Flash expose `json_object`, puis `text`.
Une erreur de transport identifiant un mécanisme structuré incompatible permettait
déjà le passage à la méthode suivante. Une réponse reçue avec succès mais échouant
sur `json.loads` passait par la réparation locale des caractères de contrôle,
puis était journalisée `invalid`. Le test de passage à la méthode suivante excluait
ce cas sous `economical` : il exigeait alors `structured_error`, qui reste `None`
pour une erreur de syntaxe levée après le retour réussi du transport.

Les erreurs du run rapportées par l'opérateur (`Expecting ',' delimiter`,
char 5689 et char 5485) correspondent à cette classe. Leurs sorties brutes exactes
ne sont pas disponibles ici ; les tests reproduisent la virgule manquante sur une
sortie économique, en forme compacte et multiligne.

Le Workbench compte un appel `ok` comme repli lorsque sa justification contient
un échec antérieur (`echec` ou `sortie invalide`). Avant le patch, la méthode payante
restait `invalid` et aucune alternative réussie de la même invocation ne suivait :
`Replis observés : 0` est cohérent avec ce mécanisme. Le compteur compte les replis
réussis parmi les vingt derniers appels visibles ; il ne compte pas un repli encore
infructueux. Aucune modification UI nécessaire.

## Patch minimal

Un seul fichier de production : `octopus/llm.py`, trois affectations booléennes
et une condition étendue dans la boucle existante.

La dernière exception de validation est suivie comme erreur syntaxique uniquement
si elle est un `json.JSONDecodeError`. Si la réparation sûre existante produit ensuite
une erreur sémantique, cette qualification redevient fausse. La méthode suivante peut
être essayée après journalisation du premier échec lorsque la sortie est structurée,
qu'une méthode suivante existe et que la dernière erreur reste syntaxique.

Avant : sous `economical`, seule une erreur de mécanisme structuré ouvrait cette
alternative après un résultat `invalid`.
Après : un `JSONDecodeError` non récupéré peut aussi ouvrir cette alternative.
Les autres profils conservent leur comportement historique.

La borne `methods[:2]` reste identique : au plus une alternative sur le même modèle,
sans retour à la première méthode et sans troisième méthode. Les messages, le provider,
le modèle, les paramètres et le plafond sont conservés. Aucun parser permissif,
aucune virgule/accolade/valeur inventée, aucun appel de réparation LLM.

Les limites historiques de routes/requêtes gratuites et de provider payant restent
actives. Un échec sémantique n'obtient pas ce nouveau retry de méthode ; les replis
historiques entre candidats restent ceux du routing existant.

## Budget et observabilité

Le premier résultat conserve son coût et sa ligne `invalid`. La boucle vérifie
de nouveau le coût consommé et le coût maximal estimé avant la méthode suivante.
Si le budget restant est insuffisant, une ligne `blocked` est écrite et aucun second
transport n'est appelé. Ni plafond, ni finance safety, ni allowance ne sont modifiés.

Chaque appel garde sa méthode dans `justification.structured_method`, son statut,
son erreur, son usage et son coût propres. La justification du second contient la
première `sortie invalide [json_object]`. `invalid → ok` est donc reconnu par les
projections actuelles du journal et du Workbench, même pour le même modèle.
Dans le test reprenant les montants rapportés, les coûts simulés de 0.00594607 et
0.0060291 sont tous deux comptés : total 0.01197517 USD, affiché 0.011975.

Si la seconde sortie reste invalide, arrêt avec `InvalidOutput`. Le runtime conserve
les résultats et produit `synthesis_unavailable`, sans troisième appel de méthode.
Timeout, auth, 429, réseau, refus métier, permission, finance ou `ValueError` arbitraire
ne déclenchent pas ce nouveau fallback syntaxique.

## Reprise du même état, sans recollecte

Deux tests utilisent le vrai gateway, le worker, le supervisor, le journal et le runtime,
avec transports provider et Web simulés sur un DataRoot isolé. Deux observations Web
sont acquises une seule fois ; les trois cycles échouent en synthèse et restent
`done_degraded`, avec `synthesis_unavailable` et objectif `paused`.

Après reprise explicite du même objectif : zéro replanification, zéro appel du sous-agent,
zéro navigation supplémentaire ; seulement deux requêtes de synthèse (`json_object`
invalide, puis `text` valide). Les résultats précédents et les anciennes lignes de coût
sont identiques, les deux nouveaux coûts sont ajoutés. Le test couvre le checkpoint
complet et le parcours historique avec seulement `plan/results` dans la sortie dégradée.
Aucune modification de production pour la reprise : ce mécanisme existait déjà.

Cela valide les deux formes d'état simulées, pas le contenu du DataRoot réel de l'opérateur.
Aucun run réel n'est lancé par ce patch.

## Validation

- Gateway seul : **84 passés**.
- Nouveau scénario de reprise : **2 passés**.
- Tests ciblés gateway/structured output, pursuit/recovery, runtime, stack, finance,
  worker, Web guard et Workbench : **908 passés, 1 ignoré**.
- Suite complète sur la base exacte #118 : **1 856 passés, 97 ignorés**.
- Suite complète sur le patch : **1 877 passés, 97 ignorés**.
- **21 nouveaux cas**, aucun nouvel échec ; ensemble des 97 skips identique dans les JUnit.

Commande complète : `python -m pytest -o addopts='' -q --junitxml=/tmp/full-tests.xml`.
Le test de réparation unique existant est adapté pour vérifier les deux méthodes de
chaque modèle concerné, sans deuxième réparation locale ni modification des limites gratuites.

Foundation, prompts de #118, modèles, catalogue, routing général, budget 0.20 USD,
permissions, finance safety, UI, séparation stratégie/capacités et acquisition restent
inchangés. Seule `complete()` change dans `llm.py` ; parser, réparation, budgets,
classification/cooldowns et fonctions de routing sont identiques à la base.

Aucun provider réel appelé pour valider, aucun vrai run économique, aucune dépense réelle,
aucune acquisition réelle, aucun compte créé, aucune permission élargie et aucun merge.

**READY TO RESUME SECOND REAL RUN**
