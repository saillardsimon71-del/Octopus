"""Offline adversarial harness. Every provider, site and effect is a fake."""
from __future__ import annotations

import json
import subprocess
import sys
import sqlite3

import pytest

from agents import config, runtime, web_guard
from agents.gui.workbench_v2_data import read_snapshot
from octopus import actions, browser_workspace as bw, economy, journal, mandates, resources, tasks, worker

BUSINESS = 'digital_b2b'
URL = 'https://new-platform.example/dashboard'


@pytest.fixture(autouse=True)
def no_external_executors(monkeypatch):
    monkeypatch.setattr(actions, '_EXECUTORS', {})
    monkeypatch.setattr(actions, '_load_configured_executors', lambda: None)


def channel(url, kind='browser_form', business=BUSINESS, access='none'):
    cid = economy.add_channel(business, kind, url, created_by='human' if access == 'act' else 'agent:test',
                              locator=url, capabilities=['browser_workspace', 'email_send', 'browser_form_submit'],
                              access=access)
    economy.update_channel(business, cid, actor='human', status='active')
    return cid


def propose(cid, key='send-one', business=BUSINESS, action='submit', **kwargs):
    return actions.propose(business, cid, action, {'text': 'offre professionnelle'}, requested_by='agent:test',
                           idempotency_key=key, **kwargs)


def public(cid, url, text=None, business=BUSINESS):
    return mandates.qualify_public(cid, business, source_url=url,
                                   observed_text=text or 'Entreprise services B2B. Contact professionnel. contact@company.example')


def account(key='new-platform', businesses=(BUSINESS,), ownership='operator', dedicated=True):
    return resources.configure_account(key, actor='human', provider='Plateforme inconnue', label='Compte dédié',
                                       url=URL, domains=['new-platform.example'], businesses=list(businesses),
                                       ownership=ownership, dedicated=dedicated, authenticated_text='Déconnexion')


def own_grant(effects=('read', 'publish'), key='new-platform', business=BUSINESS):
    return mandates.grant(business, 'Compte opérationnel', 'owned_account', effects, actor='human', resource_keys=[key])


class FakeAccountSession:
    instances = []
    logged_in = True
    secret = 'my-password-and-OTP-482913'

    def __init__(self, name, **kwargs):
        self.name, self.kwargs = name, kwargs
        self.commands = []
        self.url = URL
        self.closed = False
        self.clicks = 0
        self.extra = ''
        self.on_click = None
        self.instances.append(self)

    def close(self):
        self.closed = True

    def run(self, command, args=(), **kwargs):
        self.commands.append((command, list(args)))
        ok = lambda data=None: {'success': True, 'data': data or {}}
        if command == 'open':
            self.url = args[0]
            return ok({'url': self.url})
        if command == 'eval':
            # Exactly one fixed boolean predicate; no snapshots or cookie access in onboarding.
            assert 'document.body.innerText.includes(marker)' in args[0]
            assert 'input[type=password]' in args[0]
            return ok({'result': self.logged_in})
        if command == 'get':
            return ok({args[0]: self.url if args[0] == 'url' else 'Déconnexion · Compte B2B ' + self.extra})
        if command == 'snapshot':
            return ok({'origin': self.url, 'snapshot': '- heading "Déconnexion"\n- button "Publier" [ref=e1]\n'
                       '- button "Payout" [ref=e2]\n- textbox "Mot de passe" [ref=e3]\n'
                       '- button "Modifier le profil" [ref=e4]\n' + self.extra,
                       'refs': {'e1': {'role': 'button', 'name': 'Publier'},
                                'e2': {'role': 'button', 'name': 'Payout'},
                                'e3': {'role': 'textbox', 'name': 'Mot de passe'},
                                'e4': {'role': 'button', 'name': 'Modifier le profil'}}})
        if command == 'click':
            self.clicks += 1
            if self.on_click:
                return self.on_click()
            self.extra = 'Publication réussie'
        return ok()


def space(monkeypatch, key='new-platform', business=BUSINESS):
    monkeypatch.delenv(bw.LAB_ORIGINS_ENV, raising=False)
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda _: [__import__('ipaddress').ip_address('8.8.8.8')])
    tid = tasks.enqueue(business, 'resources.account_work', {'browser_resource_key': key})
    return bw.Workspace(bw.Scope('t' + str(tid), business, tid), web_guard.BrowseState(), session_factory=FakeAccountSession)


def test_a_no_mandate_no_rights():
    calls = []
    actions.register_executor('browser_form', 'submit', lambda c,p: calls.append(p), cost_class='local')
    cid = channel('https://company.example/contact')
    assert not public(cid, 'https://company.example/contact')
    assert propose(cid)['status'] == 'blocked'
    assert calls == []


def test_b_three_targets_one_persistent_grant_no_human_requests():
    mid = mandates.grant(BUSINESS, 'Contact B2B', 'public_business', ['contact'], actor='human')
    calls = []
    actions.register_executor('browser_form', 'submit', lambda c,p: calls.append(c['id']) or
                             {'source_ref': c['locator'], 'observation': 'message reçu'}, cost_class='local')
    actions.register_executor('email', 'send', lambda c,p: calls.append(c['id']) or
                             {'source_ref': 'smtp:fake', 'observation': 'accepté'}, cost_class='local')
    for i, url in enumerate(['https://a.example/contact', 'contact@company.example', 'https://c.example/contact']):
        cid = channel(url, 'email' if i == 1 else 'browser_form')
        assert public(cid, 'https://company.example/contact' if i == 1 else url)
        assert propose(cid, 'target-' + str(i), action='send' if i == 1 else 'submit')['status'] == 'executed'
    assert len(calls) == 3
    assert tasks.pending_human_requests() == []
    assert len(mandates.list_mandates(BUSINESS, active=True)) == 1
    assert journal.query('SELECT access FROM economic_channels')[0]['access'] == 'none'
    assert len(journal.query('SELECT * FROM events WHERE type=\'action.mandate\'')) == 3
    assert mid


def test_c_d_revocation_other_business_and_no_llm_grant():
    mid = mandates.grant(BUSINESS, 'Contact', 'public_business', ['contact'], actor='human')
    cid = channel('https://company.example/contact')
    assert public(cid, 'https://company.example/contact')
    actions.register_executor('browser_form', 'submit', lambda c,p: pytest.fail('effet interdit'), cost_class='local')
    mandates.revoke(BUSINESS, mid, actor='human')
    assert propose(cid)['status'] == 'blocked'
    other = channel('https://company.example/contact', business='other')
    assert not public(other, 'https://company.example/contact', business='other')
    assert propose(other, key='other-send', business='other')['status'] == 'blocked'
    with pytest.raises(PermissionError):
        mandates.grant(BUSINESS, 'Forgé', 'public_business', ['contact'], actor='agent')
    with pytest.raises(PermissionError):
        account_bad = resources.configure_account('forged', actor='agent', provider='x', label='x', url=URL,
            domains=['new-platform.example'], businesses=[BUSINESS], authenticated_text='Déconnexion')


def test_e_cookie_or_connected_state_never_grants_actions(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    with pytest.raises(bw.Refused, match='non mandatée'):
        space(monkeypatch)
    own_grant(('read',))
    s = space(monkeypatch)
    try:
        assert s.navigate(URL)['ok']
        with pytest.raises(bw.Refused):
            s.click('@e1')
        with pytest.raises(bw.Refused):
            s.click('@e4')
    finally:
        s.close()


def test_f_g_h_publication_finance_and_secrets(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant()
    s = space(monkeypatch)
    try:
        s.navigate(URL)
        assert s.click('@e1', expect='Publication réussie')['effect']['status'] == 'verified'
        with pytest.raises(bw.Refused, match='sensible'):
            s.click('@e2')
        with pytest.raises(bw.Refused, match='humain'):
            s.type('@e3', 'very-secret-password')
        assert s._session.clicks == 1
    finally:
        s.close()


@pytest.mark.parametrize('label', ['Pay', 'Rembourser', 'Payout', 'Modifier IBAN', 'Supprimer le compte',
                                    'Créer API key', 'Accept terms', 'Changer sécurité', 'OTP', 'Acheter', 'Signature'])
def test_sensitive_effects_cannot_be_delegated(label):
    cid = channel(URL)
    m = mandates.grant(BUSINESS, 'Contact', 'public_business', ['contact'], actor='human')
    assert not mandates.authorize(dict(journal.query('SELECT * FROM economic_channels WHERE id=?', (cid,))[0]),
                                  'contact', description=label)['allowed']


def test_i_j_unknown_platform_taint_and_fresh_public_subtask(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant()
    s = space(monkeypatch)
    try:
        s.navigate(URL)
        assert s._guard(URL)
        assert s.state.account_read
        assert not s._guard('https://public.example')
        with pytest.raises(bw.Refused):
            s.navigate('https://public.example')
        with web_guard.isolated_session() as separate:
            assert not separate.account_read
            assert web_guard.check('https://public.example', separate) == 'public'
        assert s.state.account_read
        assert s._session.kwargs['profile_dir'] == resources.account_profile('new-platform')
    finally:
        s.close()


def test_k_crash_after_effect_no_duplicate_new_scope(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant()
    s = space(monkeypatch)
    s.navigate(URL)
    def die():
        raise SystemExit('fake process death')
    s._session.on_click = die
    with pytest.raises(SystemExit):
        s.click('@e1')
    assert journal.query('SELECT status FROM channel_actions')[0]['status'] == 'ambiguous'
    s.close()
    newer = space(monkeypatch)
    try:
        newer.navigate(URL)
        with pytest.raises(bw.Refused, match='non vérifiée'):
            newer.click('@e1')
        assert newer._session.clicks == 0
    finally:
        newer.close()


def test_k_crash_before_or_after_executor_no_resend():
    mandates.grant(BUSINESS, 'Contact', 'public_business', ['contact'], actor='human')
    cid = channel('https://company.example/contact')
    assert public(cid, 'https://company.example/contact')
    calls = []
    def crash(c,p):
        calls.append(1)
        raise SystemExit('death after delivery')
    actions.register_executor('browser_form', 'submit', crash, cost_class='local')
    with pytest.raises(SystemExit):
        propose(cid)
    assert journal.query('SELECT status FROM channel_actions')[0]['status'] == 'proposed'
    assert propose(cid)['duplicate']
    assert len(calls) == 1


def test_l_restart_complete_process_retains_grant(monkeypatch):
    mid = mandates.grant(BUSINESS, 'Contact', 'public_business', ['contact'], actor='human')
    code = "from octopus import mandates; assert mandates.covering('digital_b2b','public_business','contact')['id']==" + str(mid)
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)
    assert mandates.list_mandates('other') == []


def test_malt_style_request_onboarding_and_secret_non_capture(monkeypatch):
    tid = resources.request_account('new-platform', 'NewPlatform', 'Observer demandes professionnelles', ['read'], BUSINESS,
                                    created_by='agent:test', url=URL, desired_actions=['contact'])
    assert tasks.get(tid)['status'] == 'waiting_human'
    req = tasks.pending_human_requests(BUSINESS)[0]
    assert json.loads(req['context'])['web_request']['platform'] == 'NewPlatform'
    assert resources.request_account('new-platform', 'NewPlatform', 'même besoin', [], BUSINESS,
                                    created_by='agent:test', url=URL) == tid
    account()
    connector = resources.HumanConnection('new-platform', actor='human', session_factory=FakeAccountSession)
    captured = connector.session
    assert resources.get('new-platform')['web_account']['session_status'] == 'connection_required'
    assert connector.verify() is True
    assert resources.get('new-platform')['web_account']['session_status'] == 'connected'
    assert tasks.pending_human_requests(BUSINESS) == []
    assert mandates.list_mandates(BUSINESS) == []
    assert tasks.get(tid)['status'] == 'queued'
    worker.run_one(task_id=tid)
    assert tasks.get(tid)['status'] == 'done'
    assert {c for c,_ in captured.commands} <= {'open','get','eval'}
    for table in ['resources', 'events', 'tasks', 'human_requests', 'task_steps']:
        assert FakeAccountSession.secret not in str([dict(r) for r in journal.query('SELECT * FROM ' + table)])
    assert resources.get('new-platform')['web_account']['dedicated']


def test_expired_disabled_and_other_ownership(monkeypatch):
    account(ownership='other')
    resources.set_account_session('new-platform', 'connected')
    own_grant()
    assert mandates.account_authority(BUSINESS, 'new-platform', 'read')
    assert not mandates.account_authority(BUSINESS, 'new-platform', 'publish')
    s = space(monkeypatch)
    try:
        s.navigate(URL)
        monkeypatch.setattr(FakeAccountSession, 'logged_in', False)
        with pytest.raises(bw.Refused, match='session expirée'):
            s.snapshot()
        assert resources.get('new-platform')['web_account']['session_status'] == 'expired'
    finally:
        s.close()
    resources.set_account_session('new-platform', 'connected')
    resources.disable_account('new-platform', actor='human')
    assert not mandates.account_authority(BUSINESS, 'new-platform', 'read')


def test_decline_report_no_session_or_permission_created():
    tid = resources.request_account('x', 'x', 'raison', [], BUSINESS, created_by='agent')
    req = tasks.pending_human_requests()[0]
    tasks.answer(req['id'], 'declined')
    worker.run_one(task_id=tid)
    assert tasks.get(tid)['output']['decision'] == 'declined'
    assert not resources.get('x')['web_account']
    assert not mandates.list_mandates(BUSINESS)


def test_qualification_rejects_personal_or_different_source_and_missing_executor():
    mandates.grant(BUSINESS, 'Contact', 'public_business', ['contact'], actor='human')
    cid = channel('person@company.example', kind='email')
    assert not public(cid, 'https://company.example/contact')
    cid = channel('https://company.example/contact')
    assert not public(cid, 'https://evil.example/contact')
    assert not public(cid, 'https://company.example/contact', 'Personal homepage. Contact me.')
    assert public(cid, 'https://company.example/contact')
    assert 'exécuteur' in propose(cid)['reason']
    assert tasks.pending_human_requests() == []


def test_delegated_effect_requires_stable_key_and_no_paid_grant():
    mandates.grant(BUSINESS, 'Contact', 'public_business', ['contact'], actor='human')
    cid = channel('https://company.example/contact')
    assert public(cid, 'https://company.example/contact')
    actions.register_executor('browser_form', 'submit', lambda c,p: pytest.fail('effet'), cost_class='local')
    assert 'idempotency_key' in propose(cid, key=None)['reason']
    assert propose(cid, key='paid', spend_amount=1.0, spend_currency='EUR')['status'] == 'blocked'
    assert not journal.query('SELECT * FROM spend_requests')


def test_replace_is_atomic_and_legacy_act_is_not_migrated():
    cid = channel('https://company.example/contact', access='act')
    assert mandates.list_mandates(BUSINESS) == []
    ch = dict(journal.query('SELECT * FROM economic_channels WHERE id=?', (cid,))[0])
    assert mandates.authorize(ch, 'contact')['allowed']
    mid = mandates.grant(BUSINESS, 'Contact', 'public_business', ['contact'], actor='human')
    assert public(cid, 'https://company.example/contact')
    mandates.revoke(BUSINESS, mid, actor='human')
    assert mandates.authorize(ch, 'contact')['allowed']  # explicit legacy grant remains respected
    mid = own_grant()
    with pytest.raises(ValueError):
        mandates.replace(BUSINESS, mid, 'bad', 'public_business', ['publish'], actor='human')
    assert any(m['id'] == mid for m in mandates.list_mandates(BUSINESS, active=True))
    newer = mandates.replace(BUSINESS, mid, 'lecture', 'owned_account', ['read'], actor='human', resource_keys=['new-platform'])
    assert [m['id'] for m in mandates.list_mandates(BUSINESS, active=True)] == [newer]


def test_local_artifact_no_mandate_effect_execution_or_path_escape():
    with journal.run(BUSINESS, 'test'):
        made = runtime._create_artifact({'filename': 'demo.html', 'text': '<h1>Démo</h1>'})
        assert made['local_only']
        assert runtime._create_artifact({'filename': 'demo.html', 'text': '<h1>Démo</h1>'}) == made
        with pytest.raises(ValueError):
            runtime._create_artifact({'filename': 'demo.html', 'text': 'other'})
        with pytest.raises(bw.Refused):
            runtime._create_artifact({'filename': '../secret.txt', 'text': 'x'})
    assert not journal.query('SELECT * FROM channel_actions')


def test_hub_readonly_snapshot_and_no_writes(monkeypatch):
    account()
    own_grant()
    state = read_snapshot(BUSINESS)
    assert state['accounts'][0]['label'] == 'Compte dédié'
    assert state['mandates'][0]['target'] == 'owned_account'
    monkeypatch.setenv('OCTOPUS_WORKBENCH_READONLY', '1')
    before = len(journal.query('SELECT * FROM events'))
    with pytest.raises(PermissionError):
        own_grant()
    with pytest.raises(PermissionError):
        account()
    with pytest.raises(PermissionError):
        resources.HumanConnection('new-platform', actor='human', session_factory=FakeAccountSession)
    assert read_snapshot(BUSINESS)['accounts']
    assert len(journal.query('SELECT * FROM events')) == before


def test_nested_current_task_scope_and_public_restore():
    a = tasks.enqueue(BUSINESS, 'outer')
    b = tasks.enqueue(BUSINESS, 'inner')
    with journal.run(BUSINESS, 'outer') as outer:
        with journal.connect() as conn:
            conn.execute('UPDATE tasks SET run_id=? WHERE id=?', (outer.id, a))
        with web_guard.session() as state:
            with journal.run(BUSINESS, 'inner') as inner:
                with journal.connect() as conn:
                    conn.execute('UPDATE tasks SET run_id=? WHERE id=?', (inner.id, b))
                assert tasks.current_task_id() == b
                assert runtime._current_task_id() == b
                assert bw.current_scope().task_id == b
                with web_guard.isolated_session() as authenticated:
                    authenticated.account_read = True
                    assert web_guard.current() is authenticated
                assert web_guard.current() is state and not state.account_read
            assert tasks.current_task_id() == a


def test_account_security_observation_redaction():
    assert '123456' not in bw._account_text('Code 123456')
    assert '482913' not in bw._account_text('Your OTP is 482913')
    assert 'my-secret-password' not in bw._account_text('Password my-secret-password')
    assert 'sk-abcdefghijk123456789012345' not in bw._account_text('sk-abcdefghijk123456789012345')


class FakePublicSession(FakeAccountSession):
    def run(self, command, args=(), **kwargs):
        if command == 'snapshot':
            return {'success': True, 'data': {'origin': self.url,
                'snapshot': 'Entreprise services B2B. Contact professionnel.\n- textbox "Message" [ref=e1]\n- button "Envoyer" [ref=e2]\n' + self.extra,
                'refs': {'e1': {'role': 'textbox', 'name': 'Message'}, 'e2': {'role': 'button', 'name': 'Envoyer'}}}}
        if command == 'get' and args[0] == 'text':
            return {'success': True, 'data': {'text': 'Entreprise contact professionnel ' + self.extra}}
        return super().run(command, args, **kwargs)


def test_live_public_browser_creates_three_concrete_channels_no_url_permission(monkeypatch):
    monkeypatch.delenv(bw.LAB_ORIGINS_ENV, raising=False)
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda _: [__import__('ipaddress').ip_address('8.8.8.8')])
    mid = mandates.grant(BUSINESS, 'Contact B2B', 'public_business', ['contact'], actor='human')
    s = bw.Workspace(bw.Scope('public-test', BUSINESS, None), web_guard.BrowseState(), session_factory=FakePublicSession)
    try:
        for domain in ('a.example', 'b.example', 'c.example'):
            assert s.navigate('https://' + domain + '/contact')['ok']
            s.type('@e1', 'Bonjour entreprise ' + domain)
            assert s.click('@e2', expect='Publication réussie')['effect']['verified']
            s._session.extra = ''
        assert len(journal.query('SELECT * FROM channel_actions')) == 3
        assert not tasks.pending_human_requests()
        mandates.revoke(BUSINESS, mid, actor='human')
        s.navigate('https://d.example/contact')
        with pytest.raises(bw.Refused):
            s.type('@e1', 'message après révocation')
        assert s._session.clicks == 3
    finally:
        s.close()


def test_real_account_subtask_worker_handoff_restores_parent_network_and_task(monkeypatch):
    from agents import task_handlers, agent_browser
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant()
    monkeypatch.setattr(agent_browser, 'Session', FakeAccountSession)
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda _: [__import__('ipaddress').ip_address('8.8.8.8')])
    seen = []
    def work(ctx):
        assert ctx.input['browser_resource_key'] == 'new-platform'
        assert {'search', 'browse', 'act_on_channel', 'account_task'}.isdisjoint(ctx.input['allowed_tools'])
        assert tasks.current_task_id() == ctx.id
        s = bw.workspace()
        seen.append(s)
        assert s.navigate(URL)['ok']
        assert s._guard(URL) and s.state.account_read
        assert not s._guard('https://public.example')
        return {'rapport': 'Lecture compte terminée', 'synthesis_status': 'validated'}
    monkeypatch.setattr(task_handlers, 'orbit_mission', work)
    tid = tasks.enqueue(BUSINESS, 'outer')
    with journal.run(BUSINESS, 'outer') as outer:
        with journal.connect() as conn:
            conn.execute('UPDATE tasks SET run_id=? WHERE id=?', (outer.id, tid))
        with web_guard.session() as state, bw.mission_scope():
            result = resources.account_task(BUSINESS, 'new-platform', 'Observer le compte', parent_id=tid)
            assert result['status'] == 'done'
            assert result['observations']['rapport'] == 'Lecture compte terminée'
            assert tasks.current_task_id() == tid
            assert web_guard.current() is state and not state.account_read
            assert seen[0]._session is None
            repeated = resources.account_task(BUSINESS, 'new-platform', 'Observer le compte', parent_id=tid)
            assert repeated['task_id'] == result['task_id'] and len(seen) == 1


def test_publication_grant_does_not_authorize_profile_autosave(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant()
    s = space(monkeypatch)
    try:
        s.navigate(URL)
        s._refs['e5'] = {'role': 'textbox', 'name': 'Bio du profil'}
        with pytest.raises(bw.Refused):
            s.type('@e5', 'New bio')
        assert not journal.query('SELECT * FROM channel_actions')
    finally:
        s.close()


def test_migration_v9_preserves_legacy_grants_without_global_authority():
    cid = channel('https://company.example/contact', access='act')
    with journal.connect() as conn:
        conn.execute('DROP TABLE channel_authority')
        conn.execute('DROP TABLE operational_mandates')
        conn.execute('ALTER TABLE resources DROP COLUMN web_account')
        conn.execute('PRAGMA user_version=9')
    with journal.connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 10
        assert conn.execute('SELECT access FROM economic_channels WHERE id=?', (cid,)).fetchone()[0] == 'act'
        assert not conn.execute('SELECT * FROM operational_mandates').fetchall()


def test_workbench_hub_controls_and_readonly_share_canonical_state():
    from agents.gui.workbench_v2 import WorkbenchV2
    from types import SimpleNamespace
    account()
    own_grant()
    class Card:
        def pack(self, **_): pass
    commands = []
    app = SimpleNamespace(_snapshot=read_snapshot(BUSINESS), _readonly=False, selected_business_id=BUSINESS,
        _line=lambda *a, **k: None, _section=lambda *a, **k: None, _card=lambda *a, **k: Card(),
        _secondary=lambda parent,label,command: commands.append(label), _account_form=lambda *a: None,
        _open_account=lambda *a: None, _verify_account=lambda *a: None, _disable_account=lambda *a: None,
        _revoke_mandate=lambda *a: None, _mandate_form=lambda *a: None, _set_status=lambda *a: None)
    WorkbenchV2._resource_hub(app, Card())
    assert {'Ajouter un compte', 'Ouvrir la connexion', 'J’ai terminé — vérifier', 'Désactiver',
            'Révoquer ce mandat', 'Modifier ce mandat', 'Accorder un mandat'} <= set(commands)
    commands.clear()
    app._readonly = True
    WorkbenchV2._resource_hub(app, Card())
    assert commands == []


def test_account_autosave_is_journaled_and_distinguishes_values(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant(('read','edit'))
    s = space(monkeypatch)
    try:
        s.navigate(URL)
        s._refs['e5'] = {'role': 'textbox', 'name': 'Bio du profil'}
        s.type('@e5', 'Première bio')
        s._refs['e5'] = {'role': 'textbox', 'name': 'Bio du profil'}
        s.type('@e5', 'Deuxième bio')
        rows = journal.query("SELECT * FROM channel_actions WHERE action='browser.type'")
        assert len(rows) == 2 and rows[0]['idempotency_key'] != rows[1]['idempotency_key']
        assert 'Première bio' not in str([dict(r) for r in rows])
        checkpoint = tasks.step_value(s.scope.task_id, bw.CHECKPOINT_KEY)
        assert checkpoint['form_digests']
    finally:
        s.close()


def test_account_urls_never_expose_query_session_tokens(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant()
    s = space(monkeypatch)
    try:
        view = s.navigate(URL + '?session=raw-session-token')
        assert 'raw-session-token' not in json.dumps(view)
        checkpoint = tasks.step_value(s.scope.task_id, bw.CHECKPOINT_KEY)
        assert 'raw-session-token' not in json.dumps(checkpoint)
    finally:
        s.close()
