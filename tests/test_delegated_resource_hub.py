"""Offline adversarial harness. Every provider, site and effect is a fake."""
from __future__ import annotations

import json
import subprocess
import sys
import sqlite3
from types import SimpleNamespace

import pytest

from agents import config, runtime, web_guard
from agents.gui.workbench_v2_data import read_snapshot
from octopus import actions, browser_workspace as bw, economy, journal, mandates, resources, tasks, worker

BUSINESS = 'digital_b2b'
URL = 'https://new-platform.example/dashboard'


@pytest.fixture(autouse=True)
def no_external_executors(monkeypatch):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(resources, 'time', SimpleNamespace(time=resources.time.time,
        monotonic=lambda: clock.now, sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds)))
    monkeypatch.setattr(resources, 'stable_chrome_executable', lambda: '/fake/installed/Google/Chrome/chrome.exe')
    monkeypatch.setattr(resources, '_human_processes', {})
    monkeypatch.setattr(resources, '_launch_human_browser', lambda *a: SimpleNamespace(poll=lambda: 0))
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
            if args[0].endswith('return Boolean(!login && !challenge); })()'):
                return ok({'result': self.logged_in})
            if 'octopus_challenge' in args[0] or 'octopus_traffic' in args[0]:
                return ok({'result': False})
            # Fixed boolean probes only; no snapshot, cookie or field-value access.
            if args[0].endswith('return Boolean(login); })()'):
                return ok({'result': not self.logged_in})
            if args[0].endswith('return Boolean(challenge); })()'):
                return ok({'result': False})
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
    assert resources.get('new-platform')['web_account']['session_status'] == 'connection_required'
    assert connector.verify() is True
    captured = FakeAccountSession.instances[-1]
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


def test_human_public_login_dependencies_never_expand_agent_authority(monkeypatch, transport, providers_up):
    import ipaddress
    import socket
    import requests
    from agents import agent_browser
    from octopus import llm
    from octopus.pricing import Usage

    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('real HTTP'))
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda _: [ipaddress.ip_address('8.8.8.8')])
    key = 'generic-account'
    start, verified = 'https://account.example/login', 'https://account.example/dashboard'
    flow = ['https://login.example/signin', 'https://static.cdn.example/style.css',
            'https://oauth.example/authorize', 'http://redirect.example/return', verified]
    resources.configure_account(key, actor='human', provider='Unknown', label='Compte', url=start,
        domains=['account.example'], businesses=[BUSINESS], verify_url=verified,
        authenticated_text='Déconnexion')
    before = resources.get(key)['web_account'].copy()
    parent_state = web_guard.BrowseState()
    visited = []

    class HumanSession(FakeAccountSession):
        pass  # Used only by the separate verifier, never to open or control login.

    # Capture the real GuardProxy before Session.open is invoked. No upstream connections.
    real_proxy = web_guard.GuardProxy
    proxy = None
    def capture_proxy(*a, **k):
        nonlocal proxy
        proxy = real_proxy(*a, **k)
        return proxy
    monkeypatch.setattr(web_guard, 'GuardProxy', capture_proxy)
    with web_guard.session() as state:
        parent_state = state
        connection = resources.HumanConnection(key, actor='human', session_factory=HumanSession)
        assert connection.session is None
        for url in flow:
            assert proxy._check(url), url
            visited.append(url)  # Simulated manual navigation; no browser automation.
        try:
            assert visited == flow
            assert state.visited == [] and not state.account_read and state.account_domains == ()
            assert connection.verify() is True
            human = FakeAccountSession.instances[-1]
        finally:
            connection.close()
        assert web_guard.current() is parent_state and not parent_state.account_read
    configured = resources.get(key)['web_account']
    assert configured['domains'] == before['domains'] == ['account.example']
    assert configured['businesses'] == before['businesses'] == [BUSINESS]
    assert mandates.list_mandates(BUSINESS) == []
    assert not journal.query('SELECT * FROM channel_authority')
    assert not journal.query('SELECT * FROM economic_channels')
    assert not journal.query('SELECT * FROM channel_actions')
    assert transport.calls == []
    assert {c for c, _ in human.commands} <= {'open', 'get', 'eval'}
    assert all(args == ['url'] for c, args in human.commands if c == 'get')
    assert [args[0] for c, args in human.commands if c == 'open'] == [verified]

    # A connected account still cannot be used by an agent without a human mandate.
    with pytest.raises(PermissionError):
        resources.account_task(BUSINESS, key, 'Observer le compte')
    mandates.grant(BUSINESS, 'Lecture simulée', 'owned_account', ['read'], actor='human', resource_keys=[key])
    monkeypatch.setattr(agent_browser, 'Session', FakeAccountSession)
    from browser_evidence import qualify
    qualify('deepseek/flash')
    calls = 0
    def respond(provider, request):
        nonlocal calls
        content = json.dumps(request['messages'], ensure_ascii=False)
        assert FakeAccountSession.secret not in content
        if request['max_tokens'] == 700:
            output = {'tasks': [{'role': 'SOUT', 'task': 'Lire le compte et constater le périmètre'}]}
        elif request['max_tokens'] in (500,1200):
            output = [
                {'tool': 'browser_navigate', 'args': {'url': verified}},
                {'tool': 'browser_navigate', 'args': {'url': flow[2]}},
                {'final': 'Lecture du compte ; navigation OAuth refusée', 'objective_status': 'completed', 'missing': [], 'evidence': [{'step': 1, 'quote': 'Déconnexion'}]}][calls]
            if calls == 2:
                assert 'refus' in content.lower()
            calls += 1
        else:
            assert request['max_tokens'] == 4000
            output = {'rapport': 'Compte observé ; OAuth reste inaccessible à la tâche agent.'}
        return llm.TransportResult(json.dumps(output), Usage(prompt_tokens=20, completion_tokens=20),
            request['model'], resolved_model=request['model'], resolved_provider='OfflineFake', provider_cost_usd=0.)
    transport.handler = respond
    before_instances = len(FakeAccountSession.instances)
    result = resources.account_task(BUSINESS, key, 'Lire le compte et tester le périmètre')
    assert result['status'] == 'done' and calls == 3
    task = tasks.get(result['task_id'])
    assert task['kind'] == 'resources.account_work' and task['input']['browser_resource_key'] == key
    agents = FakeAccountSession.instances[before_instances:]
    assert len(agents) == 1
    assert [args[0] for c, args in agents[0].commands if c == 'open'] == [verified]
    assert resources.get(key)['web_account']['domains'] == ['account.example']
    # Workspace may register a read channel, but never creates action access from login.
    assert all(r['access'] != 'act' for r in journal.query('SELECT * FROM economic_channels'))
    for table in ('resources', 'events', 'tasks', 'human_requests', 'task_steps', 'llm_calls'):
        persisted = str([dict(r) for r in journal.query('SELECT * FROM ' + table)])
        assert FakeAccountSession.secret not in persisted
        assert not any(url in persisted for url in flow[:2])


@pytest.mark.parametrize('url', [
    'http://localhost/', 'http://api.localhost/', 'http://router.local/',
    'http://127.0.0.1/', 'http://[::1]/', 'http://192.168.1.1/', 'http://10.0.0.1/',
    'http://172.16.0.1/', 'http://169.254.169.254/', 'http://[::ffff:127.0.0.1]/',
    'http://127.1/', 'http://2130706433/', 'https://private-dns.example/',
    'file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,login', 'ftp://public.example/',
    'https://optimizationguide-pa.googleapis.com/'])
def test_human_onboarding_still_blocks_unsafe_network_and_browser_services(monkeypatch, url):
    import ipaddress
    original = web_guard._resolved_ips
    def resolve(host):
        if host == 'private-dns.example':
            return [ipaddress.ip_address('8.8.8.8'), ipaddress.ip_address('10.0.0.1')]
        if host.endswith('.example'):
            return [ipaddress.ip_address('8.8.8.8')]
        return original(host)
    monkeypatch.setattr(web_guard, '_resolved_ips', resolve)
    account()
    connection = resources.HumanConnection('new-platform', actor='human', session_factory=FakeAccountSession)
    try:
        assert not connection.proxy._check(url)
        assert not connection.proxy.allow_private('127.0.0.1', 80)
        # DNS rebinding remains refused at the actual connection boundary, too.
        monkeypatch.setattr(web_guard.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('10.0.0.1', 443))])
        with pytest.raises(web_guard.BrowseRefused):
            connection.proxy._connect('public.example', 443)
    finally:
        connection.close()


@pytest.mark.parametrize('failure', ['foreign-domain', 'marker-absent', 'password', 'otp', 'non-boolean'])
def test_human_verification_is_required_after_oauth_and_only_returns_boolean(monkeypatch, failure):
    account()
    class UnverifiedSession(FakeAccountSession):
        def run(self, command, args=(), **kwargs):
            if command == 'get':
                assert args == ['url']
                return {'success': True, 'data': {'url': 'https://oauth.example/callback' if failure == 'foreign-domain' else URL}}
            if command == 'eval':
                predicate = args[0]
                assert 'input[type=password],input[autocomplete=one-time-code]' in predicate
                assert 'document.body.innerText.includes(marker)' in predicate
                assert json.dumps('Déconnexion') in predicate
                assert 'cookie' not in predicate and '.value' not in predicate
                # The browser predicate reports only success/failure, never credential values.
                value = 'true' if failure == 'non-boolean' else bool(
                    failure in ('password', 'otp') and predicate.endswith('return Boolean(login); })()'))
                return {'success': True, 'data': {'result': value}}
            return super().run(command, args, **kwargs)
    connection = resources.HumanConnection('new-platform', actor='human', session_factory=UnverifiedSession)
    try:
        assert connection.verify() is False
        session = FakeAccountSession.instances[-1]
    finally:
        connection.close()
    assert resources.get('new-platform')['web_account']['session_status'] == ('unavailable' if failure == 'non-boolean' else 'expired')
    assert resources.get('new-platform')['last_check_detail'] == {
        'foreign-domain': 'verify_domain_mismatch', 'marker-absent': 'authenticated_marker_missing',
        'password': 'visible_login_field_present', 'otp': 'visible_login_field_present',
        'non-boolean': 'backend_error'}[failure]
    assert not mandates.list_mandates(BUSINESS)
    assert not journal.query('SELECT * FROM economic_channels')
    if failure == 'foreign-domain':
        assert not any(c == 'eval' for c, _ in session.commands)


def test_account_configuration_accepts_optional_hint_but_requires_scoped_verify_url():
    args = dict(actor='human', provider='Unknown', label='Compte', url=URL,
                domains=['new-platform.example'], businesses=[BUSINESS])
    assert resources.configure_account('unverified', **args)['web_account']['authenticated_text'] == ''
    with pytest.raises(resources.ResourceError):
        resources.configure_account('unverified', **args, authenticated_text='Déconnexion',
                                    verify_url='https://oauth.example/callback')
    assert resources.get('unverified')['web_account']['verify_url'] == URL


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
    tid = resources.request_account('legacy-request', 'Unknown', 'Observer les demandes', ['read'], BUSINESS,
                                    created_by='agent:test', url=URL)
    request = tasks.pending_human_requests(BUSINESS)[0]
    tasks.answer(request['id'], 'reporter ; réponse humaine historique')
    tasks.save_step(tid, 'pursuit.progress', {'collect_complete': True, 'results': ['observation historique']})
    with journal.connect() as conn:
        conn.execute('DROP TABLE channel_authority')
        conn.execute('DROP TABLE operational_mandates')
        conn.execute('ALTER TABLE resources DROP COLUMN web_account')
        conn.execute('PRAGMA user_version=9')
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {}
        for table in tables:
            columns = ','.join('"' + r[1] + '"' for r in conn.execute(f'PRAGMA table_info("{table}")'))
            before[table] = (columns, [tuple(r) for r in conn.execute(f'SELECT {columns} FROM "{table}" ORDER BY rowid')])
    with journal.connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 10
        assert conn.execute('SELECT access FROM economic_channels WHERE id=?', (cid,)).fetchone()[0] == 'act'
        assert not conn.execute('SELECT * FROM operational_mandates').fetchall()
        for table, (columns, rows) in before.items():
            assert [tuple(r) for r in conn.execute(f'SELECT {columns} FROM "{table}" ORDER BY rowid')] == rows, table
    assert tasks.answer_for(tid, request['key']) == 'reporter ; réponse humaine historique'
    assert tasks.step_value(tid, 'pursuit.progress')['results'] == ['observation historique']


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


@pytest.mark.parametrize('resume_truncated', [False, True], ids=['complete', 'resume-truncated'])
def test_hub_account_pursuit_long_synthesis_and_recovery(monkeypatch, transport, providers_up, resume_truncated):
    """Both integrations execute together; token units and human onboarding are simulated."""
    import copy
    import ipaddress
    import socket
    import requests
    from agents import agent_browser, browser
    from octopus import businesses, llm, strategy, supervisor
    from octopus.pricing import Usage

    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('real HTTP'))
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('real network'))
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda _: [ipaddress.ip_address('8.8.8.8')])
    monkeypatch.setattr(agent_browser, 'Session', FakeAccountSession)
    monkeypatch.setattr(supervisor, 'PURSUIT_ROUNDS', 1)
    activity = businesses.create_activity('Produits digitaux & automatisation IA B2B', 'Observer les demandes B2B.')
    business = activity.id
    key, public_url = 'new-platform', 'https://public.example/demand'
    acquired, child_views, grant_ids = [], [], []
    state = {'child': False, 'resumed': False, 'outer_actions': 0, 'child_actions': 0}
    next_goal = 'Comparer les demandes authentifiées conservées avec les observations publiques.'
    report = 'Observations publiques et authentifiées simulées ; encaissement inconnu. ' * 110
    answer = json.dumps({'rapport': report, 'business_signals': [], 'determination': {
        'action': 'continue', 'reason': 'Comparer les observations déjà acquises.',
        'next_goal': next_goal, 'permission': ''}}, ensure_ascii=False)
    assert 1600 * 4 < len(answer) < 4000 * 4
    with pytest.raises(ValueError):
        llm.parse_json(answer[:1600 * 4])

    def browse(args):
        assert not state['resumed']
        acquired.append(args['url'])
        page = browser._page_record(requested_url=public_url, final_url=public_url, status=200,
            content_type='text/html', title='Demande publique simulée', method='http:html_main',
            rendered=False, blocked=False, error=None, text='Entreprise B2B cherche automatisation.', raw_chars=42)
        return {'url': public_url, 'texte': page.main_text, 'page': page.as_dict()}

    original_request = runtime.TOOLS['request_account']['fn']
    original_account = runtime.TOOLS['account_task']['fn']
    original_navigate = runtime.TOOLS['browser_navigate']['fn']

    def human_onboarding(args):
        result = original_request(args)
        assert tasks.get(result['task_id'])['status'] == 'waiting_human'
        resources.configure_account(key, actor='human', provider='Plateforme inconnue', label='Compte B2B',
            url=URL, domains=['new-platform.example'], businesses=[business], ownership='business',
            dedicated=True, authenticated_text='Déconnexion')
        connector = resources.HumanConnection(key, actor='human', session_factory=FakeAccountSession)
        assert connector.verify()
        worker.run_one(task_id=result['task_id'])
        assert tasks.get(result['task_id'])['status'] == 'done'
        grant_ids.append(mandates.grant(business, 'Lecture B2B simulée', 'owned_account', ['read'],
            actor='human', resource_keys=[key]))
        return result

    def account_work(args):
        assert not state['resumed']
        parent_state = web_guard.current()
        state['child'] = True
        try:
            result = original_account(args)
            assert result['status'] == 'done'
            assert result['observations']['synthesis_status'] == 'validated'
            return result
        finally:
            state['child'] = False
            assert web_guard.current() is parent_state and not parent_state.account_read

    def authenticated_observation(args):
        assert state['child']
        view = original_navigate(args)
        workspace = bw.workspace()
        assert workspace.account_mode and workspace.resource_key == key
        assert workspace.scope.business == business
        assert 'Déconnexion' in view['snapshot']
        child_views.append(view)
        return view

    for name, fn in [('browse', browse), ('request_account', human_onboarding),
                     ('account_task', account_work), ('browser_navigate', authenticated_observation)]:
        monkeypatch.setitem(runtime.TOOLS, name, {**runtime.TOOLS[name], 'fn': fn})

    from browser_evidence import qualify
    qualify('deepseek/flash')

    def respond(provider, request):
        maximum = request['max_tokens']
        messages = json.dumps(request['messages'], ensure_ascii=False)
        assert FakeAccountSession.secret not in messages
        if maximum == 700:
            assert not state['resumed']
            output = {'tasks': [{'role': 'SOUT', 'task': 'Observer le compte' if state['child'] else 'Observer les demandes B2B'}]}
        elif maximum in (500,1200):
            assert not state['resumed']
            if state['child']:
                index = state['child_actions']
                state['child_actions'] += 1
                output = ({'tool': 'browser_navigate', 'args': {'url': URL}} if index == 0
                          else {'final': 'Déconnexion · Compte B2B, observation authentifiée conservée', 'objective_status': 'completed', 'missing': [], 'evidence': [{'step': 1, 'quote': 'Déconnexion'}]})
            else:
                index = state['outer_actions']
                state['outer_actions'] += 1
                output = [
                    {'tool': 'browse', 'args': {'url': public_url}},
                    {'tool': 'request_account', 'args': {'key': key, 'platform': 'Plateforme inconnue',
                        'reason': 'Observer les demandes professionnelles', 'capabilities': ['read'], 'url': URL}},
                    {'tool': 'account_task', 'args': {'key': key, 'goal': 'Observer les demandes sans action externe'}},
                    {'final': 'Observations publiques et authentifiées conservées'}][index]
        else:
            assert maximum == 4000
            if state['child']:
                output = {'rapport': 'Déconnexion · Compte B2B, observation authentifiée simulée.'}
            else:
                assert 'authentifiées conservées' in messages.lower()
                text = answer[:1600 * 4] if resume_truncated and not state['resumed'] else answer
                return llm.TransportResult(text, Usage(prompt_tokens=100, completion_tokens=(len(text)+3)//4),
                    request['model'], resolved_model=request['model'], resolved_provider='OfflineFake', provider_cost_usd=.002)
        text = json.dumps(output, ensure_ascii=False)
        return llm.TransportResult(text, Usage(prompt_tokens=100, completion_tokens=(len(text)+3)//4),
            request['model'], resolved_model=request['model'], resolved_provider='OfflineFake', provider_cost_usd=.001)

    transport.handler = respond
    oid = supervisor.start_pursuit(business=business)
    supervisor.run_pursuit(oid, business=business)
    current = supervisor.work_tasks(business, oid)[-1]
    old_grants = copy.deepcopy(mandates.list_mandates(business))
    old_account = copy.deepcopy(resources.get(key))
    if resume_truncated:
        assert current['status'] == 'done_degraded'
        previous = copy.deepcopy(current)
        old_calls = [dict(r) for r in journal.query('SELECT * FROM llm_calls ORDER BY id')]
        count = len(transport.calls)
        state['resumed'] = True
        supervisor.start_pursuit(objective_id=oid, business=business)
        supervisor.run_pursuit(oid, business=business)
        current = supervisor.work_tasks(business, oid)[-1]
        assert len(transport.calls) == count + 1
        assert current['output']['resumed_collection']
        assert current['output']['results'] == previous['output']['results']
        assert tasks.get(previous['id']) == previous
        assert [dict(r) for r in journal.query('SELECT * FROM llm_calls ORDER BY id')][:len(old_calls)] == old_calls
    assert current['status'] == 'done'
    assert current['output']['synthesis_status'] == 'validated'
    assert current['output']['rapport'] == report
    assert current['output']['next_goal'] == next_goal
    assert mandates.list_mandates(business) == old_grants and old_grants[0]['id'] == grant_ids[0]
    assert resources.get(key) == old_account and old_account['web_account']['session_status'] == 'connected'
    assert acquired == [public_url] and len(child_views) == 1
    assert tasks.pending_human_requests(business) == []
    assert all(t['status'] != 'waiting_human' for t in tasks.list_tasks(business=business))
    assert len(journal.query('SELECT id FROM strategy_decisions WHERE origin_task_id=?', (current['id'],))) == 1
    rows = [dict(r) for r in journal.query('SELECT * FROM llm_calls ORDER BY id')]
    assert 1600 < rows[-1]['completion_tokens'] < 4000 and rows[-1]['status'] == 'ok'
    assert read_snapshot(business)['token_cost_usd'] == pytest.approx(sum(r['cost_usd'] for r in rows))
    before = (len(rows), len(strategy.list_items('decision', business)))
    supervisor.run_pursuit(oid, business=business)
    assert (len(journal.query('SELECT * FROM llm_calls')), len(strategy.list_items('decision', business))) == before
    for table in ('resources', 'events', 'tasks', 'human_requests', 'task_steps', 'llm_calls'):
        assert FakeAccountSession.secret not in str([dict(r) for r in journal.query('SELECT * FROM ' + table)])
    assert not journal.query('SELECT * FROM channel_actions')
