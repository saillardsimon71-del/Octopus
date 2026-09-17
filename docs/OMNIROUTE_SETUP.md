# OmniRoute — LLM sans dépendance DeepSeek

OCTOPUS garde le contrat OpenAI-compatible de ses agents, mais les appels peuvent maintenant passer par [OmniRoute](https://github.com/diegosouzapw/OmniRoute) au lieu de l'API DeepSeek.

## Principe

```text
agents/deepseek.py
      -> octopus.llm
      -> OmniRoute local
      -> model virtuel auto/free
      -> provider gratuit réellement disponible
```

OmniRoute annonce un catalogue large de providers free et un routage avec fallback. OCTOPUS ne fige donc pas un fournisseur gratuit unique : le modèle virtuel `omniroute/auto-free` correspond par défaut à `auto/free` et laisse OmniRoute choisir le pool disponible.

## Démarrage

Lancer OmniRoute localement, puis configurer au minimum :

```text
OMNIROUTE_ENABLED=1
OMNIROUTE_BASE_URL=http://127.0.0.1:20128/v1
OMNIROUTE_MODEL=auto/free
```

Le profil agent par défaut devient `zero_cost` quand OmniRoute est actif. Cela signifie :

- aucun modèle `paid` n'est choisi automatiquement ;
- un échec ou un quota épuisé passe au candidat gratuit suivant configuré ;
- les données ne sont pas nécessairement locales : OmniRoute peut transmettre les prompts aux providers qu'il utilise. Vérifier leurs conditions et politiques pour les données sensibles.

Pour restaurer temporairement le comportement historique :

```text
OCTOPUS_PROFILE=legacy
OMNIROUTE_ENABLED=0
```

## Intégration avec le catalogue OCTOPUS

`octopus/catalog.py` ajoute à runtime un provider `omniroute` et le modèle `omniroute/auto-free` sans modifier `octopus/config/catalog.json`.

Les tâches Podalux texte et vision ajoutent ce candidat en tête de `zero_cost`/`low_cost`. La tâche `web.describe_page` reste locale car elle est classée sensible.

## Quotas et pannes

Le mode `zero_cost` interdit les repliements payants. Une panne OmniRoute ou un provider gratuit indisponible peut donc finir en `NoEligibleModel` : c'est volontaire et préférable à une facture implicite.

`CYCLE_BUDGET_USD` continue de protéger les appels payants historiques, mais le mode `zero_cost` doit être utilisé pour les exécutions ordinaires tant que la disponibilité gratuite est suffisante.

## Vérification

```bash
python -m pytest -q tests/test_omniroute.py
```

Puis vérifier le gateway réel avec :

```bash
curl http://127.0.0.1:20128/v1/models
```

Aucun secret OmniRoute ne doit être écrit dans le dépôt. Un éventuel token de gateway reste une variable d'environnement/runtime.
