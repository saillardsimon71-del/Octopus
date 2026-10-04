# Politique de routage LLM

Le runtime utilise deux fournisseurs : OpenRouter gratuit et DeepSeek direct.
Le profil normal est `zero_cost`. Les crédits OpenRouter ne constituent jamais
une autorisation de choisir un modèle payant. Une clé DeepSeek ne suffit pas
à autoriser le paiement : profil humain explicite et budgets restent nécessaires.

Les tâches déclarent leurs besoins dans le catalogue canonique. Les candidats
OpenRouter sont construits à partir des métadonnées de `/api/v1/models`, avec
prix finis nuls et identité fixe. Le nom du modèle ne détermine aucune capacité.
Une découverte ne crée aucune preuve. Les tâches sensibles ne gagnent pas
de nouveau candidat cloud.

Les preuves générales sont enregistrées sur l'identifiant exact et le modèle
canonique. Le seuil reste cinq essais, au moins 90 %, dans les 60 jours.
Le profil `economical` n'exempte plus OpenRouter de cette preuve : il lit la
tâche demandée et ses tâches d'évaluation correspondantes. Les derniers
résultats et la santé récente ordonnent les gratuits éligibles.

`economical` borne chaque appel logique à deux routes gratuites et trois
requêtes gratuites, au plus deux méthodes par modèle, puis DeepSeek seul
sous budget. Un repair local de caractères de contrôle ne fait aucun nouvel
appel. Les plafonds de sortie restent 500 tokens pour une action, 700 pour un
plan et 1600 pour une synthèse. `quality_first` conserve la préférence payante
explicite et peut se replier sur un gratuit qualifié si le budget bloque.

Avant une requête OpenRouter, les prix du catalogue doivent être nuls et
`provider.max_price` impose prompt=0 et completion=0. Après réponse, coût
observé fini exactement nul, fournisseur amont et identité attendue sont
obligatoires. Une réponse payante ou ambiguë est refusée et suspend OpenRouter.
Le contrôle de réponse survient après l'appel ; il ne peut annuler une
facturation incorrecte du fournisseur. Aucun retry payant implicite n'existe.

Les 429 respectent `Retry-After` numérique ou HTTP-date avec un plancher de
30 secondes. Un quota compte suspend les routes OpenRouter ; un quota pool
amont suspend le modèle. Les cooldowns persistent dans `llm_calls`.
Le transport ne fait aucun retry automatique sous `economical` ou `bench`.

`browser.react_step` exige toujours `browser.trajectory/browser-v1-fidelity-1`, quel
que soit le profil, le pin ou le baseline DeepSeek. Les dix scénarios doivent
être couverts, le score moyen atteindre 90 %, les scénarios critiques réussir
et la suite être complète. Ni le JSON, ni tools, ni un faible prix ne donnent
cette qualification. Le benchmark exige une shortlist et un plafond global
`--max-requests`, indépendant de `--max-cost`.

[Découverte, cache, identités et protocole Windows](OPENROUTER_CATALOG.md).

## Mesures historiques de septembre, sans valeur de qualification courante

Sur tous les appels de qualification de cette intervention, y compris les essais de réduction des prompts, le journal totalise 0.008951724 USD, uniquement sur DeepSeek. La dernière matrice consolidée compte 14/14 réussites pour DeepSeek Flash, 13/14 pour OmniRoute Groq, 12/14 pour OpenRouter Dots et 1/14 pour OpenRouter Qwen (429 inclus). Ces taux concernent les petits exemples du banc, pas une estimation de fiabilité en production.

Le parcours autonome réduit les messages d'action répétés, garde les deux derniers tours d'outil complets et transmet une sélection bornée des faits et URL antérieurs. La synthèse utilise les handoffs compacts. Les plafonds de sortie `economical` sont 500 tokens pour une action, 700 pour un plan et 1600 pour la synthèse. Sur un scénario public de huit observations, les appels DeepSeek ont utilisé 2062 -> 995 tokens d'entrée, 21 -> 21 tokens de sortie et 0.0006438 -> 0.0003237 USD ; les deux réponses donnaient le prix et l'URL attendus. La latence de cet échantillon est 2958 -> 3838 ms, sans gain démontré.
