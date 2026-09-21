# Evidence, contrat et autorité

## Rôle exact

Le builder produit un candidat. Un gate déterministe vérifie les assertions d'un contrat immuable.
Ce mécanisme est un **filtre technique nécessaire**, pas une preuve indépendante de bon produit
et encore moins de succès économique. `ACCEPTED` n'autorise aucune fusion sans revue humaine.

```text
problème observé → development.task → AcceptanceContract
 → candidat → tests → EvidenceBundle → GateDecision
 → ACCEPTED / REJECTED / UNCERTAIN → revue humaine → promotion
 → retour à la même mission → mesure avant/après
```

Les preuves commerciales restent dans `strategy_evidence` et les encaissements dans le ledger.
Pas de deuxième moteur d'acceptation commerciale ou de nouveau registre d'autorités.

## Plus petit noyau gouverné — responsabilités, pas nouveau package

| Frontière | Implémentation existante |
|---|---|
| Droits d'agir, canal et accès ressource | `economy.add/update_channel`, `resources.update`, `actions.propose` |
| Dépenses et plafonds | `economy`, `llm`/catalogue de politique, `compute_finance` et watchdog |
| Exécution des effets externes | `actions`, `browser_actions`, `smtp_executor`, `agents/web_guard`/browser |
| État durable et propriété d'exécution | `journal`, transactions/baux de `tasks`/`worker` |
| Sémantique et provenance des preuves | `strategy`, `acceptance`, collecte dans `dev_worker` |
| Modification/promotion du code et secrets candidat | allowlists/sandbox/environnement de `dev_worker`, `night_shift`, `promotion`, image et CI |

Cette frontière est logique et ne correspond pas à six fichiers isolés. Elle dépend de la DB,
du système hôte, de Python, de l'image approuvée et de leurs accès. Le code applicatif courant
n'est pas une enclave de sécurité. Les chaînes `actor=human` sont des conventions d'API locale,
pas une authentification : seules des personnes/processus autorisés doivent écrire la DB ou appeler ces API.

Scores de modèles, capabilities, prix observés, sources, prompts et performances sont des
**connaissances**, jamais une permission d'envoyer, dépenser, accéder à un secret ou promouvoir.
Le catalogue mélange encore politique et connaissances : le traiter comme gouverné, pas comme
une surface d'apprentissage autonome. Aucun nouveau mécanisme d'autorité dynamique.

## Contrat et bundle existants

Contrat JSON version 1, canonicalisé/hashé ; `product_ticket` exige un contrat explicite avant Kilo.
Chaque `must` a un identifiant et une assertion (`equals`, `not_equals`, `contains`, `not_contains`,
`set_equals`, `truthy`, `falsy`). Probes disponibles : `none`, `tk_navigation`.
Exemple minimal qui **ne prouve que le résultat des tests** :

```json
{"version":1,"id":"tests-only","artifact_type":"code","probe":{"kind":"none"},
 "must":[{"id":"tests-green","fact":"tests.passed","op":"equals","expected":true}]}
```

`REJECTED` : une assertion échoue. `UNCERTAIN` : fait manquant/incompatible sans échec certain.
`ACCEPTED` : assertions connues satisfaites, dans les limites de leur observateur.
Les bundles sont produits hors du clone candidat, append-only/content-addressed, liés à la tâche,
au contrat et à l'empreinte de l'artefact. `promotion.verify_git` vérifie Git, le bundle, rejoue
les tests et le gate ; il exige revue humaine et `auto_merge=false`.

## Vérification adversariale et correction locale

La collecte fusionnait arbitrairement le JSON du probe dans les faits du contrôleur. Un candidat
importé par le probe pouvait ainsi tenter de remplacer `tests.passed`, `git.changed_paths` ou
`artifact.fingerprint_sha256`. Désormais seuls les namespaces `runtime` et `ui` du probe sont
acceptés ; tout autre namespace provoque un refus. Les tests couvrent les trois écrasements et
un namespace inconnu. Le marqueur stdout n'est **pas** une signature cryptographique.

## Limites conservées volontairement

- `tk_navigation` importe le candidat et lit son attribut, par exemple `nav_buttons` : ce n'est
  pas une inspection visuelle indépendante. Une liste déclarée peut différer de l'interface visible.
- Le candidat partage le processus Python du probe et peut influencer son observateur ; la
  restriction des namespaces ne rend pas `runtime`/`ui` indépendants ni infalsifiables.
- Un hash prouve l'identité d'octets, pas la vérité, le besoin client ou la pertinence du contrat.
- Les chemins protégés ne couvrent pas automatiquement toute la fermeture des dépendances.
- Un oracle connu peut être optimisé ; des tests stables peuvent mesurer le mauvais objectif.
- Une `source_ref` saisie à la main ou par un exécuteur n'est pas vérifiée indépendamment par le rapport.
- Un exécuteur payant personnalisé peut encore mal gérer un timeout : le chemin générique libère
  sa réservation sur exception. Ne pas activer de nouveau transport payant avant traitement explicite
  des résultats ambigus ; le pilote conserve les actions humaines ou transports locaux existants.

Réponse proportionnée : pas d'auto-promotion, pas de nouvelles autorités, revue humaine du résultat
réel et protections actuelles maintenues. Aucun chantier de sandbox/oracle universel dans cette session.

## Ne pas étendre maintenant

Pas de Web Control Plane, Model Lab, nouveau reviewer automatique, MCP discovery ou acquisition
de capabilities. Le prochain probe doit répondre à une régression constatée sur une mission utile,
pas à la volonté de rendre le système d'évaluation plus impressionnant.