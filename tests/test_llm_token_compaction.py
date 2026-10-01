from __future__ import annotations

import json

from agents import runtime
from octopus import journal


def test_economical_react_context_keeps_evidence_and_recent_results():
    context = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Goal"}]
    steps = []
    for index in range(8):
        url = f"https://example.org/{index}"
        result = f"URL: {url}; price: 49 EUR. " + "navigation " * 120
        context.extend([{"role": "assistant", "content": "browse"},
                        {"role": "user", "content": result}])
        steps.append({"tool": "browse", "result": result, "result_urls": [url]})

    compact = runtime._react_prompt_context(context, steps)
    content = json.dumps(compact)

    assert compact[:2] == context[:2]
    assert context[-4:] == compact[-4:]
    assert "https://example.org/2" in content
    assert len(content) < len(json.dumps(context)) * 0.6


def test_economical_synthesis_uses_compact_handoff():
    result = {"role": "SOUT", "task": "Lire une page publique", "final": "prix inconnu",
              "steps": [{"step": 1, "tool": "browse", "result": "long " * 1000,
                         "result_data": {"page": {"main_text": "prix 49 EUR", "final_url": "https://example.org/r17"}}}]}

    with journal.run("octopus", "mission", profile="economical"):
        projected = runtime._mission_prompt_results([result])

    assert projected[0]["final"] == "prix inconnu"
    assert "https://example.org/r17" in json.dumps(projected)
    assert len(json.dumps(projected)) < 1000
