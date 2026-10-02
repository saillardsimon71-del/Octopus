"""Structural capture with scripted replies, never a measure of LLM intelligence.

CLI: python tests/pursuit_prompt_capture.py --repo PATH --out PATH
The same helper can inspect the exact base checkout without modifying it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


TASK = ("Observer les demandes publiques et les alternatives payantes pour identifier "
        "des problèmes dont la résolution pourrait être achetée ; comparer les acheteurs possibles.")


def capture():
    from agents import agent_browser, deepseek, runtime, task_handlers
    from octopus import journal, strategy, supervisor, tasks
    from octopus import capability_acquisition as acquisition, strategy_separation as separation

    captured, steps, calls = {}, {}, []
    ctx = SimpleNamespace(id=1, owner="fixture", business="octopus", input={
        "goal": "Réexaminer l'état et déterminer la prochaine action admissible.",
        "profile": "economical"})
    learning = {"lessons": [], "invalidated_hypotheses": [], "available_evidence_ids": []}
    browser = {"agent_browser": "fixture://binary", "chromium": "fixture://chromium",
               "ready": True, "expected_version": "fixture", "target": "fixture"}

    def mission(goal, **kwargs):
        captured.update(goal=goal, kwargs={k: v for k, v in kwargs.items() if k != "checkpoint"})
        return {"fixture": True}

    with patch("octopus.enabled", return_value=True), \
         patch.object(strategy, "learning_context", return_value=copy.deepcopy(learning)), \
         patch.object(strategy, "list_items", return_value=[]), \
         patch.object(separation, "recorded_strategies", return_value=[]), \
         patch.object(acquisition, "recorded", return_value=[]), \
         patch.object(tasks, "step_value", return_value={}), \
         patch.object(tasks, "save_step", side_effect=lambda tid, key, value, **kw:
                      steps.update({key: copy.deepcopy(value)})), \
         patch.object(tasks, "answer_for", return_value=None), \
         patch.object(agent_browser, "availability", return_value=browser), \
         patch.object(task_handlers, "_run", side_effect=lambda ctx, fn: fn()), \
         patch.object(runtime, "run_mission", side_effect=mission):
        supervisor._pursuit_mission(ctx, {"id": 1, "statement": supervisor.FINALITY,
                                        "created_by": "octopus"})

    def reply(agent, task, model, messages, **kwargs):
        calls.append({"task": task, "messages": copy.deepcopy(messages)})
        if task == "planification":
            answer = {"tasks": [{"role": "SOUT", "task": TASK}]}
        elif task == "action":
            answer = {"final": "Réponse de fixture sans observation ni résultat économique."}
        else:
            answer = {"rapport": "Synthèse de fixture, sans preuve économique.",
                      "determination": {"action": "continue", "reason": "Incertitude stratégique à réduire.",
                                        "next_goal": "Observer les demandes publiques pour identifier les acheteurs.",
                                        "permission": ""}}
        validate = kwargs.get("validate")
        return validate(answer) if validate else answer

    with patch.object(journal, "current_run", return_value=SimpleNamespace(
            business="octopus", profile="economical")), \
         patch.object(runtime.cancel, "requested", return_value=False), \
         patch.object(runtime, "_budget_exhausted", return_value=False), \
         patch.object(deepseek, "call_json", side_effect=reply), \
         patch.object(runtime, "run_agent", side_effect=lambda role, goal, **kw:
                      runtime._run_agent(role, goal, kw["max_steps"], False, kw["allowed_tools"])), \
         patch.object(runtime.db, "post", return_value=0), \
         patch.object(runtime.db, "decide", return_value=None):
        result = runtime._run_mission(captured["goal"], 6, set(supervisor.PURSUIT_TOOLS),
                                      determination=True)
    return {**captured, "steps": steps, "calls": calls, "result": result,
            "state": json.loads(captured["goal"].rsplit("\n", 1)[-1])}


def metrics(captured):
    def size(text):
        return {"chars": len(text), "estimated_tokens": round(len(text) / 4)}
    return {"goal": size(captured["goal"]), "calls": {
        call["task"]: size("".join(m["content"] for m in call["messages"]))
        for call in captured["calls"]}, "provider_calls": 0, "quality_test": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))

    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden in structural capture")

    socket.socket.connect = socket.socket.connect_ex = forbidden
    socket.create_connection = socket.getaddrinfo = forbidden
    for key in tuple(os.environ):
        if key.endswith("API_KEY"):
            os.environ.pop(key)
    with tempfile.TemporaryDirectory(prefix="pursuit-prompt-capture-") as data:
        os.environ.update(PODALUX_ROOT=data, OCTOPUS_HOME=data,
                          OCTOPUS_DB=str(Path(data) / "fixture.db"), OCTOPUS="on")
        captured = capture()
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "metrics.json").write_text(json.dumps(metrics(captured), indent=2))
        (args.out / "capture.json").write_text(json.dumps(captured, ensure_ascii=False,
                                                        indent=2, default=lambda value: sorted(value)))
        print(json.dumps(metrics(captured)))
