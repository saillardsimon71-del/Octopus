# Regles de montage des Shorts (et pourquoi)

Chaque regle est appliquee dans `remotion/src/CashShort.tsx`, `tools/fetch_broll.py` ou
`tools/make_audio_chatterbox_full.py`. Les chiffres sont mesures sur les 4 offres reelles
(`out/<offer>/qc_metrics.json`, 18/09/2026), pas estimes.

| Regle | Application | Mesure |
|---|---|---|
| Une variation visuelle toutes les 1,5 a 2 s (moins de 1,2 s = bruit, plus de 2,5 s = decrochage) | 2 photos par segment alternees toutes les 1,8 s (`BEAT_S`) | coupes detectees 3 -> 6 a 13 par video, soit une toutes les 1,6 a 2 s |
| 2 a 4 mots affiches a la fois | fenetre `[mot courant - 1, mot courant + 1]` | 3 mots maximum a l'ecran |
| Sous-titres des la premiere seconde | premier mot affiche a 0,1 s | - |
| Sous-titres lisibles sans le son | pastille sombre + ombre portee, corps 74 a 128 | - |
| Duree utile 20 a 35 s | respiration entre segments etiree jusqu'a la cible (`PODALUX_MIN_DURATION_S`) | 18,9 a 22,3 s |
| Boucle : derniere image proche de la premiere | fondu vers l'image d'ouverture sur les 0,5 dernieres secondes | - |
| Pas de carton d'intro | l'accroche parle des la premiere image | - |
| Image pleine, pas de zone morte | plein cadre sur tous les segments | - |
| Etalonnage constant entre plans | `GRADE` applique a toutes les photos | satavg 14,7 a 19,4 (cible QC ramenee de 25 a 15) |
| Son cale et constant | LUFS -14, limiteur | LUFS -14,0 sur les 4 videos, crete -1,6 dBFS |

Sources consultees le 18/09/2026 : [YouTube Shorts retention playbook](https://aibrify.com/blog/youtube-shorts-retention-curve-playbook),
[erreurs qui font amateur sur Reels](https://www.mediaalacarte.com/post/mistakes-that-make-your-reels-look-amateur-and-how-to-fix-them).

## Voix

Bake-off mesure (3 phrases, WER via whisper tiny, variation de F0 par autocorrelation) :

| Voix piper | WER | Variation F0 |
|---|---|---|
| fr_FR-siwis-medium | **0,248** | **42,7 Hz** |
| fr_FR-tom-medium | 0,304 | 30,9 Hz |
| fr_FR-upmc-medium | 0,351 | 40,1 Hz |

`noise_w` teste a 0,8 / 0,9 / 1,1 et `noise_scale` a 0,667 / 0,8 / 0,85 : le couple retenu
(0,9 / 0,667) donne le WER le plus bas a variation de F0 egale. Conclusion : la voix par defaut
et ses reglages sont ceux qui se comprennent le mieux, mesure a l'appui.

La chaine ffmpeg (coupe des graves, creux 260 Hz, presence 4,2 kHz, compression, limiteur) fait
passer le WER de 0,170 a 0,113 et la crete de +0,1 dBFS (ecretage) a -1,6 dBFS.

Le debit varie par role (hook 1,05 / douleur 0,97 / preuve 1,0 / soulagement 1,0 / cta 1,06) :
une narration au tempo constant sonne robotique.

## Ce qui manque encore

- Voix : piper reste une voix de synthese. Une cle Azure Speech gratuite (F0, 500 000 caracteres
  par mois) donnerait une voix neuronale sans changer une ligne de code (`TTS_CHAIN`).
- B-roll : les banques CC0 (stocksnap, rawpixel, nappy) sont limitees. Une cle Pexels gratuite
  ouvre un fonds bien plus large, et des videos en plus des photos.
- Aucun plan filme : tout est photo animee. Des rushes video libres changeraient de categorie.
