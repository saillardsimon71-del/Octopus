"""Offline perception/authority contract: real PNG bytes, gateway requests and action journal."""
import base64
import copy
import io
import ipaddress
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from agents import runtime, web_guard
from agents.tool_registry import technical_refusal
from octopus import browser_workspace as bw, catalog, journal, llm, mandates, resources, tasks
from octopus.pricing import Usage

URL = 'https://account.example/'


class Page:
    def __init__(self, *, text='Interface sans indice', color='green'):
        self.text, self.color, self.url = text, color, URL
        self.login = self.challenge = self.sensitive = False
        self.commands, self.effects = [], []
        self.refs = {'e1': {'role': 'textbox', 'name': 'Contenu'},
                     'e2': {'role': 'button', 'name': '▶'},
                     'e3': {'role': 'button', 'name': '↗'}}
    def close(self): pass
    def run(self, command, args=(), **kwargs):
        args = list(args)
        self.commands.append((command, args))
        if command == 'open': self.url = args[0]
        data = {}
        if command == 'get':
            data = {args[0]: self.url if args[0] == 'url' else self.text}
        if command == 'eval':
            script = args[0]
            assert '.value' not in script and 'document.cookie' not in script
            if 'octopus_capture_safe' in script: result = not self.sensitive
            elif 'octopus_challenge' in script: result = self.challenge
            elif 'octopus_traffic' in script: result = False
            elif script.endswith('return Boolean(login); })()'): result = self.login
            elif script.endswith('return Boolean(challenge); })()'): result = self.challenge
            elif script.endswith('return Boolean(!login && !challenge); })()'):
                result = not (self.login or self.challenge)
            else: result = False  # No authenticated_text, deliberately.
            data = {'result': result}
        if command == 'snapshot':
            data = {'snapshot': self.text, 'refs': self.refs, 'origin': self.url}
        if command == 'screenshot':
            Image.new('RGB', (120, 80), self.color).save(args[0])
        if command in ('click', 'fill'):
            rows = journal.query("SELECT * FROM channel_actions WHERE status='proposed'")
            assert rows, 'effects must be durable BEFORE dispatch'
            self.effects.append((command, args))
            if command == 'click': self.text += '\nRésultat ' + args[0]
        return {'success': True, 'data': data}


@pytest.fixture
def configured(monkeypatch, tmp_path, providers_up):
    resources.configure_account('account', actor='human', provider='Unknown', label='Compte générique',
        url=URL, domains=['account.example'], businesses=['a'], authenticated_text='')
    account = resources.get('account')['web_account']
    resources._write('account', {'web_account': json.dumps(dict(account, browser_kind='chrome_stable'))})
    monkeypatch.setattr(resources, 'account_browser_options', lambda key: {})
    monkeypatch.setattr(web_guard, '_resolved_ips', lambda host: [ipaddress.ip_address('8.8.8.8')])
    from browser_evidence import qualify
    qualify('deepseek/flash')
    # Gateway uses the configured economical policy; availability/evidence are simulated only.
    monkeypatch.setattr(journal, 'evidence', lambda *a: {'eligible': True, 'reason': 'offline fixture'})
    return resources.get('account')['web_account']


def fake_reply(transport, replies, inspect=None):
    replies = iter(replies)
    def handler(provider, request):
        if inspect: inspect(request)
        return json.dumps(next(replies)), Usage(prompt_tokens=100, completion_tokens=30)
    transport.handler = handler


@pytest.mark.parametrize('ui', ['Espace personnel — identité dessinée', 'Console privée — graphique et menu'])
def test_semantic_authenticated_does_not_require_shared_marker(configured, transport, ui):
    fake_reply(transport, [{'state': 'authenticated'}])
    assert resources.verify_account_connection('account', actor='human', session_factory=lambda *a, **k: Page(text=ui))
    account = resources.get('account')['web_account']
    assert account['verification']['method'] == 'semantic_observation'
    assert account['verification']['state'] == 'authenticated'
    assert not mandates.list_mandates('a') and not journal.query('SELECT * FROM economic_channels')
    assert not journal.query('SELECT * FROM channel_actions')
    assert journal.query('SELECT * FROM llm_calls')[0]['cost_usd'] >= 0


@pytest.mark.parametrize('state,status', [('unauthenticated', 'expired'), ('challenge', 'connection_required')])
def test_model_interprets_login_or_visual_challenge(configured, transport, state, status):
    fake_reply(transport, [{'state': state}])
    page = Page(text='structure opaque pour le code', color='red')
    assert not resources.verify_account_connection('account', actor='human', session_factory=lambda *a, **k: page)
    assert resources.get('account')['web_account']['session_status'] == status
    assert not any(c in ('click', 'fill') for c, _ in page.commands)


def test_uncertain_can_choose_image_then_interpret_render_only(configured, transport):
    fake_reply(transport, [{'state': 'uncertain'}, {'tool': 'browser_screenshot', 'args': {}},
                          {'state': 'authenticated'}])
    assert resources.verify_account_connection('account', actor='human', session_factory=lambda *a, **k: Page())
    request = transport.calls[-1][1]
    parts = [part for msg in request['messages'] if isinstance(msg['content'], list) for part in msg['content']]
    image = next(part for part in parts if part['type'] == 'image_url')
    assert image['image_url']['url'].startswith('data:image/png;base64,')
    with Image.open(io.BytesIO(base64.b64decode(image['image_url']['url'].split(',')[1]))) as im:
        assert im.getpixel((0, 0)) == (0, 128, 0)  # Actual rendered information reached the gateway.
    account = resources.get('account')['web_account']
    assert account['verification']['evidence'][-1]['kind'] == 'capture'
    assert account['verification']['evidence'][-1]['business'] == 'a'
    for table in ('resources', 'events', 'task_steps', 'llm_calls'):
        assert 'base64,' not in str([dict(row) for row in journal.query('SELECT * FROM ' + table)])


def test_uncertain_can_choose_domain_navigation(configured, transport):
    page = Page()
    fake_reply(transport, [{'tool': 'browser_navigate', 'args': {'url': URL + 'area'}},
                          {'state': 'authenticated'}])
    assert resources.verify_account_connection('account', actor='human', session_factory=lambda *a, **k: page)
    assert ('open', [URL + 'area']) in page.commands


def test_inconclusive_observations_do_not_fabricate_expired(configured, transport):
    fake_reply(transport, [{'state': 'uncertain'}] * 6)
    assert not resources.verify_account_connection('account', actor='human', session_factory=lambda *a, **k: Page())
    assert resources.get('account')['last_check_detail'] == 'semantic_uncertain'
    assert resources.get('account')['web_account']['session_status'] == 'connection_required'


@pytest.mark.parametrize('boundary', ['login', 'challenge'])
def test_technical_boundary_never_reaches_vision_or_llm(configured, transport, boundary):
    page = Page()
    setattr(page, boundary, True)
    assert not resources.verify_account_connection('account', actor='human', session_factory=lambda *a, **k: page)
    assert transport.calls == [] and not any(c in ('snapshot', 'screenshot') for c, _ in page.commands)


def authorized_space(monkeypatch):
    resources.set_account_session('account', 'connected')
    mid = mandates.grant('a', 'Actions ordinaires', 'owned_account', ['read', 'contact', 'publish', 'edit'],
        actor='human', resource_keys=['account'])
    tid = tasks.enqueue('a', 'resources.account_work', {'browser_resource_key': 'account'})
    page = Page()
    scope = bw.Scope('t' + str(tid), 'a', tid)
    space = bw.Workspace(scope, web_guard.BrowseState(), session_factory=lambda *a, **k: page)
    monkeypatch.setattr(bw, 'workspace', lambda: space)
    monkeypatch.setattr(bw, 'current_scope', lambda: scope)
    return space, page, mid


def test_model_selects_full_mandated_sequence_and_visual_confirmation(configured, transport, monkeypatch):
    from browser_evidence import qualify
    qualify('deepseek/flash')
    space, page, mid = authorized_space(monkeypatch)
    fake_reply(transport, [
        {'tool': 'browser_navigate', 'args': {'url': URL}},
        {'tool': 'browser_type', 'args': {'ref': '@e1', 'text': 'Contenu révisé', 'effect': 'edit'}},
        {'tool': 'browser_click', 'args': {'ref': '@e2', 'effect': 'publish', 'expect': 'Résultat @e2'}},
        {'tool': 'browser_click', 'args': {'ref': '@e3', 'effect': 'contact', 'expect': 'Résultat @e3'}},
        {'tool': 'browser_screenshot', 'args': {}}, {'final': 'Résultat constaté', 'objective_status': 'completed', 'missing': [], 'evidence': [{'step': 4, 'quote': 'Résultat @e3'}]}])
    with journal.run('a', 'test', profile='legacy'):
        result = runtime._run_agent('ORBIT', 'Choisis les actions utiles au compte', 8, False,
            allowed_tools={'browser_navigate', 'browser_type', 'browser_click', 'browser_screenshot'})
    assert result['execution_status'] == 'completed'
    assert not tasks.pending_human_requests()
    assert len(page.effects) == 3
    assert [r['status'] for r in journal.query('SELECT * FROM channel_actions ORDER BY id')] == ['executed', 'verified', 'verified']
    final = transport.calls[-1][1]['messages']
    assert any(isinstance(m['content'], list) and any(p['type'] == 'image_url' for p in m['content']) for m in final)
    assert len(transport.calls) == 6 and len(journal.query('SELECT * FROM llm_calls')) == 6
    assert 'base64,' not in json.dumps(result)
    # Repeated terminal effects remain idempotent.
    assert space.click('@e3', effect='contact', expect='Résultat @e3')['already_done']
    assert len(page.effects) == 3
    mandates.revoke('a', mid, actor='human')
    with pytest.raises(bw.Refused): space.type('@e1', 'autre', effect='edit')
    space.close()


def test_capture_references_cannot_cross_business_task_or_revocation(configured, monkeypatch):
    space, page, mid = authorized_space(monkeypatch)
    space.navigate(URL)
    image = space.screenshot()['image']
    for business, scope in [('b', space.scope.key), ('a', 'other-task')]:
        with pytest.raises(bw.Refused): bw.image_part(image, business=business, scope_key=scope)
    mandates.revoke('a', mid, actor='human')
    with pytest.raises(bw.Refused): bw.image_part(image, business='a', scope_key=space.scope.key)
    space.close()


@pytest.mark.parametrize('label', ['Payout', 'Password', 'OTP', 'Delete account', 'Change security'])
def test_declared_ordinary_effect_cannot_override_sensitive_control(configured, monkeypatch, label):
    space, page, _ = authorized_space(monkeypatch)
    page.refs['e2']['name'] = label
    space.navigate(URL)
    with pytest.raises(bw.Refused): space.click('@e2', effect='publish')
    assert page.effects == [] and not journal.query('SELECT * FROM channel_actions')
    space.close()


def test_markers_do_not_expire_mandated_workspace(configured, monkeypatch):
    space, page, _ = authorized_space(monkeypatch)
    assert space.navigate(URL)['ok']
    page.text = 'UI entièrement différente sans mot-clé'
    assert space.snapshot()['snapshot'] == page.text
    assert resources.get('account')['web_account']['session_status'] == 'connected'
    assert not space._guard('https://other.example/')
    with pytest.raises(bw.Refused): space.navigate('https://other.example/')
    space.close()


def test_images_imply_vision_capability_and_skip_text_only_model(configured, transport, monkeypatch):
    cat = catalog.load()
    raw = copy.deepcopy(cat.raw)
    raw['profiles']['low_cost']['require_evidence'] = False
    raw['tasks']['agent.react_step']['candidates']['low_cost'] = ['openrouter/fixture/text-gamma:free', 'openrouter/fixture/vision-alpha:free']
    monkeypatch.setattr(catalog, 'load', lambda: catalog.Catalog(raw=raw, path=cat.path))
    transport.reply('{"state":"authenticated"}')
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Observe'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,offline'}}]}]
    result = llm.complete('agent.react_step', messages, profile='low_cost', json_mode=True)
    assert result.model == 'openrouter/fixture/vision-alpha:free'
    assert len(transport.calls) == 1
    assert transport.calls[0][1]['messages'] == messages


def test_human_login_lock_blocks_verifier_before_any_observation(configured, transport, monkeypatch):
    monkeypatch.setattr(resources, 'account_browser_options', lambda key: (_ for _ in ()).throw(resources.ResourceError('Fermez Chrome')))
    page = Page()
    with pytest.raises(resources.ResourceError):
        resources.verify_account_connection('account', actor='human', session_factory=lambda *a, **k: page)
    assert page.commands == [] and transport.calls == []


def test_visual_challenge_report_is_a_human_boundary(configured, monkeypatch):
    space, page, _ = authorized_space(monkeypatch)
    space.navigate(URL)
    assert space.verify(state='uncertain')['state'] == 'uncertain'
    with pytest.raises(bw.HumanBrowserRequired): space.verify(state='challenge')
    assert resources.get('account')['web_account']['session_status'] == 'connection_required'
    assert page.effects == []
    space.close()


def test_provider_echoed_image_never_enters_journal_or_caller(configured, transport):
    marker = 'data:image/png;base64,' + 'QUJD' * 100
    transport.handler = lambda *a: RuntimeError(marker)
    with pytest.raises(llm.GatewayError) as error:
        llm.complete('agent.react_step', [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': marker}}]}], profile='economical')
    assert marker not in str(error.value)
    assert 'base64,' not in str([dict(r) for r in journal.query('SELECT * FROM llm_calls')])


def test_detectable_security_screen_is_not_captured(configured, monkeypatch):
    space, page, _ = authorized_space(monkeypatch)
    space.navigate(URL)
    page.sensitive = True
    with pytest.raises(bw.Refused, match='sécurité'): space.screenshot()
    assert not any(c == 'screenshot' for c, _ in page.commands)
    space.close()


def test_capture_backend_failure_is_not_an_authority_request():
    assert technical_refusal('capture indisponible')
    assert technical_refusal('backend_error')
    assert not technical_refusal('écran de sécurité sensible : capture interdite')
    assert not technical_refusal('capture appartenant à un autre business ou une autre tâche')


def test_text_only_pin_refuses_image_before_transport(configured, transport):
    with pytest.raises(llm.NoEligibleModel):
        llm.complete('agent.react_step', [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,offline'}}]}],
            profile='legacy', pin_model='openrouter/fixture/text-gamma:free')
    assert transport.calls == []


def test_image_tampering_and_size_limit_fail_closed(configured, monkeypatch):
    space, page, _ = authorized_space(monkeypatch)
    space.navigate(URL)
    image = space.screenshot(full_page=True)['image']
    assert ('screenshot', [image['path'], '--full']) in page.commands
    Path(image['path']).write_bytes(b'changed')
    with pytest.raises(bw.Refused): bw.image_part(image, business='a', scope_key=space.scope.key)
    monkeypatch.setattr(bw, 'IMAGE_MAX_BYTES', 1)
    with pytest.raises(bw.Refused): space.screenshot()
    assert len(list((bw.task_inbox('a', space.scope.key) / 'screenshots').iterdir())) == 1
    space.close()


@pytest.mark.parametrize('label', ['Your verification code: 482913', 'API keys', 'Recovery phrase'])
def test_real_pre_capture_js_refuses_rendered_secrets(configured, monkeypatch, label):
    space, page, _ = authorized_space(monkeypatch)
    space.navigate(URL)
    original = page.run
    def run(command, args=(), **kwargs):
        if command == 'eval' and 'octopus_capture_safe' in args[0]:
            script = 'global.document={body:{innerText:' + json.dumps(label) + '}};console.log(' + args[0] + ')'
            result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True,
                                    text=True, check=True, timeout=3.)
            return {'success': True, 'data': {'result': json.loads(result.stdout)}}
        return original(command, args, **kwargs)
    page.run = run
    with pytest.raises(bw.Refused): space.screenshot()
    assert not any(c == 'screenshot' for c, _ in page.commands)
    space.close()


def test_read_mandate_allows_observed_disclosure_and_navigation_expectation(configured, monkeypatch):
    resources.set_account_session('account','connected')
    mid=mandates.grant('a','Lecture seule','owned_account',['read'],actor='human',resource_keys=['account'])
    tid=tasks.enqueue('a','resources.account_work',{'browser_resource_key':'account'})
    class Menu(Page):
        def __init__(self):
            super().__init__()
            self.refs={'e1':{'role':'button','name':'Utilisateur','expanded':False,'haspopup':'menu'},
                       'e2':{'role':'link','name':'Orders'}}
        def run(self,command,args=(),**kwargs):
            if command=='click':
                self.commands.append((command,list(args)))
                self.text='Compte opérationnel — commandes observées'
                return {'success':True,'data':{}}
            return super().run(command,args,**kwargs)
    page=Menu()
    space=bw.Workspace(bw.Scope('t'+str(tid),'a',tid),web_guard.BrowseState(),session_factory=lambda *a,**k:page)
    try:
        space.navigate(URL)
        assert space.click('e1')['ok']
        assert space.click('e2',expect='page ouverte')['ok']
        assert not journal.query('SELECT * FROM channel_actions')
        mandates.revoke('a',mid,actor='human')
        assert bw.call_on(space,'click',ref='e1')['refused']
    finally: space.close()


def test_sensitive_disclosure_cannot_be_downgraded_to_read(configured,monkeypatch):
    space,page,_=authorized_space(monkeypatch)
    try:
        space.navigate(URL)
        space._refs['e9']={'role':'button','name':'Payments','expanded':False,'haspopup':'menu'}
        assert bw.call_on(space,'click',ref='e9')['refused']
        assert not journal.query('SELECT * FROM channel_actions')
    finally: space.close()
