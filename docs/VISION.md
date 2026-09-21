# Vision OCTOPUS

## Choix fondamental

OCTOPUS doit devenir un **atelier économique supervisé qui apprend du travail acheté ou utilisé**.
Il ne doit pas devenir plus autonome avant de savoir quoi exécuter et pourquoi un client le veut.

**MARKET FIRST. AUTOMATION SECOND. GENERALIZATION LAST.**

La formulation « moteur d'entreprises autonomes » était une ambition, pas une capacité commerciale
démontrée. Le dépôt fournit déjà de bonnes protections et une persistance réutilisable. Il n'établit
pas l'existence d'un marché, d'un paiement commercial ni d'un client satisfait.

## Boucle centrale

```text
besoin externe + limites humaines
 → expérience bornée
 → travail réel (manuel au départ si nécessaire)
 → livraison vérifiable
 → paiement / résultat client distincts
 → coûts + temps humain + inconnues
 → décision humaine : continuer / corriger / arrêter
 → bottleneck observé
 → automatisation ciblée
 → nouvelle mesure sur le même travail
```

**ONE REAL WORKFLOW → ONE OBSERVED BOTTLENECK → ONE TARGETED IMPROVEMENT.**
**NO ECONOMIC SIGNAL → NO NEW GENERIC INFRASTRUCTURE.**

Un signal peut réfuter l'offre. L'argent seul ne prouve pas la qualité ; la satisfaction seule ne
prouve pas la rentabilité. Une métrique de transport n'est ni l'un ni l'autre. À court terme, réduire
l'incertitude avec quelques interventions humaines vaut mieux qu'optimiser leur disparition.

## Architecture retenue, sans réécriture

```text
Humain : objectifs / limites / revue
                  |
    CLI + strategy + economy
                  |
  tasks / worker / actions / journal (SQLite)
                  |
       preuves et observations
                  |
      décision sur la même mission
                  +---- si nécessaire : atelier development.task
                        tests / evidence / revue / promotion humaine
```

Ce monolithe est déjà largement présent. Le réduire à deux nouvelles abstractions « core » et
« economic engine » n'ajouterait rien. Le travail consiste à nommer le chemin, borner les garanties
et connecter les observations existantes, pas à déplacer les modules pour embellir un diagramme.

## Ce qui survit / ce qui attend

- **KEEP** : queue durable, reprise, journal, stratégie, ledger, contrôle des actions et budgets.
- **SIMPLIFY** : rapport économique unique et points d'entrée documentaires.
- **FREEZE** : nouveaux développements vidéo/compute, GUI, canaris et module capabilities sans
  consommateur runtime identifié. Leurs garanties et consommateurs actuels restent préservés.
- **DEPRECATE** : autonomie générique comme objectif ; self-development chargé par défaut.
- **REMOVE** : chemin documentaire imposant GPU/vidéo avant toute expérience économique.
- **LATER** : prospection automatisée et généralisation, uniquement après preuves répétées.

Les personas historiques sont des configurations d'un runtime et d'un cycle Podalux, pas six
entreprises ni six autorités. Aucun nouveau rôle. Podalux n'est plus la verticale imposée.

## Première expérience, pas choix définitif de marché

En France, proposer à quelques boutiques multimarques un enrichissement factuel sourcé :
20 références, quelques caractéristiques manquantes, CSV relu, prix hypothèse autour de 99 € HT.
Le manque constaté sert à la fois de motif de contact et d'entrée de production.
Vérifier référence exacte et source fabricant ; laisser les faits introuvables inconnus.

L'humain choisit les contacts, respecte refus/opt-out, valide l'envoi et la livraison.
Ni scraper générique, ni connecteur Shopify, ni campagne, ni CRM n'est nécessaire.
Le prix et le taux d'intérêt sont des hypothèses jusqu'à retour réel.

## Connaissance et autorité

Coûts observés, qualité d'un outil, sources et prompts sont des connaissances révisables.
Permission d'envoyer/dépenser, secrets, plafond, règles de preuve et promotion sont gouvernés.
Un nom de producteur ou un hash ne transforme pas une observation en vérité indépendante.
Le noyau gouverné et ses limites sont détaillés dans `EVIDENCE_ACCEPTANCE.md`.

Code déterministe pour les règles ; LLM gratuits quand suffisants ; modèle puissant pour une
ambiguïté coûteuse seulement sous autorisation explicite. Aucun laboratoire de modèles à construire.

La prochaine modification devra expliquer **quel coût ou obstacle observé elle réduit**, puis
montrer la différence. Sans cela, la prochaine action est une observation, pas du code.