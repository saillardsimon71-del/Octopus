# Prochaines étapes

**Mis à jour : 19/09/2026.**  
L'état détaillé est dans `docs/CURRENT_STATE.md`.

## P0 — verrouiller le compute avant de dépenser

1. **Cartographier tous les chemins GPU/cloud payants.**
   Chercher les appels directs aux providers, renderers et workers capables de créer une ressource.

2. **Forcer le chemin unique via `GuardedComputeManager`.**
   Aucun caller métier ne doit pouvoir lancer Salad/GPU.ai ou un futur provider payant directement.

3. **Rendre le watchdog réellement indépendant.**
   Définir son mode de lancement/restart et tester un crash du worker vidéo suivi d'un arrêt GPU.

4. **Faire un canary Salad minuscule.**
   Une allowance faible, pas d'auto-recharge, un job court et connu.

5. **Vérifier la facture réelle.**
   Comparer coût OCTOPUS calculé, durée provider et débit du solde.

## P1 — trouver le meilleur GPU en $/vidéo

Benchmark strictement identique sur les GPU disponibles économiquement intéressants :

- RTX 3090 ;
- RTX 5090 Laptop ;
- RTX 4090 ;
- RTX 5090 desktop ;
- autres offres uniquement si elles améliorent le coût réel.

Mesurer :

```text
cold start
image pull
poids/cache
préparation
inférence
temps total facturé
succès/échec
coût réel
coût par vidéo
```

Le choix final se fait sur **$/vidéo réussie**, pas sur $/h.

## P1 — rendre les machines éphémères robustes

Décider puis implémenter :

- image OCI Wan/worker reproductible ;
- version du modèle et dépendances verrouillées ;
- récupération des poids déterministe ;
- cache si réellement rentable ;
- bootstrap idempotent ;
- healthcheck ;
- résultats externalisés avant arrêt ;
- aucun état critique seulement sur le disque éphémère.

Objectif : une machine fraîche doit pouvoir démarrer sans bricolage manuel.

## P1 — réutiliser intelligemment un GPU chaud

Le coût cible < $0.01/vidéo sera plus réaliste si plusieurs vidéos sont produites dans une même session GPU :

```text
cold start une fois
→ charger le modèle une fois
→ batch de jobs
→ arrêter dès queue vide
```

Le breaker doit toujours conserver un cap batch et global.

## P2 — boucle business mesurée

Après stabilisation du compute :

- publication réelle ;
- analytics ;
- coût d'acquisition ;
- cash observé ;
- expériences stratégiques ;
- réinvestissement.

Ne pas optimiser dix chaînes avant qu'un premier moteur contenu → audience → revenu soit mesuré.

## Conditions avant merge de la PR compute

- CI compute verte ;
- `video-foundation` verte ;
- pas de bypass payant connu ;
- watchdog testé après restart ;
- doc à jour ;
- idéalement canary live réussi et coût observé.

La PR peut rester Draft tant que les points de sécurité live ne sont pas vérifiés.
