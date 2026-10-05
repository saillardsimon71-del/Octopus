import json
import shutil
import subprocess

import pytest

from agents import runtime
from agents.tool_registry import ToolRegistry
from octopus import actions, mandates, resources
from test_delegated_resource_hub import BUSINESS, URL, account, channel, own_grant, space


@pytest.fixture
def registry():
    effects = []
    tools = ToolRegistry({
        'browser_click': {'desc': 'click', 'params': {'ref': 'str'}, 'fn': lambda args: effects.append(args)},
        'browser_type': {'desc': 'type', 'params': {'ref': 'str', 'text': 'str'}, 'fn': lambda args: effects.append(args)},
        'browser_scroll': {'desc': 'scroll', 'params': {'direction': 'str?'}, 'fn': lambda args: effects.append(args)},
    })
    return tools, effects


@pytest.mark.parametrize('response', [
    {'tool': 'browser_click', 'ref': '@e7'},
    '{"tool":"browser_click","ref":"@e7"}',
    'Je clique ici. {"tool":"browser_click","ref":"@e7"}',
    {'action': {'tool': 'browser_click', 'ref': '@e7'}},
    {'response': {'tool': 'browser_click', 'args': {'ref': '@e7'}}},
    "{'tool': 'browser_click', 'ref': '@e7',}",
    {'tool': 'BROWSER_CLICK', 'ref': 'e7'},
    {'tool': 'click', 'ref': 7},
    {'tool': 'click', 'ref': 7.0},
    {'tool': 'browser-click', 'args': {'ref': '@e7'}},
    {'name': 'browser_click', 'arguments': {'ref': '@e7'}},
])
def test_one_clear_action_executes_once(registry, response):
    tools, effects = registry
    action = tools.parse_action(response)
    refusal, _ = tools.dispatch(action['tool'], action['args'])
    assert refusal is None
    assert effects == [{'ref': '@e7'}]


@pytest.mark.parametrize('response', [
    '{"tool":"browser_click","args":{"ref":"@e7"}}'
    '{"tool":"browser_click","ref":"@e7"}',
    '{"tool":"BROWSER_CLICK","ref":"e7"} {"action":{"tool":"click","ref":7}}',
    '{"tool":"browser_type","ref":"@e7","text":"hello"}'
    '{"tool":"browser_type","args":{"text":"hello","ref":"@e7"}}',
])
def test_repeated_representations_of_one_action_execute_once(registry, response):
    tools, effects = registry
    action = tools.parse_action(response)
    assert action['_protocol'] == 'normalized'
    assert tools.dispatch(action['tool'], action['args'])[0] is None
    assert len(effects) == 1


@pytest.mark.parametrize('response', [
    '{"tool":"browser_click","ref":"@e1"} {"tool":"browser_click","ref":"@e2"}',
    {'tool': 'browser_click', 'ref': '@e2', 'args': {'ref': '@e1'}},
    {'tool': 'browser_click', 'args': {'ref': ['@e1', '@e2']}},
    {'final': 'done', 'tool': 'browser_click', 'ref': '@e1'},
    {'tool': 'browser_click', 'args': {'ref': '@e1'}, 'arguments': {'ref': '@e2'}},
    {'tool': 'browser_click', 'action': 'browser_type', 'ref': '@e1'},
    '{"tool":"browser_click","ref":"@e1","ref":"@e2"}',
    "{'tool':'browser_click','ref':'@e1','ref':'@e2'}",
    '{"tool":"browser_type","ref":"@e7","text":"hello"}'
    '{"tool":"browser_type","ref":"@e7","text":"goodbye"}',
    '{"tool":"browser_snapshot","full":true}{"tool":"browser_snapshot","full":1}',
])
def test_real_ambiguity_returns_observation_without_effect(registry, response):
    tools, effects = registry
    action = tools.parse_action(response)
    assert action['_protocol'] == 'ambiguous'
    assert action.get('_protocol_error')
    assert effects == []


def test_literal_repair_does_not_change_message(registry):
    tools, effects = registry
    text = 'message ,} with "quotes" and apostrophe'
    action = tools.parse_action(repr({'tool': 'browser_type', 'ref': '@e7', 'text': text}))
    assert tools.dispatch(action['tool'], action['args'])[0] is None
    assert effects[0]['text'] == text
    assert action['_protocol'] == 'normalized'


def test_plain_report_does_not_require_a_proof_form(registry):
    action = registry[0].parse_action('Le code observé est ABC-42.')
    assert action['final'] == 'Le code observé est ABC-42.'


def test_incomplete_syntax_returns_format_observation(registry):
    action = registry[0].parse_action('{"tool":"browser_click",')
    assert action['_protocol'] == 'format'
    assert action['_protocol_error']


@pytest.mark.parametrize('response', ['{"tool":"browser_click",',
    '{"tool":"browser_click","ref":"@e1"} {"tool":"browser_click","ref":"@e2"}'])
@pytest.mark.parametrize('permission', ['', 'same', 'Paiement : intervention humaine requise.'])
def test_protocol_observation_does_not_request_human_authority(monkeypatch, response, permission):
    from octopus import supervisor, tasks
    from test_pursuit_recovery import determination
    action = runtime.TOOLS.parse_action(response)
    permission = action['_protocol_error'] if permission == 'same' else permission
    result = determination('request_permission' if permission else 'pause', permission)
    result['results'] = [{'steps': [{'tool': 'browser_click', 'result_data': {
        'refused': True, 'error_code': action['_protocol'], 'reason': action['_protocol_error']}}]}]
    monkeypatch.setattr(runtime, 'run_mission', lambda *a, **k: result)
    oid = supervisor.start_pursuit()
    supervisor.run_pursuit(oid)
    human = permission.startswith('Paiement')
    assert bool(tasks.pending_human_requests('octopus')) is human
    work = supervisor.work_tasks('octopus', oid)[0]
    assert work['status'] == ('waiting_human' if human else 'done')


@pytest.mark.parametrize('reason,technical', [
    ("Un effet précédent n'est pas encore confirmé sur cette page. Constate son résultat avec browser_verify avant un autre envoi.", True),
    ('Paiement, engagement financier ou création de compte : intervention humaine requise.', False),
])
def test_effect_reconciliation_is_an_observation_and_payment_remains_human(reason, technical):
    from agents.tool_registry import technical_refusal
    assert technical_refusal({'refused': True, 'reason': reason}) is technical


@pytest.mark.parametrize('permission', ['', 'same', 'Paiement : intervention humaine requise.'])
def test_old_format_permission_is_reconciled_without_repeating_work(permission):
    from octopus import supervisor, tasks
    from test_pursuit_recovery import old_waiting_request
    reason = 'Action incomplète. Donne une action courte.'
    permission = reason if permission == 'same' else permission
    oid, tid, _, result = old_waiting_request(reason=reason, permission=permission)
    result['results'][0]['steps'][0]['result_data']['error_code'] = 'format'
    tasks.save_step(tid, 'determination', result)
    assert supervisor.start_pursuit(objective_id=oid) == oid
    human = permission.startswith('Paiement')
    assert tasks.get(tid)['status'] == ('waiting_human' if human else 'queued')
    assert bool(tasks.pending_human_requests('octopus')) is human
    assert tasks.step_value(tid, 'determination') == result


def test_json_boolean_with_trailing_comma_is_recovered():
    action = runtime.TOOLS.parse_action('{"tool":"browser_snapshot","full":true,}')
    assert action['tool'] == 'browser_snapshot'
    assert action['args'] == {'full': True}


def test_secret_in_action_is_not_forwarded_or_echoed(registry):
    secret = 'sk-' + 'a' * 40
    action = registry[0].parse_action({'tool': 'browser_type', 'ref': '@e7', 'text': secret})
    assert action['_protocol_error']
    assert secret not in json.dumps(action)
    assert registry[1] == []


def test_protocol_errors_do_not_create_cognitive_failure_proofs():
    from octopus import browser_workspace, journal
    with journal.run(BUSINESS, 'agent'):
        browser_workspace.record_controller_trace({'step': 1, 'qualification': {'identity': 'fixture'},
            'stagnation': 'repeated_errors', 'failure_layer': 'PROTOCOL'})
    assert not journal.query("SELECT * FROM bench_results WHERE task='browser.execution'")


@pytest.mark.parametrize('effect', ['read', 'contact', 'publish', 'edit'])
def test_connected_entrusted_resource_requires_effect_mandate(effect):
    account()
    resources.set_account_session('new-platform', 'connected')
    assert not mandates.account_authority(BUSINESS, 'new-platform', effect)
    own_grant(effects=[effect])
    assert mandates.account_authority(BUSINESS, 'new-platform', effect)
    assert not mandates.account_authority('other-business', 'new-platform', effect)
    resources.disable_account('new-platform', actor='human')
    assert not mandates.account_authority(BUSINESS, 'new-platform', effect)


@pytest.mark.parametrize('verb', ['submit', 'publish', 'edit'])
def test_ordinary_action_executes_once_with_resource_mandate(monkeypatch, verb):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant(effects=('read', 'contact', 'edit', 'publish'))
    cid = channel(URL)
    mandates.bind_account(cid, BUSINESS, 'new-platform')
    calls = []
    monkeypatch.setattr(actions, '_EXECUTORS', {})
    actions.register_executor('browser_form', verb,
        lambda c, p: calls.append((c['business'], p)) or {'source_ref': URL, 'observation': 'Action executed'}, cost_class='local')
    kwargs = dict(requested_by='agent:test', idempotency_key='same-intent')
    first = actions.propose(BUSINESS, cid, verb, {'text': 'ordinary'}, **kwargs)
    second = actions.propose(BUSINESS, cid, verb, {'text': 'ordinary'}, **kwargs)
    assert first['status'] in ('executed', 'verified')
    assert second['duplicate']
    assert calls == [(BUSINESS, {'text': 'ordinary'})]


@pytest.mark.parametrize('description', ['Pay 10 USD', 'Sign up', 'Créer un compte', 'Accept paid subscription',
                                      'Subscribe', "S'inscrire", 'S’inscrire'])
def test_two_human_boundaries_cannot_be_granted_by_legacy_act(description):
    cid = channel(URL, access='act')
    from octopus import journal
    row = dict(journal.query('SELECT * FROM economic_channels WHERE id=?', (cid,))[0])
    assert not mandates.authorize(row, 'edit', description=description)['allowed']


@pytest.mark.parametrize('description', ['Delete draft', 'Edit profile', 'Publish contract draft', 'Accept ordinary terms'])
def test_ordinary_operations_are_not_human_boundaries(description):
    cid = channel(URL, access='act')
    from octopus import journal
    row = dict(journal.query('SELECT * FROM economic_channels WHERE id=?', (cid,))[0])
    assert mandates.authorize(row, 'edit', description=description)['allowed']


def test_browser_report_keeps_model_claim_and_runtime_provenance():
    steps = [{'step': 1, 'tool': 'browser_snapshot', 'result_data': {'ok': True, 'url': URL, 'snapshot': 'Balance observed'}}]
    result = runtime._browser_final({'final': 'Voici les informations observées.'}, steps)
    assert result['final'] == 'Voici les informations observées.'
    assert result['objective_completion_nature'] == 'model_claim'
    assert result['browser_evidence']


def test_browser_edit_does_not_need_an_effect_enum(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant(effects=('read', 'edit'))
    browser = space(monkeypatch)
    try:
        browser.navigate(URL)
        result = browser.click('@e4')
        assert result['ok']
        assert browser._session.clicks == 1
    finally:
        browser.close()


def test_unverified_submission_retains_identity_and_cannot_repeat(monkeypatch):
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant(effects=('read', 'edit', 'publish'))
    browser = space(monkeypatch)
    try:
        browser.navigate(URL)
        browser._refs['e5'] = {'role': 'textbox', 'name': 'Message'}
        browser.type('@e5', 'Ordinary message')
        assert browser.click('@e1')['ok']
        repeated = browser.click('@e1')
        assert repeated['already_done']
        assert browser._session.clicks == 1
        from octopus import browser_workspace
        with pytest.raises(browser_workspace.Refused, match='effet précédent'):
            browser.press('Enter')
        assert not any(command == 'press' for command, _ in browser._session.commands)
    finally:
        browser.close()


def test_distinct_same_named_submit_buttons_cannot_repeat_an_unconfirmed_submission(monkeypatch):
    from test_delegated_resource_hub import FakeAccountSession
    from octopus import browser_workspace
    account()
    resources.set_account_session('new-platform', 'connected')
    real_run = FakeAccountSession.run
    own_grant(effects=('read', 'publish'))

    def run(session, command, args=(), **kwargs):
        result = real_run(session, command, args, **kwargs)
        if command == 'snapshot':
            result['data']['snapshot'] += '\n- button "Publier" [ref=e5]'
            result['data']['refs']['e5'] = {'role': 'button', 'name': 'Publier'}
        return result

    monkeypatch.setattr(FakeAccountSession, 'run', run)
    browser = space(monkeypatch)
    try:
        browser.navigate(URL)
        assert browser.click('@e1')['ok']
        assert browser.click('@e1')['already_done']
        with pytest.raises(browser_workspace.Refused, match='effet précédent'):
            browser.click('@e5')
        assert browser._session.clicks == 1
    finally:
        browser.close()


@pytest.mark.parametrize('tool,field', [('ACT_ON_CHANNEL', 'action'), ('REGISTER_CHANNEL', 'name')])
def test_tool_alias_preserves_real_parameters(tool, field):
    tools = ToolRegistry({tool.lower(): {'params': {field: 'str'}, 'desc': '', 'fn': lambda args: args}})
    action = tools.parse_action({'tool': tool, field: 'submit'})
    assert action['tool'] == tool.lower()
    assert action['args'] == {field: 'submit'}


def test_browser_action_does_not_require_native_json(monkeypatch, transport, providers_up, registry):
    from octopus import catalog, llm
    cat = catalog.load()
    model = cat.model('openrouter/fixture/vision-alpha:free')
    model['capabilities'].remove('json')
    monkeypatch.setattr(catalog, 'load', lambda *a, **k: cat)
    transport.reply('Je clique. {"tool":"browser_click","ref":"@e7"}')
    completion = llm.complete('browser.bench_step', [{'role': 'user', 'content': 'Click'}], profile='bench',
        pin_model='openrouter/fixture/vision-alpha:free', json_mode=True, validate=registry[0].parse_action)
    assert completion.data['args'] == {'ref': '@e7'}
    request = transport.calls[-1][1]
    assert not request.get('response_format') and not request.get('tools')


@pytest.mark.parametrize('native_supported', [True, False])
def test_browser_native_transport_is_optional_and_clear_intent_executes_once(transport, providers_up, registry, native_supported):
    from octopus import llm
    from octopus.pricing import Usage
    class UnsupportedFormat(Exception):
        status_code = 400
        body = {'error': {'message': 'response_format is not supported'}}
    def respond(provider, request):
        if request.get('response_format'):
            if not native_supported:
                return UnsupportedFormat('response_format is not supported')
            return ('{"tool":"browser_click","ref":"@e7"}', Usage())
        return ('Je clique. {"tool":"browser_click","ref":"@e7"}', Usage())
    transport.handler = respond
    completion = llm.complete('browser.bench_step', [{'role': 'user', 'content': 'Click'}], profile='bench',
        pin_model='openrouter/fixture/vision-alpha:free', json_mode=True, validate=registry[0].parse_action)
    refusal, _ = registry[0].dispatch(completion.data['tool'], completion.data['args'])
    assert refusal is None and registry[1] == [{'ref': '@e7'}]
    assert transport.calls[0][1].get('response_format')
    if not native_supported:
        assert len(transport.calls) == 2 and not transport.calls[-1][1].get('response_format')


def test_keyboard_submission_cannot_bypass_a_human_boundary(monkeypatch):
    from octopus import browser_workspace
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant(effects=('read', 'publish'))
    browser = space(monkeypatch)
    try:
        browser.navigate(URL)
        original = browser._session.run
        def run(command, args=(), **kwargs):
            if command == 'eval' and 'octopus_commit_boundary' in args[0]:
                return {'success': True, 'data': {'result': True}}
            return original(command, args, **kwargs)
        monkeypatch.setattr(browser._session, 'run', run)
        with pytest.raises(browser_workspace.Refused, match='intervention humaine'):
            browser.press('Enter')
        assert not any(command == 'press' for command, _ in browser._session.commands)
    finally:
        browser.close()


@pytest.mark.parametrize('label,blocked', [('Subscribe', True), ("S'inscrire", True), ('Delete account', True),
                                         ('Ordinary button', True), ('Publish', False)])
def test_keyboard_probe_checks_actual_focused_label(monkeypatch, label, blocked):
    from octopus import browser_workspace
    node = shutil.which('node')
    if not node:
        pytest.skip('JavaScript runtime absent for DOM probe test')
    account()
    resources.set_account_session('new-platform', 'connected')
    own_grant(effects=('read', 'publish'))
    browser = space(monkeypatch)
    try:
        browser.navigate(URL)
        original = browser._session.run
        def run(command, args=(), **kwargs):
            if command == 'eval' and any(marker in args[0] for marker in ('octopus_commit_boundary', 'octopus_commit_label')):
                script = 'const document={activeElement:{tagName:"BUTTON", form:null, innerText:' + json.dumps(label)
                script += ',getAttribute:()=>null,matches:()=>false}}; console.log(JSON.stringify(' + args[0] + '));'
                result = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True)
                return {'success': True, 'data': {'result': json.loads(result.stdout)}}
            return original(command, args, **kwargs)
        monkeypatch.setattr(browser._session, 'run', run)
        if blocked:
            with pytest.raises(browser_workspace.Refused):
                browser.press('Enter')
        else:
            assert browser.press('Enter')['ok']
        assert any(command == 'press' for command, _ in browser._session.commands) is not blocked
    finally:
        browser.close()
