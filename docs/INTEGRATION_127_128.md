# Intégration finale #127 sur #128

## Périmètre exact

Base #128 : `f132324de73619b30d7a0134ffb054b8596f69aa`, elle-même issue de #126
`31477e263544ac0c67663c6aa800533678ff70df`.
Correctif #127 : `956996c1dfe61ac6fe988c616d63970f5c209819`.
Branche combinée : `fix/integrate-127-on-128`, PR draft empilée sur
`feat/delegated-resource-hub`. Aucun merge.

Le diff #127 contre #126 contient exactement quatre fichiers. Sa seule modification
de production est `agents/runtime.py` : `max_tokens=1600 if economical else 4000`
devient `max_tokens=4000`. Les autres modifications sont des attentes de transports
et deux régressions dans les tests. Le patch a été appliqué sur le commit exact #128,
avec fusion automatique de runtime et recovery, puis inspection des deux diffs.
Aucune réinterprétation du correctif, aucun changement de prompt, modèle, routing,
parser, fallback JSON, reasoning, permission, budget, cycle ou timeout.

Tous les fichiers de production de #128 restent identiques, sauf cette ligne de
runtime. Les adaptations #128 des tests recovery restent présentes. Le hub,
les mandats persistants, les tâches de compte, les profils de session, les actions
déléguées, les artefacts locaux et leurs frontières financières restent ceux de #128.

## Validation hors ligne

- Régressions originales #127 conservées : JSON simulé dépassant 1 600 unités mais
  restant sous 4 000 ; reprise d'une ancienne sortie tronquée avec observations et coûts conservés.
- Fallback `json_object` vers texte conservé et testé avec le plafond 4 000.
- Deux parcours supplémentaires dans `test_delegated_resource_hub.py` exécutent le
  superviseur, le runtime, le gateway, le worker et SQLite : activité existante,
  observation publique, demande pour une plateforme inconnue, connexion humaine
  simulée, mandat de lecture simulé, tâche de compte et observation authentifiée,
  longue synthèse, determination valide. Le second simule une ancienne troncature
  puis reprend uniquement la synthèse, sans collecte publique ni tâche de compte répétée.
- Mandat et état de session conservés ; aucun secret de la session simulée dans les
  requêtes LLM ou tables vérifiées ; aucune demande humaine restante ; next_goal
  conservé ; une décision par tâche ; usage et coûts journalisés ; relance du
  superviseur sans nouvelle décision ni appel pour le travail déjà terminé.
- Le test de migration legacy est renforcé avec une réponse humaine historique et
  un checkpoint d'observation : toutes les anciennes colonnes et lignes sont comparées.
- Les unités de tokens de ces transports sont approximées par quatre caractères.
  Ces tests établissent la mécanique du quota et de la reprise, pas la qualité du vrai modèle.

Suite complète : 1 991 réussites, 97 skips attendus. Parents exécutés séparément :
#127 = 1 952 réussites / 97 skips ; #128 = 1 987 réussites / 97 skips.
Les identités et motifs des skips sont comparés mécaniquement. Les résultats finaux
et les deux workflows CI sont consignés dans la PR ; le head et l'arbre publiés
sont vérifiés contre l'arbre local testé.

## Journal existant V9 → V10

Le snapshot fourni du vrai run est ouvert uniquement en lecture immutable ; une copie
temporaire reçoit la migration et le transport LLM simulé. Le fichier source reste
inchangé, SHA-256 `5bda5651c332e2a15c3faa3b6f4b4359267a81a12f6c01dc9ea84965d252878e`.
Aucune donnée du run n'est ajoutée au dépôt.

Les 28 tables existantes sont comparées ligne par ligne sur toutes leurs colonnes
originales avant toute reprise, puis après réouverture : égalité exacte. Les tables
peuplées incluent 7 runs, 13 appels LLM, 3 tâches, 27 événements, 12 checkpoints,
1 objectif, 3 décisions et 6 liens. Integrity check et foreign key check passent.
Les nouveaux mandats et channel_authority sont vides : aucune autorité ajoutée.
Le test séparé conserve aussi une réponse humaine non vide lors de la migration.

Sur la copie migrée, le même objectif et la même activité reprennent avec un seul
appel de synthèse simulé à 4 000, zéro collecte et toutes les observations réutilisées.
Les 3 tâches, 13 appels et 3 décisions historiques restent inchangés. Le coût historique
0,005899758 USD est conservé ; le nouveau coût simulé 0,008 USD et une nouvelle décision
sont ajoutés séparément. Aucune dépense, permission ou action externe.

Limite précise : ce contrôle porte sur le snapshot SQLite fourni. Le DataRoot Windows
vivant et ses fichiers de configuration/profils n'ont pas été accessibles ; le WAL
Windows non fourni peut contenir des transactions plus récentes. Ils seront conservés
par la reprise sur le répertoire original. Ne pas remplacer la base Windows par cette copie.

## Protocole Windows : même DataRoot

1. Arrêter le run et fermer Workbench et ses navigateurs. Préserver le répertoire
   DataRoot complet, y compris les éventuels `-wal` / `-shm` et profils ; ne rien supprimer.
2. Depuis le dépôt, récupérer la branche combinée et créer un worktree séparé :

```powershell
git fetch origin fix/integrate-127-on-128
git worktree add C:\Users\saill\Projects\Octopus-resume --detach origin/fix/integrate-127-on-128
Set-Location C:\Users\saill\Projects\Octopus-resume
git rev-parse HEAD
```

Comparer ce HEAD au head final exact de la PR d'intégration.

3. Réutiliser exactement les valeurs DataRoot et Python du run #126 :

```powershell
.\scripts\start-workbench.ps1 -DataRoot $dataRoot -Python $python
```

`$dataRoot` doit être le chemin existant utilisé lors du run #126, pas un nouveau
répertoire. Le script ouvre son `data\octopus.db` ; la migration au premier lancement
hors consultation est automatique. Le hub peut être vide.

4. Vérifier la même activité, le même objectif, les observations et coûts historiques.
   Dans Paramètres, connecter les premiers comptes via le navigateur humain,
   vérifier leur session et accorder les mandats voulus une fois.
5. Reprendre le même objectif depuis Missions. La reprise technique ne recollecte pas
   les observations déjà terminées. Une nouvelle réponse réelle peut encore être invalide
   ou dépasser 4 000 tokens : aucun succès économique n'est déduit de la validation.

Aucun provider, compte, login, message, publication, prospection, dépense ou mandat réel
n'a été utilisé pendant cette intégration. La prochaine étape opérateur est ce run Windows.
