# NEXT_STEPS - 17/09/2026 (session autonome video)

## Fait et verifie (mesure, pas suppose)
- 4 videos reelles produites de bout en bout, hors machine locale : 1080x1920, 30 fps,
  -14,0 LUFS, 0 image figee, 18,5 a 21,7 s, ~112 s de production par video, 0 euro.
- Voix : `tools/tts_providers.py`, chaine azure -> cloudflare -> chatterbox -> piper.
  piper (MIT, CPU, sans compte) sert de plancher : 7,4 s de voix synthetises en 1,8 s sur 2 vCPU.
- B-roll : `tools/fetch_broll.py`, 5 images CC0 pertinentes par offre (stocksnap/rawpixel/nappy,
  photos uniquement, filtre de pertinence), credits dans out/<offer>/credits.txt.
- Respiration adaptative : narration trop courte -> silences allonges (17,3 s -> 18,5 s) au lieu
  d'un refus QC.
- Suite complete verte (438 tests), dont 20 nouveaux (chaine TTS, b-roll, respiration, doctor).
- OmniRoute : `auto/free` n'existe pas, le catalogue envoie `auto/best-free` (verifie sur la
  passerelle locale : 200 en 458 ms via Groq).

## A faire sur la machine Windows
1. `python -m pip install piper-tts` puis `python -m agents.run doctor` :
   la ligne "Voix (chaine TTS)" doit lister piper.
2. `python -m agents.run cycle` : premier cycle complet avec voix locale et b-roll.
3. Pousser la branche (aucun push n'a ete fait sans autorisation) :
   `git push origin feat/autonomous-business-foundation`
4. Ensuite seulement : declencher `video-batch` dans l'onglet Actions (production de toutes les
   offres en parallele, gratuit sur les runners GitHub).

## Ameliorations possibles (non faites)
- Cle Azure Speech (F0, 500 000 caracteres/mois, sans carte) : voix neuronale a la place de piper,
  c'est le seul levier qui fera monter la note "humanite" du QC.
- Cle Pexels (gratuite) : b-roll de bien meilleure qualite que les banques CC0 actuelles.
- Synchronisation des sous-titres au mot (whisper) : aujourd'hui repartie au prorata des caracteres.
- QC vision sur le palier gratuit Gemini (1 500 requetes/jour) plutot que sur un modele generique.

## Non teste
- Cycle complet `agents.run cycle` sur Windows avec ces changements (la partie FORGE est testee,
  SOUT/CONVERT/GROWTH/LEDGER demandent la base et les LLM de la machine).
- Les deux workflows GitHub : rien n'a ete pousse, donc aucun run observe.
