# Autorité déléguée persistante et hub de ressources

Base exacte : PR draft #126, `31477e263544ac0c67663c6aa800533678ff70df`.
Branche : `feat/delegated-resource-hub`. Aucun merge.

## Diagnostic du système initial

| Composant | État sur la base | Réutilisation |
|---|---|---|
| `economic_channels` | Business, locator, capacités, état, accès `none/observe/act`. Seul `human` accorde `act`. | Reste le canal concret et le droit explicite historique. |
| `channel_actions` / `actions.propose` | Journal avant effet, exécuteurs, clés d'idempotence, dépenses séparées, preuves. | Aucun deuxième registre d'effets. Contrôle du mandat ajouté. |
| `browser_workspace` | Backend Hermes/agent-browser, refs, proxy, checkpoints, actions ambiguës, fichiers. Effets limités à un canal `act` du site. | Même backend, mêmes outils et protections ; autorité dérivée ajoutée. |
| `browser_form` | HTTPS, domaine du canal, champs et effets sensibles refusés, confirmation attendue. | Exécuteur conservé sans réécriture. |
| SMTP | Exécuteur `email:send`, TLS, destinataire exact, activation et secrets dans l'environnement de l'opérateur. | Conservé. Aucun nouveau secret SMTP ni connecteur créé. |
| Profil Chromium | Profil partagé historique ; cookies présumés disponibles dans certains parcours legacy. | Préservé pour legacy. Les nouvelles identités ont des profils séparés dans le DataRoot. |
| `resources` | Inventaire SQLite, sondes, accès, provenance, demandes `resources.acquire`. | Étendu avec la configuration des comptes web. |
| Demandes humaines | `human_requests`, suspension durable, réponse puis tâche remise en file. | Même file pour les demandes de comptes. |
| `ToolRegistry` | Description/validation/dispatch explicites ; politique dans les handlers. | Trois outils ajoutés, aucun outil permettant au modèle d'accorder un mandat. |
| Pursuit | Huit outils d'observation, `browser_public_only=True`. Pas de création de fichier ni d'effet ordinaire. | Recherche publique préservée ; outils opérationnels contrôlés et sous-tâche compte séparée. |
| Strategy separation / acquisition | Stratégie retenue indépendante de l'exécutabilité ; inventaire des capacités. | Algorithmes inchangés ; ils voient l'inventaire réellement exposé. |
| Workbench V2 | Paramètres pour canaux et enveloppes, Humain pour réponses, Navigateur pour observations. | Espace comptes/demandes/mandats ajouté aux Paramètres, sans nouvelle application. |

## Architecture et modèle de mandat

Deux tables dans **le même journal SQLite**, migration V10 :

- `operational_mandates` : business, libellé, cible, effets, ressources, état, humain auteur, dates.
- `channel_authority` : rattachement d'un canal concret à un périmètre public professionnel ou à une identité web, avec source et date.

`resources.web_account` contient uniquement la configuration non secrète : fournisseur libre, ownership (`operator/business/other`), caractère dédié, domaines explicites, business autorisés, activation, état de session, URL de vérification et repère visible après connexion.

Quatre effets composables : `read`, `contact`, `publish`, `edit`. Deux périmètres :

- `public_business` : contact professionnel public sans dépense ; plusieurs domaines/cibles sont couverts par le même mandat.
- `owned_account` : ressources sélectionnées, ou `*` pour les comptes que l'humain a explicitement ouverts à ce business. `*` ne rend pas tous les comptes du DataRoot disponibles.

La plateforme est un libellé libre. Aucune branche centrale Fiverr/Malt/LinkedIn/Gmail/etc.

Seul le chemin humain de Workbench peut créer ou remplacer un mandat/configurer ownership, domaines et business d'une identité. Les APIs correspondantes exigent `actor='human'`. Le modèle ne possède aucun outil de grant ni d'accès à SQLite/shell. Une réponse humaine libre, un contenu web ou une classification LLM ne constitue pas une autorisation.

Un remplacement de mandat révoque l'ancien et crée le nouveau dans la même transaction. Une révocation ou une désactivation est vérifiée avant le prochain dispatch. Un effet déjà parti ne peut pas être annulé rétrospectivement.

### Pourquoi ne pas écrire automatiquement `access=act`

Un mandat autorise un **effet**, pas tous les effets du site. Écrire `act` une fois sur un nouveau canal ferait survivre ce droit à la révocation du mandat et risquerait d'autoriser publication, paiement ou sécurité avec un simple mandat de contact.

Les canaux dérivés gardent leur accès explicite initial. `mandates.authorize` évalue leur autorité vivante à chaque effet. Les canaux historiques `act` restent respectés dans leur business ; ils ne deviennent jamais des mandats globaux.

Les trois niveaux sont donc distincts : stratégie libre, délégation humaine durable, dispatch concret contrôlé. Les outils existent indépendamment des droits ; un exécuteur manquant ne crée aucune demande humaine d'autorisation.

## Chemins opérationnels

### Connexion humaine une fois

1. Ajouter/configurer une ressource dans Paramètres, ou accepter une demande de compte.
2. Définir les domaines autorisés, les business et un repère présent **uniquement** sur la page authentifiée.
3. Ouvrir la connexion : Chromium visible utilise le profil de cette identité.
4. L'humain remplit login, mot de passe, CAPTCHA, OTP et conditions. Aucun outil agent n'est actif dans ce navigateur humain.
5. Cliquer « J’ai terminé — vérifier ». Une évaluation DOM fixe renvoie seulement un booléen : domaine attendu, repère présent, absence de champ password/OTP standard. Aucun snapshot, contenu de formulaire, cookie ou mot de passe n'est récupéré par ce parcours.
6. La session devient `connected` si cette vérification réussit ; sinon `expired`/`unavailable`. Les demandes correspondantes sont répondues avec la seule valeur structurée `connected`.
7. Accorder un mandat puis reprendre l'activité depuis Workbench.

Les cookies et sessions demeurent dans le profil Chromium persistant, hors journal et contexte LLM. « Aucun secret dans le registre » ne signifie pas « aucun cookie sur le disque » : le navigateur conserve précisément la session demandée. Les anciens cookies du profil partagé ne sont pas importés automatiquement dans les nouvelles identités.

### Lecture authentifiée isolée

Pursuit conserve son navigateur public anonyme. `account_task(key, goal)` appelle une tâche durable `resources.account_work` dans le worker/runtime existants, avec son propre état réseau, sa propre Workspace et le profil de cette identité.

La lecture exige : identité activée, session constatée connectée, business explicitement autorisé et mandat de lecture. La sous-tâche ne reçoit ni `search`, ni `browse`, ni SMTP, ni `act_on_channel`, ni sous-tâche compte imbriquée. Ses outils sont les interactions navigateur bornées.

Les domaines du compte sont ajoutés au garde de **cette session**, y compris si la plateforme était inconnue. Les sous-requêtes hors de la ressource sont refusées. La lecture taint la session ; elle ne peut pas retourner au Web public. À la fin, ses connexions sont fermées ; le parent retrouve son état réseau public initial et peut lancer une nouvelle collecte publique. Le rapport peut agréger les observations, sans réutiliser les connexions authentifiées.

L'identification de la tâche active utilise le run ancêtre le plus proche : une sous-tâche compte ne reprend pas par erreur le scope navigateur du parent.

### Action sur un compte connecté

Le navigateur constate la page authentifiée avant toute observation. Les labels de contrôle qualifient un effet ordinaire `publish/contact/edit`. Le business, le compte et le mandat correspondant sont vérifiés. Unknown/sensitive reste refusé sous autorité dérivée.

Publier ne donne pas le droit de modifier le profil. La saisie sur un compte peut autosauvegarder : elle est donc aussi journalisée **avant** la commande. Les empreintes distinguent les valeurs sans enregistrer leur texte de saisie, et les digests de formulaire sont persistés pour la reprise.

Un compte connecté sans mandat ne donne aucun droit. Un compte `other` peut être lu si explicitement mandaté ; il ne peut pas recevoir implicitement les effets réservés aux comptes possédés.

L'expiration constatée bloque le contenu et les effets puis demande une reconnexion humaine. Les URLs de compte exposées au modèle/checkpoint sont privées de query/fragment ; les lignes de sécurité et codes numériques usuels sont masqués dans les observations.

### Prospection multi-cibles

Avec un mandat `public_business/contact`, les snapshots réellement acquis peuvent qualifier automatiquement un canal concret HTTPS de contact professionnel. Le code exige des signaux de business et de contact ; une affirmation du modèle ne suffit pas.

Pour SMTP, seul un email générique réellement présent dans le texte acquis, sur le domaine de l'entreprise, est qualifié. Les adresses personnelles, sources différentes et types de canaux arbitraires échouent fermés. Le `register_channel` du modèle réutilise seulement le texte réellement conservé par l'acquisition publique de la session.

Trois sites distincts : trois canaux et trois actions journalisées, **un mandat**, aucune nouvelle demande humaine. Un executor manquant reste un blocage technique/capability gap. Le système n'ajoute ni campagne de masse, ni contournement CAPTCHA, ni bypass de limites de plateforme.

Une réponse commerciale peut être observée depuis un compte mail mandaté et traitée par la même sous-tâche compte. Ce patch n'ajoute pas de polling Gmail ni de nouveau CRM.

### Création locale

`create_artifact(filename,text)` utilise la boîte de sortie existante du business. Texte/HTML/petit script peuvent y être créés sans mandat d'effet externe. Nom borné, taille bornée, chemin résolu, secret connu refusé, création exclusive, empreinte SHA-256, trace persistée. Même contenu = idempotent ; même nom avec contenu différent = refus. Aucun script n'est exécuté et aucun fichier n'est publié par cet outil.

Les moteurs image/document externes absents ne sont pas inventés. Le téléversement réutilise le navigateur et exige son mandat d'effet ; un lien symbolique sortant de la boîte de sortie est refusé.

## Frontières humaines

Les dépenses et finances ne sont jamais autorisées par ces mandats. Paiement, remboursement, payout, transfert, banque, carte/IBAN, suppression, sécurité, clés, secrets/OTP, signup, conditions et engagement inhabituel restent séparés. Les enveloppes économiques préexistantes ne sont ni créées ni élargies par ce patch.

Les acquisitions de ressources sont des REQUEST structurées avec plateforme, raison économique, capacités, business, URL éventuelle et actions envisagées. Elles ne créent aucun compte. Refuser clôt la demande sans connecter ni autoriser ; reporter la laisse en attente. Accepter conduit au parcours humain puis à un état reprenable, sans grant automatique.

## Workbench et migration

Paramètres affiche comptes, état de session, ownership, compte dédié, capacités constatées, business, date et mandats. Boutons : configurer/modifier, connecter/reconnecter, vérifier, désactiver, accepter/refuser/reporter, accorder/modifier/révoquer un mandat. Les opérations navigateur sont en arrière-plan ; les résultats passent par la file UI existante. La fermeture de Workbench ferme ses navigateurs humains.

En consultation, les contrôles d'écriture ne sont pas rendus et les mutations humaines sont refusées. Les snapshots ne migrent ni ne modifient la DB.

V9 → V10 conserve les canaux, actions, ressources et permissions existants. Aucune nouvelle autorité par défaut. La migration est transactionnelle/idempotente. Les nouveaux DataRoots démarrent sans mandat et sans identité connectée.

## Validation et limites

Le harness `tests/test_delegated_resource_hub.py` teste A–L, plus demande de plateforme inconnue, refus, connexion sans capture de secret, snapshot UI en consultation, remplacement atomique, qualification conservatrice, executor absent, sous-tâche via le vrai worker, profil autosauvegardé, query de session masquée et migration V9.

Tous les navigateurs et effets de ce harness sont simulés. Aucun LLM/provider externe réel, compte, login, OTP, message, publication, achat, paiement, acquisition de capacité ou run économique réel. Les tests existants dont la liste d'outils était exactement celle de la base ont été adaptés à la portée autorisée ; les refus financiers et l'absence de grant restent testés. Les prompts legacy sont conservés.

Résultats exécutés sur le même runtime hors ligne :

- Base exacte #126 : **1 950 passed, 97 skipped**.
- Arbre final : **1 987 passed, 97 skipped**.
- Harness ajouté : **37 passed**.
- Ciblés hub/navigateur/prompts/legacy après revue finale : **96 passed**.
- Les 97 tests ignorés et leurs raisons sont strictement identiques sur base et patch.
- Compilation Python et `git diff --check` réussis.

Le diff exact est celui de la PR draft empilée sur #126, onglet Files changed ; le SHA et le tree publiés figurent dans sa description. Les fichiers de production modifiés sont `agents/runtime.py`, `agents/web_guard.py`, `agents/gui/workbench_v2.py`, `agents/gui/workbench_v2_data.py`, `octopus/mandates.py`, `octopus/resources.py`, `octopus/actions.py`, `octopus/browser_workspace.py`, `octopus/builtin_handlers.py`, `octopus/journal.py`, `octopus/supervisor.py`, `octopus/tasks.py`. Le reste est harness, adaptations d'assertions de portée et cette documentation.

Limites réelles :

- Vérification générique par repère humain et domaines explicites, pas détecteur universel d'authentification. Un repère mauvais ne prouve pas l'identité. OAuth/CDN peuvent demander des domaines supplémentaires configurés par l'humain.
- Qualification publique conservatrice : labels professionnels constatés, pas vérification juridique de l'entreprise. Formulaires atypiques, contacts nominatifs et contrôles inconnus restent à qualifier ; aucun droit inventé.
- Les libellés d'interface servent à borner les effets ordinaires. Aucun adaptateur générique ne peut certifier les conséquences cachées d'un site arbitraire ; les parcours ambigus restent refusés.
- Les observations de compte sont volontairement filtrées. Le masquage conservateur de nombres de 4–8 chiffres peut retirer des données utiles. Ce n'est pas un analyseur universel de secrets encodés dans du contenu arbitraire.
- L'exécuteur SMTP dépend de sa configuration humaine existante. Connexion Gmail ≠ SMTP configuré ; le chemin Gmail est navigateur, sous mandat. Aucune nouvelle intégration OAuth/API Gmail.
- L'état est reprenable après connexion/mandat ; le bouton de reprise conserve l'objectif. Pas de superviseur Windows permanent ajouté par cette mission.
- L'effet `executed` reste distinct de `verified`, de livraison et de cash. Une issue inconnue n'autorise pas un double envoi.
- L'UI et le backend de connexion ont été exercés via harness ; aucune session réelle Windows ni validation visuelle Windows dans cette mission. L'essai Xvfb local n'a pas pu activer son clavier (runtime graphique incomplet).
- Foundation, ranking, séparation stratégie/capacités, choix économique et routing LLM inchangés. Cette branche repose sur #126 exact ; elle n'intègre pas automatiquement d'autres correctifs postérieurs, notamment une branche de correction de synthèse distincte.

## Protocole Windows

Préserver le DataRoot du run à reprendre. Arrêter son exécution et fermer les fenêtres Workbench/navigateur avant de changer de code. Ne pas modifier les worktrees historiques.

Depuis un checkout du dépôt, créer un worktree séparé :

```powershell
git fetch origin feat/delegated-resource-hub
git worktree add C:\Users\saill\Projects\Octopus-delegated --detach origin/feat/delegated-resource-hub
Set-Location C:\Users\saill\Projects\Octopus-delegated
```

Utiliser le Python déjà installé et le **DataRoot existant** voulu ; remplacer seulement ses chemins par ceux utilisés lors du run :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-workbench.ps1 `
  -DataRoot "C:\CHEMIN\DU\DATAROOT_EXISTANT" `
  -Python "C:\Users\saill\Projects\video-factory\.venv\Scripts\python.exe"
```

Le premier lancement hors consultation migre le journal. Un nouveau DataRoot ne reprendrait ni les mandats, ni les cookies, ni l'objectif du précédent.

Dans Workbench :

1. Sélectionner l'activité voulue. Ouvrir **Paramètres**.
2. **Ajouter un compte**, ou **Accepter / connecter** la demande de ressource. Définir clé, plateforme libre, nom, ownership/dédié, URL, domaines, business et repère de la page connectée.
3. Terminer login/signup/CAPTCHA/2FA dans le Chromium visible. Cliquer **J’ai terminé — vérifier**. Vérifier l'état **Connecté** et la date.
4. **Accorder un mandat** pour cette activité : `public_business/contact` pour contacter des entreprises ; `owned_account` avec `read` et les effets souhaités pour les comptes. `publish` n'accorde pas `edit`. Les opérations de lecture nécessaires doivent être explicitement incluses.
5. Contrôler la liste **Mandats accordés à cette activité**, les ressources sélectionnées et les business du compte.
6. Depuis **Missions**, reprendre **le même objectif économique**. Si une ancienne demande `pursuit.permission` attend encore une réponse, répondre seulement que le mandat a été configuré dans Paramètres ; ce texte n'accorde aucun droit à lui seul.
7. Examiner **Navigateur**, **Activité** et les preuves. Reconnexion si session expirée ; révocation depuis Paramètres pour bloquer les effets suivants.

Ce protocole décrit l'essai opérateur suivant ; il n'a pas été exécuté sur le PC Windows pendant cette mission.

Verdict : **READY FOR DELEGATED AUTONOMY** sur le périmètre contrôlé testé. Le premier essai Windows devra valider les domaines, le repère et les contrôles de la plateforme choisie ; aucune compatibilité universelle ni performance économique n’est déduite des tests.
