"""Passerelle LLM : routage par profil, preuves du banc, budgets, repli, justification."""
from __future__ import annotations

import copy
import json
import time

import pytest

from agents import agents as ag
from agents import config, db, deepseek
from octopus import catalog, journal, llm
from octopus.pricing import Usage

MSG = [{"role": "user", "content": "Ecris le job en JSON."}]


def prove(task: str, model: str, passed: int = 5, total: int = 5, age_days: float = 0) -> None:
    """Simule un banc passe : `passed` reussites sur `total` essais."""
    with journal.run("octopus", "bench", label="preuve simulee") as ctx:
        for i in range(total):
            journal.record_bench_result({
                "ts": time.time() - age_days * 86400, "bench_run_id": ctx.id, "suite": "t", "task": task,
                "item": f"i{i}", "model": model, "passed": int(i < passed), "score": 1.0,
            })


def calls() -> list[dict]:
    return [dict(r) for r in journal.query("SELECT * FROM llm_calls ORDER BY id")]


def by_model(mapping: dict):
    """Handler de transport : reponse (ou exception) selon le modele API demande."""
    def handler(provider, request):
        out = mapping[request["model"]]
        return out if isinstance(out, BaseException) else (out, Usage(prompt_tokens=50, completion_tokens=10))
    return handler


# --- profil legacy -------------------------------------------------------------------------

def test_legacy_missing_key_fails_before_any_call(transport, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(llm.NoEligibleModel, match="DEEPSEEK_API_KEY absente"):
        deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)
    assert transport.calls == []


def test_transport_error_is_journaled_and_raised(transport):
    transport.handler = lambda p, r: TimeoutError("lent")
    with pytest.raises(TimeoutError):
        deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)
    (row,) = calls()
    assert row["status"] == "error" and "TimeoutError" in row["error"] and row["cost_usd"] == 0


# --- profils economes ----------------------------------------------------------------------

def test_zero_cost_without_evidence_never_pays(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    with pytest.raises(llm.NoEligibleModel) as err:
        deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)
    assert transport.calls == []
    assert all("preuve insuffisante" in c["reason"] for c in err.value.considered)
    assert calls() == []


def test_kilo_ling_can_bootstrap_agent_plan_without_bench(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    transport.reply('{"tasks": []}')

    completion = llm.complete("agent.plan", MSG, profile="zero_cost", json_mode=True)

    assert completion.model == "kilo/ling-3.0-flash-vl-free"
    assert transport.models == ["inclusionai/ling-3.0-flash-vl:free"]
    assert "response_format" not in transport.calls[0][1]


def test_structured_bad_request_retries_same_model_as_prompt_json(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    prove("podalux.write_job", "ollama/qwen3.5-4b")

    def handler(provider, request):
        if "response_format" in request:
            return RuntimeError("[400]: Failed to generate JSON. Please adjust your prompt.")
        return ('{"titre": "fallback texte"}', Usage(prompt_tokens=50, completion_tokens=10))

    transport.handler = handler
    result = deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)

    assert result == {"titre": "fallback texte"}
    assert transport.models == ["qwen3.5:4b", "qwen3.5:4b"]
    assert transport.calls[0][1]["response_format"] == {"type": "json_object"}
    assert "response_format" not in transport.calls[1][1]
    assert [r["status"] for r in calls()] == ["error", "ok"]
    assert json.loads(calls()[1]["justification"])["structured_method"] == "text"


def test_echoed_response_format_does_not_fake_structured_error():
    class EchoedBadRequest(Exception):
        body = {
            "error": {"message": "Invalid message role", "code": "invalid_request_error"},
            "request": {"response_format": {"type": "json_object"}},
        }

    assert llm._structured_method_error(EchoedBadRequest("400 response_format echoed")) is False


def test_failed_generation_field_is_structured_error():
    class FailedGeneration(Exception):
        body = {
            "error": {
                "message": "Failed to generate JSON. Please adjust your prompt.",
                "type": "invalid_request_error",
                "failed_generation": "{not-json",
            }
        }

    assert llm._structured_method_error(FailedGeneration("400")) is True


def test_structured_fallback_keeps_grounding_messages_identical(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    grounded = [
        {"role": "system", "content": "RÈGLE DE PREUVE: toute inconnue reste inconnue."},
        {"role": "user", "content": "Réponds en JSON."},
    ]

    class FailedGeneration(Exception):
        body = {"error": {"message": "Failed to generate JSON", "failed_generation": "x"}}

    def handler(provider, request):
        if "response_format" in request:
            return FailedGeneration("400")
        return ('{"titre": "ok"}', Usage(prompt_tokens=50, completion_tokens=10))

    transport.handler = handler
    result = llm.complete("podalux.write_job", grounded, profile="zero_cost",
                          json_mode=True, validate=llm.parse_json)

    assert result.data == {"titre": "ok"}
    assert len(transport.calls) == 2
    assert transport.calls[0][1]["messages"] == grounded
    assert transport.calls[1][1]["messages"] == grounded


def test_structured_cascade_has_bounded_call_count(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    providers_up.add("groq")

    class FailedGeneration(Exception):
        body = {"error": {"message": "Failed to generate JSON", "failed_generation": "x"}}

    def handler(provider, request):
        if request["model"] == "groq/openai/gpt-oss-120b" and "response_format" in request:
            return FailedGeneration("400")
        return RuntimeError("route indisponible après cette méthode")

    transport.handler = handler
    with pytest.raises(llm.NoEligibleModel):
        llm.complete("agent.synthesize", MSG, profile="zero_cost", json_mode=True, validate=llm.parse_json)

    # OmniRoute GPT-OSS: json_object puis text. Ling: text. Groq direct: indisponible avant transport.
    assert transport.models == [
        "groq/openai/gpt-oss-120b",
        "groq/openai/gpt-oss-120b",
        "inclusionai/ling-3.0-flash-vl:free",
    ]


def test_normal_zero_cost_routes_to_proven_local_model(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    monkeypatch.setattr(deepseek, "_client", lambda: pytest.fail("le mode normal ne doit pas appeler DeepSeek directement"))
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    transport.reply('{"titre": "ok"}')
    assert deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG) == {"titre": "ok"}
    provider, request = transport.calls[0]
    assert provider["kind"] == "local"
    assert request["model"] == "qwen3.5:4b" and request["reasoning_effort"] == "none"
    assert request["response_format"] == {"type": "json_object"}
    (row,) = calls()
    assert row["cost_class"] == "local" and row["cost_usd"] == 0
    assert "paid_reason" not in json.loads(row["justification"])
    legacy = db._conn().execute("SELECT model, cost_usd FROM costs").fetchone()
    assert (legacy["model"], legacy["cost_usd"]) == ("ollama/qwen3.5-4b", 0)


def test_insufficient_or_stale_evidence_is_refused(providers_up):
    rules = catalog.load().evidence_rules()
    prove("podalux.write_job", "ollama/qwen3.5-4b", passed=4, total=4)
    assert "4 essais < 5" in journal.evidence("podalux.write_job", "ollama/qwen3.5-4b", rules)["reason"]
    prove("podalux.write_job", "gemini/3.5-flash", passed=8, total=10)
    assert "80% < 90%" in journal.evidence("podalux.write_job", "gemini/3.5-flash", rules)["reason"]
    prove("podalux.write_job", "groq/qwen3.8-27b", age_days=61)
    assert "aucun banc recent" in journal.evidence("podalux.write_job", "groq/qwen3.8-27b", rules)["reason"]


def test_latest_bench_run_wins(providers_up):
    rules = catalog.load().evidence_rules()
    prove("podalux.write_job", "ollama/qwen3.5-4b", passed=5, total=5)
    prove("podalux.write_job", "ollama/qwen3.5-4b", passed=0, total=5)  # regression apres changement de prompt
    assert journal.evidence("podalux.write_job", "ollama/qwen3.5-4b", rules)["eligible"] is False


class _FakeRateLimit(Exception):
    status_code = 429

    def __init__(self, retry_after: float | None = None):
        super().__init__("429 rate limit reached")
        headers = {} if retry_after is None else {"retry-after": str(retry_after)}
        self.response = type("Response", (), {"status_code": 429, "headers": headers})()


def test_rate_limit_uses_retry_after_header_without_shortening_local_floor():
    assert llm._rate_limit_delay(_FakeRateLimit(12.5)) == pytest.approx(llm._DEFAULT_RATE_LIMIT_COOLDOWN_S)
    assert llm._rate_limit_delay(_FakeRateLimit(75)) == pytest.approx(75)
    assert llm._rate_limit_delay(TimeoutError("lent")) is None


def test_run_profile_overrides_ambient_environment(monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    cat = catalog.load()

    with journal.run("octopus", "mission-test", profile="flash_fallback"):
        ctx = journal.current_run()
        assert llm._resolve_profile(cat, None, ctx) == "flash_fallback"
        assert llm._resolve_profile(cat, "quality_first", ctx) == "quality_first"


def test_rate_limited_route_is_skipped_until_cooldown_expires(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")

    groq_calls = 0

    def handler(provider, request):
        nonlocal groq_calls
        if request["model"] == "groq/openai/gpt-oss-120b":
            groq_calls += 1
            if groq_calls == 1:
                return _FakeRateLimit(60)
            return llm.TransportResult(
                text='{"ok": "groq"}',
                usage=Usage(prompt_tokens=20, completion_tokens=5),
                requested_model=request["model"],
                resolved_model="openai/gpt-oss-120b",
                resolved_provider="groq",
                provider_cost_usd=0.0,
            )
        if request["model"] == "inclusionai/ling-3.0-flash-vl:free":
            return ('{"ok": "ling"}', Usage(prompt_tokens=20, completion_tokens=5))
        raise AssertionError(f"modele inattendu : {request['model']}")

    transport.handler = handler

    first = llm.complete("agent.react_step", MSG, profile="zero_cost",
                         json_mode=True, validate=llm.parse_json)
    assert first.model == "kilo/ling-3.0-flash-vl-free"
    assert transport.models == [
        "groq/openai/gpt-oss-120b",
        "inclusionai/ling-3.0-flash-vl:free",
    ]
    assert "omniroute/devworker-groq" in llm._rate_limit_cooldowns

    second = llm.complete("agent.react_step", MSG, profile="zero_cost",
                          json_mode=True, validate=llm.parse_json)
    assert second.model == "kilo/ling-3.0-flash-vl-free"
    assert transport.models == [
        "groq/openai/gpt-oss-120b",
        "inclusionai/ling-3.0-flash-vl:free",
        "inclusionai/ling-3.0-flash-vl:free",
    ]

    # Expiration simulée : la route redevient candidate et peut réussir.
    _, reason = llm._rate_limit_cooldowns["omniroute/devworker-groq"]
    llm._rate_limit_cooldowns["omniroute/devworker-groq"] = (time.monotonic() - 1, reason)

    third = llm.complete("agent.react_step", MSG, profile="zero_cost",
                         json_mode=True, validate=llm.parse_json)
    assert third.model == "omniroute/devworker-groq"
    assert transport.models[-1] == "groq/openai/gpt-oss-120b"
    assert "omniroute/devworker-groq" not in llm._rate_limit_cooldowns

    rows = calls()
    assert rows[0]["status"] == "error"
    assert json.loads(rows[0]["justification"])["rate_limit_cooldown_s"] == pytest.approx(60)
    skipped = json.loads(rows[2]["justification"])["considered"][0]
    assert skipped["model"] == "omniroute/devworker-groq"
    assert "429 rate limit; cooldown" in skipped["reason"]
    assert len(rows) == 4  # aucune ligne réseau créée pour la route sautée pendant cooldown


def test_fallback_to_next_free_model_after_failure(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    prove("podalux.write_job", "gemini/3.5-flash")
    transport.handler = by_model({"qwen3.5:4b": ConnectionError("ollama arrete"), "gemini-3.5-flash": '{"titre": "g"}'})
    assert deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG) == {"titre": "g"}
    assert [c["status"] for c in calls()] == ["error", "ok"]
    assert [c["attempt"] for c in calls()] == [1, 2]


def test_flash_fallback_uses_deepseek_only_after_free_routes_fail(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    providers_up.update({"groq", "ollama"})

    class RateLimited(Exception):
        status_code = 429
        response = type("Response", (), {"status_code": 429, "headers": {"retry-after": "60"}})()

    def handler(provider, request):
        if request["model"] == "groq/openai/gpt-oss-120b":
            return RateLimited("429 rate limit reached")
        if request["model"] == "inclusionai/ling-3.0-flash-vl:free":
            return ("pas du json", Usage(prompt_tokens=40, completion_tokens=20))
        if request["model"] == "deepseek-flash":
            return ('{"ok": "deepseek"}', Usage(prompt_tokens=400, completion_tokens=100))
        raise AssertionError(f"modele inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete("agent.react_step", MSG, profile="flash_fallback",
                          json_mode=True, validate=llm.parse_json)

    assert result.model == "deepseek/flash"
    assert result.data == {"ok": "deepseek"}
    assert transport.models == [
        "groq/openai/gpt-oss-120b",
        "inclusionai/ling-3.0-flash-vl:free",
        "deepseek-flash",
    ]
    assert result.cost_usd > 0
    assert all(row["model"] != "deepseek/v4-pro" for row in calls())


def test_low_cost_pays_only_when_alternatives_are_ineligible(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "low_cost")
    providers_up.add("ollama")
    transport.reply('{"titre": "ok"}')
    deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)
    assert transport.models == ["deepseek-flash"]  # reference du banc : pas de preuve exigee
    just = json.loads(calls()[0]["justification"])
    assert just["paid_reason"] == "alternatives_ineligible"
    assert "serveur" in just["explanation"] or "coupe par le test" in just["explanation"]
    assert "preuve insuffisante" in just["explanation"]


def test_low_cost_paid_fallback_after_free_failure_is_explained(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "low_cost")
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    transport.handler = by_model({"qwen3.5:4b": ConnectionError("down"), "deepseek-flash": '{"titre": "p"}'})
    deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)
    just = json.loads(calls()[-1]["justification"])
    assert just["paid_reason"] == "fallback_after_failure" and "ConnectionError" in just["explanation"]


def test_invalid_output_falls_back(transport, providers_up):
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    transport.handler = by_model({"qwen3.5:4b": "pas du json", "deepseek-flash": '{"titre": "p"}'})
    c = llm.complete("podalux.write_job", MSG, profile="low_cost", json_mode=True, validate=llm.parse_json)
    assert c.model == "deepseek/flash" and c.data == {"titre": "p"}
    assert [r["status"] for r in calls()] == ["invalid", "invalid", "ok"]


def test_call_json_invalid_output_falls_back_between_free_models(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    prove("podalux.write_job", "gemini/3.5-flash")
    transport.handler = by_model({"qwen3.5:4b": "pas du json", "gemini-3.5-flash": '{"titre": "g"}'})

    assert deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG) == {"titre": "g"}
    assert transport.models == ["qwen3.5:4b", "qwen3.5:4b", "gemini-3.5-flash"]
    assert [r["status"] for r in calls()] == ["invalid", "invalid", "ok"]


def test_vision_invalid_verdict_falls_back_between_free_models(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    prove("podalux.qc_vision", "ollama/qwen3.5-4b")
    prove("podalux.qc_vision", "gemini/3.5-flash")
    valid = {axis: 1 for axis in ag.AXES}
    transport.handler = by_model({
        "qwen3.5:4b": json.dumps({**valid, "hook": 40}),
        "gemini-3.5-flash": json.dumps(valid),
    })

    result = deepseek.vision("GROWTH", "qc_vision", [], "narration", "prompt", validate=ag.validate_verdict)
    assert result["hook"] == 1
    assert transport.models == ["qwen3.5:4b", "gemini-3.5-flash"]
    assert [r["status"] for r in calls()] == ["invalid", "ok"]


def test_invalid_output_without_fallback_raises(transport):
    transport.reply("texte libre")
    with pytest.raises(llm.InvalidOutput):
        llm.complete("podalux.write_job", MSG, pin_model="deepseek/flash", validate=llm.parse_json)


def test_missing_capability_is_refused(transport, providers_up):
    with pytest.raises(llm.NoEligibleModel, match="capacites manquantes : vision"):
        llm.complete("podalux.arbitrate", MSG, pin_model="deepseek/v4-pro", needs=("vision",))
    assert transport.calls == []


def test_sensitive_task_stays_local_outside_legacy(transport, providers_up):
    providers_up.add("ollama")
    with pytest.raises(llm.NoEligibleModel):
        llm.complete("web.describe_page", MSG, profile="quality_first", pin_model="deepseek/flash")
    assert transport.calls == []


def test_unknown_profile_is_an_error():
    with pytest.raises(catalog.CatalogError, match="profil inconnu"):
        llm.complete("podalux.write_job", MSG, profile="turbo")


def test_omniroute_becomes_default_when_enabled(monkeypatch):
    monkeypatch.delenv("OCTOPUS_PROFILE", raising=False)
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    cat = catalog.load()
    assert cat.default_profile == "zero_cost"
    assert cat.model("omniroute/auto-free")["api_model"] == "auto/best-free"  # auto/free n'existe pas dans OmniRoute
    assert cat.task("podalux.write_job")["candidates"]["zero_cost"][0] == "omniroute/auto-free"


def test_explicit_legacy_still_overrides_omniroute_default(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OCTOPUS_PROFILE", "legacy")
    assert catalog.load().default_profile == "legacy"


def test_legacy_wrapper_uses_catalog_default_profile(transport, providers_up, monkeypatch, tmp_path):
    raw = copy.deepcopy(catalog.load().raw)
    raw["default_profile"] = "zero_cost"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OCTOPUS_CATALOG", str(path))
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")
    monkeypatch.delenv("OCTOPUS_PROFILE", raising=False)
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    transport.reply('{"titre": "local"}')

    assert deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG) == {"titre": "local"}
    assert transport.models == ["qwen3.5:4b"]
    assert calls()[0]["profile"] == "zero_cost"


def test_orbit_mission_tasks_keep_their_gateway_contract(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.delenv("OCTOPUS_PROFILE", raising=False)
    cat = catalog.load()
    assert cat.legacy_task("ORBIT", "planification") == "agent.plan"
    assert cat.legacy_task("ORBIT", "synthese") == "agent.synthesize"
    assert cat.legacy_task("ORBIT", "action") == "agent.react_step"
    assert cat.task("agent.plan")["candidates"]["zero_cost"][:2] == [
        "omniroute/devworker-groq",
        "kilo/ling-3.0-flash-vl-free",
    ]
    assert cat.task("agent.synthesize")["candidates"]["zero_cost"][:2] == [
        "omniroute/devworker-groq",
        "kilo/ling-3.0-flash-vl-free",
    ]
    assert cat.model("omniroute/devworker-groq")["structured_methods"] == ["tool_call", "json_object", "text"]
    assert cat.model("kilo/ling-3.0-flash-vl-free")["structured_methods"] == ["tool_call", "text"]


# --- budgets -------------------------------------------------------------------------------

def test_run_budget_blocks_before_the_call(transport):
    transport.reply()
    with journal.run("podalux", "video_cycle", budget_usd=0.0001):
        with pytest.raises(llm.BudgetExceeded, match="budget du run"):
            deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)
    assert transport.calls == []
    (row,) = calls()
    assert row["status"] == "blocked" and row["cost_usd"] == 0


def test_parent_budget_includes_child_runs(transport):
    transport.reply('{"a": 1}', prompt_tokens=10, completion_tokens=1000)  # 0,0006 $
    with journal.run("podalux", "mission", budget_usd=0.0015) as mission:  # estimation avant appel : 2000 tokens max = 0,0012 $
        with journal.run("podalux", "agent"):
            deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG)
            with pytest.raises(llm.BudgetExceeded, match=f"run #{mission.id}"):
                deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG)
    assert journal.subtree_cost(mission.id) == pytest.approx((10 * 0.15 + 1000 * 0.60) / 1e6)
    assert len(transport.calls) == 1


def test_budget_is_per_run_not_lifetime(transport):
    transport.reply('{"a": 1}', prompt_tokens=10, completion_tokens=1000)
    with journal.run("podalux", "video_cycle", budget_usd=0.0015):
        deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG)
    with journal.run("podalux", "video_cycle", budget_usd=0.0015):
        deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG)  # nouveau run : budget neuf
    assert len(transport.calls) == 2


def test_daily_budget(transport, tmp_path, monkeypatch):
    raw = copy.deepcopy(catalog.load().raw)
    raw["budgets"]["daily_usd"] = 0.0005
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OCTOPUS_CATALOG", str(path))
    transport.reply('{"a": 1}', prompt_tokens=10, completion_tokens=100)
    deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG, max_tokens=100)
    with pytest.raises(llm.BudgetExceeded, match="plafond journalier"):
        deepseek.call_json("SOUT", "action", config.MODEL_FLASH, MSG)


def test_local_calls_ignore_budgets(transport, providers_up):
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    transport.reply('{"a": 1}')
    with journal.run("podalux", "video_cycle", budget_usd=0.0):
        c = llm.complete("podalux.write_job", MSG, profile="zero_cost", json_mode=True)
    assert c.cost_usd == 0 and c.model == "ollama/qwen3.5-4b"


# --- coupe-circuit -------------------------------------------------------------------------

class _FakeClient:
    def __init__(self):
        self.kwargs = []
        outer = self

        class Completions:
            def create(self, **kw):
                outer.kwargs.append(kw)
                usage = type("U", (), {"prompt_tokens": 1_000_000, "completion_tokens": 0})()
                message = type("M", (), {"content": '{"legacy": true}'})()
                return type("R", (), {"choices": [type("C", (), {"message": message})()], "usage": usage})()

        self.chat = type("Chat", (), {"completions": Completions()})()


def test_octopus_off_alone_refuses_direct_legacy(transport, monkeypatch):
    monkeypatch.setenv("OCTOPUS", "off")
    monkeypatch.delenv("OCTOPUS_ALLOW_LEGACY_DIRECT", raising=False)
    client = _FakeClient()
    monkeypatch.setattr(deepseek, "_client", lambda: client)

    with pytest.raises(RuntimeError, match="OCTOPUS_ALLOW_LEGACY_DIRECT=1"):
        deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG)

    assert client.kwargs == [] and transport.calls == []


def test_legacy_opt_in_alone_does_not_bypass_gateway(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_ALLOW_LEGACY_DIRECT", "1")
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    transport.reply('{"titre": "local"}')
    monkeypatch.setattr(deepseek, "_client", lambda: pytest.fail("opt-in seul ne doit pas appeler DeepSeek"))

    assert deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG) == {"titre": "local"}
    assert transport.models == ["qwen3.5:4b"]


def test_double_opt_in_restores_direct_legacy_calls(transport, monkeypatch, tmp_path):
    monkeypatch.setenv("OCTOPUS", "off")
    monkeypatch.setenv("OCTOPUS_ALLOW_LEGACY_DIRECT", "1")
    monkeypatch.setenv("OCTOPUS_DB", str(tmp_path / "must-not-exist.db"))
    client = _FakeClient()
    monkeypatch.setattr(deepseek, "_client", lambda: client)
    with journal.run("podalux", "video_cycle", budget_usd=1.0) as ctx:
        assert ctx is None
        assert deepseek.call_json("CONVERT", "redaction_job", config.MODEL_FLASH, MSG) == {"legacy": True}
    assert transport.calls == [] and len(client.kwargs) == 1
    assert not (tmp_path / "must-not-exist.db").exists()
    cost = db._conn().execute("SELECT cost_usd FROM costs").fetchone()["cost_usd"]
    assert cost == pytest.approx(config.PRICES[config.MODEL_FLASH]["in"])


def test_budget_block_falls_back_to_free_model(transport, providers_up):
    prove("podalux.write_job", "gemini/3.5-flash")
    transport.reply('{"a": 1}')
    with journal.run("podalux", "video_cycle", budget_usd=0.0):
        c = llm.complete("podalux.write_job", MSG, profile="quality_first", json_mode=True)
    assert c.model == "gemini/3.5-flash" and transport.models == ["gemini-3.5-flash"]
    assert [r["status"] for r in calls()] == ["blocked", "ok"]


def test_budget_block_without_alternative_raises_budget_exceeded(transport, providers_up):
    transport.reply('{"a": 1}')
    with journal.run("podalux", "video_cycle", budget_usd=0.0):
        with pytest.raises(llm.BudgetExceeded, match="gemini/3.5-flash : preuve insuffisante"):
            llm.complete("podalux.write_job", MSG, profile="quality_first", json_mode=True)
    assert transport.calls == []
