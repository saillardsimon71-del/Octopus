"""Post-login verification: real fixed JS against a synthetic DOM, no site/browser/account."""
from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from agents import web_guard
from octopus import journal, mandates, resources

URL = 'https://account.example/'
SECRET = 'never-output-password-OTP-482913-cookie-login-text'

# Browser APIs are simulated; the production predicate itself is executed unmodified.
JS_DOM = r'''
const state = JSON.parse(require('fs').readFileSync(0, 'utf8'));
function element(spec) {
  const el = { disabled: !!spec.disabled,
    matches: selector => selector === ':disabled' && !!spec.disabled,
    closest: selector => selector === '[inert]' && spec.inert ? {} : null,
    getClientRects: () => spec.rects === false ? [] : [{}],
    getBoundingClientRect: () => ({width: spec.width === undefined ? 100 : spec.width,
                                  height: spec.height === undefined ? 20 : spec.height}),
    parentElement: spec.parent ? element(spec.parent) : null,
    style: Object.assign({display:'block', visibility:'visible', opacity:'1'}, spec.style || {})};
  Object.defineProperty(el, 'value', {get() {throw Error('field value read forbidden');}});
  return el;
}
global.getComputedStyle = el => el.style;
global.document = {
  body: {innerText: state.text},
  querySelectorAll: selector => selector.startsWith('input')
    ? (state.inputs || []).filter(i => i.kind === 'password' || i.kind === 'otp').map(element)
    : (state.challenges || []).map(element)
};
Object.defineProperty(document, 'cookie', {get() {throw Error('cookie read forbidden');}});
const value = eval(state.script);
if (typeof value !== 'boolean') throw Error('non-boolean result');
process.stdout.write(JSON.stringify(value));
'''


class Clock:
    def __init__(self): self.now, self.sleeps = 0., []
    def monotonic(self): return self.now
    def sleep(self, seconds): self.sleeps.append(seconds); self.now += seconds
    @staticmethod
    def time(): return time.time()


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(resources, 'time', clock)
    return clock


@pytest.fixture
def node():
    binary = shutil.which('node')
    if not binary: pytest.skip('Node required to execute the fixed JS offline')
    return binary


class DomSession:
    def __init__(self, node, clock, *, hydrate_at=0., inputs=(), challenges=(),
                 text=None, url=URL, failure=None):
        self.node, self.clock, self.hydrate_at = node, clock, hydrate_at
        self.inputs, self.challenges, self.text, self.url = inputs, challenges, text, url
        self.failure, self.commands, self.closed = failure, [], 0
    def close(self): self.closed += 1
    def run(self, command, args=(), **kwargs):
        assert command in ('open', 'get', 'eval'), 'snapshot/cookie/interaction forbidden'
        self.commands.append((command, list(args), kwargs))
        if self.failure == 'exception': raise RuntimeError(SECRET)
        if self.failure == 'backend': return {'success': False, 'error': SECRET}
        if self.failure == 'timeout':
            self.clock.now += kwargs.get('timeout', 10.)
            raise TimeoutError(SECRET)
        if command == 'open':
            assert args == [URL]  # Exactly the human-configured verify URL, never login.
            return {'success': True, 'data': {}}
        if command == 'get':
            assert args == ['url']
            return {'success': True, 'data': {'url': self.url}}
        script = args[0]
        assert '.value' not in script and 'cookie' not in script and 'snapshot' not in script
        if self.failure == 'non-boolean': return {'success': True, 'data': {'result': 'true'}}
        text = self.text if self.text is not None else (
            'Messages ' + SECRET if self.clock.now >= self.hydrate_at else 'Chargement…')
        result = subprocess.run([self.node, '-e', JS_DOM], input=json.dumps({
            'script': script, 'text': text, 'inputs': self.inputs, 'challenges': self.challenges}),
            text=True, capture_output=True, check=True, timeout=3.)
        value = json.loads(result.stdout)
        assert isinstance(value, bool)
        return {'success': True, 'data': {'result': value}}


def existing_account(monkeypatch, tmp_path):
    resources.configure_account('account', actor='human', provider='Unknown', label='Existant',
        url='https://account.example/login', domains=['account.example'], businesses=['b2b'],
        verify_url=URL, authenticated_text='Messages')
    account = resources.get('account')['web_account']
    resources._write('account', {'web_account': json.dumps(dict(account, browser_kind='chrome_stable'))})
    executable = tmp_path / 'stable-chrome.exe'
    executable.write_bytes(b'fake executable')
    monkeypatch.setattr(resources, 'stable_chrome_executable', lambda: str(executable))
    monkeypatch.setattr(resources, '_human_processes', {})
    monkeypatch.setattr(resources, '_launch_human_browser', lambda *a: pytest.fail('no reconnect'))
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda _: [ipaddress.ip_address('8.8.8.8')])
    profile = resources.account_profile('account')
    profile.mkdir(parents=True)
    (profile / 'session-sentinel').write_text('existing profile remains untouched')
    return profile


def verify_existing(monkeypatch, tmp_path, session, *, expected_sessions=1):
    profile = existing_account(monkeypatch, tmp_path)
    factory_calls = []
    def factory(name, **kwargs):
        factory_calls.append((name, kwargs))
        return session
    result = resources.verify_account_connection('account', actor='human', session_factory=factory)
    assert isinstance(result, bool)
    assert len(factory_calls) == expected_sessions
    if factory_calls:
        assert factory_calls[0][1]['profile_dir'] == profile
    assert (profile / 'session-sentinel').read_text() == 'existing profile remains untouched'
    assert resources.account_profile('account') == profile
    assert not mandates.list_mandates('b2b')
    assert not journal.query('SELECT * FROM economic_channels')
    assert not journal.query('SELECT * FROM channel_authority')
    assert not journal.query('SELECT * FROM channel_actions')
    assert not journal.query('SELECT * FROM llm_calls')
    for table in ('resources', 'events', 'tasks', 'human_requests', 'llm_calls'):
        stored = str([dict(r) for r in journal.query('SELECT * FROM ' + table)])
        assert SECRET not in stored and '?token=' not in stored and '#fragment' not in stored
    return result, resources.get('account')


def test_hydration_eventually_connects_without_navigation_retry(node, clock, monkeypatch, tmp_path):
    session = DomSession(node, clock, hydrate_at=.6)
    ok, resource = verify_existing(monkeypatch, tmp_path, session)
    assert ok and resource['web_account']['session_status'] == 'connected' and resource['last_check_ok'] == 1
    assert clock.now == pytest.approx(.6) and clock.sleeps == [.3, .3]
    assert [args for c, args, _ in session.commands if c == 'open'] == [[URL]]
    assert all(0 < kwargs['timeout'] <= 10. for c, _, kwargs in session.commands if c != 'open')


@pytest.mark.parametrize('kind', ['password', 'otp'])
@pytest.mark.parametrize('hidden', [
    {'rects': False}, {'width': 0}, {'style': {'display': 'none'}},
    {'style': {'visibility': 'hidden'}}, {'style': {'opacity': '0'}},
    {'parent': {'style': {'opacity': '0'}}}, {'disabled': True}, {'inert': True}])
def test_hidden_auth_fields_do_not_invalidate_marker(node, clock, monkeypatch, tmp_path, kind, hidden):
    ok, resource = verify_existing(monkeypatch, tmp_path,
        DomSession(node, clock, inputs=[dict(hidden, kind=kind)]))
    assert ok and resource['web_account']['session_status'] == 'connected'
    assert clock.sleeps == []


@pytest.mark.parametrize('kind', ['password', 'otp'])
def test_visible_auth_fields_refuse_even_with_marker(node, clock, monkeypatch, tmp_path, kind):
    ok, resource = verify_existing(monkeypatch, tmp_path, DomSession(node, clock, inputs=[{'kind': kind}]))
    assert not ok and resource['web_account']['session_status'] == 'expired'
    assert resource['last_check_detail'] == 'visible_login_field_present' and resource['last_check_ok'] == 0
    assert clock.sleeps == []


def test_missing_marker_is_insufficient_proof_not_session_loss(node, clock, monkeypatch, tmp_path):
    def semantic(session, account, key, diagnostic):
        diagnostic['reason'] = 'semantic_uncertain'
        return False, {'method': 'semantic_observation', 'state': 'uncertain'}
    monkeypatch.setattr(resources, '_semantic_account_page', semantic)
    session = DomSession(node, clock, hydrate_at=100.)
    ok, resource = verify_existing(monkeypatch, tmp_path, session)
    assert not ok and resource['web_account']['session_status'] == 'connection_required'
    assert resource['last_check_detail'] == 'semantic_uncertain'
    assert clock.now == pytest.approx(10.) and max(clock.sleeps) <= .3
    assert sum(c == 'open' for c, _, _ in session.commands) == 1


def test_wrong_domain_refuses_before_dom_without_persisting_url(node, clock, monkeypatch, tmp_path):
    session = DomSession(node, clock, url='https://oauth.example/?token=' + SECRET + '#fragment')
    ok, resource = verify_existing(monkeypatch, tmp_path, session)
    assert not ok and resource['web_account']['session_status'] == 'expired'
    assert resource['last_check_detail'] == 'verify_domain_mismatch'
    assert not any(c == 'eval' for c, _, _ in session.commands)


@pytest.mark.parametrize('challenge', ['iframe', 'text', 'traffic'])
def test_challenge_requires_human_even_with_marker(node, clock, monkeypatch, tmp_path, challenge):
    session = DomSession(node, clock, challenges=[{}] if challenge == 'iframe' else [],
        text='Messages verify you are human' if challenge == 'text' else
             'Messages unusual traffic' if challenge == 'traffic' else None)
    ok, resource = verify_existing(monkeypatch, tmp_path, session)
    assert not ok and resource['web_account']['session_status'] == 'connection_required'
    assert resource['last_check_detail'] == 'challenge_detected' and clock.sleeps == []


def test_hidden_challenge_and_non_auth_field_do_not_refuse(node, clock, monkeypatch, tmp_path):
    ok, _ = verify_existing(monkeypatch, tmp_path, DomSession(node, clock,
        challenges=[{'style': {'display': 'none'}}], inputs=[{'kind': 'search'}]))
    assert ok


@pytest.mark.parametrize('failure', ['backend', 'exception', 'non-boolean', 'timeout'])
def test_backend_diagnostics_are_fixed_codes_only(node, clock, monkeypatch, tmp_path, failure):
    # Fail during DOM/get, not open, so a timeout exercises the bounded polling budget.
    session = DomSession(node, clock, failure=failure)
    original = session.run
    def run(command, args=(), **kwargs):
        if command == 'open':
            session.commands.append((command, list(args), kwargs))
            return {'success': True, 'data': {}}
        return original(command, args, **kwargs)
    session.run = run
    ok, resource = verify_existing(monkeypatch, tmp_path, session)
    assert not ok
    assert resource['last_check_detail'] == ('verify_timeout' if failure == 'timeout' else 'backend_error')
    assert resource['web_account']['session_status'] == 'unavailable'


def test_immediate_workspace_verification_does_not_sleep(node, clock):
    session = DomSession(node, clock, hydrate_at=1.)
    diagnostic = {}
    assert resources.verify_account_page(session, {'domains': ['account.example'], 'authenticated_text': 'Messages'},
        diagnostic=diagnostic) is False
    assert diagnostic == {'reason': 'authenticated_marker_missing'}
    assert clock.now == 0 and clock.sleeps == []



def test_domain_is_rechecked_after_successful_dom_probe(node, clock, monkeypatch, tmp_path):
    session = DomSession(node, clock)
    original = session.run
    reads = []
    def run(command, args=(), **kwargs):
        if command == 'get':
            reads.append(command)
            if len(reads) == 2:
                return {'success': True, 'data': {'url': 'https://foreign.example/?token=' + SECRET}}
        return original(command, args, **kwargs)
    session.run = run
    ok, resource = verify_existing(monkeypatch, tmp_path, session)
    assert not ok and resource['last_check_detail'] == 'verify_domain_mismatch'


def test_proxy_start_error_has_safe_diagnostic(node, clock, monkeypatch, tmp_path):
    def failed(*args, **kwargs): raise RuntimeError(SECRET)
    monkeypatch.setattr(web_guard, 'GuardProxy', failed)
    session = DomSession(node, clock)
    ok, resource = verify_existing(monkeypatch, tmp_path, session, expected_sessions=0)
    assert not ok and resource['last_check_detail'] == 'backend_error'
    assert not session.commands



def test_workbench_displays_safe_failure_code_without_claiming_session_loss(monkeypatch, tmp_path):
    from agents.gui.workbench_v2 import WorkbenchV2
    existing_account(monkeypatch, tmp_path)
    resources.set_account_session('account', 'expired', detail='authenticated_marker_missing')
    monkeypatch.setattr(resources, 'verify_account_connection', lambda *a, **k: False)
    messages = []
    app = SimpleNamespace(_readonly=False, _human_connections={},
        _hub_background=lambda work: messages.append(work()))
    WorkbenchV2._verify_account(app, 'account')
    assert 'authenticated_marker_missing' in messages[0]
    assert 'non réutilisable' not in messages[0]
