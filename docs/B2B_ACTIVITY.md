# Activité B2B

`octopus/config/b2b_resources.json` contient la déclaration préparée : périmètre,
quatre comptes, site public et proposition de mandat. Elle utilise les ressources,
comptes et mandats existants. Une déclaration ne connecte rien et ne confère aucun droit.

Dans Workbench, ouvrir **Paramètres**. Les comptes sont globaux : aucun rattachement
à une activité n'est demandé. Le LLM voit les ressources et choisit celles qui lui sont utiles ;
les actions conservent leur activité et exigent toujours le mandat correspondant.

**Ajouter un compte** demande seulement **Service** et **Nom (facultatif)**, puis
**Ajouter et connecter**. L'identifiant est généré. `octopus/config/web_accounts.json`
fournit les URL/domaines/providers des services connus. **Paramètres avancés** conserve
les réglages techniques et permet de configurer un autre service. Modifier une fiche
ne relance pas automatiquement la connexion.

1. Pour OVH Mail (Roundcube), Netlify, LinkedIn et TikTok, choisir **Ouvrir la connexion**.
   Chrome stable utilise un profil propre à cette ressource. L'humain réalise login,
   OAuth, 2FA et éventuel captcha. OCTOPUS ne reçoit pas les secrets.
2. Fermer les fenêtres Chrome de cette connexion, puis choisir **J'ai terminé - vérifier**.
   La vérification doit constater un compte connecté. Un refus ou une session expirée
   demande une intervention humaine ; aucune protection n'est contournée. Une erreur
   du backend ou un timeout ne prouvent jamais l'expiration : le profil et la dernière
   preuve sont préservés, l'accès agent reste bloqué jusqu'à une vérification réussie.
3. Vérifier simon@sitequivend.fr sur Roundcube, le site Netlify concerné et les profils sociaux dédiés.
   Modifier la fiche si nécessaire, notamment l'URL de vérification et les domaines.
   Le site public est déclaré séparément ; son administration passe par Netlify.
4. Choisir **Accorder un mandat**, cible `owned_account`, puis les effets
   `read`, `contact`, `edit`, `publish`. Sélectionner explicitement les clés
   `ovh-mail-pro,netlify-sitequivend,linkedin-b2b,tiktok-b2b`.
   Le mandat proposé couvre opérations B2B, contenu, démos, maquettes, audits et prototypes.
   Il reste révocable. Aucune réponse libre dans Humain ne l'accorde à elle seule.

Finance, dépenses, retrait, facturation, propriété, sécurité, secrets, nouveaux comptes
et engagements contractuels sensibles restent hors de ces mandats. L'activité choisit
offre, niche secondaire, message, canal et expérience à partir des preuves disponibles.

Les 19 fiches de compétences sont visibles dans **Paramètres** et via `resources_status`.
Elles décrivent prérequis, sorties et preuves ; elles n'ajoutent ni outils ni orchestrateur.
YouTube utilise `search`, `browse` et `record_observation` : lecture publique, résumés,
comparaisons, provenance et hypothèses. Une connaissance externe n'accorde aucun droit.

`economical` essaie des routes OpenRouter gratuites compatibles, sans banc par tâche
générique. Le coût résolu doit être attesté nul. Santé récente, capacité, protocole,
endpoint, quota et budget restent contrôlés. Deux routes et trois requêtes au maximum.
`browser.react_step` conserve sa qualification spécifique. Sans gratuit utilisable,
la demande humaine et la pause précèdent un éventuel choix humain d'un autre profil
et budget. Le profil économique ne peut appeler un modèle payant.

**Activité** montre chaque tentative, le coût et les refus. **Humain** montre les demandes.
Connecter et mandater les ressources avant une reprise économique. Ce chantier ne lance
aucun run économique et ne merge aucune PR.
