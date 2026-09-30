"""Cycle autonome réel avec l'espace de travail navigateur (backend Hermes agent-browser + Chromium).

    objectif OCTOPUS -> Superviseur -> tâche de travail -> Mission -> outils browser_* (Hermes)
      -> observation (arbre d'accessibilité réel) -> décision -> interaction -> vérification réelle
      -> persistance (channel_actions, preuve, task_step) -> nouvelle action autonome -> évaluation

Tout est réel (worker, superviseur, mission ReAct, registre d'outils, CLI agent-browser, Chromium,
proxy de garde, application web locale) sauf le modèle de langage, remplacé par
`browser_lab.ObservationDecider` : il ne connaît ni URL interne, ni ref, ni ordre des pages et
choisit chaque action à partir de la dernière observation. Aucun compte réel, aucune publication,
aucun paiement : l'application est servie sur 127.0.0.1 par le test.

Backend absent -> tests ignorés. Installation (Windows, Linux, macOS) :
    python scripts/install_agent_browser.py
    python -m playwright install chromium
    python -m octopus browser doctor --smoke
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_lab import Lab, ObservationDecider, wait_until  # noqa: E402

from agents import agent_browser, deepseek, runtime, task_handlers, web_guard  # noqa: E402,F401
from octopus import browser_workspace, builtin_handlers, economy, journal, strategy, supervisor, tasks, worker  # noqa: E402,F401

PROJECT = Path(__file__).resolve().parent.parent
BUSINESS = "octopus"
FACTS = {"Raison sociale": "Atelier Nova", "E-mail": "contact@atelier-nova.test",
         "Prestation": "Traduction", "Nombre de documents": "3"}


def _backend_env() -> dict:
    """Le binaire installé par le script vit dans le vrai projet ; les tests isolent DATA_DIR."""
    env = {}
    if not os.environ.get("OCTOPUS_AGENT_BROWSER"):
        installed = PROJECT / "agents" / "data" / "bin" / agent_browser.binary_name()
        if installed.is_file():
            env["OCTOPUS_AGENT_BROWSER"] = str(installed)
    return env


for _key, _value in _backend_env().items():
    os.environ.setdefault(_key, _value)

pytestmark = pytest.mark.skipif(not agent_browser.availability()["ready"],
                                reason="backend navigateur absent : scripts/install_agent_browser.py + "
                                       "playwright install chromium")


@pytest.fixture
def handlers():
    saved = dict(worker.HANDLERS)
    worker.load_handlers(["octopus.builtin_handlers"])
    yield worker.HANDLERS
    worker.HANDLERS.clear()
    worker.HANDLERS.update(saved)


@pytest.fixture
def lab(monkeypatch):
    def make(seed: int = 0) -> Lab:
        instance = Lab(seed=seed).start()
        monkeypatch.setenv(browser_workspace.LAB_ORIGINS_ENV, instance.origin)
        created.append(instance)
        return instance
    created: list[Lab] = []
    yield make
    for instance in created:
        instance.stop()
    browser_workspace.close_detached()


def _grant_site(origin: str) -> int:
    """Geste humain : canal du site, accès act accordé, statut actif."""
    channel_id = economy.add_channel(BUSINESS, "website", "Portail fournisseurs Nova", created_by="human",
                                     locator=origin + "/", capabilities=[browser_workspace.CAPABILITY],
                                     access="act", nature="observed", source_ref=origin + "/")
    economy.update_channel(BUSINESS, channel_id, actor="human", status="active")
    return channel_id


def _objective(origin: str) -> int:
    objective_id = strategy.create(
        "objective", BUSINESS, "Devis traduction Nova", created_by="human",
        statement=(f"Obtenir un devis de traduction auprès du portail fournisseurs Nova ({origin}/) pour la "
                   "société Atelier Nova (contact@atelier-nova.test), prestation Traduction, 3 documents ; "
                   "conserver le récapitulatif."),
        success_criteria="verified_browser_actions>=1 ; kept_browser_files>=1")
    strategy.transition("objective", objective_id, BUSINESS, "active", actor="human")
    return objective_id


def _run_worker(stop: threading.Event) -> threading.Thread:
    thread = threading.Thread(target=worker.loop, kwargs={"stop": stop, "poll_s": 0.05, "lease_s": 30,
                                                          "log": lambda line: None}, daemon=True)
    thread.start()
    return thread


def _objective_status(objective_id: int) -> str:
    return strategy.get("objective", objective_id, BUSINESS)["status"]


@pytest.mark.parametrize("seed", [0, 1])
def test_objective_to_verified_web_workflow_through_supervisor_and_real_browser(handlers, lab, monkeypatch, seed):
    site = lab(seed)
    channel_id = _grant_site(site.origin)
    decider = ObservationDecider(None, FACTS)
    monkeypatch.setattr(deepseek, "call_json", decider)
    objective_id = _objective(site.origin)
    supervisor.bootstrap(BUSINESS, tick_every_s=0.2, retry_delay_s=0.0, max_steps=25)
    sent: list[str] = []
    real_run = agent_browser.Session.run
    monkeypatch.setattr(agent_browser.Session, "run",
                        lambda self, command, *a, **k: sent.append(command) or real_run(self, command, *a, **k))

    stop = threading.Event()
    thread = _run_worker(stop)
    try:
        assert wait_until(lambda: _objective_status(objective_id) == "achieved", timeout=180), decider.calls
    finally:
        stop.set()
        thread.join(timeout=30)
    # `download` d'agent-browser est cassé sous Windows (agent-browser#1659) : jamais envoyé au CLI.
    assert "download" not in sent and "click" in sent

    # Effet réel côté site : exactement un envoi, avec les données de l'objectif.
    assert len(site.submissions) == 1
    assert site.submissions[0] | {"ref": "x"} == {"ref": "x", "societe": "Atelier Nova",
                                                 "email": "contact@atelier-nova.test",
                                                 "prestation": "Traduction", "quantite": "3"}
    # Décisions issues des observations : l'ordre des étapes dépend de la graine du site.
    tools = [c.get("tool") for c in decider.calls]
    first_edit = next(c for c in decider.calls if c.get("tool") in {"browser_type", "browser_select"})
    assert first_edit["tool"] == ("browser_type" if site.contact_first else "browser_select")
    assert {"browser_navigate", "browser_type", "browser_select", "browser_check", "browser_click",
            "browser_download"} <= set(tools)
    assert "Enregistrer le brouillon" not in json.dumps(decider.calls, ensure_ascii=False)

    # Persistance : action à effet vérifiée sur la page réelle, preuve observée, point de reprise.
    work = supervisor.work_tasks(BUSINESS, objective_id)[-1]
    rows = browser_workspace.task_actions(BUSINESS, work["id"])
    confirm = [r for r in rows if "Confirmer" in json.dumps(journal.query(
        "SELECT payload FROM channel_actions WHERE id=?", (r["id"],))[0]["payload"], ensure_ascii=False)]
    assert len(confirm) == 1 and confirm[0]["status"] == "verified" and confirm[0]["evidence_id"]
    evidence = strategy.get("evidence", confirm[0]["evidence_id"], BUSINESS)
    assert evidence["nature"] == "observed" and "/devis/confirmation/DV-1001" in evidence["source_ref"]
    assert all(r["status"] in {"executed", "verified"} for r in rows)
    assert journal.query("SELECT channel_id FROM channel_actions WHERE id=?", (confirm[0]["id"],))[0][
        "channel_id"] == channel_id
    checkpoint = tasks.step_value(work["id"], browser_workspace.CHECKPOINT_KEY, None)
    assert checkpoint and checkpoint["url"].startswith(site.origin)

    # Nouvelle action autonome après la vérification : récapitulatif téléchargé depuis la page.
    inbox = browser_workspace._files_dir(BUSINESS) / "inbox" / f"t{work['id']}"
    assert (inbox / "DV-1001.txt").is_file(), {"inbox": sorted(p.name for p in inbox.iterdir()),
                                              "download_results": [o for o in decider.observed
                                                                   if o.get("tool") == "browser_download"]}
    recap = json.loads((inbox / "DV-1001.txt").read_text(encoding="utf-8"))
    assert recap["ref"] == "DV-1001" and recap["societe"] == "Atelier Nova"
    assert sorted(p.name for p in inbox.iterdir()) == ["DV-1001.txt"]
    assert [f["file"] for f in browser_workspace.kept_files(BUSINESS, work["id"])] == ["DV-1001.txt"]

    # Superviseur : les DEUX critères mesurés (registre + disque), objectif clos.
    parts = {p["metric"]: p for p in work["output"]["objective_result"]["parts"]}
    assert work["output"]["success"] is True and work["output"]["observed"] == 2
    assert parts["verified_browser_actions"]["observed"] == 1 and parts["kept_browser_files"]["observed"] == 1
    decisions = strategy.list_items("decision", BUSINESS)
    assert any(d["decision"] == "satisfied" for d in decisions)


def test_verified_submission_without_the_kept_recap_does_not_achieve_the_objective(handlers, lab, monkeypatch):
    """L'objectif exige aussi de conserver le récapitulatif : une soumission vérifiée ne suffit pas."""
    site = lab(2)
    site.recap_downloadable = False  # le lien existe, mais le serveur ne livre aucun fichier
    _grant_site(site.origin)
    decider = ObservationDecider(None, FACTS)
    monkeypatch.setattr(deepseek, "call_json", decider)
    objective_id = _objective(site.origin)
    supervisor.bootstrap(BUSINESS, tick_every_s=0.2, retry_delay_s=0.0, max_steps=25, max_attempts=1)

    stop = threading.Event()
    thread = _run_worker(stop)
    try:
        assert wait_until(lambda: _objective_status(objective_id) != "active", timeout=180), decider.calls
    finally:
        stop.set()
        thread.join(timeout=30)

    assert _objective_status(objective_id) == "paused"  # épuisé, jamais « achieved »
    assert len(site.submissions) == 1
    work = supervisor.work_tasks(BUSINESS, objective_id)[-1]
    parts = {p["metric"]: p for p in work["output"]["objective_result"]["parts"]}
    assert work["output"]["success"] is False
    assert parts["verified_browser_actions"]["success"] is True and parts["kept_browser_files"]["observed"] == 0
    assert "browser_download" in [c.get("tool") for c in decider.calls]
    assert not any(d["decision"] == "satisfied" for d in strategy.list_items("decision", BUSINESS))


def test_worker_killed_during_submission_resumes_without_blind_repeat(handlers, lab, monkeypatch, tmp_path):
    """Le worker (processus séparé) est tué pendant l'envoi : le serveur a reçu la confirmation
    mais OCTOPUS ne l'a jamais su. La reprise ne renvoie pas ; elle vérifie sur le site."""
    site = lab(3)
    _grant_site(site.origin)
    objective_id = _objective(site.origin)
    supervisor.bootstrap(BUSINESS, tick_every_s=0.5, retry_delay_s=0.0, max_steps=25)

    env = {**os.environ, "PODALUX_ROOT": str(Path(os.environ["OCTOPUS_HOME"])),
           browser_workspace.LAB_ORIGINS_ENV: site.origin}
    proc = subprocess.Popen([sys.executable, str(PROJECT / "tests" / "browser_lab.py"), "worker",
                             "--facts", json.dumps(FACTS), "--lease", "3"], cwd=str(PROJECT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    killed = threading.Event()

    def kill_worker(ref):
        proc.kill()  # arrêt brutal, multiplateforme : aucune écriture après ce point
        proc.wait()
        killed.set()

    site.on_confirm = kill_worker
    try:
        assert killed.wait(180), proc.stdout.read() if proc.poll() is not None else "worker jamais tué"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    assert len(site.submissions) == 1
    work = supervisor.work_tasks(BUSINESS, objective_id)[-1]
    pending = browser_workspace.task_actions(BUSINESS, work["id"])
    assert [r["status"] for r in pending][-1] == "proposed"  # intention persistée, résultat inconnu

    site.on_confirm = None
    decider = ObservationDecider(None, FACTS)
    monkeypatch.setattr(deepseek, "call_json", decider)
    stop = threading.Event()
    thread = _run_worker(stop)
    try:
        assert wait_until(lambda: _objective_status(objective_id) == "achieved", timeout=180), \
            json.dumps({"calls": decider.calls, "observed": decider.observed,
                        "tasks": [(t["id"], t["kind"], t["status"], t["attempts"], str(t.get("error"))[:300])
                                  for t in tasks.list_tasks(business=BUSINESS, limit=50)]},
                       ensure_ascii=False, indent=1)
    finally:
        stop.set()
        thread.join(timeout=30)

    assert len(site.submissions) == 1, "aucun second envoi après la reprise"
    rows = browser_workspace.task_actions(BUSINESS, work["id"])
    assert rows[-1]["status"] == "verified" and rows[-1]["evidence_id"]
    assert "interruption" not in (rows[-1]["reason"] or "")
    # La reprise a tenté le même envoi, a été refusée, puis a vérifié sur « Mes demandes ».
    tools = [c.get("tool") for c in decider.calls]
    assert "browser_verify" in tools
    resumed = tasks.get(work["id"])
    assert resumed["attempts"] == 2 and resumed["status"] == "done"


def test_without_human_act_grant_the_workflow_stays_read_only(lab, monkeypatch):
    site = lab(0)
    decider = ObservationDecider(None, FACTS)
    monkeypatch.setattr(deepseek, "call_json", decider)
    goal = f"Demander un devis au portail {site.origin}/ pour Atelier Nova."
    result = runtime.run_mission(goal, business=BUSINESS, max_steps_per_agent=12, max_duration_s=120)
    assert site.posts == [] and site.submissions == []
    assert journal.query("SELECT COUNT(*) AS n FROM channel_actions")[0]["n"] == 0
    steps = result["results"][0]["steps"]
    assert any("canal actif avec accès 'act'" in s["result"] for s in steps if s["tool"] == "browser_type")
    assert "Accès en écriture non accordé" in str(result["results"][0]["final"])


def test_real_browser_egress_is_filtered_by_the_guard_proxy(lab):
    site = lab(0)
    space = browser_workspace.Workspace(browser_workspace.Scope("guard", BUSINESS, None), web_guard.BrowseState())
    try:
        view = space.navigate(site.origin + "/fuite")
        assert view["ok"] and "Offre spéciale" in view["snapshot"]
        blocked = " ".join(view.get("blocked_requests") or []) + " ".join(space._proxy.blocked)
        wait_until(lambda: "10.0.0.1" in blocked + " ".join(space._proxy.blocked), timeout=5)
        blocked += " ".join(space._proxy.blocked)
        assert "169.254.169.254" in blocked
        # Seule l'application a été contactée. Les services propres au navigateur (Chrome for
        # Testing sous Windows embarque les clés Google) sont refusés avant connexion et listés à part.
        unexpected = [u for u in space._proxy.allowed_urls if not u.startswith(site.origin + "/")]
        assert not unexpected, json.dumps({"allowed_unexpected": unexpected,
                                           "browser_internal_refused": space._proxy.browser_internal,
                                           "chromium": agent_browser.chromium_executable()}, indent=1)
        assert not any(web_guard.browser_service(u) for u in space._proxy.allowed_urls)
        # Un hôte privé non déclaré reste refusé, même demandé explicitement.
        refused = browser_workspace.call_on(space, "navigate", url="http://127.0.0.1:9/")
        assert refused.get("refused")
        # Après lecture d'un compte (taint), même le laboratoire est refusé : aucune sortie.
        space.state.account_read = True
        again = browser_workspace.call_on(space, "navigate", url=site.origin + "/")
        assert again.get("refused")
    finally:
        space.close()
