"""Espace de travail navigateur : politiques OCTOPUS sur le backend Hermes, sans navigateur.

Le CLI agent-browser est remplacé par `FakeSession` (même contrat JSON que le vrai, observé dans
le laboratoire réel) ; journal, registre `channel_actions`, preuves, proxy de garde, superviseur et
registre d'outils sont réels. Le cycle avec Chromium réel : tests/test_browser_workspace_e2e.py.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from agents import agent_browser, runtime, web_guard
from octopus import browser_workspace as bw
from octopus import economy, journal, strategy, supervisor, tasks

ORIGIN = "http://127.0.0.1:8123"
BUSINESS = "octopus"

SITE = {
    "/": ("Accueil", [("link", "Formulaire de contact", "/form"), ("link", "Supprimer mon compte", "/")], ""),
    "/form": ("Contact", [("textbox", "Nom", None), ("textbox", "Mot de passe", None),
                          ("combobox", "Sujet", None), ("checkbox", "J'accepte", None),
                          ("button", "Envoyer", "/done")], "Écrivez-nous."),
    "/done": ("Merci", [("link", "Retour", "/"), ("link", "Télécharger le récapitulatif", "file:R-42.txt")],
              "Merci, message reçu. Référence R-42"),
}


class FakeSession:
    """Contrat JSON de agent-browser 0.26 (open/snapshot/get/fill/click/...)."""
    instances: list["FakeSession"] = []

    def __init__(self, name, *, proxy_url, profile_dir=None, headed=False, download_dir=None):
        self.name, self.proxy_url, self.download_dir = name, proxy_url, download_dir
        self.path = None
        self.commands: list[tuple[str, list]] = []
        self.clicks: list[str] = []
        self.on_click = None
        self.closed = 0
        self.extra_text = ""
        FakeSession.instances.append(self)

    def _page(self):
        return SITE[self.path]

    def _refs(self):
        return {f"e{i + 1}": {"role": role, "name": name} for i, (role, name, _) in enumerate(self._page()[1])}

    def run(self, command, args=(), *, timeout=None, should_abort=None):
        args = list(args)
        self.commands.append((command, args))
        ok = lambda data=None: {"success": True, "data": data or {}, "error": None}
        if command == "close":
            self.closed += 1
            return ok({"closed": True})
        if command == "open":
            self.path = args[0][len(ORIGIN):] or "/"
            return ok({"url": args[0], "title": self._page()[0]})
        if command == "snapshot":
            lines = [f'- {role} "{name}" [ref=e{i + 1}]' for i, (role, name, _) in enumerate(self._page()[1])]
            text = self._page()[2] + self.extra_text
            return ok({"origin": ORIGIN + self.path, "refs": self._refs(),
                       "snapshot": "\n".join([f'- heading "{self._page()[0]}" [level=1]',
                                              f'- StaticText "{text}"', '- ListMarker "• "', *lines])})
        if command == "get":
            if args[0] == "url":
                return ok({"url": ORIGIN + self.path})
            if args[0] == "title":
                return ok({"title": self._page()[0]})
            return ok({"text": self._page()[0] + "\n" + self._page()[2] + self.extra_text})
        if command == "click":
            key = args[0].lstrip("@")
            self.clicks.append(key)
            if self.on_click is not None:
                result = self.on_click(self, key)
                if result is not None:
                    return result
            target = self._page()[1][int(key[1:]) - 1][2]
            if target and target.startswith("file:"):
                self._download(target[len("file:"):])  # pièce jointe : la page ne change pas
            elif target:
                self.path = target
            return ok({"clicked": args[0]})
        if command == "download":
            # agent-browser 0.26 sous Windows : répertoire canonicalisé en \\?\C:\... -> Chrome annule.
            return {"success": False, "data": None, "error": "Download was canceled"}
        return ok()

    def _download(self, name):
        """Comme Chrome avec `--download-path` (comportement `allow`) : fichier partiel puis final,
        de façon asynchrone, sous le nom suggéré par le serveur."""
        partial = os.path.join(self.download_dir, name + ".crdownload")
        with open(partial, "w", encoding="utf-8") as handle:
            handle.write("recap")

        def finish():
            time.sleep(0.3)
            with open(partial, "a", encoding="utf-8") as handle:
                handle.write(" R-42")
            os.replace(partial, os.path.join(self.download_dir, name))
        threading.Thread(target=finish, daemon=True).start()

    def close(self):
        self.run("close")


@pytest.fixture(autouse=True)
def lab_origin(monkeypatch):
    monkeypatch.setenv(bw.LAB_ORIGINS_ENV, ORIGIN)
    FakeSession.instances.clear()
    yield
    bw.close_detached()


def _space(key: str = "t1", task_id: int | None = None) -> bw.Workspace:
    return bw.Workspace(bw.Scope(key, BUSINESS, task_id), web_guard.BrowseState(), session_factory=FakeSession)


def _channel(locator: str = ORIGIN + "/", *, access: str = "act", status: str = "active",
             capabilities=(bw.CAPABILITY,)) -> int:
    channel_id = economy.add_channel(BUSINESS, "website", "Site", created_by="human", locator=locator,
                                     capabilities=list(capabilities), access=access, nature="observed",
                                     source_ref=locator)
    if status != "discovered":
        economy.update_channel(BUSINESS, channel_id, actor="human", status=status)
    return channel_id


def _ref(space: bw.Workspace, name: str) -> str:
    return "@" + next(k for k, v in space._refs.items() if v["name"] == name)


def _to_form(space: bw.Workspace) -> None:
    space.navigate(ORIGIN + "/")
    space.click(_ref(space, "Formulaire de contact"))


def _rows() -> list[dict]:
    return [dict(r) for r in journal.query("SELECT * FROM channel_actions ORDER BY id")]


# --- permissions ----------------------------------------------------------------------------

def test_reading_is_free_but_editing_requires_a_human_act_channel_for_this_site():
    space = _space()
    view = space.navigate(ORIGIN + "/")
    assert view["ok"] and "Formulaire de contact" in view["snapshot"]
    assert "ListMarker" not in view["snapshot"]  # bruit élagué
    assert space.click(_ref(space, "Formulaire de contact"))["url"] == ORIGIN + "/form"  # lien = lecture
    with pytest.raises(bw.Refused, match="accès 'act' accordé par l'humain"):
        space.type(_ref(space, "Nom"), "Alice")
    _channel(access="observe")
    _channel("https://autre-site.example/")
    _channel(ORIGIN + "/", capabilities=("email",))
    with pytest.raises(bw.Refused):
        space.type(_ref(space, "Nom"), "Alice")
    discovered = _channel(status="discovered")
    with pytest.raises(bw.Refused, match=f"canal #{discovered}"):
        space.type(_ref(space, "Nom"), "Alice", channel_id=discovered)
    granted = _channel()
    assert space.type(_ref(space, "Nom"), "Alice")["ok"]
    assert space.select(_ref(space, "Sujet"), "Devis", channel_id=granted)["ok"]
    assert _rows() == []  # saisir n'est pas envoyer : rien au registre avant le clic


def test_an_agent_cannot_grant_itself_act_access():
    with pytest.raises(Exception, match="seul un humain"):
        economy.add_channel(BUSINESS, "website", "Site", created_by="agent:GROWTH", locator=ORIGIN + "/",
                            capabilities=[bw.CAPABILITY], access="act")


def test_risky_link_is_an_effect_and_needs_a_channel():
    space = _space()
    space.navigate(ORIGIN + "/")
    with pytest.raises(bw.Refused):
        space.click(_ref(space, "Supprimer mon compte"))
    assert FakeSession.instances[-1].clicks == []


# --- registre, vérification, reprise --------------------------------------------------------

def test_intent_is_persisted_before_the_click_then_verified_on_the_real_page():
    channel_id = _channel()
    space = _space()
    _to_form(space)
    space.type(_ref(space, "Nom"), "Alice")
    seen = []
    FakeSession.instances[-1].on_click = lambda s, key: seen.append([r["status"] for r in _rows()])
    view = space.click(_ref(space, "Envoyer"), expect="message reçu")
    assert seen == [["proposed"]]  # écrit AVANT l'action externe
    assert view["effect"]["status"] == "verified" and view["url"].endswith("/done")
    row = _rows()[-1]
    assert row["status"] == "verified" and row["channel_id"] == channel_id and row["action"] == "browser.click"
    evidence = strategy.get("evidence", row["evidence_id"], BUSINESS)
    assert evidence["nature"] == "observed" and evidence["source_ref"] == ORIGIN + "/done"
    payload = json.loads(row["payload"])
    assert "Alice" not in row["payload"] and payload["target"] == "button:Envoyer"


def test_expect_already_visible_before_the_action_is_refused_without_effect():
    _channel()
    space = _space()
    _to_form(space)
    before = list(FakeSession.instances[-1].clicks)
    with pytest.raises(bw.Refused, match="déjà visible"):
        space.click(_ref(space, "Envoyer"), expect="Écrivez-nous")
    assert FakeSession.instances[-1].clicks == before and _rows() == []


def test_hard_kill_leaves_proposed_then_resume_marks_ambiguous_and_refuses_the_repeat():
    _channel()
    space = _space("t9")
    _to_form(space)
    space.type(_ref(space, "Nom"), "Alice")

    class Killed(BaseException):
        pass

    def kill(session, key):
        session.path = "/done"  # le site a reçu l'envoi...
        raise Killed()          # ...mais le processus meurt avant d'en être informé
    FakeSession.instances[-1].on_click = kill
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bw.Workspace, "_settle", lambda *a, **k: None)  # mort brutale : aucune écriture
        with pytest.raises(Killed):
            space.click(_ref(space, "Envoyer"), expect="message reçu")
    assert [r["status"] for r in _rows()] == ["proposed"]

    resumed = _space("t9")  # nouveau processus, même tâche
    view = resumed.navigate(ORIGIN + "/form")
    assert [r["status"] for r in _rows()] == ["ambiguous"]
    assert view["reprise"]["ambiguous_actions"][0]["target"] == "button:Envoyer"
    resumed.type(_ref(resumed, "Nom"), "Alice")
    with pytest.raises(bw.Refused, match="résultat inconnu"):
        resumed.click(_ref(resumed, "Envoyer"), expect="message reçu")
    assert FakeSession.instances[-1].clicks == []  # jamais répété à l'aveugle
    resumed.navigate(ORIGIN + "/done")
    check = resumed.verify("message reçu", action_id=_rows()[0]["id"])
    assert check["verified"] is True and _rows()[0]["status"] == "verified"
    resumed.navigate(ORIGIN + "/form")
    resumed.type(_ref(resumed, "Nom"), "Alice")
    again = resumed.click(_ref(resumed, "Envoyer"), expect="message reçu")
    assert again["already_done"] is True and FakeSession.instances[-1].clicks == []


def test_in_process_interruption_is_ambiguous_not_failed():
    _channel()
    space = _space()
    _to_form(space)

    def interrupted(session, key):
        raise KeyboardInterrupt()
    FakeSession.instances[-1].on_click = interrupted
    with pytest.raises(KeyboardInterrupt):
        space.click(_ref(space, "Envoyer"))
    assert _rows()[-1]["status"] == "ambiguous"


def test_verify_rejects_text_that_was_already_there_before_the_action():
    _channel()
    space = _space()
    _to_form(space)
    view = space.click(_ref(space, "Envoyer"))
    assert view["effect"]["status"] == "executed"
    check = space.verify("Merci")  # présent avant ? non : titre de la page de confirmation
    assert check["verified"] is True
    _to_form(space)
    space.type(_ref(space, "Nom"), "Bob")
    space.click(_ref(space, "Envoyer"))
    space.navigate(ORIGIN + "/form")
    weak = space.verify("Écrivez-nous")
    assert weak["verified"] is False and "déjà présent" in weak["note"]


def test_duplicate_in_another_task_is_blocked_until_the_human_resolves_it():
    _channel()
    first = _space("t1")
    _to_form(first)
    FakeSession.instances[-1].on_click = lambda s, k: {"success": False, "error": "connection reset"}
    result = first.click(_ref(first, "Envoyer"))
    assert result["status"] == "ambiguous"
    action_id = _rows()[-1]["id"]

    second = _space("t2")
    _to_form(second)
    with pytest.raises(bw.Refused, match="autre tâche"):
        second.click(_ref(second, "Envoyer"))
    with pytest.raises(PermissionError):
        bw.resolve(BUSINESS, action_id, executed=False, actor="agent:GROWTH")
    assert bw.resolve(BUSINESS, action_id, executed=False)["status"] == "failed"
    assert second.click(_ref(second, "Envoyer"))["effect"]["status"] == "executed"


def test_known_pre_dispatch_error_is_failed_and_can_be_retried():
    _channel()
    space = _space()
    _to_form(space)
    session = FakeSession.instances[-1]
    session.on_click = lambda s, k: {"success": False, "error": "Element not found for ref @e5"}
    assert space.click(_ref(space, "Envoyer"))["status"] == "failed"
    session.on_click = None
    assert space.click(_ref(space, "Envoyer"))["effect"]["status"] == "executed"
    assert [r["status"] for r in _rows()] == ["executed"]  # même ligne, relancée


def test_timeout_during_click_is_ambiguous():
    _channel()
    space = _space()
    _to_form(space)
    FakeSession.instances[-1].on_click = lambda s, k: {"success": False, "error": "délai", "indeterminate": True}
    assert space.click(_ref(space, "Envoyer"))["status"] == "ambiguous"


# --- anti-exfiltration, observations ----------------------------------------------------------

def test_secrets_passwords_and_stale_refs_are_refused():
    _channel()
    space = _space()
    with pytest.raises(bw.Refused, match="secret"):
        space.navigate(ORIGIN + "/?key=sk-abcdefghijklmnopqrstuvwxyz0123")
    _to_form(space)
    with pytest.raises(bw.Refused, match="secret"):
        space.type(_ref(space, "Nom"), "ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    with pytest.raises(bw.Refused, match="réservé à l'humain"):
        space.type(_ref(space, "Mot de passe"), "hunter2")
    with pytest.raises(bw.Refused, match="absente du dernier snapshot"):
        space.click("@e99")
    with pytest.raises(bw.Refused, match="forme @e12"):
        space.click("#submit")
    FakeSession.instances[-1].extra_text = " clé sk-abcdefghijklmnopqrstuvwxyz0123"
    view = space.snapshot()
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in view["snapshot"] and "masqué" in view["snapshot"]
    assert view["source"] == web_guard.UNTRUSTED_NOTE


def test_non_declared_private_hosts_and_tainted_sessions_are_refused():
    space = _space()
    with pytest.raises(bw.Refused):
        space.navigate("http://127.0.0.1:9999/")
    with pytest.raises(bw.Refused):
        space.navigate("file:///etc/passwd")
    space.state.account_read = True
    with pytest.raises(bw.Refused):
        space.navigate(ORIGIN + "/")


def test_guard_proxy_is_the_browser_egress_and_follows_the_mission_taint():
    space = _space()
    space.navigate(ORIGIN + "/")
    assert FakeSession.instances[-1].proxy_url == space._proxy.url
    assert space._guard(ORIGIN + "/x") is True
    assert space._guard("http://10.0.0.1/collect") is False
    space.state.account_read = True
    assert space._guard(ORIGIN + "/x") is False


def test_effect_budget_is_capped(monkeypatch):
    monkeypatch.setattr(bw, "MAX_EFFECTS", 1)
    _channel()
    space = _space()
    _to_form(space)
    space.click(_ref(space, "Envoyer"))
    space.navigate(ORIGIN + "/form")
    space.type(_ref(space, "Nom"), "Autre")
    with pytest.raises(bw.Refused, match="plafond"):
        space.click(_ref(space, "Envoyer"))


def test_checkpoint_reopens_the_last_page_of_the_task_after_restart():
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1})
    space = _space(f"t{task_id}", task_id)
    _to_form(space)
    space.close()
    assert tasks.step_value(task_id, bw.CHECKPOINT_KEY, None)["url"] == ORIGIN + "/form"
    resumed = _space(f"t{task_id}", task_id)
    view = resumed.snapshot()  # pas de navigation : la reprise rouvre la dernière page
    assert view["url"] == ORIGIN + "/form" and view["reprise"]["checkpoint_url"] == ORIGIN + "/form"


def test_download_goes_to_the_task_inbox_and_upload_only_from_the_outbox():
    _channel()
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1})
    space = _space(f"t{task_id}", task_id)
    space.navigate(ORIGIN + "/done")
    got = space.download(_ref(space, "Télécharger le récapitulatif"), "recap.txt")
    inbox = bw.task_inbox(BUSINESS, f"t{task_id}")
    assert got["ok"] and got["bytes"] == len("recap R-42"), got
    assert os.path.samefile(got["file"], inbox / "recap.txt")
    assert sorted(p.name for p in inbox.iterdir()) == ["recap.txt"]  # ni partiel ni nom d'origine
    # Le chemin donné à Chromium est celui de la tâche, et la commande `download` (cassée sous
    # Windows) n'est jamais utilisée.
    session = FakeSession.instances[-1]
    assert os.path.samefile(session.download_dir, inbox)
    assert not any(c == "download" for c, _ in session.commands)
    assert tasks.step_value(task_id, bw.FILES_KEY)[0]["sha256"] == got["sha256"]
    with pytest.raises(bw.Refused, match="nom de fichier"):
        space.download(_ref(space, "Retour"), "../evil.txt")
    _to_form(space)
    with pytest.raises(bw.Refused, match="absent"):
        space.upload(_ref(space, "Nom"), "offre.pdf")
    outbox = bw.outbox_dir(BUSINESS)
    outbox.mkdir(parents=True)
    (outbox / "offre.pdf").write_bytes(b"%PDF-1.4")
    assert space.upload(_ref(space, "Nom"), "offre.pdf")["ok"]


def test_a_link_that_downloads_nothing_is_reported_not_counted(monkeypatch):
    monkeypatch.setattr(bw, "DOWNLOAD_START_S", 0.3)
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1})
    space = _space(f"t{task_id}", task_id)
    space.navigate(ORIGIN + "/done")
    got = space.download(_ref(space, "Retour"), "recap.txt")  # simple navigation, aucun fichier
    assert got["ok"] is False and "aucun fichier" in got["error"]
    assert tasks.step_value(task_id, bw.FILES_KEY, []) == [] and bw.kept_files(BUSINESS, task_id) == []


def test_kept_files_are_measured_on_disk_not_on_the_tool_result():
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1})
    space = _space(f"t{task_id}", task_id)
    space.navigate(ORIGIN + "/done")
    assert space.download(_ref(space, "Télécharger le récapitulatif"), "DV.txt")["ok"]
    assert [f["file"] for f in bw.kept_files(BUSINESS, task_id)] == ["DV.txt"]
    path = bw.task_inbox(BUSINESS, f"t{task_id}") / "DV.txt"
    path.write_text("altéré", encoding="utf-8")
    assert bw.kept_files(BUSINESS, task_id) == []  # contenu différent du fichier téléchargé
    path.unlink()
    assert bw.kept_files(BUSINESS, task_id) == []  # fichier disparu
    tasks.save_step(task_id, bw.FILES_KEY, [{"file": "../../secrets.txt", "sha256": "x"}])
    assert bw.kept_files(BUSINESS, task_id) == []  # jamais hors de l'espace de la tâche


def test_objective_requiring_a_kept_file_is_not_satisfied_by_a_verified_submission_alone():
    _channel()
    criterion = supervisor.criterion_for({"success_criteria": "verified_browser_actions>=1 ; kept_browser_files>=1"})
    assert criterion == {"all": [{"metric": "verified_browser_actions", "gte": 1},
                                 {"metric": "kept_browser_files", "gte": 1}]}
    assert supervisor.parse_criterion("verified_browser_actions>=1 et kept_browser_files>=1") == criterion
    assert supervisor.parse_criterion("verified_browser_actions>=1 et client signé") is None  # rien d'inventé
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1})
    objective = {"id": 1, "business": BUSINESS}
    space = _space(f"t{task_id}", task_id)
    _to_form(space)
    space.type(_ref(space, "Nom"), "Alice")
    assert space.click(_ref(space, "Envoyer"), expect="message reçu")["effect"]["status"] == "verified"

    output = supervisor.work_output(BUSINESS, objective, criterion, {"execution_status": "completed"},
                                    task_id=task_id)
    parts = {p["metric"]: p for p in output["objective_result"]["parts"]}
    assert output["success"] is False and output["observed"] == 1 and output["objective_result"]["target"] == 2
    assert parts["verified_browser_actions"]["success"] and not parts["kept_browser_files"]["success"]
    assert output["human_boundary"] is None  # rien d'ambigu : simple nouvelle tentative possible

    assert space.download(_ref(space, "Télécharger le récapitulatif"), "R-42.txt")["ok"]
    output = supervisor.work_output(BUSINESS, objective, criterion, {"execution_status": "completed"},
                                    task_id=task_id)
    assert output["success"] is True and output["observed"] == 2
    reason = supervisor._reason("satisfied", output, 1, 2)
    assert "verified_browser_actions : 1 >= 1" in reason and "kept_browser_files : 1 >= 1" in reason
    goal = supervisor.goal_text({"id": 1, "business": BUSINESS, "statement": "Devis",
                                 "success_criteria": "verified_browser_actions>=1 ; kept_browser_files>=1"})
    assert "kept_browser_files = fichiers téléchargés" in goal and "Tous les critères sont exigés" in goal


# --- intégration runtime / superviseur --------------------------------------------------------

def test_runtime_registers_browser_tools_and_closes_sessions_with_the_mission(monkeypatch):
    assert {"browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_select",
            "browser_check", "browser_press", "browser_scroll", "browser_back", "browser_verify",
            "browser_download", "browser_upload"} <= set(runtime.TOOLS)
    monkeypatch.setattr(agent_browser, "Session", FakeSession)
    with bw.mission_scope():
        refusal, result = runtime.TOOLS.dispatch("browser_navigate", {"url": ORIGIN + "/"})
        assert refusal is None and result["ok"]
        refusal, refused = runtime.TOOLS.dispatch("browser_type", {"ref": "@e1", "text": "x"})
        assert refused["refused"] is True
        view = runtime._tool_result_view("browser_navigate", result)
        assert view.startswith("{") and "PAGE (refs @eN) :" in view and "Formulaire de contact" in view
    assert FakeSession.instances[-1].closed >= 2  # fermeture de démarrage + fin de mission
    assert runtime.TOOLS.dispatch("browser_click", {"ref": 3})[0].startswith("invalid_tool_arguments:")


def test_supervisor_measures_verified_browser_actions_and_escalates_ambiguity():
    _channel()
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1})
    objective = {"id": 1, "business": BUSINESS}
    criterion = supervisor.criterion_for({"success_criteria": "verified_browser_actions>=1"})
    assert criterion == {"metric": "verified_browser_actions", "gte": 1}

    space = _space(f"t{task_id}", task_id)
    _to_form(space)
    FakeSession.instances[-1].on_click = lambda s, k: {"success": False, "error": "socket closed"}
    space.click(_ref(space, "Envoyer"), expect="message reçu")
    output = supervisor.work_output(BUSINESS, objective, criterion, {"execution_status": "completed"},
                                    task_id=task_id)
    assert output["success"] is False and output["browser_ambiguous_actions"]
    assert "browser resolve" in output["human_boundary"]

    FakeSession.instances[-1].on_click = None
    FakeSession.instances[-1].path = "/done"
    space.verify("message reçu")
    output = supervisor.work_output(BUSINESS, objective, criterion, {"execution_status": "completed"},
                                    task_id=task_id)
    assert output["success"] is True and output["observed"] == 1 and output["human_boundary"] is None


def test_closed_browser_read_can_reopen_without_replaying_an_effect(monkeypatch):
    task_id = tasks.enqueue(BUSINESS, "supervisor.objective_work", {"objective_id": 1, "pursuit": True})
    space = _space(f"t{task_id}", task_id)
    monkeypatch.setattr(bw, "workspace", lambda: space)
    assert bw.call("navigate", url=ORIGIN + "/")["ok"]
    saved = tasks.step_value(task_id, "browser.observation")
    original = FakeSession.run

    def run(session, command, args=(), **kwargs):
        if session.path is None and command == "snapshot":
            return {"success": False, "error": "Target page, context or browser has been closed"}
        return original(session, command, args, **kwargs)

    monkeypatch.setattr(FakeSession, "run", run)
    FakeSession.instances[-1].path = None
    lost = bw.call("snapshot")
    assert lost["ok"] is False and "closed" in lost["error"]
    assert tasks.step_value(task_id, "browser.observation")["snapshot"] == saved["snapshot"]
    reopened = bw.call("navigate", url=ORIGIN + "/")
    assert reopened["ok"] and "Accueil" in reopened["snapshot"]
    assert tasks.pending_human_requests(BUSINESS) == []
    assert _rows() == []
    assert space._commands < bw.MAX_COMMANDS


def test_goal_text_lists_only_human_granted_browser_sites():
    objective_id = strategy.create("objective", BUSINESS, "Devis", created_by="human", statement="Obtenir un devis")
    objective = strategy.get("objective", objective_id, BUSINESS)
    assert "browser_*" not in supervisor.goal_text(objective)
    channel_id = _channel()
    text = supervisor.goal_text(objective)
    assert f"canal #{channel_id}" in text and ORIGIN in text


def test_cli_resolve_and_actions(capsys):
    from octopus.__main__ import main
    _channel()
    space = _space("t3")
    _to_form(space)
    FakeSession.instances[-1].on_click = lambda s, k: {"success": False, "error": "reset"}
    space.click(_ref(space, "Envoyer"))
    action_id = _rows()[-1]["id"]
    assert main(["browser", "actions", BUSINESS]) == 0
    assert "ambiguous" in capsys.readouterr().out
    assert main(["browser", "resolve", BUSINESS, str(action_id), "executed"]) == 0
    assert _rows()[-1]["status"] == "executed" and _rows()[-1]["decided_by"] == "human"


def test_a_new_attempt_of_the_same_objective_never_repeats_a_verified_submission():
    """Tentative 1 : envoi vérifié mais critère incomplet (ex. fichier non conservé) ; le
    superviseur relance. Tentative 2 : même envoi -> `already_done`, aucun clic. Un AUTRE objectif
    peut, lui, faire une nouvelle demande identique (décision de l'IA, sous canal humain)."""
    _channel()

    def attempt(objective_id: int, n: int) -> dict:
        task_id = tasks.enqueue(BUSINESS, supervisor.WORK_KIND, {"objective_id": objective_id},
                                idempotency_key=f"w{objective_id}-{n}")
        space = _space(f"t{task_id}", task_id)
        _to_form(space)
        space.type(_ref(space, "Nom"), "Alice")
        clicks = len(FakeSession.instances[-1].clicks)
        result = space.click(_ref(space, "Envoyer"), expect="message reçu")
        result["_clicked"] = len(FakeSession.instances[-1].clicks) - clicks
        space.close()
        return result

    first = attempt(7, 1)
    assert first["effect"]["status"] == "verified" and first["_clicked"] == 1
    retry = attempt(7, 2)
    assert retry["already_done"] is True and retry["_clicked"] == 0
    assert retry["action_id"] == first["effect"]["action_id"] and retry["previous_task_id"]
    other = attempt(8, 1)
    assert other["effect"]["status"] == "verified" and other["_clicked"] == 1
    assert [r["status"] for r in _rows()] == ["verified", "verified"]
