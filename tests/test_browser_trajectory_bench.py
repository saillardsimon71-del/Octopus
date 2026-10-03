"""Offline harness tests: synthetic decisions prove machinery, never a real model's quality."""
import base64
import contextlib
import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from agents import agent_browser, runtime, web_guard
from octopus import browser_bench as bb, browser_workspace as bw, catalog, journal, llm
from octopus.pricing import Usage
from browser_evidence import qualify


class FixtureSession:
    """Backend double reads the local fixture HTML; snapshots never contain canvas pixels."""
    def __init__(self, fixture):
        self.fixture=fixture
        self.url=fixture.start_url
        self.menu=False
        self.generation=0
        self.refs={}
    def close(self): pass
    def parse(self):
        body=self.fixture.render(self.url[len(self.fixture.origin):])
        if not self.menu:
            body=re.sub(r'<nav id=menu hidden>.*?</nav>','',body)
        body=re.sub(r'<script>.*?</script>','',body)
        entries=[]
        for match in re.finditer(r'<(a|button)\b([^>]*)>(.*?)</\1>',body):
            tag,attrs,text=match.groups()
            name_match=re.search(r'aria-label="([^"]+)"',attrs)
            name=name_match.group(1) if name_match else re.sub('<.*?>','',text)
            href=re.search(r'href="([^"]+)"',attrs)
            target=re.search(r"location.href='([^']+)'",attrs)
            entries.append({'role':'link' if tag=='a' else 'button','name':name,
                'href':href.group(1) if href else None, 'target':target.group(1) if target else None,
                'expanded':self.menu if 'aria-expanded' in attrs else None,
                'haspopup':'menu' if 'aria-haspopup' in attrs else None})
        self.refs={f'e{self.generation*10+i+1}':item for i,item in enumerate(entries)}
        heading=re.search('<h1>(.*?)</h1>',body)
        text=(heading.group(1) if heading else 'Marketplace')
        if self.url.endswith(self.fixture.routes['done']) and self.fixture.scenario!='vision':
            text+='\nAccount evidence code: '+self.fixture.token
        snapshot=text+'\n'+'\n'.join(f'- {v["role"]} "{v["name"]}" [ref={k}]' for k,v in self.refs.items())
        return snapshot
    def run(self, command, args=(), **kwargs):
        data={}
        if command=='open': self.url=args[0];self.generation+=1
        if command=='snapshot':
            snap=self.parse()
            data={'origin':self.url,'snapshot':snap,'refs':{k:{'role':v['role'],'name':v['name']} for k,v in self.refs.items()}}
        if command=='get':
            data={args[0]:self.url if args[0]=='url' else self.parse()}
        if command=='eval':
            script=args[0]
            data={'result':'octopus_capture_safe' in script or '!login && !challenge' in script}
        if command=='click':
            ref=args[0].lstrip('@')
            if ref not in self.refs:
                return {'success':False,'error':'stale ref not found'}
            item=self.refs[ref]
            if item['href'] or item['target']:
                self.url=self.fixture.origin+(item['href'] or item['target']);self.generation+=1;self.menu=False
            elif item['haspopup']: self.menu=not self.menu;self.generation+=1
        if command=='back': self.url=self.fixture.origin+self.fixture.routes['home'];self.generation+=1
        if command=='batch':
            rows=[]
            for action in args:
                _,_,ref,attr=action.split()
                item=self.refs.get(ref.lstrip('@'),{})
                value={'href':item.get('href'),'aria-expanded':str(item.get('expanded')).lower() if item.get('expanded') is not None else None,
                       'aria-haspopup':item.get('haspopup')}.get(attr)
                rows.append({'success':True,'result':{'value':value}})
            data={'results':rows}
        if command=='screenshot':
            im=Image.new('RGB',(800,180),'white')
            ImageDraw.Draw(im).text((20,75),'Evidence: '+self.fixture.token,fill='black')
            im.save(args[0])
        return {'success':True,'data':data}


def simulated_decider(fixture, space, captures):
    """Test oracle chooses different valid paths; no such planner exists in production."""
    opened=False
    screenshot=False
    def choose(messages):
        nonlocal opened,screenshot
        if not opened:
            opened=True
            return {'tool':'browser_navigate','args':{'url':fixture.start_url}}
        last=messages[-1]['content']
        if isinstance(last,list):
            data=next(p['image_url']['url'] for p in last if p['type']=='image_url')
            with Image.open(io.BytesIO(base64.b64decode(data.split(',')[1]))) as im:
                assert im.size==(800,180)
            captures.append(True)
        if not space._refs:
            return {'tool':'browser_snapshot','args':{}}
        if fixture.scenario in ('vision','ambiguous_dom') and not screenshot and (fixture.scenario=='ambiguous_dom' or space._url.endswith(fixture.routes['done'])):
            screenshot=True
            return {'tool':'browser_screenshot','args':{}}
        if space._url.endswith(fixture.routes['done']):
            return {'final':'Observed evidence code '+fixture.token}
        if space._url==fixture.start_url and fixture.scenario=='recovery':
            target='Home'
        elif any(v.get('haspopup')=='menu' for v in space._refs.values()) and not any(v['role']=='link' for v in space._refs.values()):
            target='user menu' if fixture.scenario!='language_layout' else 'compte'
        elif fixture.scenario=='ambiguous_dom' and space._url.endswith(fixture.routes['home']):
            keys=[key for key,val in space._refs.items() if val['role']=='button']
            return {'tool':'browser_click','args':{'ref':keys[-1]}}
        else:
            target= {'manage':'Orders','orders':'Performance','performance':'Operational state'}.get(
                next((k for k in ('manage','orders','performance') if space._url.endswith(fixture.routes[k])),''), 'Account management')
            if fixture.scenario!='multi_screen' and space._url.endswith(fixture.routes['manage']): target='Operational state'
            if fixture.scenario=='language_layout': target='État opérationnel' if space._url.endswith(fixture.routes['manage']) else 'Mon espace professionnel'
        key=next(key for key,val in space._refs.items() if target in val['name'])
        return {'tool':'browser_click','args':{'ref':key}}
    return choose


@pytest.mark.parametrize('scenario',sorted(llm.BROWSER_SCENARIOS))
def test_ten_real_fixture_definitions_with_offline_backend_and_oracle(monkeypatch,scenario):
    with bb.Fixture(scenario) as fixture:
        session=FixtureSession(fixture)
        monkeypatch.setattr(agent_browser,'Session',lambda *a,**k:session)
        with bb.local_workspace(fixture) as space:
            captures=[]
            outcome=bb.trajectory('deepseek/flash',fixture,space,decide=simulated_decider(fixture,space,captures))
            assert outcome['passed'],outcome
            assert len(outcome['trace'])<=bb.MAX_STEPS
            if scenario in ('vision','ambiguous_dom'): assert captures and outcome['checks']['vision']
            assert outcome['identities']==set()  # A fake does not impersonate a real evaluated model.
    assert not journal.query('SELECT * FROM operational_mandates')
    assert all(r['access']=='act' and r['status']=='suspended' for r in journal.query('SELECT * FROM economic_channels'))


def test_bench_rejects_factually_wrong_report_even_after_reaching_goal(monkeypatch):
    with bb.Fixture('sufficient_dom') as fixture:
        session=FixtureSession(fixture)
        monkeypatch.setattr(agent_browser,'Session',lambda *a,**k:session)
        with bb.local_workspace(fixture) as space:
            real=simulated_decider(fixture,space,[])
            def liar(messages):
                action=real(messages)
                return {'final':'everything good; code INVENTED'} if 'final' in action else action
            out=bb.trajectory('deepseek/flash',fixture,space,decide=liar)
            assert not out['passed'] and out['checks']['goal'] and not out['checks']['evidence']


def test_missing_backend_fails_before_any_provider_call(monkeypatch,transport):
    monkeypatch.setattr(agent_browser,'require_backend',lambda:(_ for _ in ()).throw(agent_browser.BackendUnavailable('absent')))
    with pytest.raises(agent_browser.BackendUnavailable): bb.run(['deepseek/flash'],allow_paid=True)
    assert not transport.calls


def test_request_cap_counts_structured_retries_and_invalidates_partial_run(monkeypatch, transport, providers_up, tmp_path):
    mid = 'openrouter/fixture/vision-alpha:free'
    qualify(mid)
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: None)
    original = bb.local_workspace
    def workspace(fixture):
        monkeypatch.setattr(agent_browser, 'Session', lambda *a, **k: FixtureSession(fixture))
        return original(fixture)
    monkeypatch.setattr(bb, 'local_workspace', workspace)
    class UnsupportedFormat(Exception):
        status_code = 400
        body = {'error': {'message': 'response_format is not supported'}}
    transport.handler = lambda provider, request: UnsupportedFormat('400')
    logs = []
    result = bb.run([mid, 'openrouter/fixture/vision-beta:free'], repeats=2,
                    max_requests=1, max_cost_usd=0, out_dir=tmp_path, log=logs.append)
    assert result['requests_used'] == len(transport.calls) == 1
    assert result['incomplete'] and len(result['rows']) == 1
    assert result['preflight']['trajectories'] == 40
    assert result['preflight']['step_requests_upper_bound'] == 480
    assert result['preflight']['structured_requests_upper_bound'] == 960
    assert logs[0].startswith('Preflight: ')
    checks = json.loads(result['rows'][0]['checks'])
    assert checks['benchmark_incomplete'] and checks['calls'] == 1
    assert not llm.browser_quality(catalog.load().model(mid))['eligible']
    assert result['summaries'][0]['cost_per_completed_objective_usd'] is None
    assert not journal.query('SELECT * FROM operational_mandates')


@pytest.mark.parametrize('limit', [0, -1, True, 1.5])
def test_invalid_request_ceiling_fails_before_backend_or_provider(limit, monkeypatch, transport):
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: pytest.fail('backend contacted'))
    with pytest.raises(ValueError, match='max_requests'):
        bb.run(['deepseek/flash'], allow_paid=True, max_requests=limit)
    assert not transport.calls


@pytest.mark.parametrize('failure', [TimeoutError, llm.BudgetExceeded])
def test_failed_rerun_does_not_retain_browser_qualification(failure, monkeypatch, tmp_path):
    mid = 'openrouter/fixture/vision-alpha:free'
    qualify(mid)
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: None)
    def fail(*args, **kwargs):
        raise failure('fixture')
    monkeypatch.setattr(bb, 'trajectory', fail)
    monkeypatch.setattr(bb, 'local_workspace', lambda fixture: contextlib.nullcontext(None))
    result = bb.run([mid], max_cost_usd=0, out_dir=tmp_path, log=lambda line: None)
    assert not llm.browser_quality(catalog.load().model(mid))['eligible']
    assert all(not row['passed'] for row in result['rows'])
    if failure is llm.BudgetExceeded:
        assert result['incomplete'] and len(result['rows']) == 1


def test_live_stagnation_is_separate_from_provider_health_and_rerun_can_restore():
    identity=qualify('deepseek/flash')
    for step in (1,2):
        with journal.run('a','agent'):
            bw.record_controller_trace({'step':step,'qualification':{'identity':identity},'stagnation':'repeated_action'})
    assert not llm.browser_quality(catalog.load().model('deepseek/flash'))['eligible']
    assert not journal.query('SELECT * FROM llm_calls')
    qualify('deepseek/flash')
    assert llm.browser_quality(catalog.load().model('deepseek/flash'))['eligible']


@pytest.mark.parametrize('failure', [llm.NoEligibleModel, TimeoutError])
def test_unavailable_candidate_stops_without_cognitive_failures(failure, monkeypatch, tmp_path):
    mid = 'openrouter/fixture/vision-alpha:free'
    qualify(mid)
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: None)
    monkeypatch.setattr(bb, 'local_workspace', lambda fixture: contextlib.nullcontext(None))
    def fail(*args, **kwargs):
        if failure is llm.NoEligibleModel:
            raise failure('browser.bench_step', 'bench', [{'model': mid, 'reason': 'cooldown'}])
        raise failure('offline provider')
    monkeypatch.setattr(bb, 'trajectory', fail)
    result = bb.run([mid], max_cost_usd=0, out_dir=tmp_path, log=lambda line: None)
    assert result['incomplete'] and len(result['rows']) == 1
    assert result['not_run'] == 19
    assert result['rows'][0]['status'] == 'INCOMPLETE_INFRA'
    assert result['summaries'][0]['evaluated'] == 0
    assert result['summaries'][0]['failed'] == 0
    import csv
    with Path(result['files'][0]).open(encoding='utf-8', newline='') as fh:
        exported = list(csv.DictReader(fh))
    assert len(exported) == 20
    assert sum(r['status'] == 'NOT_RUN' for r in exported) == 19
    assert all(r['score'] == '' for r in exported)
    assert len(journal.query('SELECT * FROM bench_results WHERE bench_run_id=?', (result['bench_run_id'],))) == 1
    proof = llm.browser_quality(catalog.load().model(mid))
    assert not proof['eligible'] and proof['quality'] is None


@pytest.mark.parametrize('image_request', [False, True])
def test_429_retries_same_decision_once_then_stops(image_request, monkeypatch, transport, providers_up, tmp_path):
    mid = 'openrouter/fixture/vision-alpha:free'
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: None)
    original = bb.local_workspace
    def workspace(fixture):
        monkeypatch.setattr(agent_browser, 'Session', lambda *a, **k: FixtureSession(fixture))
        return original(fixture)
    monkeypatch.setattr(bb, 'local_workspace', workspace)
    clock = {'elapsed': 0.}
    real_mono, real_wall = bb.time.monotonic, bb.time.time
    monkeypatch.setattr(bb.time, 'monotonic', lambda: real_mono() + clock['elapsed'])
    monkeypatch.setattr(bb.time, 'time', lambda: real_wall() + clock['elapsed'])
    monkeypatch.setattr(bb.time, 'sleep', lambda delay: clock.update(elapsed=clock['elapsed'] + delay))
    class RateLimitError(Exception):
        status_code = 429
        body = {'error': {'metadata': {'limit_source': 'upstream_provider_shared_pool'}}}
    def respond(provider, request):
        if image_request and len(transport.calls) == 1:
            return json.dumps({'tool': 'browser_navigate', 'args': {'url': request['messages'][1]['content'].split('Start at ')[1]}}), Usage()
        if image_request and len(transport.calls) == 2:
            return json.dumps({'tool': 'browser_screenshot', 'args': {}}), Usage()
        return RateLimitError('429 rate limit')
    transport.handler = respond
    result = bb.run([mid], max_cost_usd=0, out_dir=tmp_path, log=lambda line: None)
    assert result['incomplete'] and result['not_run'] == 19
    assert result['requests_used'] == (4 if image_request else 2)
    assert len(result['rows']) == 1 and result['rows'][0]['status'] == 'INCOMPLETE_INFRA'
    assert 30 <= clock['elapsed'] <= 60
    assert transport.calls[-2][1]['messages'] == transport.calls[-1][1]['messages']
    assert result['summaries'][0]['evaluated'] == 0
    assert not llm.browser_quality(catalog.load().model(mid))['eligible']


def test_legacy_rate_limit_run_is_not_a_cognitive_zero():
    mid = 'openrouter/fixture/vision-alpha:free'
    identity = qualify(mid)
    with journal.run('browser-benchmark', 'bench') as ctx:
        for item in sorted(llm.BROWSER_SCENARIOS):
            journal.record_bench_result({'ts': bb.time.time(), 'bench_run_id': ctx.id,
                'suite': 'octopus.browser_bench', 'task': llm.BROWSER_BENCH_TASK, 'item': item,
                'model': identity, 'prompt_version': llm.BROWSER_BENCH_VERSION,
                'passed': 0, 'score': 0., 'error': 'RateLimitError' if item == 'affordance' else 'NoEligibleModel'})
    proof = llm.browser_quality(catalog.load().model(mid))
    assert not proof['eligible'] and proof['quality'] is None


def test_single_429_resumes_the_existing_trajectory(monkeypatch, transport, providers_up):
    mid = 'openrouter/fixture/vision-alpha:free'
    clock = {'elapsed': 0.}
    real_mono, real_wall = bb.time.monotonic, bb.time.time
    monkeypatch.setattr(bb.time, 'monotonic', lambda: real_mono() + clock['elapsed'])
    monkeypatch.setattr(bb.time, 'time', lambda: real_wall() + clock['elapsed'])
    monkeypatch.setattr(bb.time, 'sleep', lambda delay: clock.update(elapsed=clock['elapsed'] + delay))
    class RateLimitError(Exception):
        status_code = 429
    with bb.Fixture('sufficient_dom') as fixture:
        monkeypatch.setattr(agent_browser, 'Session', lambda *a, **k: FixtureSession(fixture))
        with bb.local_workspace(fixture) as space, journal.run('browser-benchmark', 'bench'):
            choose = simulated_decider(fixture, space, [])
            def respond(provider, request):
                if len(transport.calls) == 1:
                    return RateLimitError('429 rate limit')
                return json.dumps(choose(request['messages'])), Usage()
            transport.handler = respond
            budget = bb.RequestBudget(5)
            outcome = bb.trajectory(mid, fixture, space, request_budget=budget, log=lambda line: None)
    assert outcome['passed'] and budget.used == 5
    assert [r['tool'] for r in outcome['trace']].count('browser_navigate') == 1
    assert transport.calls[0][1]['messages'] == transport.calls[1][1]['messages']


@pytest.mark.parametrize('request_limit,retry_after', [(1, '30'), (240, '120')])
def test_429_retry_respects_request_and_wait_limits(request_limit, retry_after, monkeypatch, transport, providers_up, tmp_path):
    from types import SimpleNamespace
    mid = 'openrouter/fixture/vision-alpha:free'
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: None)
    monkeypatch.setattr(bb, 'local_workspace', lambda fixture: contextlib.nullcontext(None))
    monkeypatch.setattr(bb.time, 'sleep', lambda delay: pytest.fail('retry must not wait'))
    class RateLimitError(Exception):
        status_code = 429
        response = SimpleNamespace(headers={'retry-after': retry_after})
    transport.handler = lambda provider, request: RateLimitError('429 rate limit')
    result = bb.run([mid], max_requests=request_limit, max_cost_usd=0, out_dir=tmp_path, log=lambda line: None)
    assert result['incomplete'] and result['requests_used'] == 1 and result['not_run'] == 19


def test_invalid_json_is_a_cognitive_failure(monkeypatch, transport, providers_up, tmp_path):
    mid = 'openrouter/fixture/vision-alpha:free'
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: None)
    monkeypatch.setattr(bb, 'local_workspace', lambda fixture: contextlib.nullcontext(None))
    transport.reply('not json')
    result = bb.run([mid], max_cost_usd=0, out_dir=tmp_path, log=lambda line: None)
    assert not result['incomplete'] and result['not_run'] == 0
    assert result['summaries'][0]['evaluated'] == result['summaries'][0]['failed'] == 20


@pytest.mark.parametrize('action', [{'tool': ['browser_navigate'], 'args': {}}, {'tool': 'browser_click', 'args': []}])
def test_invalid_action_envelope_is_cognitive(action, monkeypatch, transport, providers_up, tmp_path):
    mid = 'openrouter/fixture/vision-alpha:free'
    monkeypatch.setattr(agent_browser, 'require_backend', lambda: None)
    monkeypatch.setattr(bb, 'local_workspace', lambda fixture: contextlib.nullcontext(None))
    transport.reply(json.dumps(action))
    result = bb.run([mid], max_cost_usd=0, out_dir=tmp_path, log=lambda line: None)
    assert not result['incomplete'] and result['summaries'][0]['failed'] == 20


def test_recovered_429_does_not_retry_later_cognitive_failure(monkeypatch, transport, providers_up):
    mid = 'openrouter/fixture/vision-alpha:free'
    clock = {'elapsed': 0.}
    real_mono, real_wall = bb.time.monotonic, bb.time.time
    monkeypatch.setattr(bb.time, 'monotonic', lambda: real_mono() + clock['elapsed'])
    monkeypatch.setattr(bb.time, 'time', lambda: real_wall() + clock['elapsed'])
    monkeypatch.setattr(bb.time, 'sleep', lambda delay: clock.update(elapsed=clock['elapsed'] + delay))
    class RateLimitError(Exception):
        status_code = 429
    with bb.Fixture('sufficient_dom') as fixture:
        monkeypatch.setattr(agent_browser, 'Session', lambda *a, **k: FixtureSession(fixture))
        with bb.local_workspace(fixture) as space, journal.run('browser-benchmark', 'bench'):
            def respond(provider, request):
                if len(transport.calls) == 1:
                    return RateLimitError('429 rate limit')
                if len(transport.calls) == 2:
                    return json.dumps({'tool': 'browser_navigate', 'args': {'url': fixture.start_url}}), Usage()
                return 'not json', Usage()
            transport.handler = respond
            with pytest.raises(llm.InvalidOutput):
                bb.trajectory(mid, fixture, space, request_budget=bb.RequestBudget(8), log=lambda line: None)
    assert clock['elapsed'] == 30 and len(transport.calls) == 4


def test_multimodal_escalation_delivers_real_png_and_keeps_capture_history(monkeypatch,transport,providers_up):
    qualify('openrouter/fixture/vision-alpha:free',fail=('sufficient_dom',))
    qualify('deepseek/flash')
    qualify('openrouter/fixture/text-gamma:free')  # Synthetic proof; capabilities still forbid this text-only model after image.
    with bb.Fixture('vision') as fixture:
        session=FixtureSession(fixture)
        monkeypatch.setattr(agent_browser,'Session',lambda *a,**k:session)
        with bb.local_workspace(fixture) as space:
            monkeypatch.setattr(bw,'workspace',lambda:space)
            monkeypatch.setattr(bw,'current_scope',lambda:space.scope)
            n={'value':0}
            def decide(provider,request):
                n['value']+=1
                if n['value']==1: action={'tool':'browser_navigate','args':{'url':fixture.origin+fixture.routes['done']}}
                elif request['model']!='deepseek-flash': action={'tool':'browser_screenshot','args':{}}
                else:
                    images=[part for m in request['messages'] if isinstance(m['content'],list) for part in m['content'] if part['type']=='image_url']
                    assert len(images)>=3
                    for image in images:
                        with Image.open(io.BytesIO(base64.b64decode(image['image_url']['url'].split(',')[1]))) as im: assert im.size==(800,180)
                    text=json.dumps(request['messages'])
                    hashes=re.findall(r'\\"sha256\\": \\"([a-f0-9]{64})',text)
                    # Read a scoped capture hash from the model-visible header, never raw image bytes in final.
                    if not hashes:
                        hashes=re.findall(r'"sha256": "([a-f0-9]{64})',text.replace('\\"','"'))
                    assert hashes
                    action={'final':'Visual interpretation', 'objective_status':'completed','missing':[],
                            'evidence':[{'step':2,'image_sha256':hashes[0]}]}
                return json.dumps(action),Usage(prompt_tokens=10,completion_tokens=5)
            transport.handler=decide
            with journal.run('browser-benchmark','agent',profile='economical'):
                result=runtime.run_agent('SOUT','Observe image evidence',max_steps=8,allowed_tools={'browser_navigate','browser_screenshot'})
            assert result['execution_status']=='completed',result
            last=transport.calls[-1][1]
            assert last['model']=='deepseek-flash'
            assert result['steps'][-1]['browser_controller']['escalation']
            assert all('base64' not in (r['data'] or '') for r in journal.query("SELECT * FROM events WHERE type='browser.controller'"))
            assert all('base64' not in (r['output_preview'] or '') for r in journal.query('SELECT * FROM llm_calls'))
