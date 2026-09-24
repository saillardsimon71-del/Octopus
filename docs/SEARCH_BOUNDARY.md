# Frontière SEARCH

Document de frontière, pas un manifeste. Il décrit le code tel qu'il est.

## 1. Ce que possède le provider, ce que possède OCTOPUS

| | Provider (Brave, Tavily, Bing RSS, Google News, Wikipédia) | OCTOPUS |
|---|---|---|
| Choisir les sources du web | oui | non |
| Décider de la pertinence d'une page | non | oui (selector, gate) |
| Donner la forme d'un résultat | non | oui : six champs, toujours |
| Rouvrir une page pour la prouver | non | oui (`browse`) |

Un provider ne décide jamais qu'un fait est établi. Il rend des items.

## 2. Objet machine, vue texte

SEARCH produit **une** sortie machine : une enveloppe structurée.

```python
{
    "query": "retards paiement PME",
    "effective_query": "retards paiement PME site:bpifrance.fr",
    "purpose": "general" | "business_signal",
    "items": [{"title": ..., "url": ..., "source": ..., "date": ..., "snippet": ..., "provider": ...}],
    "errors": ["Bing Web : TimeoutError ..."],
}
```

* les **six champs** d'un item survivent du provider jusqu'au selector, au cache et à
  `step.result_data` ;
* les **erreurs** de provider sont séparées des résultats : une panne n'est pas un marché
  vide, et zéro résultat n'est pas une panne ;
* le **cache** garde l'enveloppe complète ; le consommateur reçoit une copie ;
* le **texte** (`render_envelope`, `_search_view`) est une vue bornée pour le LLM et
  l'humain : extraits tronqués, URL intactes.

Règle : **STRUCTURE → TEXTE est une vue.** Le runtime ne refait jamais
STRUCTURE → TEXTE → REGEX → STRUCTURE sur son propre chemin machine. Le parsing
texte historique (`_search_result_candidates_from_text`) ne sert plus qu'aux valeurs
d'ancien format ; les tests l'interdisent sur le parcours natif.

Conséquence directe : une URL écrite dans un extrait, un titre ou un message d'erreur
n'est **pas** un résultat de recherche et n'est jamais proposée à `browse`.

## 3. Deux intentions, deux politiques de providers

`business_signal`

```text
Brave (si clé + cost class) → Tavily (si clé + cost class) → Bing Web keyless → arrêt
```

Pas de complément automatique par Bing News, Google News ou Wikipédia : une actualité
générale ou une page encyclopédique complète le bruit, pas un signal d'affaires.

`general`

Politique historique conservée : Brave / Tavily → Bing Web + Bing News → Google News /
Wikipédia si la recherche n'est pas contrainte par un domaine.

Aucun provider n'a été déclaré meilleur : aucun benchmark live n'a pu être exécuté
(pas de clé, pas de sortie réseau). Voir §6.

## 4. SEARCH n'est pas une preuve

```text
SEARCH   → des résultats (des URL proposées par un provider)
BROWSE   → une acquisition (page réellement ouverte, texte extrait, horodatée)
GATE #94 → une qualification (citations littérales retrouvées dans CETTE acquisition)
```

Un signal d'affaires n'existe qu'après les trois. Le gate reste intangible :
`_browse_result_meta`, `_canonical_evidence_url`, `_evidence_text`,
`_verified_browse_pages`, `_verified_browse_urls`, `_qualify_business_signals`.

## 5. Ce que ce chantier ne prétend pas

* aucune amélioration de pertinence mesurée ;
* aucun nouveau provider, aucun framework multi-provider ;
* aucune modification du gate de qualification.

Il prétend : préserver la donnée structurée, supprimer le reparsing destructif,
réduire les compléments non business, et rendre un futur changement de provider
localisé à `agents/search.py`. Le selector, dans le même esprit, ne garde que sa
responsabilité technique minimale : aucune liste lexicale métier, aucun bonus ni
pénalité d'hôte (voir `docs/ARCHITECTURE_BOUNDARY.md`).

## 6. État connu

`LIVE PROVIDER QUALITY = INCONNUE` : le sandbox n'a ni clé Brave/Tavily, ni classe de
coût SEARCH déclarée, ni sortie HTTPS vers Bing RSS. Toute affirmation de pertinence
live serait inventée.
