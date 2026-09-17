# NEXT_STEPS — reprise (17/09/2026, ~11:20 UTC)

## État
- Commité : registre des activités (`python -m octopus businesses`), pont WanGP, studio, garde-fou matériel.
- Commité (WIP testé partiellement, tests media OK) : mesures de performance `octopus/media/perf.py` (table media_runs, schéma v4),
  préréglages `octopus/media/presets.py` (brouillon / standard / qualite / h3), CLI `video presets`, `video perf`,
  `video generate --preset`, estimation de durée avant lancement, studio.py (préréglage, réglages conservés à la réutilisation).
- MESURE RÉELLE (tâche #2, GTX 1060 3 Go) : t2v_1.3B 480x832, 49 images, 20 étapes, CFG → 136-139 s/étape (≈ 47 min). Trop lent.
- Test demandé à l'utilisateur : `python -m octopus cancel 2` puis
  `python -m octopus video generate "..." --model t2v_nexus_1.3B --seconds 3 --resolution 288x512 --steps 6 --setting guidance_scale=1 --setting flow_shift=5 --wait`
  (ou désormais `--preset brouillon`). Estimation théorique ≈ 2-5 min + 3,1 Go de téléchargement. NON MESURÉ.

## Priorités
1. Récupérer le résultat du test Nexus : `python -m octopus video perf` (mesures réelles), corriger si échec.
2. Lancer toute la suite de tests (`python -m pytest`) ; écrire tests pour perf.py / presets.py (analyze, estimate, apply, CLI perf/presets).
3. GUI `agents/gui/studio.py` : menu préréglage (studio.PRESET_CHOICES, défaut « Brouillon rapide »), libellé durée prévue
   (studio.estimate_text), passer `preset` et `settings` dans `_form` / `_reuse`.
4. Calibrer TOKEN_EXPONENT avec 2 mesures de même architecture ; mettre à jour docs/GENERATION_VIDEO.md.
5. Mode « serve » du pont (modèle gardé chargé), intégration b-roll Podalux/Remotion.
6. `git push origin main` (à faire par l'utilisateur, pas d'identifiants dans la VM).

## Commandes de reprise
```
cd C:\Users\saill\Projects\video-factory
git push origin main
python -m pytest -q
python -m octopus video perf
python -m octopus video presets
```
