# Business « veille »

Deuxième activité sur le moteur OCTOPUS. Elle sert à vérifier que le moteur n'est pas lié à la vidéo : aucune ligne du moteur n'a été modifiée pour elle, hormis la mémorisation des étapes (`ctx.memo`), utile à toute tâche qui attend un humain.

## Déroulé d'une tâche `veille.brief`

1. Collecte : `agents.search.search_items` (Brave/Tavily si clé, sinon Google News et Wikipedia), sources numérotées sans doublon. Moins de 2 sources : échec explicite.
2. Synthèse : passerelle LLM, tâche `veille.brief`, profil `low_cost` (modèle local ou gratuit s'il a réussi le banc, sinon deepseek/flash), budget 0,02 $.
3. Vérification en code : chaque fait cite des numéros de sources existants, au moins 2 sources citées, aucune URL dans les textes. Sortie refusée sinon (2 tentatives).
4. Brief Markdown dans `businesses/veille/out/` (hors git), sources en lien.
5. Question à l'humain : transmettre le brief à SOUT ? Si oui, faits et pistes vont dans la mémoire de SOUT, marqués « NON VÉRIFIÉ ». La reprise après réponse ne refait ni la collecte ni l'appel payant.

## Commandes

```
python -m octopus enqueue veille veille.brief --input "{\"topic\": \"recouvrement\"}"
python -m octopus enqueue veille veille.brief --input "{\"query\": \"facture électronique 2026\", \"label\": \"E-facture\"}"
python -m octopus worker
python -m octopus ask
python -m octopus answer <id> oui
python -m octopus bench --suite businesses.veille.evals --models deepseek/flash --allow-paid --max-cost 0.02
```

Sujets et règles : `business.toml`. Aucune planification n'est créée d'office.
