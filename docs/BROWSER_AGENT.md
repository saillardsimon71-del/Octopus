# Browser Agent OCTOPUS

`octopus.browser_agent.BrowserCapability` est la frontière navigateur du runtime G.
Elle utilise le `BrowserTool` Playwright existant (Chromium), sans Astra ni runtime Hermes.

## Capacités

- profil persistant pour les sessions authentifiées (`account=True`), fenêtre visible pour
  la connexion humaine;
- navigation protégée par `web_guard`, observation bornée du texte et de l'accessibility
  snapshot, capture à la demande;
- clic, saisie, sélection, scroll, clavier, upload et navigation;
- vérification explicite par texte, sélecteur ou URL;
- checkpoints SQLite via `octopus.tasks.save_step`, reprise via `browser.completed`;
- aucune décision à conséquence n'est autorisée hors des politiques OCTOPUS existantes.

Le module ne fournit volontairement pas de cerveau : `run(objective, policy)` reçoit une
politique externe qui transforme chaque observation en une action et un contrôle attendu.
Les actions terminées sont journalisées avant l'étape suivante. Une interruption peut donc
recréer la capacité avec le même `task_id` et reprendre à la dernière étape vérifiée.

## Smoke Chromium réel

```bash
python -m playwright install chromium
pytest -q tests/test_browser_integration.py
```

Le test d'intégration existant démarre Chromium et intercepte un réseau local simulé : il
valide redirections, garde anti-exfiltration et contenu JavaScript. Les tests
`test_browser_capability.py` valident le contrat de checkpoint/reprise sans réseau.
Aucun test ne publie, contacte un tiers ou dépense de l'argent.

Limites opérationnelles : CAPTCHA, login et consentements restent des handoffs humains;
la capacité ne contourne pas ces contrôles. Le backend desktop CUA/MCP Hermes n'est pas
activé car aucune lacune web démontrée ne le justifie encore.
