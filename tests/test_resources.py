"""Environnement de ressources reelles : inventaire, sondes, frontiere humaine, promotion en canal."""
from __future__ import annotations

import json

import pytest

from octopus import builtin_handlers, economy, resource_probes, resources, tasks, worker  # noqa: F401

QUIET = dict(log=lambda s: None)
DECL = '''
[[resource]]
key = "site"
kind = "site"
label = "Site publie"
locator = "https://exemple.test"
capabilities = ["publier_page"]
probe = "http"

[[resource]]
key = "banque"
kind = "finance"
label = "Compte bancaire"
needs = ["login", "2fa"]
'''


@pytest.fixture
def declared(isolated):
    (isolated / "resources.toml").write_text(DECL, encoding="utf-8")
    resources._cache.clear()
    return isolated


# --- inventaire ------------------------------------------------------------------------------

def test_declarations_reject_unknown_human_boundary(isolated):
    (isolated / "resources.toml").write_text('[[resource]]\nkey = "x"\nneeds = ["telepathie"]\n', encoding="utf-8")
    resources._cache.clear()
    with pytest.raises(resources.ResourceError, match="frontiere humaine inconnue"):
        resources.declarations()


def test_declarations_reject_duplicate_key(isolated):
    (isolated / "resources.toml").write_text('[[resource]]\nkey = "a"\n\n[[resource]]\nkey = "a"\n', encoding="utf-8")
    resources._cache.clear()
    with pytest.raises(resources.ResourceError, match="deux fois"):
        resources.declarations()


def test_sync_is_idempotent_and_keeps_observed_state(declared):
    first = resources.sync()
    assert set(first["added"]) == {"site", "banque"}
    resources.update("site", actor="human", state="available", nature="observed", source_ref="https://exemple.test")
    second = resources.sync()
    assert second["added"] == [] and second["updated"] == []
    assert resources.get("site")["state"] == "available"  # une declaration n'ecrase pas un constat


def test_new_resource_starts_declared_not_available(declared):
    resources.sync()
    assert resources.get("banque")["state"] == "declared"
    assert resources.get("banque")["access"] == "none"
    assert resources.get("banque")["nature"] == "unverified"


# --- sondes ----------------------------------------------------------------------------------

def test_check_records_what_the_probe_saw(declared, monkeypatch):
    resources.sync()
    monkeypatch.setitem(resource_probes.PROBES, "http",
                        lambda r: resource_probes.ProbeResult(True, "HTTP 200", source_ref=r["locator"]))
    item = resources.check("site")
    assert item["state"] == "available" and item["nature"] == "observed"
    assert item["source_ref"] == "https://exemple.test" and item["last_check_ok"] == 1


def test_check_failure_marks_unavailable(declared, monkeypatch):
    resources.sync()
    monkeypatch.setitem(resource_probes.PROBES, "http",
                        lambda r: resource_probes.ProbeResult(False, "HTTP 502", source_ref=r["locator"]))
    assert resources.check("site")["state"] == "unavailable"


def test_resource_without_probe_stays_declared(declared):
    resources.sync()
    item = resources.check("banque")  # aucune sonde : rien ne peut etre suppose
    assert item["state"] == "declared" and item["last_check_ok"] is None
    assert "constater" in item["last_check_detail"]


def test_broken_probe_proves_nothing(declared, monkeypatch):
    resources.sync()
    monkeypatch.setitem(resource_probes.PROBES, "http", lambda r: (_ for _ in ()).throw(RuntimeError("boum")))
    item = resources.check("site")
    assert item["state"] == "declared" and item["last_check_ok"] is None and "erreur" in item["last_check_detail"]


def test_env_probe_reads_real_environment(declared, monkeypatch):
    resources.declare("cle", "api", "Service", created_by="test", probe="env",
                      probe_args={"vars": ["DEMO_TOKEN_TEST"]})
    monkeypatch.delenv("DEMO_TOKEN_TEST", raising=False)
    assert resources.check("cle")["state"] == "unavailable"
    monkeypatch.setenv("DEMO_TOKEN_TEST", "x")
    assert resources.check("cle")["state"] == "available"


# --- acces et honnetete ------------------------------------------------------------------------

def test_only_a_human_grants_the_right_to_act(declared):
    resources.sync()
    with pytest.raises(resources.ResourceError, match="seul un humain"):
        resources.update("site", actor="agent:ORBIT", access="act")
    assert resources.update("site", actor="human", access="act")["access"] == "act"
    assert resources.update("site", actor="agent:ORBIT", access="observe")["access"] == "observe"


def test_observed_requires_a_source(declared):
    resources.sync()
    with pytest.raises(resources.ResourceError, match="source_ref"):
        resources.update("banque", actor="human", nature="observed")


def test_blocked_lists_what_prevents_use(declared, monkeypatch):
    resources.sync()
    monkeypatch.setitem(resource_probes.PROBES, "http",
                        lambda r: resource_probes.ProbeResult(True, "HTTP 200", source_ref=r["locator"]))
    resources.check("site")
    reasons = {b["key"]: b["reason"] for b in resources.blocked()}
    assert reasons["banque"] == "jamais constatee"
    assert reasons["site"] == "acces non accorde"  # disponible mais sans droit d'usage
    resources.update("site", actor="human", access="observe")
    assert "site" not in {b["key"] for b in resources.blocked()}


# --- frontiere humaine -------------------------------------------------------------------------

def test_request_waits_for_the_human_then_resumes_alone(declared, monkeypatch):
    resources.sync()
    task_id = resources.request("banque", "login", "Connecte le compte bancaire", created_by="agent:ORBIT")
    assert tasks.get(task_id)["kind"] == "resources.acquire"

    worker.run_one("w", kinds=["resources.acquire"], **QUIET)
    assert tasks.get(task_id)["status"] == "waiting_human"
    pending = tasks.pending_human_requests()
    assert pending and pending[0]["question"] == "Connecte le compte bancaire"
    assert json.loads(pending[0]["context"])["key"] == "banque"

    probes = []
    monkeypatch.setitem(resource_probes.PROBES, "manual_ok",
                        lambda r: resource_probes.ProbeResult(True, "compte connecte", source_ref="human"))
    resources._write("banque", {"probe": "manual_ok"})
    tasks.answer(pending[0]["id"], "c'est fait")
    result = worker.run_one("w", kinds=["resources.acquire"], **QUIET)
    assert result["status"] == "done"
    assert result["output"]["state"] == "available"
    assert resources.get("banque")["state"] == "available"
    assert probes == []  # rien d'autre n'a ete suppose


def test_request_on_unknown_resource_declares_it_first(declared):
    task_id = resources.request("nouvelle_boutique", "create", "Cree la boutique", created_by="agent:ORBIT",
                                kind="marketplace", label="Boutique")
    item = resources.get("nouvelle_boutique")
    assert item["state"] == "declared" and item["kind"] == "marketplace"
    assert tasks.get(task_id)["input"]["need"] == "create"


def test_request_rejects_an_invented_boundary(declared):
    with pytest.raises(resources.ResourceError, match="frontiere humaine inconnue"):
        resources.request("banque", "voyance", "?", created_by="agent:ORBIT")


# --- liens avec les primitives existantes --------------------------------------------------------

def test_promote_to_channel_reuses_the_economic_channel(declared, monkeypatch):
    resources.sync()
    monkeypatch.setitem(resource_probes.PROBES, "http",
                        lambda r: resource_probes.ProbeResult(True, "HTTP 200", source_ref=r["locator"]))
    resources.check("site")
    channel_id = resources.promote_to_channel("site", "podalux", created_by="agent:ORBIT")
    assert resources.promote_to_channel("site", "podalux", created_by="agent:ORBIT") == channel_id  # pas de doublon
    channel = economy.channels("podalux")[0]
    assert channel["id"] == channel_id and channel["access"] == "none"
    assert resources.get("site")["channel_id"] == channel_id


def test_audit_handler_reports_state_without_deciding(declared, monkeypatch):
    monkeypatch.setitem(resource_probes.PROBES, "http",
                        lambda r: resource_probes.ProbeResult(True, "HTTP 200", source_ref=r["locator"]))
    task_id = tasks.enqueue("octopus", "resources.audit", {})
    result = worker.run_one("w", kinds=["resources.audit"], **QUIET)
    assert result["status"] == "done"
    output = result["output"]
    assert output["available"] == ["site"]
    assert {b["key"] for b in output["blocked"]} == {"banque", "site"}  # site : disponible, acces non accorde
    events = [e for e in journal_events(task_id) if e["type"] == "resources.audited"]
    assert events and json.loads(events[0]["data"])["checked"] == 2


def journal_events(task_id: int) -> list[dict]:
    from octopus import journal
    return [dict(r) for r in journal.query("SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,))]
