from __future__ import annotations

import pytest

from agents.gui import agnes_missions
from octopus import economy, strategy, tasks


def test_gui_requires_explicit_human_authorization_and_channel():
    with pytest.raises(ValueError, match="Autorisation humaine"):
        agnes_missions.create("octopus", "A calm sea", authorized=False)
    assert strategy.list_items("objective", "octopus") == []
    with pytest.raises(ValueError, match="canal Agnes"):
        agnes_missions.create("octopus", "A calm sea", authorized=True)
    assert strategy.list_items("objective", "octopus") == []


def test_gui_snapshot_is_scoped_and_costs_are_labeled_unknown():
    channel = economy.add_channel("octopus", "agnes_video", "Agnes local", created_by="human",
                                  locator="http://127.0.0.1:8765", capabilities=["agnes_submit"])
    economy.update_channel("octopus", channel, actor="human", status="active", access="act")
    objective_id = agnes_missions.create("octopus", "A calm sea", authorized=True)
    assert strategy.get("objective", objective_id, "octopus")["status"] == "active"
    assert {t["kind"] for t in tasks.list_tasks(business="octopus")} == {
        "supervisor.tick", "agnes.generate_video"}
    before = len(tasks.list_tasks(business="octopus"))
    state = agnes_missions.snapshot("octopus")
    assert state["objectives"][0]["id"] == objective_id
    assert state["channels"][0]["access"] == "act"
    assert state["agnes_api_cost"] == {"nature": "unknown", "value": None}
    assert len(tasks.list_tasks(business="octopus")) == before
    assert agnes_missions.snapshot("other")["objectives"] == []
