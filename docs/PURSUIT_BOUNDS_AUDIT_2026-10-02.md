# Bornes pursuit — audit du 2 octobre 2026

## A. Verdict

**BLOCKING BUG FOUND AND FIXED.** Base exacte : #124, `275369275061ec51dc088753faa52c738ba4ca68`.

Une reprise discovery perdait le but ciblé après le troisième cycle. De plus, le code transformait une continuation en décision économique `pause` selon la frontière du batch. Le correctif conserve décision, prochain but et intention ; trois cycles restent la borne d'exécution et une reprise explicite reste nécessaire. La suppression des plafonds de coût LLM pursuit suit l'addendum explicite de l'opérateur, indépendamment de ce correctif.

## B. Inventaire des bornes

Les valeurs suivantes sont celles du head audité, sauf changement avant/après indiqué. Les contrôles de permissions et d'effets ne sont pas modifiés.

| Borne / source | Valeur et portée | Classe ; effet et état durable ; reprise / effet cognitif |
|---|---|---|
| `supervisor.PURSUIT_ROUNDS`, `run_pursuit`, `execute_pursuit` | 3 tâches/cycles par démarrage ou reprise | Quantum. Avant : troisième décision forcée en pause. Après : décision du modèle conservée, `cycle_limit_reached` si continuation, objectif paused avec note opérationnelle, aucune quatrième tâche automatiquement créée. Reprise explicite possible. |
| `supervisor._queue_pursuit`, `_pursuit_mission` | 120 s par mission/cycle ; 6 étapes par sous-agent | Temps coopératif et travail borné, pas deadline globale. Les valeurs sont aussi écrites dans l'input de tâche ; l'appel runtime applique les littéraux 120/6. Les changer seulement dans l'input ne configure pas pursuit. |
| Durée globale | Aucune deadline globale autonome de pursuit | Trois cycles ne garantissent pas six minutes réelles. Provider, HTTP ou Chromium en cours peuvent dépasser la cible ; lease/heartbeat n'interrompent pas une opération bloquée. |
| Budget pursuit | Avant : 0,20 USD par batch, variable `OCTOPUS_PURSUIT_LLM_BUDGET_USD` | Avant : reliquat calculé entre cycles, remise à neuf au round 1 d'une reprise, blocage LLM pouvant devenir waiting_human. Après : supprimé ; variable ignorée, nouveaux budgets de tâche/run NULL, coût descriptif uniquement. |
| Budgets LLM implicites | `agents.config.CYCLE_BUDGET_USD=1.00` ; catalogue global journalier 2 USD ; plafond journalier d'activité facultatif | Avant : gardes supplémentaires. Après : ne bloquent pas pursuit ; conservés pour les autres parcours. Aucun plafond de remplacement. Le worker identifie uniquement `supervisor.objective_work` avec `input.pursuit is True` et propage le mode de coût descriptif aux runs imbriqués. |
| `runtime.MAX_PLAN_TASKS` | 5 sous-tâches maximum par mission ; proposées en trop tronquées et signalées | Borne de ressources. Le plan retenu et les sous-tâches/étapes sont checkpointés ; le surplus n'est pas automatiquement exécuté à la reprise. Le modèle peut le reproposer dans un cycle ultérieur. Pas de minimum d'idées/sources. |
| `runtime._run_agent` | 6 décisions/action ou final par sous-agent pursuit | Ressources. Atteinte : `step_limit`, étapes intactes et résultat de mission `incomplete`, synthèse encore possible. Une reprise de la même collecte ne remet pas le compteur à zéro ; un nouveau cycle peut planifier la suite. Le planner voit cette borne avec sa portée d'exécution explicitée. |
| Anti-boucle / cache search | Avertissement après trois mêmes signatures outil + résultat ; cache de recherches normalisées dans la mission | Protection contre répétition, pas verdict business. Le modèle reçoit une invitation à changer d'approche/finaliser ; pas d'arrêt imposé ni ask_human disponible dans pursuit. Le cache ne compare pas la valeur économique des pistes. |
| `runtime._search`, `search.search_envelope` | 6 résultats ; DDGS/Brave/Tavily puis Bing Web pour business ; requêtes HTTP explicites 15 s | Ressources/provider. Erreurs séparées des items. Pas de nombre minimum de résultats. Les moteurs tiers gardent leurs propres comportements ; 15 s n'est pas une deadline globale de recherche. Erreurs techniques ne créent pas de permission. |
| `browser.fetch_public_http` | 8 redirections ; jusqu'à 9 requêtes, timeout connect/read 15 s ; corps 3 000 000 octets | Sécurité/ressources. Acquisition structurée et erreur conservées après retour. Le timeout read n'est pas une borne murale globale d'un flux progressif ; acquisition non terminée avant checkpoint peut être répétée. |
| `browser.acquire_public_page` / `BrowserTool` | Texte conservé 40 000 caractères ; goto 30 s, attente rendu 5 s, extractions body/selector 5 s ; redirects navigateur 10 | Ressources/provenance. Texte tronqué signalé, acquisition complète structurée dans le checkpoint. Startup Playwright et `route.fetch` ont aussi leurs timeouts de bibliothèque, non plafonnés par la deadline de mission. Pas de faux humain pour un échec de startup depuis #124. |
| `browser_workspace` / `agent_browser` | 300 commandes/tâche ; CLI ordinaire 30 s, ouverture 120 s la première puis 60 s ; polling 0,2 s, contrôle d'arrêt chaque 5 polls | Sécurité/runaway. CLI tué en cas d'arrêt ; résultat indéterminé, jamais succès d'effet inventé. Snapshot 6 000 caractères (complet 15 000), texte page 20 000. Plafond 30 effets et autres limites upload/download existent mais ces effets ne sont pas autorisés par pursuit. |
| Contexte supervisor | Leçons 8 ; preuves examinées 30 puis présentées 15 ; décisions 10 ; stratégies 12 ; études de capacité 6 ; observations précédentes 12 × 1 500 caractères, rapport 6 000 | Projection de ressources, pas effacement du journal. Extraits strings 240 en discovery / 1 200 en validation. Discovery condense les stratégies à 3 × 90 caractères, retire décisions/écarts ; le journal complet reste conservé. Cette fenêtre partielle peut limiter la mémoire présentée, sans interdire une piste. |
| Plafond de projection supervisor | Cible 64 000 caractères : retrait d'éléments entiers dans les listes | Ressources, pas plafond absolu du prompt : le squelette et la description humaine restent. Preuves et hypothèses en base intactes ; IDs transmis ajustés aux preuves réellement montrées. |
| Contexte runtime | Vue browse 6 000 caractères, handoff 8 artefacts × 1 200 ; ReAct compacté au-delà de 10 messages : début + 4 derniers + 8 anciens extraits de 200 caractères | Ressources. Payloads structurés et sources gardés hors de ces vues. Pas de contexte illimité garanti au LLM ; les frontières de batch testées n'altèrent pas le classement. |
| Représentation des stratégies | 12 propositions normalisées ; capacité/study : 12 capacités, 6 options | Structure/ressources, pas catégories économiques fermées. Propositions supplémentaires à répartir sur d'autres sorties ; pas de remplacement par une stratégie simplement disponible. |
| Structured output economical | Plan 700 tokens, action 500, synthèse/détermination 1 600 ; méthodes pertinentes tronquées à 2 | Ressources et contrat syntaxique. JSON invalide journalisé ; réparation locale sûre de caractères de contrôle, puis méthode suivante au plus. Pas de parser inventant champs/valeurs. Sous-tâches brutes conservées si synthèse indisponible. |
| Gateway economical | 2 routes gratuites / 3 requêtes gratuites maximum par appel logique ; DeepSeek seul payant ; retries SDK 0 ; jusqu'à 2 méthodes du modèle | Routing/provider, inchangé. Paid timeout 90 s, indépendamment du catalogue DeepSeek général 600 s / 2 retries. Cooldown 429 minimum 30 s ou Retry-After plus long ; provider injoignable 60 s ; OpenRouter gratuit non attesté 3 600 s. Erreurs/quota ne prouvent pas absence de marché. |
| Providers susceptibles d'être examinés | Catalogue : Groq/Cerebras 60 s ; OpenRouter/Gemini/Kilo 90 s ; Ollama/LM Studio 300 s ; OmniRoute env par défaut 120 s, health 2 s | Limites du transport, pas temps total strict d'un appel logique avec replis. Disponibilité réelle du PC/provider non testée. Les quotas fournisseurs externes ne sont pas connus depuis le dépôt. |
| Worker / recovery | max_attempts pursuit 1 ; lease par défaut 60 s ; heartbeat 20 s ; demandes humaines sans expiration par défaut | Scheduler/idempotence. Crash : failed après expiration, reprise explicite ; checkpoint de collecte réutilisé. Crash post-décision : même tâche et racine de coût depuis #124. Réponse humaine ne donne aucun droit. Pas de boucle automatique infinie après batch. |

Aucune durée/provider ne constitue une garantie d'interruption de tout blocage natif. Les nombres exacts de quotas externes et les fenêtres de contexte des modèles ne sont pas imposés par cette implémentation ; une erreur provider peut les révéler. HTTP 413 déjà constaté pour le même contenu/provider est mémorisé pendant 24 h pour éviter son renvoi.

## C. Origine démontrée

`git blame` et `git log -S` attribuent `PURSUIT_ROUNDS=3` et les 120 s à `00810976cc2ec945d48387e46e14c0f7258270e0` du 1 octobre 2026, « align OCTOPUS foundation and add bounded neutral startup ». Son `FIRST_START.md` dit déjà : reprise humaine après trois cycles, délai coopératif, choix d'implémentation/autorisation. **Pourquoi exactement 3 et 120 : origine/intention numérique non démontrée.** Workbench dit « délai cible de deux minutes chacun », pas six minutes strictes. Les 0,20 USD viennent de `029c8011` ; la documentation les décrit par démarrage/reprise, sans justification démontrée d'un optimum économique.

## D–F. Fin de batch et reprises

Sur #124, `execute_pursuit` remplaçait `continue` par `pause` au troisième cycle, ajoutait « Limite de trois cycles atteinte » à la raison et persistait cette décision. La détermination brute et le prochain but restaient en base, mais la reprise discovery passait à un but générique ; la correction de but existante ne couvrait que validation.

Après correctif : tâche done, objectif paused, décision continue et prochain but/intention conservés, flag technique `cycle_limit_reached`. Le modèle ne reçoit pas un épuisement de marché inventé. Le prochain clic explicite reprend le même objectif/activité ; les cycles locaux recommencent à 1. Le modèle peut continuer, pivoter ou faire une vraie pause. Une demande concernant seulement une capacité absente reste distincte d'une vraie permission, y compris au troisième cycle.

Scénario de six unités scriptées : possibilités → comparaison → approfondissement → incertitude → définition d'expérience → vraie permission d'envoi. Les deux modes discovery/validation atteignent l'étape 6 après reprise ; six acquisitions distinctes, 24 appels, six décisions sans double persistance, classement 1 conservé. La vraie permission finale reste waiting_human. Un autre scénario fait neuf unités sur trois reprises, sans recollecte ni budget recréé. Ce sont des fixtures de mécanismes, pas une méthode économique imposée ou un benchmark de créativité.

## G. Travail comparable, frontières différentes

Les seuls changements de `PURSUIT_ROUNDS` sont des monkeypatchs de test, sans configuration de production changée. Même séquence de réponses, mêmes six sources, mêmes limites par sous-agent et même nombre d'appels :

| Découpage simulé | Appels | Coût simulé | Décisions #124 | Décisions corrigées | Pauses opérationnelles | Humain |
|---|---:|---:|---|---|---:|---:|
| 3 batches × 2 | 24 | 0,024 USD | 3 continue + 3 pause | 6 continue | 3 | 0 |
| 2 batches × 3 | 24 | 0,024 USD | 4 continue + 2 pause | 6 continue | 2 | 0 |
| 1 batch × 6 | 24 | 0,024 USD | 5 continue + 1 pause | 6 continue | 1 | 0 |

Avant/après : mêmes next_goal en validation, même stratégie retenue, six sources sans duplication, six décisions, état final paused. En discovery, la perte de but de #124 est reproduite séparément. Après correctif, les trois parcours transmettent chacun 141 868 caractères cumulés dans la dernière validation ; petites variations locales de longueur dues aux métadonnées datées, pas davantage de travail. Les quanta modifient toujours le nombre de rendus de main, pas les décisions du modèle.

## H. Quatre états réels

1. **Quantum terminé** : objectif paused + décision continue + flag de borne ; reprise explicite. Aucune demande humaine d'autorité nécessaire. Workbench utilise encore le libellé général « En pause », accompagné du prochain but ; le détail technique est dans le résultat/journal.
2. **Pause cognitive** : décision pause du modèle avec sa raison, objectif paused ; aucun flag de continuation due au quantum.
3. **Frontière d'exécution** : task waiting_human pour une permission réelle ; checkpoint de détermination conservé. Une capacité absente est séparée dans l'assessment, sans dégrader le rang économique ni accorder un droit.
4. **Objectif terminé** : `strategy.objective.status=achieved` appartient au parcours de preuve/critère vérifié. **Pursuit lui-même n'a pas d'action finish/achieved** dans son contrat (`continue`, `pause`, `request_permission`) et ne clôt pas automatiquement un objectif économique. Task done ou synthèse valide ne valent jamais cash ni objectif atteint.

Ces états ne sont pas quatre nouveaux statuts logiciels. Le correctif retire la confusion quantum/décision, sans créer une nouvelle machine à états. `exhausted` dans le superviseur générique vise les tentatives d'autres objectifs ; il ne prouve aucun épuisement de marché.

## I. Coût LLM et addendum

Comportement #124 démontré : trois reprises avec coût simulé 0,12 USD par batch totalisent 0,36 USD ; chaque round 1 reprend 0,20 USD. Ce n'était donc pas un plafond d'objectif. Le journal quotidien 2 USD restait une limite distincte.

Comportement corrigé demandé : aucun plafond LLM run/batch/journée ne bloque pursuit. Le booléen de contexte transmis par le worker ne modifie pas le routing ou les droits ; il empêche seulement la recréation de plafonds de mission et la vérification de seuils monétaires LLM dans ce parcours. Les autres missions gardent leurs gardes de coût. Les actions économiques externes gardent allowances, permissions, idempotence et finance safety.

Test paid-only : neuf cycles, 36 appels DeepSeek simulés à 0,25 USD = **9 USD enregistrés**, sans stop ni humain dû au coût, budgets de nouveaux runs/tâches NULL. Workbench calcule le coût sur toute la chaîne de reprises, avec déduplication des racines. Les anciens coûts/runs/budgets restent lisibles ; une ancienne demande canonique uniquement due au plafond pursuit est annulée lors de reprise explicite, sans accorder de permission. Un test JSON malformé puis text valide dépasse également l'ancien seuil : deux appels distincts invalid/ok, coûts additionnés.

Le coût provient du provider quand disponible, sinon du tarif configuré appliqué à l'usage ; il ne faut pas appeler cette estimation une facture indépendante vérifiée. Reprises explicites répétées ne possèdent désormais **aucun plafond de dollars LLM** ; elles restent bornées en travail et soumises au routing/providers/timeouts.

## J. Timeout et points de sauvegarde

`cancel.scope(120)` place une deadline monotone ; `cancel.requested()` est contrôlé entre opérations. HTTP/SDK/Chromium synchrones ne sont pas préemptés par cette deadline. Le gateway peut encore parcourir ses replis déjà bornés au sein d'un appel logique engagé. Le heartbeat renouvelle le lease même pendant ces opérations ; ce n'est pas un watchdog mural de mission.

Checkpoint du plan avant collecte, puis de chaque étape retournée et de chaque sous-tâche terminée ; checkpoint collect_complete avant synthèse. Timeout après une observation retournée : étape conservée, done_degraded/timeout, pas de faux humain ; reprise réutilise l'observation sans nouvelle navigation. Test supplémentaire : réponse de synthèse arrivée après deadline, non promue en détermination durable ; reprise refait uniquement la synthèse. Cinq appels réels de transport simulé sont journalisés, dont deux synthèses, une seule source acquise. Ce coût supplémentaire est une nouvelle requête pour du travail non checkpointé, pas une double comptabilisation.

Un crash avant le checkpoint d'une lecture peut la répéter. Une décision déjà persistée reprend sans nouvelle synthèse depuis #124, vérifié à nouveau. Aucun timeout ne démontre que le marché/piste est épuisé.

## K. Croissance du contexte et coût estimé

Six unités comparables : environ 141,9 k caractères entrants cumulés, soit ≈35,5 k tokens avec chars/4. Planner : 4 352 → environ 9 936 caractères ; synthèse : 5 084 → environ 11 132. Ce cumul n'est pas un prompt unique. Contexte partiel borné, journal intégral persistant ; pas une mémoire LLM infinie.

Le coût 0,024 USD des fixtures est arbitraire et sert uniquement à vérifier les additions. Selon le tarif Flash du catalogue audité (0,15 USD/M tokens entrants sans cache, 0,60 USD/M sortants), 35,5 k tokens entrants représentent ≈0,0053 USD ; les plafonds de sortie des 24 appels totalisent au plus 19,8 k tokens, soit ≈0,0119 USD. Ordre de grandeur purement configuré ≈0,0172 USD hors pic pour ce volume sans cache et sans repli, pas prix live garanti. Cache, gratuits, usage effectif, pic et replis changent le résultat.

## L–M. Espace du prochain run et choix opérateur

Le parcours permet mécaniquement terrain déclaré, hypothèses libres, approfondissement, comparaison et définition d'une expérience par prochaines recherches/propositions. Il reste observation/analyse : envoyer, publier, acheter ou acquérir une capacité demande un autre parcours autorisé. Rien ne garantit la qualité d'un LLM réel depuis ces tests.

| Option | Risque / coût / continuité / observabilité / réversibilité |
|---|---|
| A — #124 exactement | But discovery perdu et décisions pause artificielles reproduits ; ancien plafond renouvelé. Pas recommandé et incompatible avec l'addendum. |
| B — batchs actuels avec correctif | Recommandé : trois cycles, revue de l'état, reprise explicite du même objectif si continuation utile. Coût cumulatif visible sans plafond arbitraire ; aucun budget nouveau, aucun droit ajouté. Arrêt/pause possible à chaque batch. Le LLM réel reste à observer. |
| C — configuration existante plus longue | Pas d'option publique de cycles/durée pour pursuit identifiée : constantes/littéraux, aucun flag CLI/env existant adapté. Les monkeypatchs de test ne sont pas une configuration recommandée. Runtime général n'est pas un batch pursuit plus long. |

Configuration recommandée : head corrigé empilé sur #124 ; DataRoot neuf ; activité humaine et description exactes de la mission ; economical existant ; trois cycles ; 120 s coopératives/cycle ; six étapes/agent ; permissions externes inchangées. Dans Workbench, sélectionner l'activité puis « Démarrer / reprendre l'activité », ensuite « Reprendre » sur **le même objectif** si la continuation reste utile. Pas de nouvelle mission/activité pour chaque batch. Ne pas répondre à une vraie attente humaine comme si c'était une reprise ordinaire.

Équivalent CLI de reprise, dans le même DataRoot déjà sélectionné :

```sh
python -m octopus pursue --business IDENTIFIANT_ACTIVITE --objective IDENTIFIANT_OBJECTIF
```

Aucune variable de budget pursuit requise. Aucune modification automatique du PC Windows effectuée.

## N–O. Correctif, tests et limites

Production : supervisor (quantum/intention/coût ancien), worker/journal/runtime (coût descriptif hérité, conservation de racine à la reprise), gateway (exemption de seuils seulement dans ce contexte), projection et ligne de coût Workbench. Aucun modèle, provider, routing, Foundation, finance safety, tool permission, planner ou scoring nouveau/modifié. Tests de l'ancien contrat monétaire adaptés, onze nouvelles régressions, FIRST_START et ce rapport actualisés.

Validation : **874 passed, 1 skipped** ciblés ; **1 949 passed, 97 skipped** suite complète. Base exacte #124 dans le même environnement : **1 938 passed, 97 skipped**. Les deux nouvelles assertions de décision continue au troisième cycle échouent sur #124. Les tests offline désactivent tout provider réel ; les nouvelles trajectoires refusent aussi les connexions réseau. Un test de retour de synthèse après timeout, extérieur au dépôt : **1 passed**. Les skips sont comparés à la base. Le lien de PR draft et le SHA sont fournis lors de livraison.

Une première exécution via un interpréteur sans pytest visible dans les sous-processus a fait échouer les mêmes sept tests dev_worker sur base et patch ; le lien de l'environnement Python de tests a été restauré hors dépôt, puis les deux suites complètes ci-dessus sont vertes. Aucun correctif de production dev_worker n'a été fait.

Aucun provider réel, réseau Web réel, run économique, dépense, contact, message externe, publication commerciale, compte, permission nouvelle, acquisition réelle de capacité ou merge. Publication uniquement du correctif en PR draft explicitement autorisée. Les tests prouvent les mécanismes, pas l'utilité commerciale de six étapes scriptées.
