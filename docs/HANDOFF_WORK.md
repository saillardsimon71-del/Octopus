# Golden path — première expérience économique supervisée

## 1. Périmètre, bornes et règles

**Une expérience = un lot client / une offre pilote**, pas un CRM.
Hypothèse : une boutique multimarque française achèterait un CSV de 20 références enrichies
avec quelques caractéristiques factuelles manquantes, chacune reliée à une source fabricant.
Prix à tester : environ 99 € HT. Ce n'est ni un tarif validé ni une vente acquise.

Au plus cinq contacts qualifiés et revus, sept jours, quatre heures humaines. La limite de temps
est surveillée par l'humain grâce au rapport ; ce n'est pas un coupe-circuit automatique.
Pas de publicité ni de dépense externe nécessaire. Aucun appel LLM ou envoi n'est lancé par les
commandes de tenue du pilote ci-dessous. Les états et preuves sont conservés dans SQLite.

Avant contact : vérifier le besoin, la référence exacte, la source fabricant et le destinataire
professionnel pertinent. L'humain valide le message et traite refus/opt-out sans relance imposée.
Si commande, convenir du périmètre, produire, vérifier puis livrer. Si aucun intérêt, mesurer le
signal avant de changer l'offre. Ne pas construire l'outil de production avant la première demande.

## 2. Créer l'expérience avec la CLI existante

Exemples Bash. Sous PowerShell, adapter uniquement l'affectation des variables et la continuation
des lignes. Remplacer les valeurs d'identifiants par celles réellement affichées, jamais les supposer.

```bash
B=pilote_fiches_fr
python -m octopus strategy add objective "$B" "Tester un besoin de données produit" --by human \
  --set statement="Vérifier si un client achète et utilise un lot sourcé" \
  --set success_criteria="Livraison, paiement et retour client distincts ; coûts et minutes relevés"
read -r -p "ID de l'objectif affiché : " O
python -m octopus strategy move objective "$O" "$B" active --by human
python -m octopus strategy add hypothesis "$B" "Le manque factuel vaut un service" --by human --parent "$O" \
  --set statement="20 références sourcées peuvent se vendre environ 99 EUR HT" \
  --set stop_criterion="5 contacts ou 7 jours ou 240 minutes humaines ; revue avant poursuite"
read -r -p "ID de l'hypothèse affiché : " H
DEADLINE=$(python -c 'import time; print(time.time()+7*86400)')
python -m octopus strategy add experiment "$B" "Un lot supervisé" --by human --parent "$H" \
  --set action="Qualifier, contacter après revue humaine, produire seulement après accord, livrer et mesurer" \
  --set metric=customer_acceptance --set target_value=1 --set stop_value=0 \
  --set budget_limit=0 --set budget_currency=EUR --set deadline_at="$DEADLINE"
read -r -p "ID de l'expérience affiché : " E
python -m octopus strategy move experiment "$E" "$B" running --by human
python -m octopus economy outcome "$B" "$E"
```

L'acceptation client est ici la métrique configurée ; elle ne remplace **pas** le contrôle du
paiement et des coûts. Les limites d'envoi/temps sont humaines, aucune campagne n'est créée.
Un budget de zéro interdit toute dépense mais ne réfute pas une expérience qui n'a rien dépensé.

Le signal prospect, l'offre et l'accord peuvent être des preuves textuelles `strategy add evidence`
rattachées à `experiment_id`, sans nouvelle entité prospect/order. Garder les pièces client privées
hors Git, avec références stables. Chaque lot ultérieur a sa propre expérience.

## 3. Produire et enregistrer des faits, jamais des exemples comme réels

Les commandes suivantes sont des **gabarits conditionnels** : ne les exécuter qu'après le fait réel.
Renseigner `SOURCE` avec une pièce consultable et `AT` avec sa date réelle (timestamp Unix), pas
un identifiant fictif. Ne pas conserver de contenu client confidentiel dans un dépôt public.

```bash
read -r -p "Référence réelle de la preuve de livraison : " SOURCE
read -r -p "Timestamp Unix réel de cette observation : " AT
python -m octopus strategy add evidence "$B" "Lot livré" --by human \
  --set nature=observed --set source_type=manual_review --set source_ref="$SOURCE" --set captured_at="$AT" \
  --set experiment_id="$E" --set metric=delivery --set value=1 --set observation="CSV sourcé transmis, pièce vérifiée"
```

Même commande pour chaque autre fait, en changeant métrique, source, date et observation :

| Métrique | Valeur | Sens |
|---|---|---|
| `delivery` | 1 / 0 | livré / non livré constaté |
| `customer_acceptance` | 1 / 0 | accepté / refusé par le client |
| `customer_use` | 1 / 0 | utilisé / non utilisé constaté |
| `human_minutes:research` | minutes >= 0 | temps réellement relevé pour cette séquence |

Sans preuve observée : `unknown`. Pour ces trois booléens, le dernier constat par date prévaut.
Un email accepté par SMTP ne prouve ni réception humaine ni livraison validée par le client.
Le CSV garde référence, caractéristique, valeur, unité, URL fabricant et date de consultation.
Pas de valeur devinée ; référence ambiguë ou source absente = information non livrable à signaler.

Phases conseillées pour le temps : `research`, `qualification`, `prospecting`, `customer_exchange`,
`production`, `verification`, `delivery`, `correction`, `incident`, `supervision`.
Chaque séquence a une source distincte (par exemple fragment d'un relevé de temps).
Les minutes observées s'additionnent ; les estimations/non-vérifiées ne comptent pas comme travail mesuré.
La CLI d'ajout de preuve n'est pas idempotente : ne pas rejouer une saisie réussie. Inspecter les IDs.

Pour corriger : `strategy move evidence ID BUSINESS retracted --by human`, puis nouvelle preuve.
Un retrait est explicite, l'historique reste conservé. Une estimation de coût peut être une preuve
`nature=unverified`, `metric=cost_estimate:EUR`, `value=...` avec sa source/méthode, jamais un cash-in.

## 4. Encaissements et dépenses : un seul ledger

Après consultation d'un relevé de paiement réel (une facture seule ne prouve pas l'encaissement) :

```bash
read -r -p "Montant réellement encaissé : " AMOUNT
read -r -p "Référence du paiement consulté : " PAYMENT_SOURCE
python -m octopus economy cash "$B" in "$AMOUNT" EUR customer_payment \
  --source "$PAYMENT_SOURCE" --experiment "$E"
```

Le paiement ne s'exécute pas dans OCTOPUS : cette commande enregistre un fait vérifié manuellement.
Catégories canoniques du rapport : `customer_payment`, `customer_refund` (sortie), `variable_cost`
(sortie, restitution possible par entrée). Garder des montants/devise cohérents, ne pas mélanger HT,
TTC et frais nets ; détailler la base dans la pièce et la revue. La CLI cash n'est pas idempotente :
contrôler le journal avant toute reprise. Pour un export réel, préférer `economy import` avec
`--category customer_payment --experiment "$E"` ; son dédoublonnage repose sur fichier + référence.
Ne pas importer ensemble virements clients, apports et frais sous une seule catégorie.

`contribution_by_currency` exclut apports et catégories non classées. Il sépare devises et estimations.
`cash_evidence` conserve les références, dates et montants du ledger pour la revue et la reprise.
Sans coût observé, la contribution reste `null`, pas une marge supposée de 99 €.
Même avec des coûts enregistrés, le périmètre est **cash enregistré uniquement**, hors temps
humain, créances, fiscalité et coûts manquants. Une revue humaine doit établir la marge contributive
complète, y compris un coût réellement nul et sa preuve, avant de conclure à la rentabilité.
Les coûts LLM calculés ne s'additionnent pas automatiquement aux coûts déjà payés du ledger.

## 5. Rapport puis décision

```bash
python -m octopus economy outcome "$B" "$E"
python -m octopus economy status "$B"
python -m octopus economy cycle "$B"
python -m octopus strategy list decision "$B"
```

`cycle` évalue la métrique et propose une décision, sans LLM. Il peut appliquer une politique de
réinvestissement **déjà autorisée par l'humain** ; n'en créer aucune pour ce pilote. `outcome` est
le rapport en lecture seule à préférer avant toute décision. Une cible atteinte ne commande plus
d'étendre automatiquement : revoir livraison, client, cash, coûts et temps.
À échéance sans mesure, `inconclusive`. Un zéro observé et daté peut en revanche réfuter la cible.

Pour arrêter tôt : `strategy move experiment ID BUSINESS cancelled --by human --note "Motif réel"`.
Créer une décision motivée via `strategy add decision ... --set decision="Arrêter/continuer/corriger"
--set rationale="Preuves, marge, minutes et inconnues"`, puis la lier à l'expérience et l'approuver
avec `strategy move decision ... approved --by human`. Le résultat accepté peut être suivi après
clôture de l'expérience : ajouter des preuves reste possible, le rapport reflète ces nouvelles observations.

## 6. Travail automatisé / effets externes : seulement si déjà utiles

Une tâche existante s'attache avec `strategy link BUSINESS experiment E task T executed_by`.
`strategy mission BUSINESS "Objectif borné" --experiment E` est facultatif et utilise le runtime
ORBIT/LLM configuré ; pas nécessaire pour ce pilote manuel.
Le temps wall-clock du worker n'est pas du temps humain.

Pour envoyer via OCTOPUS : canal actif, droit `act` humain, configuration SMTP ou formulaire borné,
clé d'idempotence obligatoire et contrôle de la réponse. Utiliser `economy act --help`.
Pas de transport configuré ici ; l'envoi manuel et sa preuve suffisent. Ne pas transformer
la validation du transport en succès client. Aucun paiement ou envoi automatique n'est requis.

## 7. Self-development : une observation, un changement, une nouvelle mesure

Pas de nouveaux champs obligatoires ni de graphe. Réutiliser preuve + lien :

```bash
python -m octopus strategy link BUSINESS evidence OBSERVATION_ID task DEVELOPMENT_TASK_ID motivates
python -m octopus strategy link BUSINESS experiment EXPERIMENT_ID task DEVELOPMENT_TASK_ID improves
```

Les objets doivent appartenir au même business. Une tâche qui améliore l'outil (`improves`) n'est
pas une tâche qui exécute le lot (`executed_by`). Les anciennes tâches sans ces liens restent valides.
Dans le goal : problème constaté, référence de preuve, minutes/coût/erreurs avant changement,
périmètre autorisé et mesure attendue sur le même travail. Puis mesurer après ; un gain supposé
ne vaut pas un gain observé. Les protections product_ticket restent obligatoires.

Pour charger explicitement cet atelier dans un processus dédié :

```bash
OCTOPUS_HANDLERS=octopus.dev_worker python -m octopus enqueue BUSINESS development.task --input '...JSON validé...'
OCTOPUS_HANDLERS=octopus.dev_worker python -m octopus worker --once
```

Le JSON suit les contraintes existantes (`docs/PYTHON_CANARY.md`, `docs/EVIDENCE_ACCEPTANCE.md`).
Ne pas exécuter le gabarit tel quel. `night-shift` charge aussi explicitement l'atelier ; les plans
canary historiques sont des outils de maintenance gelés, pas le backlog commercial.
Le worker standard ne prend plus les tâches de développement d'un processus frais non configuré.
Un processus qui importe volontairement `dev_worker` l'a chargé explicitement.

**Règle suivante : aucun nouveau code sans une observation qui justifie son coût de maintenance.**