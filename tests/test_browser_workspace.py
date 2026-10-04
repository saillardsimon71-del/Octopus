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
        if command == 'eval' and 'octopus_commit_boundary' in args[0]:
            return ok({'result': False})
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


def test_requested_full_observation_reaches_controller_with_late_controls(monkeypatch):
    controls = [('button', 'Control ' + str(i), None) for i in range(1, 61)]
    monkeypatch.setitem(SITE, '/', ('Long page', controls, 'Observed text ' * 1500))
    space = _space()
    try:
        brief = space.navigate(ORIGIN + '/')
        assert 'tronquées' in brief['snapshot']
        assert brief['elements_truncated'] is True
        full = space.snapshot(full=True)
        head, _, page = runtime._tool_result_view('browser_snapshot', full).partition('\nPAGE (refs @eN) :\n')
        assert json.loads(head)['elements'][-1]['ref'] == '@e60'
        assert not full['elements_truncated']
        assert 'Control 60' in page and 'tronquées' not in page
        assert len(page) > 15000
    finally:
        space.close()


def test_form_state_keeps_distinct_same_named_fields(monkeypatch):
    monkeypatch.setitem(SITE, '/', ('Form', [('textbox', 'Value', None), ('textbox', 'Value', None)], ''))
    _channel()
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        space.type('@e1', 'first')
        first = space._fingerprint(1, 'click', 'submit')[1]
        space.type('@e2', 'second')
        both = space._fingerprint(1, 'click', 'submit')[1]
        space.type('@e1', 'changed')
        changed = space._fingerprint(1, 'click', 'submit')[1]
        assert len(space._typed[bw._page_key(space._url)]) == 2
        assert len({first, both, changed}) == 3
    finally:
        space.close()


def test_public_control_metadata_preserves_observed_numbers_and_urls():
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        space._refs = {'e1': {'role': 'link', 'name': 'Report 2026', 'href': ORIGIN + '/records/1234'}}
        observed = space._element_metadata()[0]
        assert observed['name'] == 'Report 2026'
        assert observed['href'] == ORIGIN + '/records/1234'
    finally:
        space.close()


def test_metadata_never_turns_long_url_into_another_target_and_full_keeps_label():
    space = _space()
    name, href = 'Visible label ' * 40, ORIGIN + '/' + 'segment/' * 60
    try:
        space.navigate(ORIGIN + '/')
        space._refs = {'e1': {'role': 'link', 'name': name, 'href': href}}
        brief = space._element_metadata()[0]
        assert brief['href'] == href
        assert brief['name_truncated'] is True
        full = space._element_metadata(full=True)[0]
        assert full['name'] == name and full['href'] == href
    finally:
        space.close()


def test_keyboard_effects_distinguish_focus_and_keep_replay_idempotent(monkeypatch):
    _channel()
    space = _space()
    focused = ['button-one']
    try:
        space.navigate(ORIGIN + '/')
        original = space._session.run
        def observe_focus(command, args=(), **kwargs):
            if command == 'eval' and 'octopus_focus_identity' in args[0]:
                return {'success': True, 'data': {'result': focused[0]}}
            return original(command, args, **kwargs)
        monkeypatch.setattr(space._session, 'run', observe_focus)
        space.press('Enter')
        focused[0] = 'button-two'
        second = space.press('Enter')
        assert not second.get('already_done')
        assert space.press('Enter')['already_done']
        assert len([c for c in space._session.commands if c[0] == 'press']) == 2
    finally:
        space.close()


@pytest.mark.parametrize('context', ['?record=second', '#second'])
def test_effect_identity_preserves_observed_url_context(context):
    space = _space()
    space._url = ORIGIN + '/view?record=first'
    first = space._fingerprint(1, 'click', ['button:Confirm', 0])[0]
    space._url = ORIGIN + '/view' + context
    second = space._fingerprint(1, 'click', ['button:Confirm', 0])[0]
    assert first != second


def test_effect_verification_can_observe_late_receipt_without_losing_pre_action_truth(monkeypatch):
    monkeypatch.setitem(SITE, '/', ('Action', [('button', 'Act', None)], 'Text ' * 5000))
    _channel()
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        def receipt(session, ref):
            session.extra_text = '\nRECEIPT_CREATED'
        space._session.on_click = receipt
        result = space.click('@e1', expect='RECEIPT_CREATED')
        assert result['effect']['status'] == 'verified'
    finally:
        space.close()


def test_late_existing_text_is_not_mistaken_for_proof_of_effect(monkeypatch):
    monkeypatch.setitem(SITE, '/', ('Action', [('button', 'Act', None)], 'Text ' * 2500 + '\nALREADY_VISIBLE'))
    _channel()
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        space.click('@e1')
        verified = space.verify('ALREADY_VISIBLE')
        assert verified['present'] and not verified['verified']
    finally:
        space.close()


def test_old_truncated_pre_observation_cannot_certify_a_new_effect(monkeypatch):
    monkeypatch.setitem(SITE, '/', ('Action', [('button', 'Act', None)], 'Text ' * 2500))
    _channel()
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        space.click('@e1')
        row = _rows()[0]
        payload = json.loads(row['payload'])
        payload['pre_text'] = payload['pre_text'][:8000]
        payload.pop('pre_text_complete')
        with tasks._tx() as conn:
            conn.execute('UPDATE channel_actions SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
        space._session.extra_text = '\nNEW_RECEIPT'
        assert space.verify('NEW_RECEIPT')['verified'] is False
        assert _rows()[0]['status'] == 'executed'
    finally:
        space.close()


def test_failed_pre_observation_never_becomes_evidence_of_absence(monkeypatch):
    _channel()
    space = _space()
    try:
        _to_form(space)
        original = space._session.run
        def broken(command, args=(), **kwargs):
            if command == 'get' and list(args) == ['text', 'body']:
                return {'success': False, 'error': 'observation unavailable'}
            return original(command, args, **kwargs)
        monkeypatch.setattr(space._session, 'run', broken)
        before = list(space._session.clicks)
        with pytest.raises(RuntimeError, match='observation unavailable'):
            space.click(_ref(space, 'Envoyer'))
        assert space._session.clicks == before and not _rows()
    finally:
        space.close()


@pytest.fixture
def duplicate_controls(monkeypatch):
    monkeypatch.setitem(SITE, '/', ('Accueil', [('button', 'Open section', '/form'),
                                               ('button', 'Open section', '/done')], ''))
    _channel()


def test_distinct_same_named_buttons_dispatch_independently(duplicate_controls):
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        assert space.click('@e1')['url'] == ORIGIN + '/form'
        space.navigate(ORIGIN + '/')
        assert space.click('@e2')['url'] == ORIGIN + '/done'
        assert space._session.clicks == ['e1', 'e2']
        assert len(_rows()) == 2
    finally:
        space.close()


@pytest.mark.parametrize('ref', ['@e1', '@e2'])
def test_same_named_button_replay_still_dispatches_only_once(duplicate_controls, ref):
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        first = space.click(ref)
        space.navigate(ORIGIN + '/')
        repeated = space.click(ref)
        assert repeated['already_done'] and repeated['action_id'] == first['effect']['action_id']
        assert space._session.clicks == [ref[1:]]
        assert len(_rows()) == 1
    finally:
        space.close()


@pytest.mark.parametrize('shift,second_ref,first_ref', [(1, '@e3', '@e2'), (8, '@e10', '@e9')])
def test_reobserved_button_identity_survives_unrelated_ref_renumbering(
        duplicate_controls, monkeypatch, shift, second_ref, first_ref):
    original_refs = FakeSession._refs
    monkeypatch.setattr(FakeSession, '_refs', lambda session: dict(sorted(original_refs(session).items())))
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        first = space.click('@e2')
        title, controls, text = SITE['/']
        monkeypatch.setitem(SITE, '/', (title, [('link', f'Help {n}', '/') for n in range(shift)] + controls, text))
        space.navigate(ORIGIN + '/')
        assert space._refs[second_ref[1:]]['name'] == 'Open section'
        repeated = space.click(second_ref)
        assert repeated['already_done'] and repeated['action_id'] == first['effect']['action_id']
        assert space._session.clicks == ['e2']
        assert space.click(first_ref)['url'] == ORIGIN + '/form'
        assert space._session.clicks == ['e2', first_ref[1:]]
        assert len(_rows()) == 2
    finally:
        space.close()


def test_duplicate_button_crash_resume_never_repeats_unknown_effect(duplicate_controls, monkeypatch):
    task_id = tasks.enqueue(BUSINESS, 'supervisor.objective_work', {'objective_id': 1})
    key = f't{task_id}'
    space = _space(key, task_id)

    class Killed(BaseException):
        pass

    def kill(session, ref):
        session.path = '/done'
        raise Killed()

    try:
        space.navigate(ORIGIN + '/')
        space._session.on_click = kill
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(bw.Workspace, '_settle', lambda *a, **k: None)
            with pytest.raises(Killed):
                space.click('@e2')
        assert _rows()[0]['status'] == 'proposed'
    finally:
        space.close()
    title, controls, text = SITE['/']
    monkeypatch.setitem(SITE, '/', (title, [('link', 'Help', '/'), *controls], text))
    resumed = _space(key, task_id)
    try:
        resumed.snapshot()
        assert _rows()[0]['status'] == 'ambiguous'
        with pytest.raises(bw.Refused, match='résultat inconnu'):
            resumed.click('@e3')
        assert resumed._session.clicks == []
        assert len(_rows()) == 1
    finally:
        resumed.close()


def test_legacy_label_only_journal_cannot_be_bypassed_with_new_control_identity(duplicate_controls):
    space = _space()
    try:
        space.navigate(ORIGIN + '/')
        space.click('@e1')
        space.navigate(ORIGIN + '/')
        row = _rows()[0]
        payload = json.loads(row['payload'])
        payload.pop('target_identity', None)
        fingerprint, _ = space._fingerprint(row['channel_id'], 'click', 'button:Open section')
        with tasks._tx() as conn:
            conn.execute('UPDATE channel_actions SET payload=?, idempotency_key=? WHERE id=?',
                         (json.dumps(payload), f'browser:{fingerprint}:{space.scope.key}', row['id']))
        before = list(space._session.clicks)
        try:
            repeated = space.click('@e2')
        except bw.Refused:
            pass
        else:
            assert repeated['already_done']
        assert space._session.clicks == before
        assert len(_rows()) == 1
    finally:
        space.close()


# --- permissions ----------------------------------------------------------------------------

def test_untrusted_page_cannot_mutate_without_a_resource_for_this_business():
    space = _space()
    view = space.navigate(ORIGIN + "/")
    assert view["ok"] and "Formulaire de contact" in view["snapshot"]
    assert 'ListMarker "• "' in view['snapshot']
    assert space.click(_ref(space, "Formulaire de contact"))["url"] == ORIGIN + "/form"  # lien = lecture
    with pytest.raises(bw.Refused, match="aucune ressource opérationnelle"):
        space.type(_ref(space, "Nom"), "Alice")
    _channel(access="observe")
    _channel("https://autre-site.example/")
    _channel(ORIGIN + "/", capabilities=("email",))
    with pytest.raises(bw.Refused):
        space.type(_ref(space, "Nom"), "Alice")
    discovered = _channel(status="discovered")
    with pytest.raises(bw.Refused, match="contexte de la page actuelle"):
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


def test_preexisting_expectation_does_not_block_action_or_prove_its_effect():
    _channel()
    space = _space()
    _to_form(space)
    session = FakeSession.instances[-1]
    session.extra_text = "Écrivez-nous"
    before = list(session.clicks)
    view = space.click(_ref(space, "Envoyer"), expect="Écrivez-nous")
    assert session.clicks == before + ['e5'] and len(_rows()) == 1
    assert view['effect']['status'] == 'executed'
    assert view['effect']['verified'] is False
    assert _rows()[0]['evidence_id'] is None
    assert space.verify("Écrivez-nous")['verified'] is False
    assert space.verify("message reçu")['verified'] is True
    _to_form(space)
    again = space.click(_ref(space, "Envoyer"), expect="Écrivez-nous")
    assert again['already_done'] is True and session.clicks.count('e5') == 1


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
