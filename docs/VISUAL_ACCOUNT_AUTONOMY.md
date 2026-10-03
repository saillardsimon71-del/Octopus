# Audit avant patch (base #132 exacte)

- `browser_snapshot` passe par Workspace → registre runtime → vue JSON bornée → message
  texte de `_run_agent`. Le résultat et sa provenance sont conservés dans les task_steps.
- Session expose la commande agent-browser `screenshot PATH [--full]`. Aucun screenshot
  n'est aujourd'hui un outil du runtime. L'ancien BrowserTool a un appel vision séparé ;
  il n'est pas le workspace durable, et ne doit pas devenir un second pilote.
- Gateway accepte déjà les parties OpenAI `text`/`image_url`, conserve les messages dans
  la requête, estime les images et journalise hash/usage/coût. Le besoin vision doit être
  appliqué aux messages du runtime, afin d'écarter les modèles text-only.
- Catalogue : capacités vision déclarées pour DeepSeek flash, Ollama Qwen3.5, Gemini,
  Groq Qwen et le routeur OmniRoute auto-free. Les routes GPT-OSS et dots ne déclarent
  pas vision. La disponibilité et les connexions réelles du pool OmniRoute ne sont pas
  prouvées par ce catalogue ; aucun provider ne sera interrogé pendant ce chantier.
- `_check_account` impose encore le marqueur à chaque observation : faux arbitre cognitif.
  Le vérificateur post-login expire sur marqueur absent : même défaut.
- `_effect`/`_form_effect` classent par libellé et refusent unknown sous mandat : classification
  utile à la sécurité, mais les libellés ne doivent pas être l'unique déclaration d'effet.
- HumanBrowserRequired → handler → waiting_human : challenge réel. Resources.acquire demande
  compte/login/2FA/mandat : frontière réelle. Actions refuse absence/révocation/périmètre,
  finance, secrets, ambiguïté : frontières réelles. Pursuit remonte les refus de politique
  et les demandes du modèle ; aucune réécriture pursuit n'est nécessaire.
- Domaines/proxy/taint, business/resource, mandat actif, sensibilité et journal avant effet
  sont garde-corps. Aucun de ces contrôles ne choisit la stratégie ni la pertinence d'action.

Implémentation minimale visée : un outil capture dans Workspace, images attachées en mémoire
aux requêtes runtime, fichiers bornés et références par tâche ; gateway filtre vision ;
vérificateur sémantique read-only avec observations libres et sortie enum validée ; déclaration
d'effet ordinaire par le modèle sous mandat, avec refus déterministes sensibles conservés.
Pas de schema, framework, workflow plateforme, changement Foundation ni migration.

## Chemin livré et audit final

1. **Image réelle** : `browser_screenshot` → Session `screenshot PATH [--full]` → PNG
   contrôlé → référence business/task/scope/hash → runtime charge les octets pour l'appel
   seulement → partie OpenAI `image_url` avec `data:image/png;base64,...` → gateway → transport.
   Le checkpoint conserve la référence, jamais cette partie multimodale. Le MIME JPEG
   historique reste conservé pour les autres consommateurs.
2. **Routing** : une partie image ajoute obligatoirement `vision` aux besoins. Un modèle
   text-only est inéligible, même pin explicite. Les alternatives gratuites du même task
   peuvent être utilisées si les préférences economical sont textuelles ; aucun fournisseur
   payant supplémentaire n'est autorisé. Le catalogue actuel retient les modèles fixes
   OpenRouter dont les métadonnées attestent vision et gratuité, ainsi que DeepSeek Flash
   dans la politique payante explicitement autorisée. Le choix dépend de disponibilité,
   évidence et profil. Le journal indique le modèle demandé/résolu, pas une disponibilité inventée.
   Les pools opaques sont exclus. La vision vient de `architecture.input_modalities`,
   jamais d'une déclaration d'environnement. Absence de route compatible
   → erreur explicite, jamais faux expired.
3. **Coût** : comptabilité et ledger existants, usage provider réel si fourni, estimation
   d'image existante pour l'admission. Aucun plafond monétaire pursuit ajouté. Les captures
   sont opt-in par le modèle, viewport par défaut/detail low ; full_page seulement demandé.
4. **Choix du capteur** : DOM, capture ou navigation sont des choix de prochaine action du
   modèle, sans séquence UI codée. Les limites d'exécution existantes restent en vigueur.
5. **Login humain** : HumanConnection et lancement Chrome stable natif inchangés. Aucun
   appel DOM/capture/gateway pendant ce parcours. La fermeture/verrou du profil est vérifiée
   avant le vérificateur séparé et avant l'utilisation d'une capture de compte au runtime.
6. **Indice optionnel** : configuration accepte un indice vide ; son changement seul
   n'invalide pas une session. Présent et constaté sans login/challenge : fast-path human_hint.
   Absent après settle : observations sémantiques, pas expiration automatique.
7. **État sémantique** : vérificateur read-only, même Session/proxy/profil ; enum validé dans
   la gateway. Le modèle choisit snapshot/capture/navigation dans les domaines configurés.
   `uncertain` permet davantage d'observations, jusqu'à la limite de six décisions. Résultat
   et provenance (`method`, state, run/business/resource, hashes/références) dans le JSON existant.
   Ce résultat est une interprétation, pas une preuve cryptographique. Backend indisponible
   → unavailable ; incertitude persistante/challenge → connection_required ; unauthenticated
   → expired. Les diagnostics techniques restent des codes sans contenu de page.
8. **Faux blocages retirés** : marqueur absent ne refuse plus chaque observation de compte
   ni ne force une reconnexion. Un contrôle ordinaire sans libellé reconnu peut recevoir
   `effect=contact|publish|edit` choisi par le modèle et vérifié contre le mandat. Les refus
   sensibles et les classifications déjà constatables ne peuvent pas être contredits par
   cette déclaration. Aucune micropermission ajoutée pour un effet couvert.
9. **Frontières humaines restantes** : login/OTP/challenge, ressource manquante, absence ou
   révocation de mandat, changement de périmètre, finance/sécurité/suppression, effet ambigu
   non réconcilié. `browser_verify(state=challenge|unauthenticated)` permet au modèle de
   signaler aussi une frontière vue à l'image. `uncertain` ne suspend pas immédiatement.
10. **Autonomie** : fixture compte générique, read/contact/publish/edit : le modèle simulé
    choisit naviguer, modifier un contenu, cliquer deux contrôles sans libellé métier pour
    publier puis contacter, demander une capture et conclure. Les trois effets sont
    journalisés avant dispatch ; confirmations textuelles existantes réconcilient les clics,
    et l'image atteint la dernière requête. Aucun waiting_human. La confirmation visuelle
    seule ne remplace pas la réconciliation des effets ambigus ni une preuve économique.
11–12. **Garde-corps et catastrophe empêchée** :

| Contrôle conservé | Perte empêchée |
| --- | --- |
| Mandat humain actif et effet couvert, recontrôlé avant commande | Agir après révocation ou sous un droit de lecture seul |
| Resource/business/domaines, proxy et taint | Compte d'une autre activité, accès Stripe depuis une tâche d'un autre compte, exfiltration |
| HTTP(S), DNS/IP publiques, réseau local refusé | Accès services internes et SSRF |
| Secrets/champs password/OTP et surfaces sensibles | Saisie/transmission de credentials, changements sécurité et finance non autorisés |
| Challenge structurel ou signalé par modèle | Résolution/bypass automatisé |
| Journal AVANT effet, empreinte, proposed/ambiguous, reprise | Double envoi ou répétition après réponse réseau incertaine |
| Capture contrôlée avant/après, taille/hash/espace/autorité de lecture | Image sensible, fichier forgé ou image d'un autre business/tâche |
| Séparation action observée / ledger économique | Transformer une publication ou image en cash/revenu fictif |

13. **Générique** : aucun fournisseur web, marqueur « Messages », bouton ou layout ajouté
    dans le code. Les regex conservées portent sur sécurité/classification d'effets, jamais
    sur une règle universelle d'authentification.
14. **Session existante** : identité/profil stable inchangés, aucun import ni migration.
    Cliquer « J'ai terminé — vérifier » réutilise le profil existant sans nouveau login.

## Fichiers et limites

Production : `agents/deepseek.py` (MIME), `agents/runtime.py` (outil et parties image),
`agents/gui/workbench_v2.py` (indice optionnel), `agents/task_handlers.py` (frontière login/challenge),
`agents/tool_registry.py` (erreur de capture technique ≠ permission manquante),
`octopus/browser_workspace.py` (capture et effets déclarés), `octopus/llm.py` (capacité et confidentialité
des erreurs multimodales), `octopus/resources.py` (interprétation et provenance),
`octopus/catalog.py` (ne pas attribuer vision à un pool automatique indéterminé),
`octopus/supervisor.py` (uniquement liste d'outils : capture). Aucun autre changement pursuit.

`Workspace._guard`, mandates, schema/journal, ledger, Foundation, lancement natif et profil
restent identiques à #132. Captures locales : PNG ≤8 MiB, ≤16 M pixels, permissions locales
restreintes ; les fichiers restent dans l'espace de leur tâche, sans base64 SQLite.
Les erreurs provider multimodales réaffichant la requête sont réduites à une classification
avant journal ou recovery. Capturer une interface ne prouve pas le succès de son action.

Les tests n'appellent aucun provider ni compte réel. Les modèles simulés prouvent le transport,
le routing et les invariants, pas la qualité d'interprétation d'un provider sur le compte réel.
Chrome/Windows, disponibilité réelle du pool vision et rendu réel du compte doivent être
constatés au smoke. Les domaines agent restent stricts, y compris pour les assets ; aucun
apprentissage de CDN. Aucun contournement de challenge. Les captures restent des données
non fiables, comme le DOM. Le champ texte d'authentification reste un hint humain fast-path.

Sources primaires consultées sans API provider :
[commande screenshot agent-browser](https://github.com/vercel-labs/agent-browser/blob/main/skill-data/core/references/commands.md),
[vision DeepSeek flash](https://api-docs.deepseek.com/guides/vision/).
Le binaire local figé **agent-browser 0.26.0 --help** confirme screenshot [path] et --full ;
aucun navigateur réel de compte n'est lancé pendant le chantier.

Validation locale finale : **2 094 passed, 97 skipped** (base #132 : 2 065 passed,
97 skipped ; identités des skips strictement identiques). **29 cas ajoutés**, fixtures
de prompts/listes d'outils adaptées aux capacités livrées. Sélection transversalement
hub/vision/gateway/recovery/Workbench/pursuit/finance/stack : **645 passed, 7 skipped** ;
dernière sélection sécurité/vision/native/gateway après durcissement : **181 passed**.
Les 7 skips browser workspace E2E locaux proviennent du backend absent ; aucun nouveau
skip ajouté. DOM fixe réellement exécuté sous Node, PNG réellement décodé dans la
requête gateway simulée. Ni provider ni compte réel ; le rendu CLI réel et Windows
restent à observer au smoke, pas présentés comme déjà validés.

## Prochain smoke Windows

1. Arrêter run et Workbench, charger le head exact de la PR ; relancer avec exactement le
   même `-DataRoot` et Python. Ne pas recréer/copier/importer/supprimer le profil.
2. Fermer toutes les fenêtres du Chrome humain associé. Dans Paramètres, cliquer directement
   **J'ai terminé — vérifier**. Aucun nouveau login ; les domaines/mandats restent les mêmes.
3. Si « Messages » n'est pas constaté, le vérificateur doit demander les observations utiles
   au modèle, puis afficher connecté via semantic_observation, ou une raison précise
   d'incertitude/challenge/indisponibilité. Examiner la provenance et les coûts du run account.verify.
4. Avec le mandat read déjà existant, effectuer seulement une observation compte ; le modèle
   peut choisir snapshot puis screenshot et doit pouvoir décrire le rendu. Vérifier que la
   route résolue est vision, que les fichiers appartiennent à cette tâche et que SQLite ne
   contient aucune image brute. Aucun message/publication/paiement nécessaire à ce smoke.
5. Un challenge arrête le parcours pour l'humain. Un backend vision indisponible n'est pas
   traité comme session perdue. Ne jamais forcer connected pour faire passer le smoke.
