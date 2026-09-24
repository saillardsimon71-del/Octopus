# DIAGNOSTIC — Bottleneck BROWSE / WEB_GUARD après H3 Tavily (pré-fix)

> Document de travail qui accompagne la PR « fix: distinguish public browsing from
> authenticated account reads ». Rédigé avant toute modification, depuis `origin/main`
> (`e8f215a`, PR #97 mergée).

## SYMPTÔME

H3 (lockstep SEARCH→BROWSE avec Tavily) : le LLM browse une URL **publique** `reddit.com/...`
retournée par Tavily. `web_guard` marque alors **toute l'exécution** `account_read=True`.
Toute navigation ultérieure vers des sources publiques indépendantes (BOAMP, Indeed,
Welcome to the Jungle…) est refusée :

```
page hors comptes refusée : cette exécution a déjà lu un compte connecté (risque de fuite).
```

Résultat : pistes pertinentes trouvées par SEARCH, mais 0/3 signaux qualifiés faute de BROWSE.

## LAYER

Frontière BROWSE / permission :

- `agents/web_guard.py` — politique (classification, taint, garde anti-fuite) ;
- `agents/runtime.py::_browse` — orchestration du contexte d'acquisition ;
- `agents/browser.py` — mécanique d'acquisition (HTTP anonyme / Chromium éphémère / profil persistant) ;
- `agents/config.py::ACCOUNT_DOMAINS` — liste de domaines « capables d'héberger un compte ».

## OWNERSHIP

**Cette responsabilité appartient réellement à OCTOPUS.** La question « une donnée issue d'un
contexte authentifié a-t-elle réellement été lue ? » est une propriété externe vérifiable qui
doit être contrainte en code déterministe (règle C8) — pas déléguée au LLM ni au prompting.
Ce n'est ni une stratégie SEARCH, ni une question de prompt.

## CAUSE RACINE

1. `web_guard.classify(url)` (web_guard.py:103-116) renvoie `ACCOUNT` pour tout hôte dont le
   domaine est dans `config.ACCOUNT_DOMAINS` (config.py:103-106 : `stripe.com`, `youtube.com`,
   `*.google.com`, `fiverr.com`, **`reddit.com`**, `x.com`, `twitter.com`, `linkedin.com`,
   `gumroad.com`). C'est une propriété du **domaine** (« peut héberger un compte »), pas du
   **contexte d'acquisition**.
2. `runtime._browse` (runtime.py:434-441) convertit directement cette classe de domaine en
   **choix de navigateur** : `ACCOUNT` ⇒ `browser.new_browser(account=True)` ⇒
   `BrowserTool(persistent=True)` ⇒ `launch_persistent_context(profile_dir)` — le profil
   connecté (browser.py:330-350).
3. Le taint `BrowseState.account_read` est posé par deux mécanismes, tous deux déclenchés par
   la seule classe de domaine :
   - `web_guard.allowed()` (web_guard.py:136-147 — préemptif, **avant** la réponse, via
     `_browser_request_allowed(..., account_context=True)` runtime.py:421-431, câblé dans
     `BrowserTool._route`) ;
   - `web_guard.record(final, ACCOUNT, state)` (runtime.py:477-478, web_guard.py:149-152).
4. `web_guard.check()` (web_guard.py:119-133) refuse ensuite **toute** URL `PUBLIC` tant que
   `account_read` est vrai. Le `BrowseState` est partagé par toute la mission
   (`web_guard.session()` scopée à `run_mission`, runtime.py:1308) : une seule URL de domaine
   « compte » paralyse l'ensemble.

### Réponses aux 7 questions préalables

1. **Comportement exact de la contamination** : `browse(URL publique reddit)` →
   `classify=ACCOUNT` → navigateur à profil persistant → `allowed()`/`record()` posent
   `account_read=True` → `check()` refuse tout `PUBLIC` ultérieur (« risque de fuite »).
2. **Code classant Reddit ACCOUNT** : `web_guard.classify()` + `config.ACCOUNT_DOMAINS`.
3. **Événement marquant `account_read`** : `web_guard.allowed()` (pré-réponse, navigateur
   connecté) et `web_guard.record()` (post-navigation). **Jamais** une observation
   d'authentification (cookies, session, réponse serveur).
4. **Une URL publique Reddit suffit-elle ?** **Oui.** Aucun signal de session n'est consulté.
5. **Session réelle dans ce cas ?** **Non.** Sur le runner H3, le profil persistant est vierge
   (aucun login humain, aucun cookie). La page servie est objectivement anonyme ; le code ne
   vérifie jamais si le contexte possède des credentials.
6. **Risque réel ou confusion ?** Le garde protège un risque **réel** (exfiltration d'une
   donnée de compte vers une URL publique — prouvé par
   `test_injection_scenario_from_the_audit_is_blocked`). Mais en liant le taint au **domaine**
   au lieu du **contexte d'acquisition**, il tainte aussi des lectures objectivement anonymes :
   confusion domaine/contexte d'authentification → faux positif fail-closed qui bloque la
   mission (et envoie de plus ces pages publiques dans un navigateur headed coûteux).
7. **Frontière architecturale correcte** :
   - la classification de domaine sert à décider **quel contexte d'acquisition peut être
     nécessaire** (profil persistant capable de session vs moteur anonyme) ;
   - le taint doit refléter un **fait** : « le contexte de l'agent a reçu des données servies
     grâce à des credentials » ;
   - sans cookie persisté pour une origine, aucun serveur ne peut rattacher la requête à un
     compte : l'acquisition est **anonyme par construction** → aucun taint ne se justifie,
     quel que soit le domaine.

## INVARIANT À PROTÉGER

- Après une lecture **réellement authentifiée**, toute sortie publique reste refusée —
  y compris le taint préemptif pré-réponse dans le navigateur connecté (fenêtre de course).
- Les limites post-taint sur les URL de compte (query ≤ 100 chars, pas de redirection)
  restent appliquées.
- Blocages SSRF / schémas / hôtes privés inchangés.
- Une origine de compte **sans session** ne passe en anonyme que tant qu'**aucune** lecture
  de compte n'a eu lieu (sinon le chemin anonyme contournerait le garde anti-fuite).
- Gate #94 (provenance, observation vs inférence), budgets, dry-run, classification des
  actions externes : hors périmètre, intacts.

## CORRECTION MINIMALE

Propriété explicite du contexte d'acquisition : **anonymous acquisition** vs
**authenticated/account acquisition**.

- `browser.profile_has_cookies(url)` : fait externe vérifiable — le profil persistant
  détient-il des cookies pour cette origine ? Fail-closed (`True`) si indéterminable ;
  `False` prouvé si le profil n'existe pas ou ne contient aucun cookie pour l'origine.
- `_browse` : `ACCOUNT` **avec** credentials ⇒ comportement **historique inchangé**
  (navigateur connecté, taint préemptif + record, garde anti-fuite). `ACCOUNT` **sans**
  credentials ⇒ acquisition publique anonyme existante (`acquire_public_page` : `requests`
  sans session, fallback Chromium **éphémère non persistant**) avec un guard qui autorise
  explicitement la navigation vers ce domaine anonymisé, **sans taint**, et uniquement tant
  que `account_read` est faux.
- `web_guard.py` : **aucune modification** — la politique (taint + anti-fuite) reste exacte
  dans le contexte authentifié.
- Redirection d'une page publique ordinaire vers un domaine de compte : garde public existant,
  refus **fail-closed sans taint** (comportement conservé).
- Rien de spécifique à Reddit : le critère (présence de cookies pour l'origine) s'applique
  uniformément à tout domaine de `ACCOUNT_DOMAINS` — et à tout domaine ajouté demain.
