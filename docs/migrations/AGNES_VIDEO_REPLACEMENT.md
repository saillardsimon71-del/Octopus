# Atelier vidéo Agnes — contrat de remplacement

Source normative pour cette migration: document de transfert fourni par l'utilisateur le 2026-09-25.

IMPORTANT: les endpoints, limites RPM et comportements Agnes ci-dessous sont recopiés comme exigences du projet. Ils n'ont pas été revalidés extérieurement dans ce document.

## But

Application web autonome grand public:
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
- conserver le préfixe `data:image/...;base64,`;
- `image` au niveau racine;
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
- clé API + sauvegarde + statut;
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
