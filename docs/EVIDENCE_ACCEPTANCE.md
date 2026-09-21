# Evidence → Contract → Gate

## Purpose

A development task is not successful because the builder says it is finished or because pytest is green.
The builder produces a **candidate**. A protected evaluator must own the evidence and decide whether the
candidate satisfies an immutable acceptance contract.

This foundation intentionally stays small:

```text
mission
  -> AcceptanceContract
  -> builder
  -> ready_for_evaluation
  -> EvidenceBundle
  -> deterministic GateDecision
  -> ACCEPTED / REJECTED / UNCERTAIN
  -> commit + promotion only after ACCEPTED
```

## Trust boundary

The product builder may read the contract and modify only its declared `allowed_paths`.
It cannot modify:

- `octopus/acceptance.py`
- `octopus/acceptance_probe.py`
- the self-development worker/promotion policy
- the deterministic tests
- this governance contract

Evidence is produced by the supervising process and written under `data/acceptance-evidence/`, outside
the builder's isolated clone. Records are content-addressed and never overwritten: identical replay is
idempotent, while different evidence for the same task/attempt/contract is appended under a different
path. Each record identifies `octopus.acceptance.gate` as its producer and is checksum-bound to the
task, contract, gate decision, and artifact fingerprint.

The capability registry planned for later is **knowledge** and will remain untrusted input. Gate authority
is **governed policy** and is not delegated to builders or dynamically discovered models.

## AcceptanceContract v1

The contract is JSON, versioned and canonicalized to a SHA-256 hash. A product ticket must provide it
before Kilo starts. Its `must` rules are hard requirements. The initial deterministic operators are
`equals`, `not_equals`, `contains`, `not_contains`, `set_equals`, `truthy`, and `falsy`.

The first runtime probe is deliberately narrow: `tk_navigation`. It launches a Tk workbench under
Docker/Xvfb, records whether initialization succeeds, and observes the primary navigation labels.

Example:

```json
{
  "version": 1,
  "id": "gui-primary-nav-v1",
  "artifact_type": "desktop_gui",
  "probe": {
    "kind": "tk_navigation",
    "module": "agents.gui.intelligence",
    "class": "EntrepreneurialWorkbench",
    "attribute": "nav_buttons"
  },
  "must": [
    {
      "id": "runtime_launches",
      "fact": "runtime.launched",
      "op": "equals",
      "expected": true
    },
    {
      "id": "primary_nav_exact",
      "fact": "ui.primary_nav",
      "op": "equals",
      "expected": ["Home", "Operate", "Build", "Review"]
    },
    {
      "id": "no_intelligence_primary",
      "fact": "ui.primary_nav",
      "op": "not_contains",
      "expected": "Intelligence"
    }
  ]
}
```

This contract rejects both failures observed during the rejected GUI experiment:

1. a Tk initialization exception such as the invalid `<Control-Key-10>` binding;
2. a primary navigation that still exposes `Intelligence`.

## Gate semantics

- `ACCEPTED`: every MUST has conclusive evidence and passes.
- `REJECTED`: at least one MUST conclusively fails.
- `UNCERTAIN`: no MUST is known to fail, but required evidence is missing/incompatible.
- `WAITING_FOR_HUMAN`, `WAITING_FOR_RESOURCE`, `ABORTED_SAFE`: reserved states for the next layer.

A rejected deterministic gate may feed its failed criteria back to Kilo for another bounded pass. An
uncertain gate fails closed; missing evidence is not treated as success.

## EvidenceBundle

The bundle records:

- schema version and trusted producer identity;
- task and attempt;
- contract id/hash;
- deterministic test fact;
- changed paths;
- runtime/UI facts from trusted probes;
- gate decision.

The builder does not write this file.

## Next extensions

Do not add a Model Lab or arbitrary MCP installation before this foundation has production evidence.
Extend the same structure one domain at a time:

- web control plane: Playwright/DOM facts, screenshots, visual regression;
- video: frames, duration, transcript/audio facts, QC;
- business: observed analytics/payment facts;
- self-development: runtime smoke and repository invariants.

A future vision reviewer is an additional evidence source, not the authority. Deterministic L0 facts run
first. Human/reviewer disagreements become calibration data later.
