"""Automatic taxonomic review is retired; historical outputs remain readable."""
from copy import deepcopy
import pytest
from agents import runtime
from test_business_signal_evidence import evidence_case


@pytest.mark.parametrize("target", [1, 2, 999])
def test_candidate_count_never_triggers_extra_provider_calls(monkeypatch, target):
    raw, acquisitions = evidence_case()
    calls = []
    def model(agent, stage, *_args, **kwargs):
        calls.append(stage)
        if stage == "planification":
            return {"tasks": [{"role": "SOUT", "task": "Travail libre"}]}
        assert stage == "synthese"
        return {"rapport": "Interprétation inconnue", "business_signals": [deepcopy(raw)]}
    monkeypatch.setattr(runtime.deepseek, "call_json", model)
    monkeypatch.setattr(runtime, "run_agent", lambda *a, **k: {"steps": acquisitions[0]["steps"], "execution_status": "completed"})
    result = runtime.run_mission("Étudier", business="octopus", business_signal_focus=True, business_signal_target=target)
    assert calls == ["planification", "synthese"]
    assert result["business_signal_review_status"] == "not_requested"
    assert "actionable_business_signal_count" not in result
    assert result["business_signals"][0]["nature"] == "inferred"
