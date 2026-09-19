# Gates d'acceptation OCTOPUS

**Date : 19/09/2026**  
**But : remplacer les formulations vagues par des critères binaires et vérifiables.**

Une étape n'est pas DONE parce qu'un fichier existe, qu'une IA dit « terminé » ou qu'un test isolé passe.

Chaque gate exige une preuve, des tests, un état persistant et le scénario d'échec pertinent.

## G0 — Dépôt et reprise fiables

DONE si :

- branche de travail explicite ;
- `main` non modifié directement ;
- `docs/CURRENT_STATE.md` correspond à Git ;
- `docs/HANDOFF_WORK.md` donne la prochaine mission ;
- anciens handoffs sous `docs/archive/` ;
- workflows concernés du dernier HEAD code connus ;
- aucun secret runtime commité.

**État actuel : ~90 %.** Le rangement documentaire existe ; le dernier HEAD de code vérifié avait `Compute finance safety` et `video-foundation` verts.

## G1 — Cerveau LLM contrôlé et mesurable

DONE si :

### Routage
- tous les agents de production passent par `octopus.llm` ;
- les appels directs legacy ne sont pas accessibles accidentellement en production ;
- les tâches choisissent capacité/politique, pas une marque dans leur logique métier.

### Zero-cost
- le pool OmniRoute `zero_cost` est free-only par construction ou le coût réel est attesté après appel ;
- modèle/provider réellement exécuté journalisé ;
- route non attestable jamais comptée comme « 0 $ certain » ;
- panne du pool gratuit n'entraîne jamais un appel payant implicite.

### Validation
- JSON invalide déclenche le fallback dans `octopus.llm` ;
- verdict vision invalide peut fallback avant retour caller ;
- chaque tentative a le bon statut `ok/invalid/error/blocked`.

### Paid
- le mode normal n'utilise **aucun LLM payant** ;
- un LLM payant éventuel nécessite un mode exceptionnel explicitement activé par politique humaine ;
- aucune route payante ne peut être atteinte implicitement depuis `zero_cost`.

Tests minimum :
- OmniRoute gratuit down → aucun paid call ;
- JSON invalide → candidat suivant ;
- resolved model enregistré ;
- upstream payant sous zero_cost → refus ;
- aucun fallback payant implicite.

**État actuel : PARTIAL.** G1.1 à G1.5 sont couverts hors réseau, dont la double activation explicite du bypass legacy direct. Preuve manquante : vérification live du pool OmniRoute free-only. Voir `audits/LLM_BRAIN_AUDIT_2026-09-19.md`.

## G2 — Frontière financière universelle

DONE si aucune dépense automatique connue ne peut être créée hors politique.

### Compute instance
- Salad/GPU.ai ne peuvent être provisionnés depuis un handler métier que via `GuardedComputeManager` ;
- un test statique/AST empêche la régression.

### Serverless/API metered
- RunPod renderer et H3 ont réservation, soumission et settlement ;
- timeout local n'est jamais interprété comme annulation distante ;
- soumission ambiguë jamais retry automatiquement.

### Autres services
- TTS/search déclarés free_quota/paid ;
- tout futur mode LLM payant reste exceptionnel, explicitement activé et économiquement gardé ;
- connecteurs futurs déclarent leur propre cost class.

### Ledger
Une requête peut produire le coût engagé et le coût réellement observé/calculé du jour, par business et catégorie, sans addition manuelle de dashboards.

**État actuel : ~60 %.** Compute instance bien avancé ; autres familles partielles. Voir `audits/PAID_PATHS_AUDIT_2026-09-19.md`.

## G3 — Worker Salad lifecycle prouvé

DONE si un canary réel démontre :

```text
reserve
→ create/start
→ ready
→ submit
→ output externe
→ stop
→ provider confirme stopped
→ settle
```

et :
- pas d'auto-recharge nécessaire ;
- aucune ressource orpheline ;
- restart policy non bouclante ;
- watchdog réellement séparé ;
- tuer le producteur n'empêche pas l'arrêt ;
- restart OCTOPUS réconcilie l'état ;
- output possède checksum/manifest ;
- output COMPLETED non recalculé.

Crash test obligatoire :

```text
GPU running
→ tuer worker/control-plane
→ watchdog
→ stop confirmé
```

**État actuel : ~20 %.** Code de protection présent, preuve fournisseur absente.

## G4 — Benchmark économique reproductible

DONE si toutes les conditions de `benchmarks/GPU_COST_BENCHMARK_PLAN.md` sont satisfaites.

Minimum :
- workload versionné ;
- prix live enregistrés ;
- mêmes paramètres ;
- échecs inclus ;
- coût avec nature explicite ;
- >=2 candidats comparables ou indisponibilité documentée ;
- primary + fallback déterminés ;
- données consommées par `ComputeBroker`.

**État actuel : ~10 %.** Protocole écrit, runs live non réalisés.

## G5 — Production vidéo < 1 centime GPU

Jalon économique principal.

DONE si, sur **10 vidéos finales consécutives** du profil de production :

- aucune intervention manuelle dans le compute ;
- toutes les vidéos attendues récupérées et valides ;
- aucune double génération facturable ;
- aucun GPU orphelin ;
- coût GPU moyen <= **$0.008/video** ;
- coût GPU maximum normal <= **$0.010/video** ;
- coûts des échecs/préemptions inclus ;
- même baseline modèle/qualité que le benchmark retenu ;
- batch prédit hors cap refusé avant création ;
- ledger et métriques retrouvent le même nombre de vidéos.

Le « coût vidéo » ici est le GPU de génération. TTS, storage, LLM et publication restent suivis séparément pour obtenir ensuite le coût total de contenu.

**État actuel : ~25 %.** Les briques existent, KPI non prouvé.

## G6 — Publication réelle et analytics

DONE si :
- canal réel enregistré ;
- accès `act` accordé explicitement ;
- executor réel ;
- publication idempotente ;
- source/ref externe sauvegardée ;
- analytics récupérées sans chiffres inventés ;
- données `observed` ;
- retry sans doublon de publication.

Au moins une vidéo doit parcourir :

```text
OCTOPUS → publication réelle → identifiant externe → analytics observées
```

**État actuel : ~20 %.**

## G7 — Boucle d'expérience économique fermée

DONE si une vraie expérience fait :

```text
objectif
→ hypothèse
→ expérience
→ action/contenu
→ monde réel
→ mesure observée
→ ledger
→ evaluate_experiment
→ décision
→ prochaine action
```

avec aucune valeur inventée, coût complet attribué, source réelle, décision persistée et learning vu par ORBIT au cycle suivant.

**État actuel : ~20 %.** La boucle est testée en simulation/code, pas encore fermée avec canal commercial réel.

## G8 — V1 OCTOPUS exploitable

La V1 n'exige pas une entreprise universelle 100 % autonome.

DONE si :

### Fiabilité
- 50 jobs média/compute sans double facturation ;
- 0 GPU orphelin connu ;
- reprise après restart vérifiée ;
- dépenses réconciliées ;
- tâches bloquées demandent un humain au lieu d'inventer.

### Économie
- au moins un business réel ;
- un canal réel ;
- coûts observés ;
- métriques observées ;
- une expérience complétée de bout en bout.

### Opérations
- GUI/CLI permet de voir ce qui tourne ;
- état, coût, blocage, prochaine action inspectables ;
- providers remplaçables sans réécrire ORBIT.

### Sécurité
- secrets hors Git ;
- actions payantes bornées ;
- frontières humaines respectées ;
- aucune preuve `observed` sans source.

## Tableau de progression

Ce tableau est une estimation d'ingénierie, pas une moyenne mathématique.

| Gate | État estimé | Prochaine preuve |
|---|---:|---|
| G0 Dépôt/reprise | 90 % | docs/CI synchronisées |
| G1 LLM broker | 60 % | attestation OmniRoute + fallback validation |
| G2 Finance universelle | 60 % | metered lease + no-bypass |
| G3 Salad live | 20 % | canary + crash test |
| G4 Benchmark GPU | 10 % | premiers runs comparables |
| G5 <1¢ vidéo | 25 % | série réelle de 10 vidéos |
| G6 Publication/analytics | 20 % | executor réel |
| G7 Boucle économique réelle | 20 % | expérience monde réel |
| G8 V1 exploitable | **~55–60 % global** | fermer G1→G7 |

La vision longue « entreprise autonome générique » reste autour de **30–35 %**, car les connecteurs réels, la publication, les analytics et la boucle de revenu restent à prouver.

## Ordre strict recommandé

```text
G0
 ↓
G1 + G2
 ↓
G3
 ↓
G4
 ↓
G5
 ↓
G6
 ↓
G7
 ↓
G8
```

G1 et G2 peuvent avancer en parallèle.

## Règle de session Work

Une session annonce :
- gate visée ;
- preuve manquante ;
- changements prévus ;
- tests prévus ;
- critère de sortie.

En fin de session, `DONE` est autorisé uniquement si tous les critères sont prouvés. Sinon :

```text
PARTIAL — preuve manquante : ...
```

Ce vocabulaire remplace les « ça a l'air prêt ».
