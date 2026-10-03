"""Competence vs JSON, trajectory recovery, bounded escalation and sanitized provenance."""
import copy
import json
import time
import pytest

from agents import deepseek, runtime
from octopus import browser_workspace as bw, catalog, journal, llm
from octopus.pricing import Usage
from browser_evidence import qualify


def reply(transport, action=None):
    transport.handler = lambda provider, request: (json.dumps(action or {'final': 'ok'}), Usage(prompt_tokens=10, completion_tokens=5))


def test_json_success_is_not_browser_qualification(transport, providers_up, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    reply(transport)
    with journal.run('a', 'test', profile='economical'):
        journal.record_bench_result({'ts': time.time(), 'bench_run_id': journal.current_run().id, 'suite': 'json',
            'task': 'octopus.json', 'item': 'valid', 'model': 'openrouter/dots-3-free', 'passed': 1, 'score': 1.})
        with pytest.raises(llm.NoEligibleModel):
            llm.complete('browser.react_step', [{'role': 'user', 'content': 'navigate'}], json_mode=True)
    assert transport.calls == []


@pytest.mark.parametrize('profile', ['legacy', 'zero_cost', 'economical', 'low_cost', 'quality_first', 'bench'])
def test_every_profile_requires_browser_evidence(profile, transport, providers_up):
    reply(transport)
    with pytest.raises(llm.NoEligibleModel):
        llm.complete('browser.react_step', [], profile=profile, pin_model='deepseek/flash')
    assert not transport.calls


def test_free_qualified_beats_equivalent_paid(transport, providers_up):
    qualify('ollama/qwen3.5-4b'); qualify('deepseek/flash')
    reply(transport)
    c = llm.complete('browser.react_step', [], profile='economical')
    assert c.model == 'ollama/qwen3.5-4b'
    assert c.justification['browser_qualification']['eligible']


def test_qualified_paid_fallback_and_zero_cost_stays_free(transport, providers_up):
    qualify('deepseek/flash'); reply(transport)
    c = llm.complete('browser.react_step', [], profile='economical')
    assert c.model == 'deepseek/flash'
    with pytest.raises(llm.NoEligibleModel):
        llm.complete('browser.react_step', [], profile='zero_cost')


def test_technical_failure_keeps_existing_fallback(transport, providers_up):
    qualify('ollama/qwen3.5-4b'); qualify('deepseek/flash')
    def handler(provider, request):
        if request['model'] == 'qwen3.5:4b': raise ConnectionError('offline')
        return '{}', Usage(prompt_tokens=10, completion_tokens=2)
    transport.handler = handler
    assert llm.complete('browser.react_step', [], profile='economical').model == 'deepseek/flash'


@pytest.mark.parametrize('failed', [('vision',), ('recovery',), ('invalid_args',), ('stale_refs',), ('ambiguous_dom',), ('affordance','sufficient_dom')])
def test_critical_scenarios_or_overall_threshold_are_mandatory(failed):
    qualify('deepseek/flash', fail=failed)
    assert not llm.browser_quality(catalog.load().model('deepseek/flash'))['eligible']


def test_latest_rerun_and_expiry_not_old_reputation():
    qualify('deepseek/flash')
    qualify('deepseek/flash', fail=('vision',))
    assert not llm.browser_quality(catalog.load().model('deepseek/flash'))['eligible']
    qualify('ollama/qwen3.5-4b', age=15*86400)
    assert not llm.browser_quality(catalog.load().model('ollama/qwen3.5-4b'))['eligible']


def test_pool_without_fixed_identity_never_acquires_qualification():
    assert llm.browser_identity({'api_model': 'auto/best-free', 'provider': 'omniroute'}) is None
    assert llm.browser_identity({'api_model': 'kilo-auto/free', 'provider': 'kilo'}) is None


def test_resolved_identity_changed_is_blocked(transport, providers_up):
    qualify('deepseek/flash')
    transport.handler = lambda provider, request: llm.TransportResult('{}', Usage(), request['model'],
        resolved_model='other', resolved_provider='deepseek')
    with pytest.raises(llm.NoEligibleModel): llm.complete('browser.react_step', [], profile='economical')
    assert journal.query('SELECT status FROM llm_calls')[-1]['status'] == 'blocked'


def test_unknown_pool_bench_records_underlying_identity_only(transport, providers_up):
    transport.handler = lambda provider, request: llm.TransportResult('{}', Usage(), request['model'],
        resolved_model='underlying-v1', resolved_provider='vendor')
    c = llm.complete('browser.bench_step', [], profile='bench', pin_model='kilo/auto-free')
    assert llm.browser_identity(catalog.load().model(c.model), c.resolved_model) == 'browser.model:underlying-v1'
    assert not llm.browser_quality(catalog.load().model(c.model))['eligible']


@pytest.mark.parametrize('tool,args', [
    ('browser_click', {'ref':'e1','effect':'navigate'}),
    ('browser_click', {'ref':'e1','channel_id':'symbol'}),
    ('browser_snapshot', {'full':'true'}),
    ('browser_screenshot', {'full_page':1}),
    ('browser_click', {'ref':'stale'}),
    ('browser_navigate', {'url':'relative/path'}),
    ('browser_navigate', {'url':'https://user:password@example.com/'}),
    ('browser_click', {'ref':'e1','wrong':1}),
])
def test_strict_schema_before_any_dispatch(monkeypatch, tool, args):
    invoked = []
    monkeypatch.setitem(runtime.TOOLS[tool], 'fn', lambda args: invoked.append(args))
    reason, result = runtime.TOOLS.dispatch(tool, args)
    assert reason and not result and not invoked
    from agents.tool_registry import technical_refusal
    assert technical_refusal(reason)


def test_channel_ids_hidden_from_model_but_typed_for_legacy():
    assert 'channel_id' not in runtime.TOOLS.describe(runtime.BROWSER_TOOLS, browser_details=True)
    assert runtime.TOOLS.validate('browser_click', {'ref':'e1','channel_id':17}) is None


@pytest.mark.parametrize('kind', ['repeat', 'state', 'cycle', 'errors', 'stale'])
def test_stagnation_detects_operational_nonprogress(kind):
    t = bw.BrowserTrajectory()
    last = None
    for i in range(5):
        if kind == 'errors': result={'ok':False,'error':'invalid_tool_arguments'}
        elif kind == 'stale': result={'ok':False,'refused':True,'reason':'ref @e1 absente'}
        else: result={'ok':True,'url':'https://example.com/' + (str(i%2) if kind=='cycle' else ''), 'snapshot':'same' if kind!='cycle' else str(i%2)}
        last=t.record('browser_click' if kind!='state' else 'browser_'+str(i), {'ref':'e1'}, result)
        if last['stagnation']: break
    assert last['stagnation']


def test_ref_churn_is_not_progress_and_new_observations_are():
    t=bw.BrowserTrajectory()
    assert t.record('browser_snapshot', {}, {'ok':True,'url':'https://x.test/','snapshot':'name [ref=e1]'})['progress']
    assert not t.record('browser_snapshot', {}, {'ok':True,'url':'https://x.test/','snapshot':'name [ref=e5]'})['progress']
    assert t.record('browser_snapshot', {}, {'ok':True,'url':'https://x.test/','snapshot':'another page'})['progress']


def test_trace_never_logs_form_contents_secrets_or_image_bytes():
    args = {'text':'password 1234', 'expect':'OTP 987654', 'url':'https://x.test/?cookie=secret', 'ref':'not-a-ref', 'random':'cookie'}
    out = json.dumps(bw.trace_args(args))
    assert '1234' not in out and '987654' not in out and 'cookie' not in out and 'not-a-ref' not in out


def runtime_fake(monkeypatch, transport, pages, action_fn):
    qualify('ollama/qwen3.5-4b', fail=('sufficient_dom',)); qualify('deepseek/flash')
    calls=[]
    def handler(provider, request):
        calls.append(copy.deepcopy(request))
        return json.dumps(action_fn(request, len(calls))), Usage(prompt_tokens=10,completion_tokens=5)
    transport.handler=handler
    def dispatch(tool,args,allowed=None):
        return None, pages(tool,args)
    monkeypatch.setattr(runtime.TOOLS, 'dispatch', dispatch)
    return calls


def test_escalation_retains_full_history_and_does_not_reset_browser(monkeypatch, transport, providers_up):
    state={'clicked':0}
    def page(tool,args):
        if tool=='browser_click': state['clicked']+=1
        return {'ok':True,'url':'https://x.test/','snapshot':'Account visible [ref=e1]'}
    def action(request,n):
        if request['model']=='qwen3.5:4b': return {'tool':'browser_click','args':{'ref':'e1'}}
        return {'final':'observed', 'objective_status':'completed','missing':[], 'evidence':[{'step':1,'quote':'Account visible'}]}
    calls=runtime_fake(monkeypatch, transport,page,action)
    checkpoints=[]
    with journal.run('a','task',profile='economical'):
        result=runtime.run_agent('SOUT','read',max_steps=6,allowed_tools={'browser_click'},checkpoint=lambda steps: checkpoints.append(copy.deepcopy(steps)))
    assert result['execution_status']=='completed'
    assert calls[-1]['model']=='deepseek-flash'
    assert state['clicked']==3
    assert len(calls[-1]['messages'])>=8
    assert len(result['steps'])==3 and result['steps'][-1]['browser_controller']['escalation']
    assert sum(st['browser_controller'].get('escalation',False) for st in result['steps'])==1
    assert checkpoints


def test_second_controller_stagnation_stops(monkeypatch, transport, providers_up):
    calls=runtime_fake(monkeypatch,transport,lambda *_:{'ok':True,'url':'https://x.test/','snapshot':'same'},
                       lambda *_:{'tool':'browser_snapshot','args':{}})
    with journal.run('a','test',profile='economical'):
        result=runtime.run_agent('SOUT','read',max_steps=6,allowed_tools={'browser_snapshot'})
    assert result['execution_status']=='stagnated'
    assert len(calls)<12


def test_progress_extends_soft_bound_but_hard_ceiling_remains(monkeypatch, transport, providers_up):
    n={'value':0}
    def pages(*_):
        n['value']+=1
        return {'ok':True,'url':'https://x.test/'+str(n['value']), 'snapshot':'observed '+str(n['value'])}
    runtime_fake(monkeypatch,transport,pages,lambda *_:{'tool':'browser_snapshot','args':{}})
    with journal.run('a','test',profile='economical'):
        result=runtime.run_agent('SOUT','many screens',max_steps=6,allowed_tools={'browser_snapshot'})
    assert len(result['steps'])==18 and result['execution_status']=='step_limit'


def test_invalid_args_are_observations_then_corrected(monkeypatch,transport,providers_up):
    qualify('deepseek/flash')
    actions=iter([{'tool':'browser_click','args':{'ref':'e1','effect':'navigate'}},
                  {'tool':'browser_snapshot','args':{}},
                  {'final':'report','objective_status':'completed','missing':[], 'evidence':[{'step':2,'quote':'useful'}]}])
    transport.handler=lambda *a:(json.dumps(next(actions)),Usage())
    monkeypatch.setitem(runtime.TOOLS['browser_snapshot'],'fn',lambda args:{'ok':True,'url':'https://x.test/','snapshot':'useful'})
    with journal.run('a','test',profile='economical'):
        result=runtime.run_agent('SOUT','read',allowed_tools={'browser_click','browser_snapshot'})
    assert result['execution_status']=='completed'
    assert result['steps'][0]['failure_class']=='technical'
    assert result['steps'][0]['browser_controller']['schema_error']
    assert not journal.query('SELECT * FROM human_requests')


def test_browser_final_requires_grounding_and_acknowledges_missing():
    steps=[{'step':1,'result_data':{'ok':True,'snapshot':'Welcome to account'}}]
    assert runtime._browser_final({'final':'Everything healthy'},steps)['execution_status']=='incomplete'
    assert runtime._browser_final({'final':'Balance 999','objective_status':'completed','missing':[],
        'evidence':[{'step':1,'quote':'Balance 999'}]},steps)['execution_status']=='incomplete'
    out=runtime._browser_final({'final':'Healthy','objective_status':'completed','missing':['orders'],
        'evidence':[{'step':1,'quote':'Welcome'}]},steps)
    assert out['execution_status']=='incomplete' and 'Healthy' not in out['final']


def test_escalation_resume_keeps_exclusions_and_does_not_replay_effect(monkeypatch,transport,providers_up):
    qualify('ollama/qwen3.5-4b',fail=('sufficient_dom',));qualify('deepseek/flash')
    saved=[{'step':1,'tool':'browser_click','args':{'ref':'e1'},'result_data':{'ok':True,'url':'https://x.test/','snapshot':'Existing evidence'},
            'result':'Existing evidence','browser_controller':{'escalation':True,'excluded_models':['ollama/qwen3.5-4b'],'min_quality':.9}}]
    def respond(provider,request):
        assert request['model']=='deepseek-flash'
        assert 'Existing evidence' in json.dumps(request['messages'])
        return json.dumps({'final':'grounded','objective_status':'completed','missing':[], 'evidence':[{'step':1,'quote':'Existing evidence'}]}),Usage()
    transport.handler=respond
    monkeypatch.setitem(runtime.TOOLS['browser_click'],'fn',lambda args:pytest.fail('effect replayed'))
    with journal.run('a','test',profile='economical'):
        result=runtime.run_agent('SOUT','finish',allowed_tools={'browser_click'},resume_steps=saved)
    assert result['execution_status']=='completed' and result['steps']==saved


def test_navigation_provenance_does_not_ban_model_supplied_routes():
    trajectory=bw.BrowserTrajectory()
    first=trajectory.record('browser_navigate',{'url':'https://x.test/'}, {'ok':True,'url':'https://x.test/',
        'snapshot':'links','elements':[{'href':'https://x.test/observed'}]})
    observed=trajectory.record('browser_navigate',{'url':'https://x.test/observed'}, {'ok':True,'url':'https://x.test/observed','snapshot':'next'})
    supplied=trajectory.record('browser_navigate',{'url':'https://x.test/guessed'},{'ok':True,'url':'https://x.test/guessed','snapshot':'new'})
    assert first['url_provenance']=='model_supplied_unobserved'
    assert observed['url_provenance']=='observed_or_historical'
    assert supplied['url_provenance']=='model_supplied_unobserved' and supplied['tool_ok']


def test_incomplete_browser_cannot_be_embellished_by_synthesis(monkeypatch):
    monkeypatch.setattr(runtime.deepseek,'call_json',lambda *a,**k: {'tasks':[{'role':'SOUT','task':'read'}]} if a[1]=='planification' else {'rapport':'Compte sain, solde 999, toutes commandes traitées'})
    monkeypatch.setattr(runtime,'run_agent',lambda *a,**k:{'execution_status':'step_limit','final':'MISSION INCOMPLÈTE — seulement page observée',
        'steps':[{'step':1,'tool':'browser_navigate','result':'welcome','result_data':{'ok':True,'url':'https://x.test/','snapshot':'Welcome'}}]})
    result=runtime.run_mission('Read account state',business='a',profile='economical')
    assert result['execution_status']=='incomplete'
    assert 'MISSION INCOMPLÈTE' in result['rapport'] and '999' not in result['rapport'] and 'Compte sain' not in result['rapport']


def test_task16_technically_ok_sequence_cannot_qualify_mouse_controller(monkeypatch,transport,providers_up):
    monkeypatch.setenv('OPENROUTER_API_KEY','offline-fixture')
    sequence=[{'tool':'browser_navigate','args':{'url':'https://market.example/'}},
              {'tool':'browser_click','args':{'ref':'e34','channel_id':'symbol','effect':'navigate'}},
              {'tool':'browser_click','args':{'ref':'e34'}},
              {'tool':'browser_snapshot','args':{'full':True}},
              {'tool':'browser_click','args':{'ref':'e33'}},
              {'tool':'browser_navigate','args':{'url':'https://market.example/dashboard'}}]
    pending=iter(sequence)
    def handler(provider,request):
        output=next(pending) if request['model'].endswith(':free') else {'tool':'browser_snapshot','args':{}}
        return llm.TransportResult(json.dumps(output),Usage(prompt_tokens=10,completion_tokens=5),request['model'],
            resolved_model=request['model'],resolved_provider='fixture',provider_cost_usd=0.)
    transport.handler=handler
    for _ in sequence:
        c=llm.complete('agent.react_step',[],profile='economical',json_mode=True,validate=llm.parse_json)
        assert c.model=='openrouter/dots-3-free'
    assert all(r['status']=='ok' for r in journal.query("SELECT status FROM llm_calls WHERE task='agent.react_step'"))
    qualify('deepseek/flash')
    c=llm.complete('browser.react_step',[],profile='economical',json_mode=True,validate=llm.parse_json)
    assert c.model=='deepseek/flash'
    assert not llm.browser_quality(catalog.load().model('openrouter/dots-3-free'))['eligible']


def test_unsupported_screenshot_is_recoverable_without_human(monkeypatch,transport,providers_up):
    qualify('deepseek/flash')
    actions=iter([{'tool':'browser_screenshot','args':{}},{'tool':'browser_snapshot','args':{}},
        {'final':'grounded','objective_status':'completed','missing':[], 'evidence':[{'step':2,'quote':'known state'}]}])
    transport.handler=lambda *_:(json.dumps(next(actions)),Usage())
    monkeypatch.setitem(runtime.TOOLS['browser_screenshot'],'fn',lambda args:{'ok':False,'error':'capture indisponible: unsupported'})
    monkeypatch.setitem(runtime.TOOLS['browser_snapshot'],'fn',lambda args:{'ok':True,'url':'https://x.test/','snapshot':'known state'})
    with journal.run('a','test',profile='economical'):
        out=runtime.run_agent('SOUT','read',allowed_tools={'browser_screenshot','browser_snapshot'})
    assert out['execution_status']=='completed'
    assert not out['steps'][0]['browser_controller']['progress']
    assert not journal.query('SELECT * FROM human_requests')


@pytest.mark.parametrize('args', [[], {'url': []}])
def test_malformed_navigation_args_remain_recoverable_observations(monkeypatch, transport, providers_up, args):
    qualify('deepseek/flash')
    actions = iter([{'tool': 'browser_navigate', 'args': args}, {'tool': 'browser_snapshot', 'args': {}},
        {'final': 'grounded', 'objective_status': 'completed', 'missing': [], 'evidence': [{'step': 2, 'quote': 'known state'}]}])
    transport.handler = lambda *_: (json.dumps(next(actions)), Usage())
    monkeypatch.setitem(runtime.TOOLS['browser_snapshot'], 'fn', lambda args: {'ok': True, 'url': 'https://x.test/', 'snapshot': 'known state'})
    with journal.run('a', 'test', profile='low_cost'):
        out = runtime.run_agent('SOUT', 'read', allowed_tools={'browser_navigate', 'browser_snapshot'})
    assert out['execution_status'] == 'completed'
    assert out['steps'][0]['browser_controller']['schema_error']
    assert not journal.query('SELECT * FROM human_requests')


def test_unknown_tools_count_toward_stagnation_and_are_traced(transport, providers_up):
    qualify('deepseek/flash')
    transport.handler = lambda *_: (json.dumps({'tool': 'browser_invented', 'args': {}}), Usage())
    with journal.run('a', 'test', profile='low_cost'):
        out = runtime.run_agent('SOUT', 'read', allowed_tools={'browser_snapshot'})
    assert out['execution_status'] == 'browser_unqualified'
    assert len(out['steps']) == 2
    assert out['steps'][-1]['browser_controller']['stagnation'] == 'repeated_errors'
    assert out['steps'][-1]['browser_controller']['tool'] == '[unknown tool]'
    assert not journal.query('SELECT * FROM human_requests')


def test_invalid_enumeration_contents_are_not_logged():
    assert bw.trace_args({'direction': 'personal content', 'state': 'OTP contents'}) == {
        'direction': '[invalid direction]', 'state': '[invalid state]'}
