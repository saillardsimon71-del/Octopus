# Génération vidéo locale (WanGP / MiniMax H3)

OCTOPUS pilote WanGP (Wan2GP), installé par Pinokio, pour générer des vidéos depuis l'application, la ligne de commande ou une tâche automatisée.

## Fonctionnement

```
Studio vidéo (GUI) / CLI / tâche planifiée
        │  file OCTOPUS : tâche media.video_generate (ressource « gpu », une à la fois)
        ▼
worker OCTOPUS (python -m octopus worker)
        │  vérification matérielle, détection d'une interface WanGP déjà ouverte
        ▼
octopus/media/wangp_bridge.py  ← lancé avec le Python de WanGP (C:\pinokio\api\wan.git\app\venv)
        │  API Python officielle de WanGP (shared/api.py), sans navigateur
        ▼
data/media/video/task-NNNNNN/   job.json, events.jsonl, preview.jpg, bridge.log, result.json
data/media/video/NNNNNN/        vidéo finale + ligne en base (table media_generations)
```

- **Découverte** : `~/.pinokio/config.json` indique le dossier de Pinokio (`C:\pinokio`), puis `api/*/app/wgp.py`. Surcharges : `OCTOPUS_WANGP_ROOT`, `OCTOPUS_WANGP_PYTHON`.
- **Variantes** : toutes partent dans un seul lancement, le modèle n'est donc chargé qu'une fois. Les graines sont distinctes.
- **Annulation** : bouton Annuler, `python -m octopus cancel ID` ou arrêt du worker. WanGP s'arrête proprement ; au bout de 90 s, l'arbre de processus est tué.
- **Reprise** : une tentative ratée (Chatterbox, mémoire, crash) est relancée une fois. Les lignes de bibliothèque déjà créées sont réutilisées.
- **Garde-fou matériel** (`octopus/media/requirements.py`) : un modèle nettement trop gros est refusé sans nouvelle tentative, plutôt que de bloquer le PC. `--force` permet d'essayer quand même.
- **Prompts H3** : une description libre est mise au format FL2VA (`integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music`).

## Utilisation

```powershell
cd C:\Users\saill\Projects\video-factory
python -m octopus video doctor          # installation, WanGP déjà ouvert ?, dernier diagnostic
python -m octopus video probe           # démarre WanGP à vide : GPU, RAM, modèles disponibles (1 à 3 min)
python -m octopus video generate "A red fox running through snow" --model t2v_1.3B --resolution 480x832 --seconds 3 --steps 20 --wait
python -m octopus video generate "..." --variants 3 --seed 42          # série de variantes
python -m octopus video list | show ID | reuse ID --variants 2
python -m octopus worker                # exécute la file (ou bouton ⚙ Worker de la GUI)
```

Dans l'application : bouton **🎬 Studio vidéo**. On y trouve le prompt, le modèle, la résolution, la durée, les étapes, la graine et les variantes, ainsi que la progression, l'aperçu, l'historique et les actions Ouvrir, Dossier, Réutiliser et Annuler.

**Fermer WanGP dans Pinokio avant une génération automatique** : deux copies du modèle ne tiennent pas en mémoire. Le logiciel détecte l'interface ouverte et refuse de lancer la génération (option « Autoriser avec l'interface WanGP ouverte » pour passer outre).

## Matériel mesuré le 17/09/2026

| Élément | Valeur (probe réel) |
|---|---|
| GPU | NVIDIA GeForce GTX 1060 3 Go, capacité 6.1 (Pascal), CUDA 12.8 |
| RAM | 15,9 Go |
| CPU | 4 cœurs logiques |
| WanGP | venv Python 3.11.15, torch 2.7.1+cu128, démarrage du runtime 91 s (18 s ensuite) |

**MiniMax H3 n'est pas utilisable sur cette machine.** FL2VA 33B, c'est 34 Go en int8, plus 26,7 Go pour l'encodeur de texte Qwen3-VL-32B et 5,2 Go de VAE. La documentation de WanGP annonce 5 à 6 Go de VRAM au minimum pour 5 s en 832x480, et les checkpoints W4A8 plus légers exigent une RTX 30 ou plus récente. Les modèles réalistes ici sont de la famille Wan 1.3B (lents mais possibles).

Pour H3, il faut soit une machine avec au moins 8 Go de VRAM et 64 Go de RAM (recommandation prudente), soit un GPU loué exécutant WanGP. Le même pont s'y branche (voir NEXT_STEPS.md).

## Ajouter un fournisseur ou un modèle

- Nouveau modèle WanGP : aucun code, passer `--model <model_type>` ; ajouter ses besoins dans `requirements.py` si connus.
- Nouveau fournisseur (API cloud, autre moteur local) : écrire un module à côté de `wangp.py` qui prend un manifest et renvoie `{success, generated_files, errors}`, puis l'appeler depuis `handlers.py`. La bibliothèque, la file, la GUI et les variantes restent inchangées.

## Tests

`python -m pytest tests/test_media_*.py` : le vrai pont est testé contre un faux WanGP (`tests/fixtures/fake_wangp`) qui respecte le même contrat. Les cas couverts : variantes, échec partiel, crash, annulation, délai dépassé, interface ouverte, matériel insuffisant, téléchargements.
