# Frontière OCTOPUS — contraindre les conséquences, pas l'intelligence

## Le principe

```text
OBJECTIF ÉCONOMIQUE
      ↓
LLM — raisonne librement (quoi chercher, comment, quand changer d'angle)
      ↓
OUTILS SIMPLES — search(query, site?) → items structurés ; browse(url) → page acquise
      ↓
INVARIANTS DÉTERMINISTES — permissions, coût, provenance, preuve, budget, journal
      ↓
MESURE ÉCONOMIQUE RÉELLE
```

Le code n'intervient dans le raisonnement que lorsqu'il protège une propriété nécessaire
du système. Tout le reste appartient au LLM.

## Qui décide quoi

| Décision | Propriétaire |
|---|---|
| quoi chercher, comment formuler, quand reformuler, quand abandonner, quelle piste approfondir | **LLM** |
| si une page est une opportunité d'affaires | **LLM** |
| si une action est autorisée | **code** (allowlist de tools) |
| combien elle coûte | **code** (budget de run, cost class provider) |
| si une action produit un effet externe | **code** (dry-run, exécuteur, accès humain) |
| ce qui compte comme preuve | **code** (gate #94) |
| ce qui est observé vs inféré | **code** (`nature`, `action_fields_nature`) |
| comment c'est tracé | **code** (journal, provenance) |

## Rôle exact de chaque pièce

**SEARCH** — outil bête. `search(query, site?)` → `[title, url, snippet, source, date,
provider]`. Il ne sait pas ce qu'est un bon business, une douleur économique ou un marché
intéressant. Le moteur fournit des documents ; le LLM comprend leur utilité.

**Sélecteur** (lockstep expérimental uniquement) — responsabilité minimale :

* l'URL vient **toujours** d'un résultat réellement retourné par SEARCH (jamais inventée) ;
* elle n'est jamais un hôte de redirection (`bing.com`, `news.google.com`) : une acquisition
  là-bas récupère une redirection, pas la page de l'éditeur ;
* une contrainte `site:` explicite n'est jamais violée ;
* à défaut, le meilleur résultat par recouvrement lexical avec la requête, avec un plancher
  de pertinence générale (un seul mot partagé ne suffit pas à forcer une ouverture).

Le sélecteur **ne décide pas** de la pertinence métier. Pas de liste de mots interdits, pas
de bonus de source officielle, pas de pénalité de homepage, pas de vocabulaire métier.

**Lockstep `search_browse_lockstep`** — `EXPERIMENTAL CONTROL`, pas un principe
architectural. Il force l'acquisition d'une URL réellement retournée, pour mesurer une
chaîne de preuve. Hors lockstep, l'agent ouvre n'importe quelle URL quand il le juge bon.

**Planner (ORBIT)** — reçoit l'objectif, les rôles et les critères de résultat. Il ne reçoit
**aucune** procédure de recherche. Il ne préconstruit pas de chaîne aval sans artefact amont.

**Prompt business signal** — objectif + critères de résultat + champs obligatoires. Aucune
consigne sur le nombre de termes, l'année, `site:`, les guillemets ou une séquence de
reformulation. La date est fournie comme **fait**, jamais comme instruction de requête.

**Gate #94** — intangible. Aucune preuve sans acquisition réelle ; citations littérales de la
même page ; provenance calculée depuis l'outil. Il contraint la preuve, il ne juge pas la
qualité de l'idée.

## Ce qui a été supprimé et pourquoi

Ces règles protégeaient des **runs particuliers**, pas des invariants :

| Règle supprimée | Run d'origine | Pourquoi ce n'était pas un invariant |
|---|---|---|
| stopwords `appel`, `mission`, `free`, `work` | #62 (Apple/`appel`, AlloCiné/`mission`) | bruit du provider keyless Bing RSS ; un vrai moteur ne renvoie pas ces résultats |
| mots de bruit `film`, `dictionnaire`, `définition`… + cas AlloCiné/IMDb | #62 / #93 | même cause : qualité du provider, pas règle métier |
| bonus `_EVIDENCE_HOSTS` (INSEE, BpiFrance, `.gouv.fr`…) | #88 | objectif « préférer les sources officielles » figé en ranking francocentré |
| pénalité `_LOW_EVIDENCE_HOSTS` (dont `www.soutien67.fr`) | #88 / historique | pouvait refuser d'ouvrir un domaine que l'agent avait explicitement demandé |
| pénalité homepage racine | #88 | heuristique de classement sans invariant |
| stopwords de pertinence génériques (19 mots) | #88 | ajustement de score sans propriété protégée |
| « privilégie {année} » dans le contexte de fraîcheur | #90 | contredisait le contrat business et a produit les requêtes du run #63 |
| bloc « STRATÉGIE DE RECHERCHE » (2–5 termes, `site:` en 2ᵉ intention, séquence de reformulation) | #92 | réapprend à chercher un LLM qui sait déjà chercher |
| double variant du contexte de date (business vs général) | #95 | patch d'une contradiction au lieu de la supprimer à la racine |

**Remplacé par** : un plancher de pertinence lexicale général (`distinct_overlap >= 2`) qui
rejette les collisions à un mot — y compris Apple/`appel` et AlloCiné/`mission` — sans aucun
vocabulaire métier. Voir `tests/test_prompt_boundaries.py`.

## Tests qui protègent la frontière

`tests/test_prompt_boundaries.py` vérifie que :

* le prompt business ne contient aucune prescription de recherche (longueur, année, `budget`,
  `site:`, guillemets, séquence conditionnelle) ;
* l'objectif et les critères de résultat sont toujours là ;
* le contrat reste sous 2200 caractères (garde-fou anti-accumulation de patches) ;
* la date reste un fait, jamais une instruction de requête ;
* le gate #94 échoue toujours fermé (URL non ouverte, citation inventée, page bloquée,
  citations de pages différentes) ;
* la allowlist de tools, le budget et la cost class restent appliqués ;
* SEARCH accepte n'importe quelle requête libre, y compris celles du run #63 ;
* une URL valide n'est pas bloquée à cause d'un mot métier ambigu ;
* aucun analyseur de stratégie de recherche n'existe dans le runtime.

## État connu

`LIVE PROVIDER QUALITY = INCONNUE` : aucune mesure de pertinence live n'est revendiquée ici.
Ce changement porte sur la frontière des responsabilités, pas sur la qualité des sources.
