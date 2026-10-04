"""Native login / agent separation. Fake processes and fake browser responses only."""
import ipaddress
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import agent_browser, config, runtime, task_handlers, web_guard
from octopus import browser_workspace as bw, journal, mandates, resources, tasks, worker

URL = 'https://account.example/dashboard'


def configure():
    resources.configure_account('account', actor='human', provider='Unknown', label='Compte',
        url=URL, domains=['account.example'], businesses=['b2b'], authenticated_text='Déconnexion')


class Verifier:
    instances = []
    logged_in = True
    challenge = False
    traffic = False
    def __init__(self, name, **kwargs):
        self.name, self.kwargs, self.commands = name, kwargs, []
        self.instances.append(self)
    def close(self): pass
    def run(self, command, args=(), **kwargs):
        self.commands.append((command, list(args)))
        data = {}
        if command == 'get': data = {'url': URL, 'title': 'Compte'}
        if command == 'eval':
            if args[0].endswith('return Boolean(challenge); })()'):
                return {'success': True, 'data': {'result': self.challenge}}
            if args[0].endswith('return Boolean(login); })()'):
                return {'success': True, 'data': {'result': False}}
            data = {'result': self.traffic if 'octopus_traffic' in args[0] else
                self.challenge if 'octopus_challenge' in args[0] else self.logged_in}
        if command == 'snapshot': data = {'origin': URL, 'snapshot': 'Compte Déconnexion', 'refs': {}}
        return {'success': True, 'data': data}


@pytest.fixture
def native(monkeypatch, tmp_path):
    clock = SimpleNamespace(now=0.)
    monkeypatch.setattr(resources, 'time', SimpleNamespace(time=resources.time.time,
        monotonic=lambda: clock.now, sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds)))
    executable = tmp_path / 'Google/Chrome/Application/chrome.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fake executable')
    process = SimpleNamespace(returncode=None, poll=lambda: process.returncode)
    launches = []
    monkeypatch.setattr(resources, 'stable_chrome_executable', lambda: str(executable))
    monkeypatch.setattr(resources, '_human_processes', {})
    monkeypatch.setattr(resources, '_launch_human_browser', lambda *a: launches.append(a) or process)
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda _: [ipaddress.ip_address('8.8.8.8')])
    monkeypatch.setattr(Verifier, 'instances', [])
    return process, launches, executable


def test_native_login_has_no_automation_and_finish_waits_for_close(native, monkeypatch, transport):
    process, launches, executable = native
    configure()
    legacy = resources.account_profile('account')
    connection = resources.HumanConnection('account', actor='human', session_factory=Verifier)
    try:
        assert connection.session is None and Verifier.instances == [] and transport.calls == []
        assert launches[0][0] == str(executable)
        assert launches[0][1] != legacy and launches[0][1].name.endswith('-stable')
        for url in ('https://login.example/', 'https://cdn.example/a.js', 'https://oauth.example/'):
            assert connection.proxy._check(url)
        with pytest.raises(resources.ResourceError, match='Fermez'):
            connection.verify()
        assert Verifier.instances == [] and connection.proxy is not None
        assert resources.get('account')['web_account']['session_status'] == 'connection_required'
        process.returncode = 0
        assert connection.verify()
        verifier = Verifier.instances[-1]
        assert verifier.kwargs['executable_path'] == str(executable)
        assert verifier.kwargs['profile_dir'] == launches[0][1]
        assert verifier.kwargs['inherit_extra_args'] is False
        assert [c for c, _ in verifier.commands] == ['open', 'get', 'eval', 'get']
        assert verifier.commands[0][1] == [URL]
        assert '.value' not in verifier.commands[-2][1][0] and 'cookie' not in verifier.commands[-2][1][0]
        assert resources.get('account')['web_account']['domains'] == ['account.example']
        assert not mandates.list_mandates('b2b') and not journal.query('SELECT * FROM economic_channels')
        assert transport.calls == []
    finally:
        connection.close()


def test_direct_launch_argv_and_environment(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'never-child-secret')
    monkeypatch.setenv('OCTOPUS_CHROMIUM', '/ChromeForTesting/chrome.exe')
    monkeypatch.setenv('OCTOPUS_BROWSER_ARGS', '--remote-debugging-port=9222,--enable-automation')
    monkeypatch.setattr(__import__('subprocess'),
        'Popen', lambda argv, **kw: seen.append((argv, kw)) or 'native')
    assert resources._launch_human_browser('/stable/chrome.exe', tmp_path / 'profile', 'http://127.0.0.1:7777', URL) == 'native'
    argv, kwargs = seen[0]
    assert argv[0] == '/stable/chrome.exe' and argv[-1] == URL and kwargs['shell'] is False
    assert '--new-window' in argv and '--proxy-bypass-list=<-loopback>' in argv
    forbidden = ('remote-debugging', 'automation', 'agent-browser', 'user-agent', 'disable-blink', 'ChromeForTesting')
    assert not any(item in str(argv) for item in forbidden)
    assert 'DEEPSEEK_API_KEY' not in kwargs['env'] and 'OCTOPUS_BROWSER_ARGS' not in kwargs['env']
    assert not any(k.startswith('AGENT_BROWSER') for k in kwargs['env'])


def test_stable_discovery_ignores_cft_no_fallback(monkeypatch, tmp_path):
    chrome = tmp_path / 'Google/Chrome/Application/chrome.exe'
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b'fake')
    monkeypatch.setenv('PROGRAMFILES', str(tmp_path))
    monkeypatch.setenv('OCTOPUS_CHROMIUM', '/testing/chrome.exe')
    # Restore sys.platform immediately; the surrounding test runtime remains Linux.
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'platform', 'win32')
        assert resources.stable_chrome_executable() == str(chrome.resolve())
        chrome.unlink()
        for key in ('PROGRAMFILES(X86)', 'LOCALAPPDATA'): patch.delenv(key, raising=False)
        with pytest.raises(resources.ResourceError, match='introuvable'): resources.stable_chrome_executable()


def test_close_never_terminates_human_and_restart_is_fail_closed(native):
    process, _, _ = native
    configure()
    connection = resources.HumanConnection('account', actor='human', session_factory=Verifier)
    connection.close()
    assert process.poll() is None
    with pytest.raises(resources.ResourceError, match='Fermez'):
        resources.verify_account_connection('account', actor='human', session_factory=Verifier)
    assert not Verifier.instances
    process.returncode = 0
    assert resources.verify_account_connection('account', actor='human', session_factory=Verifier)
    # Configuration edits preserve the chosen browser identity, with no DB migration.
    profile = resources.account_profile('account')
    configure()
    assert resources.account_profile('account') == profile


def test_profile_lock_survives_lost_process_handle(native, monkeypatch):
    process, _, _ = native
    configure()
    connection = resources.HumanConnection('account', actor='human', session_factory=Verifier)
    connection.close()
    resources._human_processes.clear()  # Simulated Workbench restart.
    real_open = os.open
    def locked(path, *args, **kwargs):
        if str(path).endswith('lockfile'): raise PermissionError('profile in use')
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', locked)
    with pytest.raises(resources.ResourceError, match='Fermez'):
        resources.verify_account_connection('account', actor='human', session_factory=Verifier)
    assert not Verifier.instances


@pytest.mark.parametrize('failure', ['backend', 'lost-session', 'missing-stable'])
def test_no_fabricated_session_when_reuse_fails(native, monkeypatch, failure):
    process, _, _ = native
    configure()
    connection = resources.HumanConnection('account', actor='human', session_factory=Verifier)
    process.returncode = 0
    if failure == 'backend':
        def broken(*a, **k): raise RuntimeError('raw-cookie-password-OTP must not be logged')
        factory = broken
    else: factory = Verifier
    if failure == 'lost-session': monkeypatch.setattr(Verifier, 'logged_in', False)
    if failure == 'missing-stable':
        def missing(): raise resources.ResourceError('stable absent')
        monkeypatch.setattr(resources, 'stable_chrome_executable', missing)
        with pytest.raises(resources.ResourceError): connection.verify()
    else:
        connection.session_factory = factory
        assert not connection.verify()
        assert resources.get('account')['web_account']['session_status'] in ('expired', 'unavailable')
    connection.close()
    assert resources.get('account')['web_account']['session_status'] != 'connected'
    assert not mandates.list_mandates('b2b')
    for table in ('resources', 'events', 'tasks', 'human_requests', 'llm_calls'):
        assert 'raw-cookie-password-OTP' not in str([dict(r) for r in journal.query('SELECT * FROM ' + table)])


def test_stable_account_keeps_strict_guard_and_executable(native, monkeypatch):
    process, launches, executable = native
    configure()
    connection = resources.HumanConnection('account', actor='human', session_factory=Verifier)
    process.returncode = 0
    assert connection.verify()
    mid = mandates.grant('b2b', 'Lecture', 'owned_account', ['read'], actor='human', resource_keys=['account'])
    tid = tasks.enqueue('b2b', 'resources.account_work', {'browser_resource_key': 'account'})
    workspace = bw.Workspace(bw.Scope('stable-test', 'b2b', tid), web_guard.BrowseState(), session_factory=Verifier)
    try:
        assert workspace.navigate(URL)['ok']
        assert workspace._session.kwargs['executable_path'] == str(executable)
        assert workspace._session.kwargs['profile_dir'] == launches[0][1]
        assert not workspace._guard('https://oauth.example/')
        assert not workspace._guard('https://stripe.example/')
        assert not workspace._guard('http://127.0.0.1/')
        with pytest.raises(bw.Refused, match='hors de la ressource'): workspace.navigate('https://cdn.example/')
        assert resources.get('account')['web_account']['session_status'] == 'connected'
        assert mandates.list_mandates('b2b', active=True)[0]['id'] == mid
    finally: workspace.close()


def test_agent_session_explicit_backend_never_uses_cft(native, monkeypatch):
    _, _, executable = native
    monkeypatch.setattr(agent_browser, 'find_binary', lambda: '/fake/agent-browser')
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: pytest.fail('CfT fallback'))
    monkeypatch.setenv('OCTOPUS_BROWSER_ARGS', '--user-agent=spoof')
    session = agent_browser.Session('stable-verifier', proxy_url='http://127.0.0.1:1',
        executable_path=str(executable), inherit_extra_args=False)
    assert session.chromium == str(executable) and session.extra_args == ()
    assert '--executable-path' in session._global_args()
    with pytest.raises(agent_browser.BackendUnavailable, match='aucun repli'):
        agent_browser.Session('missing', proxy_url='', executable_path=str(executable) + '-absent')


@pytest.mark.parametrize('account_mode', [False, True])
def test_captcha_stops_agent_and_waits_durably_without_snapshot(native, monkeypatch, account_mode):
    process, _, _ = native
    if account_mode:
        configure()
        connector = resources.HumanConnection('account', actor='human', session_factory=Verifier)
        process.returncode = 0
        assert connector.verify()
        mandates.grant('b2b', 'Lecture', 'owned_account', ['read'], actor='human', resource_keys=['account'])
    monkeypatch.setattr(Verifier, 'challenge', True)
    tid = tasks.enqueue('b2b', 'test.challenge', {'browser_resource_key': 'account'} if account_mode else {})
    @worker.handler('test.challenge')
    def challenge_task(ctx):
        workspace = bw.Workspace(bw.Scope('challenge', 'b2b', ctx.id), web_guard.BrowseState(), session_factory=Verifier)
        try:
            return task_handlers._run(ctx, lambda: runtime.TOOLS.dispatch('browser_navigate', {'url': URL}))
        finally: workspace.close()
    # Run the real dispatch/runtime exception boundary with this task's fake workspace.
    def navigate(args):
        task = tasks.get(tid)
        space = bw.Workspace(bw.Scope('challenge', 'b2b', tid), web_guard.BrowseState(), session_factory=Verifier)
        try: return space.navigate(args['url'])
        finally: space.close()
    monkeypatch.setitem(runtime.TOOLS, 'browser_navigate', dict(runtime.TOOLS['browser_navigate'], fn=navigate))
    result = worker.run_one(task_id=tid)
    assert result['status'] == 'waiting_human'
    request = tasks.pending_human_requests('b2b')[0]
    assert json.loads(request['context'])['need'] == 'captcha'
    assert not any(c in ('snapshot', 'click', 'fill') for instance in Verifier.instances for c, _ in instance.commands)
    if account_mode:
        assert resources.get('account')['web_account']['session_status'] == 'connection_required'
        monkeypatch.setattr(Verifier, 'challenge', False)
        assert resources.verify_account_connection('account', actor='human', session_factory=Verifier)
        assert tasks.get(tid)['status'] == 'queued' and not tasks.pending_human_requests('b2b')


def test_unusual_traffic_is_unavailable_source_without_solver(native, monkeypatch):
    monkeypatch.setattr(Verifier, 'traffic', True)
    workspace = bw.Workspace(bw.Scope('public-google', 'b2b', None), web_guard.BrowseState(), session_factory=Verifier)
    try:
        with pytest.raises(bw.Refused, match='source Web inaccessible'): workspace.navigate('https://www.google.com/search')
        assert not any(c == 'snapshot' for c, _ in workspace._session.commands)
        assert not tasks.pending_human_requests('b2b')
    finally: workspace.close()


def test_real_account_task_runtime_captcha_propagates_without_synthesis(native, monkeypatch, transport, providers_up):
    from octopus import llm
    from octopus.pricing import Usage
    process, _, _ = native
    configure()
    connection = resources.HumanConnection('account', actor='human', session_factory=Verifier)
    process.returncode = 0
    assert connection.verify()
    mandates.grant('b2b', 'Lecture', 'owned_account', ['read'], actor='human', resource_keys=['account'])
    monkeypatch.setattr(Verifier, 'challenge', True)
    monkeypatch.setattr(agent_browser, 'Session', Verifier)
    def response(provider, request):
        assert request['max_tokens'] in (700, 500, 1200)  # No synthesis after the boundary.
        output = {'tasks': [{'role': 'SOUT', 'task': 'Observer le compte'}]} if request['max_tokens'] == 700 else {
            'tool': 'browser_navigate', 'args': {'url': URL}}
        return llm.TransportResult(json.dumps(output), Usage(prompt_tokens=10, completion_tokens=10),
            request['model'], resolved_model=request['model'], resolved_provider='OfflineFake', provider_cost_usd=0.)
    from browser_evidence import qualify
    qualify('openrouter/fixture/vision-alpha:free')
    transport.handler = response
    result = resources.account_task('b2b', 'account', 'Observer le compte')
    assert result['status'] == 'waiting_human' and len(transport.calls) == 2
    assert len(tasks.pending_human_requests('b2b')) == 1
    assert not any(c == 'snapshot' for instance in Verifier.instances for c, _ in instance.commands)
    assert journal.query('SELECT COUNT(*) AS n FROM llm_calls')[0]['n'] == 2
    assert resources.get('account')['web_account']['session_status'] == 'connection_required'
    assert len(mandates.list_mandates('b2b', active=True)) == 1
