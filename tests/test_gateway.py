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


def test_kilo_auto_free_requires_evidence_before_zero_cost_use(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OCTOPUS_PROFILE", "zero_cost")
    monkeypatch.setenv("OMNIROUTE_ENABLED", "0")

    with pytest.raises(llm.NoEligibleModel) as err:
        llm.complete("agent.plan", MSG, profile="zero_cost", json_mode=True)

    assert transport.calls == []
    kilo = next(item for item in err.value.considered if item["model"] == "kilo/auto-free")
    assert "preuve insuffisante" in kilo["reason"]


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


def test_unrelated_http_400_with_echoed_format_does_not_retry_method():
    class BadRequest(Exception):
        status_code = 400
        body = {"error": {"message": "max_tokens invalide; response_format=json_object"}}

    assert llm._structured_method_error(BadRequest("400")) is False


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

    # OmniRoute GPT-OSS: json_object puis text. La route auto-free prend ensuite le relais.
    # Kilo Auto reste ineligible en zero_cost tant qu'il n'a pas de preuve de banc.
    assert transport.models == [
        "groq/openai/gpt-oss-120b",
        "groq/openai/gpt-oss-120b",
        "auto/best-free",
    ]


def test_http_413_skips_same_provider_for_same_prompt_across_calls(transport, providers_up, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")
    prove("agent.plan", "kilo/auto-free")

    class TooLarge(Exception):
        status_code = 413

    def handler(provider, request):
        if provider["base_url"].endswith("/v1") and request["model"] in (
            "groq/openai/gpt-oss-120b", "auto/best-free"
        ):
            return TooLarge("Request too large")
        return ('{"plan": []}', Usage(prompt_tokens=50, completion_tokens=10))

    transport.handler = handler
    assert llm.complete("agent.plan", MSG, profile="zero_cost").text == '{"plan": []}'
    llm._rate_limit_cooldowns.clear()
    llm._provider_cooldowns.clear()
    assert llm.complete("agent.plan", MSG, profile="zero_cost").text == '{"plan": []}'

    assert transport.models.count("groq/openai/gpt-oss-120b") == 1
    assert "auto/best-free" not in transport.models
    assert len([row for row in calls() if row["status"] == "request_too_large"]) == 1


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
        if request["model"] == "auto/best-free":
            return llm.TransportResult(
                text='{"ok": "auto"}',
                usage=Usage(prompt_tokens=20, completion_tokens=5),
                requested_model=request["model"],
                resolved_model="free/fallback-model",
                resolved_provider="free-provider",
                provider_cost_usd=0.0,
            )
        raise AssertionError(f"modele inattendu : {request['model']}")

    transport.handler = handler

    first = llm.complete("agent.react_step", MSG, profile="zero_cost",
                         json_mode=True, validate=llm.parse_json)
    assert first.model == "omniroute/auto-free"
    assert transport.models == [
        "groq/openai/gpt-oss-120b",
        "auto/best-free",
    ]
    assert "omniroute/devworker-groq" in llm._rate_limit_cooldowns

    second = llm.complete("agent.react_step", MSG, profile="zero_cost",
                          json_mode=True, validate=llm.parse_json)
    assert second.model == "omniroute/auto-free"
    assert transport.models == [
        "groq/openai/gpt-oss-120b",
        "auto/best-free",
        "auto/best-free",
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


def test_flash_fallback_page_inspection_prefers_free_route_over_pinned_flash(
        transport, providers_up, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_ENABLED", "1")
    monkeypatch.setenv("OMNIROUTE_MODEL", "groq/qwen3.8-27b")
    monkeypatch.setenv("OMNIROUTE_ZERO_COST_ATTESTATION", "free_only")

    def handler(provider, request):
        if request["model"] == "groq/qwen3.8-27b":
            return llm.TransportResult(
                text="page visible",
                usage=Usage(prompt_tokens=100, completion_tokens=20),
                requested_model=request["model"],
                resolved_model="free/vision-model",
                resolved_provider="free-provider",
                provider_cost_usd=0.0,
            )
        if request["model"] == "deepseek-flash":
            pytest.fail("DeepSeek ne doit pas être appelé tant que la route gratuite réussit")
        raise AssertionError(f"modele inattendu : {request['model']}")

    transport.handler = handler
    result = llm.complete(
        "web.inspect_page",
        [{"role": "user", "content": "Décris la capture."}],
        profile="flash_fallback",
        pin_model="deepseek/flash",
        needs=("vision",),
    )

    assert result.model == "omniroute/auto-free"
    assert result.cost_usd == 0
    assert transport.models == ["groq/qwen3.8-27b"]


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
        if request["model"] == "auto/best-free":
            return RuntimeError("auto free indisponible")
        if request["model"] == "kilo-auto/free":
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
        "auto/best-free",
        "kilo-auto/free",
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
        "omniroute/auto-free",
    ]
    assert cat.task("agent.synthesize")["candidates"]["zero_cost"][:2] == [
        "omniroute/devworker-groq",
        "omniroute/auto-free",
    ]
    assert cat.model("omniroute/devworker-groq")["structured_methods"] == ["tool_call", "json_object", "text"]
    assert cat.model("kilo/auto-free")["structured_methods"] == ["tool_call", "text"]
    assert cat.model("kilo/ling-3.0-flash-vl-free") is None


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



def test_zero_ceiling_allows_proven_free_calls_but_blocks_paid(transport, providers_up):
    prove("podalux.write_job", "ollama/qwen3.5-4b")
    transport.reply('{"ok": true}')
    with journal.run("atelier", "task", budget_usd=0):
        out = llm.complete("podalux.write_job", MSG, profile="zero_cost", json_mode=True)
        assert out.cost_usd == 0
        free_calls = len(transport.calls)
        with pytest.raises(llm.BudgetExceeded):
            llm.complete("podalux.write_job", MSG, profile="legacy", json_mode=True,
                         pin_model="deepseek/flash")
        assert len(transport.calls) == free_calls
    assert all(row["cost_usd"] == 0 for row in calls())



def test_explicit_paid_profile_shares_two_dollar_ceiling_without_external_allowance(
        transport, providers_up, monkeypatch):
    from agents import runtime
    from octopus import pricing
    # Deterministic simulated costs, no provider shortcut in production.
    monkeypatch.setattr(pricing, "estimate_max_cost", lambda price, *a: 0.7 if price else 0)
    def reply(provider, request):
        if request["model"] != "deepseek-flash":
            return RuntimeError("free route unavailable")
        return llm.TransportResult(
            '{"final":"analysis"}', Usage(prompt_tokens=10, completion_tokens=10),
            request["model"], provider_cost_usd=0.7)
    transport.handler = reply
    with journal.run("atelier", "task:orbit.mission", budget_usd=2, profile="flash_fallback") as parent:
        for role in ("SOUT", "CONVERT"):
            result = runtime.run_agent(role, "analyze", allowed_tools=set())
            assert result["execution_status"] == "completed", result
        result = runtime.run_agent("LEDGER", "analyze", allowed_tools=set())
        assert result["execution_status"] == "llm_unavailable"
        assert "BudgetExceeded" in result["execution_error"]
        assert journal.subtree_cost(parent.id) == pytest.approx(1.4)
    assert transport.models.count("deepseek-flash") == 2
    assert not journal.query("SELECT id FROM spend_allowances")
    assert not journal.query("SELECT id FROM spend_requests")


def test_economical_uses_eligible_free_route_without_paid_cost(transport, providers_up):
    prove("agent.react_step", "groq/gpt-oss-120b")
    transport.handler = by_model({"openai/gpt-oss-120b": '{"final":"ok"}'})
    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)
    assert result.model == "groq/gpt-oss-120b"
    assert result.cost_usd == 0
    assert transport.models == ["openai/gpt-oss-120b"]
    assert calls()[0]["cost_class"] == "free_quota"


def test_economical_bounds_free_attempts_then_uses_deepseek(transport, providers_up):
    prove("podalux.select_offer", "ollama/qwen3.5-2b")
    prove("podalux.select_offer", "groq/gpt-oss-120b")
    prove("podalux.select_offer", "gemini/3.5-flash-lite")
    transport.handler = by_model({"qwen3.5:2b": "pas du JSON",
                                  "openai/gpt-oss-120b": TimeoutError("quota"),
                                  "deepseek-flash": '{"ok":true}'})
    result = llm.complete("podalux.select_offer", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)
    assert result.model == "deepseek/flash"
    assert transport.models == ["qwen3.5:2b", "openai/gpt-oss-120b", "deepseek-flash"]
    assert [row["status"] for row in calls()] == ["invalid", "error", "ok"]
    assert "limite d'essais gratuits" in result.justification["explanation"]
    assert result.justification["paid_reason"] == "fallback_after_failure"
    assert all(row["cost_usd"] == 0 for row in calls()[:2])
    assert calls()[-1]["provider"] == "deepseek" and calls()[-1]["cost_usd"] > 0


def test_economical_goes_directly_to_deepseek_without_eligible_free_model(transport, providers_up):
    from octopus import status
    transport.reply('{"ok":true}')
    result = llm.complete("agent.plan", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)
    assert result.model == "deepseek/flash"
    assert transport.models == ["deepseek-flash"]
    assert result.justification["paid_reason"] == "alternatives_ineligible"
    assert status.llm_routing()["fallbacks_observed"] == 0


def test_economical_invalid_free_json_falls_back_once_to_deepseek(transport, providers_up):
    prove("agent.react_step", "groq/gpt-oss-120b")
    transport.handler = by_model({"openai/gpt-oss-120b": "invalide",
                                  "deepseek-flash": '{"final":"ok"}'})
    result = llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)
    assert result.model == "deepseek/flash"
    assert transport.models == ["openai/gpt-oss-120b", "deepseek-flash"]
    assert [row["status"] for row in calls()] == ["invalid", "ok"]


def test_economical_repairs_control_character_without_another_provider_call(transport, providers_up):
    from agents import runtime
    transport.reply('{"rapport":"ligne 1\nligne 2","determination":'
                    '{"action":"pause","reason":"preuve absente","next_goal":"","permission":""}}',
                    prompt_tokens=10, completion_tokens=20)

    result = llm.complete("agent.decision", MSG, profile="economical", json_mode=True,
                          validate=lambda text: runtime._validate_synthesis_contract(llm.parse_json(text), True))

    assert result.data["rapport"] == "ligne 1\nligne 2"
    assert result.data["determination"]["action"] == "pause"
    assert transport.models == ["deepseek-flash"]
    row = calls()[0]
    assert row["status"] == "ok" and row["cost_usd"] > 0
    assert json.loads(row["justification"])["json_repair"] == "control_chars"


def test_economical_failed_single_repair_falls_back_without_second_repair(transport, providers_up):
    prove("agent.react_step", "groq/gpt-oss-120b")
    transport.handler = by_model({"openai/gpt-oss-120b": '{"final":"line\nbreak" "other":1}',
                                  "deepseek-flash": '{"final":"line\nbreak" "other":1}'})

    with pytest.raises(llm.InvalidOutput):
        llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                     validate=llm.parse_json)

    assert transport.models == ["openai/gpt-oss-120b"] * 2 + ["deepseek-flash"] * 2
    rows = calls()
    assert [row["status"] for row in rows] == ["invalid"] * 4
    assert json.loads(rows[0]["justification"])["json_repair_attempted"] is True
    assert all("json_repair_attempted" not in json.loads(row["justification"]) for row in rows[1:])
    assert [json.loads(row["justification"])["structured_method"] for row in rows] == [
        "json_object", "text", "json_object", "text"]
    assert all(row["cost_usd"] == 0 for row in rows[:2])
    assert all(row["cost_usd"] > 0 for row in rows[2:])


@pytest.mark.parametrize("malformed", [
    '{"rapport":"Observation conservée." "determination":{"action":"pause",'
    '"reason":"Revenu inconnu","next_goal":"","permission":""}}',
    '{\n  "rapport": "Observation conservée."\n  "determination": {"action":"pause",'
    '"reason":"Revenu inconnu","next_goal":"","permission":""}\n}',
])
def test_economical_missing_comma_uses_one_method_alternative_and_counts_both_costs(
        transport, providers_up, malformed):
    from agents import runtime
    from agents.gui.workbench_v2_data import read_snapshot
    from octopus import status

    with pytest.raises(json.JSONDecodeError, match="Expecting ',' delimiter"):
        llm.parse_json(malformed)
    assert llm._repair_json_control_chars(malformed) is None
    valid = '{"rapport":"Observation conservée.","determination":{"action":"pause",'
    valid += '"reason":"Revenu inconnu","next_goal":"","permission":""}}'
    responses = iter([(malformed, .00594607), (valid, .0060291)])

    def respond(provider, request):
        text, cost = next(responses)
        return llm.TransportResult(text, Usage(prompt_tokens=100, completion_tokens=50),
                                   request["model"], resolved_model=request["model"],
                                   resolved_provider="deepseek", provider_cost_usd=cost)

    transport.handler = respond
    with journal.run("octopus", "task:mission", budget_usd=.20, profile="economical") as run:
        result = llm.complete("agent.decision", MSG, json_mode=True, max_tokens=50,
                              validate=lambda text: runtime._validate_synthesis_contract(llm.parse_json(text), True))
        assert journal.subtree_cost(run.id) == pytest.approx(.00594607 + .0060291)
    assert result.data["determination"]["action"] == "pause"
    assert transport.models == ["deepseek-flash"] * 2
    first, second = [request for _, request in transport.calls]
    assert first["response_format"] == {"type": "json_object"}
    assert "response_format" not in second
    assert first["messages"] == second["messages"] == MSG
    assert first["max_tokens"] == second["max_tokens"] == 50
    assert transport.calls[0][0] == transport.calls[1][0]
    rows = calls()
    assert [row["status"] for row in rows] == ["invalid", "ok"]
    assert "JSONDecodeError" in rows[0]["error"] and "Expecting ',' delimiter" in rows[0]["error"]
    assert [row["cost_usd"] for row in rows] == pytest.approx([.00594607, .0060291])
    assert [json.loads(row["justification"])["structured_method"] for row in rows] == ["json_object", "text"]
    assert "sortie invalide [json_object]" in json.dumps(result.justification, ensure_ascii=False)
    snapshot = read_snapshot("octopus")
    assert snapshot["token_cost_usd"] == pytest.approx(.011975)
    assert sum(call["fallback"] for call in snapshot["llm_calls"]) == 1
    assert status.llm_routing()["fallbacks_observed"] == 1
    assert not journal.query("SELECT id FROM spend_allowances")
    assert not journal.query("SELECT id FROM spend_requests")


@pytest.mark.parametrize("budget", [.006, .00594607])
def test_economical_syntax_alternative_checks_remaining_budget_before_call(
        transport, providers_up, monkeypatch, budget):
    from octopus import pricing
    monkeypatch.setattr(pricing, "estimate_max_cost", lambda *args: .001)
    transport.handler = lambda provider, request: llm.TransportResult(
        '{"rapport":"Observation" "other":1}', Usage(prompt_tokens=100, completion_tokens=50),
        request["model"], provider_cost_usd=.00594607)
    with journal.run("octopus", "task:mission", budget_usd=budget, profile="economical") as run:
        with pytest.raises(llm.BudgetExceeded, match="budget du run"):
            llm.complete("agent.decision", MSG, json_mode=True, validate=llm.parse_json)
        assert journal.subtree_cost(run.id) == pytest.approx(.00594607)
    assert transport.models == ["deepseek-flash"]
    assert [row["status"] for row in calls()] == ["invalid", "blocked"]
    assert calls()[1]["cost_usd"] == 0


@pytest.mark.parametrize("text", ['{"rapport":123}', '{"rapport":"ligne\nbreak"}'])
def test_economical_semantic_failure_does_not_retry_even_after_safe_repair(
        transport, providers_up, text):
    from agents import runtime
    transport.reply(text)
    with pytest.raises(llm.InvalidOutput, match="ValueError"):
        llm.complete("agent.decision", MSG, profile="economical", json_mode=True,
                     validate=lambda raw: runtime._validate_synthesis_contract(llm.parse_json(raw), True))
    assert transport.models == ["deepseek-flash"]
    assert calls()[0]["status"] == "invalid"
    if "\n" in text:
        assert json.loads(calls()[0]["justification"])["json_repair_attempted"] is True


@pytest.mark.parametrize("reason", ["permission refusée", "finance refusée", "contenu refusé", "résultat insuffisant"])
def test_economical_arbitrary_validator_value_error_never_triggers_syntax_retry(
        transport, providers_up, reason):
    transport.reply('{"ok":true}')

    def refuse(text):
        llm.parse_json(text)
        raise ValueError(reason)

    with pytest.raises(llm.InvalidOutput, match=reason):
        llm.complete("agent.decision", MSG, profile="economical", json_mode=True, validate=refuse)
    assert transport.models == ["deepseek-flash"]
    assert calls()[0]["status"] == "invalid"


@pytest.mark.parametrize("declared,expected_calls", [
    (["json_object"], 1), (["json_object", "text", "json_object"], 2),
])
def test_economical_syntax_retry_uses_only_one_existing_method_and_stops(
        transport, providers_up, monkeypatch, declared, expected_calls):
    cat = copy.deepcopy(catalog.load())
    cat.raw["models"]["deepseek/flash"]["structured_methods"] = declared
    monkeypatch.setattr(catalog, "load", lambda: cat)
    transport.reply('{"rapport":"Observation" "other":1}')
    with pytest.raises(llm.InvalidOutput, match="JSONDecodeError"):
        llm.complete("agent.decision", MSG, profile="economical", json_mode=True, validate=llm.parse_json)
    assert transport.models == ["deepseek-flash"] * expected_calls
    assert [row["status"] for row in calls()] == ["invalid"] * expected_calls
    assert all(row["cost_usd"] > 0 for row in calls())


def test_economical_unstructured_validator_json_error_does_not_retry(transport, providers_up):
    transport.reply('{"rapport":"Observation" "other":1}')
    with pytest.raises(llm.InvalidOutput, match="JSONDecodeError"):
        llm.complete("agent.decision", MSG, profile="economical", validate=llm.parse_json)
    assert transport.models == ["deepseek-flash"]
    assert json.loads(calls()[0]["justification"])["structured_method"] is None


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ConnectionError("network"),
                                     ValueError("business transport failure")])
def test_economical_transport_error_does_not_trigger_syntax_retry(transport, providers_up, failure):
    transport.handler = lambda provider, request: failure
    with pytest.raises(llm.NoEligibleModel):
        llm.complete("agent.decision", MSG, profile="economical", json_mode=True, validate=llm.parse_json)
    assert transport.models == ["deepseek-flash"]
    assert calls()[0]["status"] == "error"


@pytest.mark.parametrize("status_code", [401, 429])
def test_economical_auth_and_quota_errors_do_not_trigger_syntax_retry(transport, providers_up, status_code):
    class Failure(Exception):
        pass
    error = Failure("request rejected")
    error.status_code = status_code
    transport.handler = lambda provider, request: error
    with pytest.raises(llm.NoEligibleModel):
        llm.complete("agent.decision", MSG, profile="economical", json_mode=True, validate=llm.parse_json)
    assert transport.models == ["deepseek-flash"]
    assert calls()[0]["status"] == "error"
    if status_code == 429:
        assert "deepseek/flash" in llm._rate_limit_cooldowns


def test_legacy_syntax_method_fallback_remains_unchanged(transport, providers_up):
    responses = iter(['{"ok":true "other":1}', '{"ok":true}'])
    transport.handler = lambda provider, request: (next(responses), Usage(prompt_tokens=50, completion_tokens=10))
    result = llm.complete("agent.decision", MSG, pin_model="deepseek/flash", profile="legacy",
                          json_mode=True, validate=llm.parse_json)
    assert result.data == {"ok": True}
    assert transport.models == ["deepseek-flash"] * 2
    assert [row["status"] for row in calls()] == ["invalid", "ok"]


@pytest.mark.parametrize("arguments", ["", "[]", '{"query":"prix","extra":1}', '{"query":42}', "{}"])
def test_economical_invalid_tool_arguments_try_structured_alternative(transport, providers_up, arguments):
    from types import SimpleNamespace
    prove("agent.react_step", "groq/gpt-oss-120b")
    schema = {"type": "object", "properties": {"query": {"type": "string"}},
              "required": ["query"], "additionalProperties": False}
    tool = {"type": "function", "function": {"name": "search", "parameters": schema}}

    def handler(provider, request):
        if request.get("tool_choice") == "required":
            message = SimpleNamespace(tool_calls=[SimpleNamespace(
                function=SimpleNamespace(name="search", arguments=arguments))])
            return llm._declarative_tool_text(request, message)
        return ('{"tool":"search","args":{"query":"prix"}}', Usage(prompt_tokens=20, completion_tokens=10))

    transport.handler = handler
    result = llm.complete("agent.react_step", MSG, profile="economical", json_schema=schema,
                          tool_schemas=[tool], validate=llm.parse_json)

    assert result.data["args"]["query"] == "prix"
    assert transport.models == ["openai/gpt-oss-120b"] * 2
    assert transport.calls[0][1]["tool_choice"] == "required"
    assert transport.calls[1][1]["response_format"] == {"type": "json_object"}
    assert [row["status"] for row in calls()] == ["error", "ok"]


def test_transport_preserves_usage_when_tool_call_is_invalid(monkeypatch):
    from types import SimpleNamespace
    provider = {"base_url": "mock", "api_key_env": None, "timeout_s": 1, "max_retries": 0}
    request = {"model": "deepseek-flash", "messages": MSG, "tool_choice": "required",
               "tools": [{"type": "function", "function": {"name": "search", "parameters": {
                   "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]}
    message = SimpleNamespace(content="", tool_calls=[SimpleNamespace(
        function=SimpleNamespace(name="search", arguments="{}"))])
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10), model="deepseek-flash")
    raw = SimpleNamespace(parse=lambda: response, headers={}, request_id="request-1")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        with_raw_response=SimpleNamespace(create=lambda **kwargs: raw))))
    monkeypatch.setattr(llm, "_transport_override", None)
    llm._clients[("mock", None, 1, 0)] = client

    with pytest.raises(llm.StructuredResponseError) as error:
        llm._transport(provider, request)

    result = error.value.result
    assert str(error.value).startswith("arguments requis absents")
    assert result.usage.prompt_tokens == 20 and result.usage.completion_tokens == 10
    assert result.request_id == "request-1"


def test_paid_malformed_tool_response_is_charged_before_method_fallback(transport, providers_up):
    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    tool = {"type": "function", "function": {"name": "search", "parameters": schema}}
    transport.handler = lambda provider, request: llm.StructuredResponseError(
        "arguments requis absents pour l'outil structuré search: ['query']",
        llm.TransportResult("", Usage(prompt_tokens=20, completion_tokens=10), request["model"],
                            resolved_model=request["model"], resolved_provider="deepseek", provider_cost_usd=0.001))

    with journal.run("octopus", "task:mission", budget_usd=0.001, profile="economical") as run:
        with pytest.raises(llm.BudgetExceeded, match="budget du run"):
            llm.complete("agent.decision", MSG, profile="economical", json_schema=schema,
                         tool_schemas=[tool], validate=llm.parse_json, max_tokens=50)
        assert journal.subtree_cost(run.id) == pytest.approx(0.001)

    assert transport.models == ["deepseek-flash"]
    assert calls()[0]["status"] == "invalid" and calls()[0]["cost_usd"] == pytest.approx(0.001)


def test_economical_method_change_rechecks_consumed_run_budget(transport, providers_up):
    class UnsupportedFormat(Exception):
        status_code = 400
        body = {"error": {"message": "response_format is not supported"}}

    def handler(provider, request):
        run = journal.current_run()
        journal.record_llm_call({"ts": time.time(), "run_id": run.id, "root_run_id": run.root_id,
                                 "business": "octopus", "task": "other", "model": "deepseek/flash",
                                 "provider": "deepseek", "profile": "economical", "cost_class": "paid",
                                 "status": "ok", "cost_usd": 0.001})
        return UnsupportedFormat("400")

    transport.handler = handler
    with journal.run("octopus", "task:mission", budget_usd=0.001, profile="economical"):
        with pytest.raises(llm.BudgetExceeded, match="budget du run"):
            llm.complete("agent.decision", MSG, profile="economical", json_mode=True,
                         max_tokens=50, validate=llm.parse_json)

    assert transport.models == ["deepseek-flash"]


def test_economical_deepseek_structured_400_uses_one_text_alternative(transport, providers_up):
    class UnsupportedFormat(Exception):
        status_code = 400
        body = {"error": {"message": "json_object response_format is not supported"}}

    def handler(provider, request):
        if "response_format" in request:
            return UnsupportedFormat("400")
        return ('{"rapport":"Aucune preuve bancaire."}', Usage(prompt_tokens=20, completion_tokens=10))

    transport.handler = handler
    result = llm.complete("agent.decision", MSG, profile="economical", json_mode=True,
                          validate=llm.parse_json)

    assert result.data == {"rapport": "Aucune preuve bancaire."}
    assert transport.models == ["deepseek-flash", "deepseek-flash"]
    assert "response_format" not in transport.calls[1][1]
    assert [row["status"] for row in calls()] == ["error", "ok"]


def test_economical_invalid_plan_contract_falls_back_to_deepseek(transport, providers_up):
    from agents import runtime
    prove("agent.plan", "groq/gpt-oss-120b")
    transport.handler = by_model({"openai/gpt-oss-120b": '{"tasks":"invalid"}',
                                  "deepseek-flash": '{"tasks":[]}'})
    result = llm.complete("agent.plan", MSG, profile="economical", json_mode=True,
                          validate=lambda text: runtime._validate_plan_contract(llm.parse_json(text)))
    assert result.model == "deepseek/flash"
    assert [row["status"] for row in calls()] == ["invalid", "ok"]


def test_economical_deepseek_failure_stops_without_another_paid_route(transport, providers_up):
    transport.handler = by_model({"deepseek-flash": TimeoutError("indisponible")})
    with pytest.raises(llm.NoEligibleModel, match="agent.plan") as error:
        llm.complete("agent.plan", MSG, profile="economical", json_mode=True,
                     validate=llm.parse_json)
    assert isinstance(error.value.last_error, TimeoutError)
    assert transport.models == ["deepseek-flash"]
    assert calls()[-1]["status"] == "error"


def test_economical_never_journals_a_key_echoed_by_provider(transport, providers_up):
    transport.handler = by_model({"deepseek-flash": RuntimeError("test-key rejected")})
    with pytest.raises(llm.NoEligibleModel):
        llm.complete("agent.plan", MSG, profile="economical")
    assert "test-key" not in calls()[-1]["error"]
    assert "[redacted]" in calls()[-1]["error"]


def test_economical_stops_all_calls_when_run_budget_consumed(transport, providers_up):
    prove("agent.react_step", "groq/gpt-oss-120b")
    transport.reply('{"ok":true}')
    with journal.run("octopus", "mission", budget_usd=0.01, profile="economical") as ctx:
        journal.record_llm_call({"ts": time.time(), "run_id": ctx.id, "root_run_id": ctx.root_id,
                                 "business": "octopus", "task": "agent.plan", "model": "deepseek/v4-pro",
                                 "provider": "deepseek", "profile": "economical", "cost_class": "paid",
                                 "status": "ok", "cost_usd": 0.01})
        with pytest.raises(llm.BudgetExceeded, match="budget du run"):
            llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                         validate=llm.parse_json)
    assert transport.calls == []


def test_resumed_run_keeps_llm_spend_for_budget_gate(transport, providers_up):
    transport.reply('{"ok":true}', prompt_tokens=10, completion_tokens=1000)
    with journal.run("octopus", "task:mission", budget_usd=0.0012, profile="economical") as first:
        llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                     validate=llm.parse_json)
    with journal.run("octopus", "task:mission", budget_usd=0.0012,
                     resume_run_id=first.id) as resumed:
        assert resumed.root_id == first.root_id
        assert journal.subtree_cost(first.id) > 0
        with pytest.raises(llm.BudgetExceeded):
            llm.complete("agent.react_step", MSG, profile="economical", json_mode=True,
                         validate=llm.parse_json)
    assert transport.models == ["deepseek-flash"]
