# Environnement de ressources reelles

OCTOPUS ne recoit pas de strategie commerciale. Il recoit un **environnement** : des ressources qui
existent vraiment, dans un etat donne, avec des frontieres que seul un humain peut franchir. Ce
document decrit la couche qui porte cet inventaire. Elle ne dit jamais quoi faire d'une ressource.

## Ce qui existait deja, et qui n'a pas ete duplique

| Besoin | Primitive reutilisee |
|---|---|
| Executer, journaliser, planifier | `octopus.tasks`, `octopus.worker`, table `events` |
| Attendre un humain et reprendre seul | demande humaine existante (`ctx.ask_human`, `octopus answer`) |
| Argent, canaux economiques, enveloppes de depense | `octopus.economy` (rien de l'argent n'est reecrit ici) |
| Valeur chiffree constatee | preuves `octopus.strategy` (nature `observed`) |
| Sources de donnees d'un business | `octopus.connectors` (sondes par domaine metier) |

Ce qui manquait : l'inventaire lui-meme. `octopus.resources` l'ajoute, et rien d'autre.

## Modele

Une ressource a une cle, un type, un libelle, un localisateur, des **capacites** (ce qu'elle permet
techniquement), des **frontieres humaines** (`login`, `oauth`, `2fa`, `captcha`, `kyc`, `signature`,
`bank_validation`, `legal`, `payment_method`) et un etat.

- `state` : `declared` -> `available` / `degraded` / `unavailable` -> `retired`.
  **`declared` signifie « declaree par l'humain, jamais constatee »**. Aucune ressource n'est reputee
  disponible sans constat.
- `access` : `none` / `observe` / `act`, meme vocabulaire que les canaux economiques.
  **Seul un humain accorde `act`.**
- `nature` : `observed` (une sonde ou un humain l'a constatee, avec `source_ref`), `unverified`,
  `hypothesis`.

## Declaration

`resources.toml` a la racine du projet contient l'inventaire fourni par l'humain. C'est une liste de
ressources, pas un plan : aucune ligne n'indique quoi en faire.

```toml
[[resource]]
key = "site_sitequivend"
kind = "site"
label = "Site publie (Netlify)"
locator = "https://sitequivend.fr"
capabilities = ["publier_page", "collecter_contact"]
probe = "http"
```

`python -m octopus resources sync` aligne la base sur ce fichier. Une declaration n'ecrase jamais un
etat constate.

## Sondes

Une sonde constate, ou avoue ne pas savoir : `ok=True` (disponible), `ok=False` (indisponible),
`ok=None` (**etat inchange**). Une sonde qui plante prouve seulement que la sonde a plante.

| Sonde | Constat |
|---|---|
| `http` | l'URL repond (401/403 = existe mais authentification requise -> `degraded`) |
| `dns` | le domaine resout |
| `env` | les variables d'environnement declarees sont presentes |
| `command` | les binaires declares sont installes |
| `host` | la machine qui execute OCTOPUS (systeme, coeurs, disque libre) |

Une ressource derriere un login humain n'a pas de sonde : elle reste `declared` jusqu'a constat.

## Frontiere humaine

`resources.request(key, need, question)` met en file la tache `resources.acquire`. Elle pose la
question, la tache passe en `waiting_human` (la tentative ne compte pas), et **apres la reponse la
tache reprend seule** : la sonde est repassee et l'etat mis a jour. C'est exactement le mecanisme de
reprise deja utilise par les autres taches, pas un second runtime.

## Commandes

```
python -m octopus resources sync        # aligne la base sur resources.toml
python -m octopus resources check [cle] # passe les sondes
python -m octopus resources list        # inventaire (--state, --kind)
python -m octopus resources blocked     # ce qui manque pour avancer
python -m octopus resources request <cle> <besoin> "question"   # frontiere humaine
python -m octopus resources grant <cle> --access observe|act    # accorde un acces (humain)
python -m octopus resources overview    # resume chiffre
python -m octopus schedule octopus resources.audit --every 86400  # constat quotidien
```

## Outils des agents

- `resources_status` : inventaire reel, etat constate, ce qui manque. Aucune consigne d'usage.
- `request_resource` : demande a l'humain de creer, connecter ou autoriser une ressource manquante.

Un agent qui veut agir sur une ressource passe par les regles existantes : `access = act` accorde par
un humain, executeur enregistre, depense couverte par une enveloppe (`octopus.economy`).

## Lien avec l'economie

`resources.promote_to_channel(cle, business)` cree un canal economique a partir d'une ressource et
garde le lien. L'argent, les autorisations de depense et les actions restent geres par
`octopus.economy` : la ressource ne les duplique pas.

## Ce que cette couche ne fait pas

- Elle ne choisit pas de strategie, de canal ni d'offre.
- Elle n'invente aucun etat : sans constat, `declared`.
- Elle ne cree pas de comptes separes par principe. Le compte Google unique reste le point d'acces
  tant qu'un besoin observe n'impose pas autre chose.
