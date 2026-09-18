# Plan de production video — etat, ecart avec le marche, et chemin

Ecrit le 18/09/2026. Toutes les valeurs chiffrees viennent soit de nos mesures (`out/*/qc_metrics.json`),
soit des sources listees en fin de document.

## 1. Ou nous en sommes, sans complaisance

Ce que le pipeline produit aujourd'hui : des photos libres animees (zoom, recadrage toutes les 1,8 s),
une voix de synthese locale, des sous-titres cales au mot, un montage Remotion. Techniquement propre :
1080x1920, -14 LUFS, 0 image figee, 10 a 13 coupes par video, 0 euro, 2 minutes de calcul.

Ce que ce n'est pas : une video. Il n'y a **aucun plan filme**, **aucun visage**, **aucune voix humaine**.
Un spectateur le voit en une seconde. Le probleme n'est pas le moteur de montage, ce sont les
**entrees** : nous assemblons des photos parce que nous n'avons ni rushes, ni acteur, ni voix.

## 2. Ce que font ceux qui reussissent en 2026

| Ce qu'ils utilisent | Prix reel | Ce que ca donne |
|---|---|---|
| Banques de **video** libres (Pexels, Pixabay) | 0 € | Plans filmes reels, usage commercial, 200 requetes/heure |
| **Voix neuronale** (Azure F0) | 0 € jusqu'a 500 000 caracteres/mois | Voix qui passe pour humaine |
| **Avatar qui parle** (InfiniteTalk et equivalents) | **0,015 $/s**, soit 0,30 $ pour 20 s | Le format qui convertit en publicite |
| **Generation video** Kling 3.0 | 0,09 a 0,14 $/s | Plans impossibles a trouver en banque, sans son |
| **Generation video** Veo 3.1 Fast | 0,15 $/s, **son natif** | Un plan + son en une passe |
| Veo 3.1 Lite | 0,05 $/s | Brouillons, puis re-rendu des plans retenus |

Ordres de grandeur : une pub de 30 s coute **1,50 à 12 $** selon le niveau ; un clip social de 8 s,
**0,40 à 3,20 $**. Le gratuit existe (Veo dans Google Flow, ~10 clips/jour) mais **avec filigrane**,
donc inutilisable pour du commercial.

Trois enseignements qui comptent plus que le choix de l'outil :

1. **Le script decide, pas le modele.** « L'ecart de qualite entre les outils se resserre ; l'ecart
   de qualite entre les scripts, non. » Nos scripts sortent de modeles gratuits sans structure imposee.
2. **La matrice de test est le vrai travail** : 3 accroches x 3 presentateurs x 2 appels a l'action
   = 18 variantes, produites en une journee. Nous produisons 1 variante par offre.
3. **L'avatar IA a rattrape l'humain sur trafic froid**, sauf sur les sujets ou la relatabilite fait
   tout. Le conseil aux independants (nos offres) est justement un sujet ou un visage aide.

## 3. Le plan, en trois paliers

Chaque palier a un critere d'acceptation mesurable. On ne passe au suivant que s'il est atteint.

### Palier 1 — Des plans filmes, une vraie voix (0 €, ~1 jour de travail)

- **Banque video Pexels** a la place des photos : `tools/fetch_broll.py` interroge
  `api.pexels.com/v1/videos/search` (portrait, HD), telecharge 2 a 3 rushes par segment,
  Remotion les joue avec les memes coupes. Cle gratuite, usage commercial, attribution par un lien.
- **Voix Azure neuronale** : la chaine `TTS_CHAIN` la prend deja, il ne manque que la cle (F0, sans
  carte bancaire). Piper reste le filet de securite.
- **Script structure** : imposer au modele la forme qui marche (accroche en 3 s, probleme chiffre,
  preuve, appel a l'action) et lui faire produire **3 accroches et 2 appels a l'action** par offre.

  *Critere d'acceptation* : sur les 4 offres, 100 % des segments ont un plan filme, la voix passe
  le test a l'aveugle (« humain ou synthese ? ») pour au moins 3 personnes sur 5, et la note QC
  d'humanite passe au-dessus de 3/5.

### Palier 2 — Un visage qui parle (environ 0,30 à 0,60 $ par video)

- Accroche et appel a l'action portes par un **avatar** (InfiniteTalk ou equivalent) : image de
  presentateur + notre piste voix, rendu 480p/720p. Le reste de la video reste en rushes.
- Le meme script genere **3 variantes d'accroche** : c'est la matrice de test, pas la video unique.

  *Critere d'acceptation* : une video complete avec avatar produite de bout en bout par le pipeline,
  cout reel mesure sous 1 $, et retention a 3 s superieure a la version sans visage (mesuree en ligne).

### Palier 3 — Des plans generes (1 à 3 $ par video, seulement quand la banque ne suffit pas)

- 2 à 3 plans generes par video sur Kling 3.0 (0,09 $/s) ou Veo 3.1 Fast (0,15 $/s, son natif),
  la ou aucun rush ne raconte la scene. Brouillon sur un palier bas, re-rendu du plan retenu.
- Integration dans `octopus/video/` comme fournisseur, derriere l'enveloppe de depense existante
  (`economy.authorize_spend`) : aucune generation payante sans autorisation.

  *Critere d'acceptation* : cout par video sous 3 $, et un jury a l'aveugle ne distingue plus nos
  videos de celles d'un studio low-cost.

### Transversal — La boucle qui manque vraiment

Tant que rien n'est publie, « bonne qualite » reste une opinion. Il faut brancher la mesure :
publication, puis retention a 3 s, taux de replay, clics. C'est la seule chose qui dira si le
palier 1 suffit ou s'il faut payer le palier 3. La couche strategie/economie existe deja pour
enregistrer ces observations.

## 4. Ce que je recommande de decider maintenant

1. **Creer deux cles gratuites** : Pexels (video) et Azure Speech F0 (voix). Sans carte bancaire,
   sans engagement. C'est ce qui fait passer du diaporama a la video.
2. **Autoriser une enveloppe d'essai de 5 $** pour le palier 2 : environ 10 videos avec avatar.
   Rien ne part sans cette autorisation explicite.
3. **Choisir une offre temoin** et produire 6 variantes (3 accroches x 2 appels a l'action) plutot
   que 4 videos uniques. C'est la methode de ceux qui mesurent.

## Sources

- [Prix des API video 2026](https://modelslab.com/blog/api/veo-3-1-vs-kling-3-sora-2-ai-video-api-cost-2026)
- [Cout reel par seconde et par projet](https://nodetool.ai/blog/ai-video-generation-cost)
- [Outils UGC IA et ce qui convertit](https://adlibrary.com/posts/best-ai-ugc-video-tools-2026)
- [Modeles video open source 2026](https://ltx.io/blog/open-source-video-generation-models-guide)
- [Acces gratuit a Veo et filigrane](https://www.veo3ai.io/blog/veo-3-1-free-for-everyone-how-to-use-2026)
- [API Pexels (photos et videos)](https://www.pexels.com/api/documentation/)
- [Retention sur les Shorts](https://aibrify.com/blog/youtube-shorts-retention-curve-playbook)
