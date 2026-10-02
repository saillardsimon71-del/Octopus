# Validation intégrée finale OCTOPUS — 2026-10-02

## A. Verdict

La pile #114 → #115 → #116 → #117 passe la validation intégrée hors réseau après
deux corrections locales dans `octopus/supervisor.py`. Aucun changement de finalité,
de classement économique, de permission, de Foundation ou d'architecture.

**Recommandation : NOT READY pour le premier run réel sur le poste Windows visé.**
La pile testée est cohérente; le smoke-check du DataRoot réel et du Workbench/session
Hermes reste non vérifié ici, comme déjà documenté dans
`docs/STABILIZATION_2026-10-01.md`. Aucun défaut restant de la pile n'est reproduit
dans les scénarios exécutés. Cette réserve porte sur l'environnement de lancement,
pas sur un revenu ou sur la valeur commerciale d'une stratégie.

## Références exactes

| Étape | SHA validé / point de comparaison |
|---|---|
| #114 | `e28289d5c7ce8047d44bc66464fdf17b704cd2b7` |
| #115 | `f89fb65df6bbc86329c2b03e28e9b87ee024c41c` |
| #116 | `bd8a09de84f37b4bb4c086d0a24a1eedcd3c8f06` |
| #117, début de cette validation | `c2985b7bfc118aeefd581eedea61749ba8259c37` |

HEAD local et head distant de #117 vérifiés avant travail. Les trois bases sont
ancêtres du head attendu. Les quatre PR sont ouvertes, en draft et non mergées.
Le diff #114 → #117 et les points d'intégration de #114 ont été inspectés.

## B. Scénarios exécutés

Le nouveau module `tests/test_octopus_stack_integration.py` ajoute **43 cas** :

- **14 variantes de la chaîne principale** : parcours normal, runtime natif simulé,
  coupures avant/après annotation, avant/après étude, avant/après création de décision,
  avant/après création de la tâche suivante, avant complétion du worker, pause,
  annulation et reprise explicite avec la politique pursuit réelle à un essai.
- **1 parcours complet par le gateway, les contrats structurés, le runtime et le
  worker réels**, avec transport HTTP factice et ressources temporaires.
- **1 contrôle de conservation de la politique du caller** lorsque `search` est absent.
- **6 transitions d'inventaire pendant un crash/retry** : missing ↔ permission_denied,
  available ↔ missing, available ↔ temporarily_unavailable.
- **9 erreurs techniques dans le runtime intégré** : timeout, DNS, 403, 404, JSON
  invalide, endpoint cassé, navigateur fermé, exception/crash, validation échouée.
- **11 frontières humaines conservées** : login, oauth, 2FA, captcha, KYC, signature,
  validation bancaire, légal, moyen de paiement, permission explicite, matériel physique.
- **1 concurrence avec quatre processus indépendants** : évaluation d'une expérience
  nouvelle, review, annotation A/B, étude du gap, tâche identique et worker factice.

L'historique principal contient une expérience fixture évaluée : 40 EUR de recettes
client, 5 EUR de coût variable, résultat métrique 35 EUR et 0,031 USD de coût LLM
simulé. L'évaluation conserve preuve, décision et review canonique. Une déclaration
LLM non vérifiée est présente mais exclue des preuves disponibles.

La détermination factice consomme effectivement la leçon rechargée pour proposer
A (cash_received/margin, rang 1, phone_call absent) et B (growth, rang 2, search
exécutable). Malgré un `next_goal` proposant B, A reste retenue. Le gap est étudié
et persisté, sans tâche d'acquisition, builder, permission ou effet économique.
Chaque nouveau cycle ajoute également un coût cognitif **simulé** de 0,01 USD :
il est conservé exactement une fois après interruption et reste dans le journal.

Les tests existants complètent ces scénarios : rollback/reprise de reviews,
réfutation et reconsidération avec preuve nouvelle admissible, source rétractée,
coûts imbriqués, limites de contexte, acquisition locale factice, validation/stale
acquisition, décisions defer/reject/human_required, CLI, garde financier et permissions.

## C–D. Défauts reproduits et corrections minimales

1. **Étude opérationnelle périmée après reprise du même task.** #116 reclassait
   l'exécutabilité depuis le registre réel, mais #117 rendait le vieux mémo
   `pursuit.capability_acquisition`. Exemple reproduit : capacité apparue mais
   non autorisée → annotation permission_denied, étude encore missing.
   Les six changements d'état échouent sur le head initial. Correction : recalculer
   l'étude pure et sauvegarder son état courant. La persistance idempotente existante
   réutilise une annotation identique et conserve l'ancienne lorsqu'un état change.
   La détermination LLM n'est pas rejouée, le classement ne change pas.

2. **Politique du caller perdue lorsque l'outil est absent.** L'inventaire construit
   localement par pursuit ne conservait pas la politique explicite pour les cibles
   absentes. `search`, autorisé dans PURSUIT_TOOLS mais retiré du registre, donnait
   authorized=false. Correction : réutiliser `capability_acquisition.system_inventory`
   avec exactement PURSUIT_TOOLS, plutôt que reconstruire son inventaire partiellement.
   capable=false, authorized=true et available=false sont maintenant distincts.

Le module d'intégration final, copié sans modification dans un worktree du head
initial exact `c2985b7`, donne **7 failures / 36 passed**. Les sept échecs correspondent
aux deux défauts ci-dessus. Après correction, les **43 cas passent**.

Le diff de production porte uniquement sur deux petites fonctions du superviseur :
9 lignes ajoutées, 10 retirées. Aucun nouveau mécanisme, schéma, builder ou executor.

## E. Invariants confirmés

- Les outils, executors, ressources/probes et politique du caller restent les autorités.
  Une disponibilité ne provient ni du modèle, ni d'une étude, ni d'un ancien mémo.
- Les capacités limitent l'exécution; A ne devient pas moins intéressante lorsque
  sa capacité manque. B n'est pas exécutée par substitution.
- Les études restent calculées et opérationnelles. Elles n'entrent pas dans les
  preuves économiques ni dans les leçons expérimentales admissibles.
- Pas d'acquisition automatique, de demande humaine technique, de budget ou de droit
  inventé par pursuit. Les vraies demandes humaines demeurent bloquantes à la reprise.
- Les coûts simulés et les preuves historiques restent conservés; les sunk costs ne
  deviennent pas une justification de poursuivre ou d'acquérir.
- Aucun doublon logique de review, étude identique, annotation identique, décision
  par task, clef de tâche ou exécution de la tâche fixture concurrente.
- Une étude différente conserve l'ancienne preuve immutable. Les retours à l'état
  courant sont reflétés dans le task step et dans l'annotation courante.
- Les handlers et registres de builders restent vides de builders de production;
  une acquisition ambiguë reste fail-closed selon les tests existants.

## F–H. Tests et résultats

Environnement : Linux, Python 3.12.14, venv isolé avec pytest. Bases SQLite et
DataRoots temporaires; seuls les tests utilisent les workers. Aucun transport réel.

| Suite | Résultat |
|---|---|
| Nouveaux cas sur le head initial c2985b7 | 36 passed, 7 failed (reproductions attendues) |
| Nouveaux cas sur code corrigé | 43 passed |
| Pile ciblée : intégration, learning, recovery, separation, acquisition | 297 passed |
| Moteur voisin : journal, worker, economy, finance, guards, gateway, Foundation start, actions/resources, Workbench, boucles existantes | 345 passed, 1 skipped |
| Suite complète corrigée | **1835 passed, 97 skipped, 0 failed** |
| Base #116 exacte, même environnement, suite complète | **1673 passed, 97 skipped, 0 failed** |

La différence avec #116 correspond aux 119 cas de #117 et aux 43 nouveaux cas intégrés.
Les skips concernent les prérequis de plateforme/navigateur/collection optionnelle.
Ni ffmpeg/ffprobe ni tkinter ne produisent d'échec dans cet environnement.
Compilation Python et `git diff --check` passent.

Commandes reproductibles :

```bash
python -m pytest -o addopts='' -q tests/test_octopus_stack_integration.py \
  tests/test_pursuit_learning.py tests/test_pursuit_recovery.py \
  tests/test_strategy_separation.py tests/test_capability_acquisition.py
python -m pytest -o addopts='' -q
```

## I. Pause, crash et reprise

Les fixtures de retry du même task utilisent deux essais **dans la base temporaire
seulement**; la politique de production n'est pas changée. Un scénario distinct
conserve le vrai max_attempts=1 de pursuit : expiration du bail → failed, reprise
explicitement choisie → nouvelle tâche liée à la précédente, sans doublon de clef.

Pause/annulation terminent le task précédent puis reprennent dans le même objectif.
Les cycles se terminent en paused, sans tâche active ni action d'acquisition.
Une demande humaine réelle reste waiting_human; les reprises sans réponse ne la
dupliquent pas et n'exécutent pas le travail.

Une seconde invocation de run_pursuit sur le cycle terminé ne produit aucune mission.
Le retry conserve la détermination LLM mémorisée et relit seulement l'état opérationnel
actuel. Un changement de capacité n'est jamais une cause de changement économique.

## J. Boucles et bornes inspectées

| Mécanisme | Borne vérifiée |
|---|---|
| Un lancement pursuit | 3 cycles, plafond cognitif 0,20 USD par défaut |
| Runtime | 5 sous-tâches au plus, 6 étapes par rôle, 120 s coopératives par cycle |
| Propositions / exigences | 12 stratégies, 12 capacités par stratégie; troncature → not_established |
| Acquisition | 12 capacités, 6 options par capacité; aucune file automatique |
| Contexte pursuit | 64 000 caractères, extraits de chaînes 1 200 caractères |
| Leçons | 8 dans pursuit; preuves par leçon 8, claims non vérifiés 4, tâches techniques 8 |
| Autres projections | 12 stratégies, 6 études, 15 preuves, 10 décisions |
| Observations précédentes | 12 résultats × 1 500 caractères, rapport borné avant les extraits |
| Routes economical | 2 routes gratuites, 3 requêtes gratuites, 1 repair JSON local; repli payant sous plafond existant |

Les corpus longs des tests existants et le scénario intégré vérifient le plafond
de contexte. Aucun feed-back étude → preuve économique → leçon n'est admis.
La persistance réutilise les annotations identiques au fil des cycles/retries.
Une replanification sans changement finit à la troisième itération.

Limite de volume explicite : learning_context et les lecteurs d'annotations parcourent
un historique fini avant de limiter la projection. Leur coût de lecture croît avec
le corpus; ce n'est pas une requête SQL plafonnée ni une boucle infinie. Aucune
refonte de stockage ou de sélection n'est ajoutée dans cette validation.

## K–L. CI et SHA

Sur le head initial exact : python-foundation et Compute finance safety sont verts.
Le statut externe Vercel est en échec, comme déjà documenté sur les bases empilées;
aucun déploiement ni changement Vercel n'entre dans cette validation.

Les correctifs, tests et ce rapport sont un commit supplémentaire sur la branche de
#117. Le SHA final et les conclusions CI associées sont donnés dans le compte rendu
de livraison après vérification du head distant. PR conservée en draft, sans merge.

## M–N. Limites et recommandation binaire

**NOT READY** pour une reprise réelle du poste visé tant que le contrôle déjà prévu
dans `docs/STABILIZATION_2026-10-01.md` n'a pas vérifié le DataRoot Windows existant
et le Workbench/Hermes de ce poste. Les contrôles sur copies fixtures passent, mais
ne renseignent pas l'état réel de ce poste. Aucun accès à ce DataRoot ni navigateur
Hermes installé dans cet environnement; aucune disponibilité live de provider testée.

Les limites préexistantes restent : délai coopératif, lecture publique pouvant être
répétée si interrompue avant checkpoint, fenêtre réponse LLM/journalisation nécessitant
une réconciliation de coût éventuelle. Les callbacks Python de builders sont des
extensions locales de confiance, pas un sandbox pour code tiers arbitraire; aucun
builder de production n'est enregistré et aucune acquisition réelle n'est déclenchée.

La qualité commerciale et un revenu réel ne sont pas établis. Les nombres financiers
de cette validation sont exclusivement des données de fixture, pas des encaissements.

**Confirmations :** aucun vrai run autonome; aucun provider réel; aucun achat;
aucun compte créé; aucun message, publication ou appel réel; aucune acquisition réelle;
aucune permission élargie; Foundation inchangé; aucun merge. Les appels GitHub servent
uniquement au dépôt et à la CI demandés, pas à une action économique.
