# Atelier vidéo Agnes — contrat de remplacement

Source normative fonctionnelle pour cette migration: document de transfert fourni par l'utilisateur le 2026-09-25.

Validation publique complémentaire effectuée le 2026-09-25: le gateway Agnes documente toujours `POST /v1/videos`, l'authentification Bearer, le polling recommandé par `video_id`, et le protocole legacy `agnes-video-v2.0`. Agnes propose aussi des modèles vidéo 2.5 plus récents; **cette migration reste volontairement sur v2.0** afin de ne pas changer de contrat pendant le remplacement.

Les limites peuvent dépendre du compte. Le 1 RPM ci-dessous est une référence publique, pas une garantie d'entitlement pour la clé de l'opérateur.

## But

Application web autonome **locale/privée pour l'opérateur dans cette V1**:

> Sécurité: la documentation Agnes recommande de garder les clés API côté serveur. Une application réellement publique/multi-utilisateur ne doit donc pas embarquer une clé personnelle persistante dans le client. Le choix « fichier HTML unique, sans backend » est conservé aujourd'hui uniquement comme outil local. Ne pas présenter cette V1 comme un déploiement public sécurisé.

Caractéristiques:
- un unique fichier `index.html`;
- HTML/CSS/JS uniquement;
- aucune dépendance externe/CDN/framework;
- upload multiple d'images depuis téléphone;
- génération d'une vidéo IA par image;
- Android + iOS;
- anti-veille pendant les longues générations.

Chemin cible:
`apps/agnes-video/index.html`

L'application est indépendante du moteur OCTOPUS. OCTOPUS peut éventuellement la lancer/servir plus tard, mais ne doit pas réintroduire Remotion/TTS/RunPod pour elle.

## API Agnes fournie

Créer:
```
POST https://apihub.agnes-ai.com/v1/videos
```

Poll:
```
GET https://apihub.agnes-ai.com/agnesapi?video_id=<ID>&model_name=agnes-video-v2.0
```

Modèle:
`agnes-video-v2.0`

Payload validé par le document source:

```js
{
  model: "agnes-video-v2.0",
  prompt: "...prompt en anglais...",
  image: "<data-uri-complet>",
  num_frames: 153,
  frame_rate: 24
}
```

Contraintes source:
- ne pas utiliser `first_frame`;
- ne pas utiliser `mode: "keyframes"`;
- conserver le préfixe `data:image/...;base64,` selon le document de transfert;
- `image` au niveau racine;
- **ambiguïté connue**: la documentation publique v2.0 consultée le 2026-09-25 décrit `image` comme une URL de référence; le support Data URI vidéo n'a pas été confirmé publiquement. Ne pas réécrire l'app autour d'un uploader/backend spéculatif. Implémenter le contrat de transfert, puis considérer le premier appel live comme la vérification de cette hypothèse.
- créations vidéo: 1/minute effective;
- polling parallèle autorisé.

## Durées

```
121 frames = 5.0 s
153 frames = 6.4 s recommandé
241 frames = 10.0 s
441 frames = 18.4 s max
```

`num_frames = 8n + 1`, max 441.

## Pipeline

```
T=0s    create image 1 -> poll 1
T=62s   create image 2 -> poll 2
T=124s  create image 3 -> poll 3
...
```

Les créations sont espacées; les pollers restent parallèles.

## Anti-429 adaptatif

Intervalle:
- nominal: 62 s;
- sur 429: +8 s jusqu'à 90 s;
- après 3 succès consécutifs: -4 s progressivement jusqu'à 62 s.

Backoff source:

```
429:     15,30,45,60,90,120,180 s
503:     5,10,15,20,30,45 s
network: 3,5,8,12,20,30 s
```

Toute réponse HTTP non OK doit être traitée avant parsing JSON.

## Cache timing local

Clé:
`agnes_timing_cache_v10`

Forme:
```json
{"121":[62340,58900],"153":[75200]}
```

- 10 derniers échantillons par durée;
- médiane;
- premier poll vers 80% de l'estimation.

## Prompt final

Ordre:
1. instruction continuité ou transformation;
2. effet visuel;
3. prompt utilisateur, priorité haute;
4. cadrage;
5. intensité;
6. action aléatoire;
7. mouvement caméra aléatoire;
8. éclairage aléatoire;
9. sound design minimal foley, no music;
10. portrait 9:16, 24fps, no text, no watermarks.

### Catégorie A — ambiance / continuité
12 effets:
- Cinématique
- Golden Hour
- Noir & Blanc
- Pastel Rêveur
- Néon Urbain
- Vintage Super 8
- Contraste Dramatique
- Brume Mystique
- Chiaroscuro
- Tropical Saturé
- Clair de Lune
- Aube Douce

Instruction de base:
`Preserve the exact subject identity, face, pose, and clothing`

### Catégorie B — transformations
12 effets:
- Simpson
- Ghibli
- Manga N&B
- Polar
- Cyberpunk
- Pixar
- BD franco-belge
- Aquarelle
- Peinture à l'huile
- Claymation
- Vaporwave
- Surréaliste

Instruction:
`Transform the entire image into this style`

## Interface

Un seul `index.html` contenant:
- CSS;
- header;
- info banner;
- clé API + sauvegarde locale + statut, avec avertissement explicite « usage local/privé — ne pas publier avec une clé personnelle »;
- contrôle wake lock;
- upload multiple;
- thumbnails;
- prompt principal;
- 7 presets;
- choix effet;
- durée/cadrage/intensité;
- Créer;
- Arrêter;
- queue + progression + ETA;
- galerie vidéo;
- status bar;
- toast;
- canvas/video cachés anti-veille;
- logique JS complète.

## Anti-veille

Niveau 1:
```js
wakeLockSentinel = await navigator.wakeLock.request('screen');
```

Niveau 2:
- canvas 2x2;
- alternance très légère;
- `captureStream(1)`;
- vidéo muette;
- relance à `visibilitychange`;
- préférence `agnes_wakelock_enabled`.

## Performance images

Pour 50+ images:
- ne pas laisser toutes les Data URIs originales dans le DOM;
- thumbnails ~200 px;
- `loading="lazy"`;
- conserver les originaux seulement dans l'état JS nécessaire aux requêtes.

## Critères d'acceptation

- fichier unique;
- aucune dépendance/CDN;
- clé API sauvegardable avec feedback;
- multi-upload mobile;
- queue stable;
- une création Agnes à la fois par fenêtre ~62 s;
- pollers parallèles;
- backoff adaptatif;
- bouton Arrêter fonctionnel;
- wake lock + fallback;
- cache timing;
- effets A préservent la continuité par prompt;
- effets B assument transformation;
- vidéos affichées/téléchargeables lorsqu'elles sont prêtes;
- aucun ancien moteur vidéo OCTOPUS requis.

## Hors scope initial

- ffmpeg.wasm;
- montage final concaténé;
- TTS externe;
- collaboration;
- backend serveur;
- historique IndexedDB avancé.

## Faits runtime à ne pas inventer

Sans appel réel avec la clé de l'opérateur, restent inconnus:
- CORS navigateur depuis la page locale;
- acceptation d'un Data URI Base64 dans le champ vidéo v2.0 `image` (la doc publique montre une URL, le transfert affirme Data URI);
- schéma exact de réponse live pour ce compte;
- entitlement/quota effectif;
- disponibilité v2.0 pour cette clé.

Les tests du dépôt doivent rester statiques/déterministes et ne pas effectuer de génération payante. Si un de ces faits bloque l'exécution réelle, le signaler; ne pas construire un backend ou changer de modèle spontanément.
