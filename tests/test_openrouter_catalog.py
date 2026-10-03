import json
import time
from types import SimpleNamespace

import pytest

from octopus import catalog, journal, llm, paths
from octopus.__main__ import cmd_models
from octopus.pricing import Usage
from browser_evidence import prove, qualify

MID = 'openrouter/vendor/arbitrary:free'


def entry(**changes):
    value = {'id': 'vendor/arbitrary:free', 'canonical_slug': 'vendor/arbitrary-20261003',
        'name': 'Unknown model', 'pricing': {'prompt': '0', 'completion': '0'},
        'context_length': 8192, 'architecture': {'input_modalities': ['text', 'image'],
                                               'output_modalities': ['text']},
        'supported_parameters': ['tools', 'response_format', 'structured_outputs', 'reasoning']}
    value.update(changes)
    return value


def install(monkeypatch, *entries):
    monkeypatch.setattr(catalog, '_fetch_openrouter', lambda key: {'data': list(entries)})
    return catalog.load(refresh=True)


def test_metadata_and_identity_are_observed(monkeypatch):
    model = install(monkeypatch, entry()).model(MID)
    assert set(model['capabilities']) == {'vision', 'tools', 'json', 'reasoning'}
    assert model['context_length'] == 8192
    assert model['source'] == catalog.OPENROUTER_SOURCE and model['fetched_at'] <= time.time()
    assert model['evidence_identity'] == MID + '@vendor/arbitrary-20261003'


@pytest.mark.parametrize('task', ['browser.bench_step', 'browser.react_step'])
@pytest.mark.parametrize('efforts,want', [(['xhigh', 'medium', 'low'], 'low'), (['high'], None), (None, 'low')])
def test_browser_reasoning_uses_supported_low_effort(task, efforts, want, monkeypatch, transport, providers_up):
    install(monkeypatch, entry(reasoning={'mandatory': False, 'default_enabled': True,
                                         'supported_efforts': efforts, 'default_effort': 'xhigh'}))
    qualify(MID)
    model = catalog.load().model(MID)  # Exercise the persisted catalog as well.
    assert model['reasoning']['supported_efforts'] == efforts
    transport.reply('{"final":"observed"}')
    result = llm.complete(task, [{'role': 'user', 'content': 'Observe'}],
                          profile='bench', pin_model=MID, json_mode=True, validate=llm.parse_json)
    sent = transport.calls[-1][1]['extra_body'].get('reasoning', {}).get('effort')
    assert sent == want
    assert result.justification.get('reasoning') == ({'effort': want} if want else None)


def test_nonbrowser_reasoning_defaults_are_preserved(monkeypatch, transport, providers_up):
    install(monkeypatch, entry(reasoning={'supported_efforts': ['high', 'low']}))
    transport.reply()
    llm.complete('octopus.json', [], profile='bench', pin_model=MID, json_mode=True, validate=llm.parse_json)
    assert 'reasoning' not in transport.calls[-1][1]['extra_body']


@pytest.mark.parametrize('field,value', [
    ('pricing', {'prompt': '0.01', 'completion': '0'}),
    ('pricing', {'prompt': '0', 'completion': '0', 'request': '0.001'}),
    ('pricing', {'prompt': '0', 'completion': '0', 'image': '0.1'}),
    ('pricing', {'prompt': '0', 'completion': '0', 'internal_reasoning': '0.01'}),
    ('pricing', {'prompt': '0', 'completion': '0', 'unknown_fee': None}),
    ('pricing', {'prompt': 'NaN', 'completion': '0'}),
    ('pricing', {'prompt': '-1', 'completion': '0'}),
    ('pricing', {'prompt': True, 'completion': '0'}),
    ('pricing', {'prompt': 'Infinity', 'completion': '0'}),
    ('architecture', {}), ('supported_parameters', None),
    ('canonical_slug', None), ('context_length', 0), ('context_length', True),
    ('id', 'openrouter/free'), ('id', 'openrouter/auto'), ('id', 'unscoped'),
])
def test_unknown_or_nonzero_metadata_is_not_runnable(field, value):
    paid = entry(id='vendor/paid', pricing={'prompt': '1', 'completion': '1'})
    assert not catalog.normalize_openrouter({'data': [entry(**{field: value}), paid]}, 100.)


def test_missing_required_prices_reject_refresh():
    with pytest.raises(catalog.CatalogError):
        catalog.normalize_openrouter({'data': [entry(pricing={'prompt': '0'})]}, 100.)


def test_zero_priced_fixed_model_does_not_need_free_suffix():
    models = catalog.normalize_openrouter({'data': [entry(id='vendor/fixed')]}, 100.)
    assert models['openrouter/vendor/fixed']['free_verified']


def test_capabilities_do_not_follow_a_familiar_name():
    model = next(iter(catalog.normalize_openrouter({'data': [entry(name='Vision GPT Tools',
        architecture={'input_modalities': ['text'], 'output_modalities': ['text']},
        supported_parameters=[])]}, 100.).values()))
    assert model['capabilities'] == [] and model['structured_methods'] == ['text']


def test_duplicates_are_deterministic_and_conflicts_fail_closed():
    original = entry()
    assert len(catalog.normalize_openrouter({'data': [original, original]}, 100.)) == 1
    assert not catalog.normalize_openrouter({'data': [original, entry(canonical_slug='vendor/changed')]}, 100.)


def test_fresh_cache_survives_memory_clear_without_network(monkeypatch):
    install(monkeypatch, entry())
    catalog._cache.clear()
    monkeypatch.setattr(catalog, '_fetch_openrouter', lambda key: pytest.fail('fresh cache must not fetch'))
    assert catalog.load().model(MID)
    assert catalog.load().raw['openrouter_catalog']['state'] == 'fresh'
    saved = (paths.data_dir() / 'openrouter-models.json').read_text(encoding='utf-8')
    assert 'offline-fixture' not in saved and 'Authorization' not in saved
    assert not list(paths.data_dir().glob('*.tmp'))


@pytest.mark.parametrize('fault', ['malformed', 'timeout', 'write'])
def test_refresh_failure_keeps_healthy_cache_and_redacts(monkeypatch, fault):
    install(monkeypatch, entry())
    saved = (paths.data_dir() / 'openrouter-models.json').read_bytes()
    def fail(*args):
        raise TimeoutError('Authorization: Bearer offline-fixture')
    if fault == 'malformed':
        monkeypatch.setattr(catalog, '_fetch_openrouter', lambda key: {'data': [{'invalid': True}]})
    elif fault == 'write':
        monkeypatch.setattr(catalog, '_write_discovery', fail)
    else:
        monkeypatch.setattr(catalog, '_fetch_openrouter', fail)
    cat = catalog.load(refresh=True)
    assert cat.model(MID) and cat.raw['openrouter_catalog']['refresh_error']
    assert (paths.data_dir() / 'openrouter-models.json').read_bytes() == saved
    assert 'offline-fixture' not in json.dumps(cat.raw)
    monkeypatch.setattr(catalog, '_fetch_openrouter', lambda key: pytest.fail('refresh backoff'))
    assert catalog.load().model(MID)


def test_stale_cache_is_bounded_and_not_refetched_each_call(monkeypatch):
    install(monkeypatch, entry())
    now = time.time()
    monkeypatch.setattr(catalog.time, 'time', lambda: now + catalog.CATALOG_TTL_S + 1)
    attempts = []
    def fail(key):
        attempts.append(key)
        raise TimeoutError('unavailable')
    monkeypatch.setattr(catalog, '_fetch_openrouter', fail)
    assert catalog.load().raw['openrouter_catalog']['state'] == 'stale'
    assert catalog.load().model(MID) and len(attempts) == 1
    monkeypatch.setattr(catalog.time, 'time', lambda: now + catalog.CATALOG_MAX_AGE_S + 1)
    assert catalog.load().model(MID) is None


def test_explicit_refresh_can_recover_during_automatic_backoff(monkeypatch):
    install(monkeypatch, entry())
    def fail(key):
        raise TimeoutError('offline')
    monkeypatch.setattr(catalog, '_fetch_openrouter', fail)
    assert catalog.load(refresh=True).raw['openrouter_catalog']['refresh_error']
    refreshed = install(monkeypatch, entry(canonical_slug='vendor/recovered'))
    assert refreshed.model(MID)['canonical_slug'] == 'vendor/recovered'
    assert not refreshed.raw['openrouter_catalog']['refresh_error']


@pytest.mark.parametrize('replacement', ['vendor/next', None])
def test_general_bench_keeps_identity_captured_before_catalog_changes(monkeypatch, transport, tmp_path, replacement):
    import sys
    from types import ModuleType
    from octopus import bench
    from octopus.bench import CheckResult, EvalItem, EvalTask

    original = install(monkeypatch, entry()).model(MID)['evidence_identity']
    module = ModuleType('identity_fixture_suite')
    module.build_suite = lambda root: [EvalTask('agent.plan', 'identity', [
        EvalItem('one', [{'role': 'user', 'content': 'x'}], lambda text: CheckResult(True, 1., {}))])]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(bench.time, 'sleep', lambda seconds: None)
    def reply(provider, request):
        install(monkeypatch, *([entry(canonical_slug=replacement)] if replacement else []))
        return '{}', Usage()
    transport.handler = reply
    bench.run_bench(module.__name__, [MID], out_dir=tmp_path, log=lambda line: None)
    assert journal.query('SELECT model FROM bench_results')[0]['model'] == original
    model = catalog.load().model(MID)
    current = model['evidence_identity'] if model else MID
    assert not journal.evidence('agent.plan', current, {**catalog.load().evidence_rules(), 'min_samples': 1})['eligible']


def test_no_key_or_cache_produces_no_openrouter_models(monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY')
    monkeypatch.setattr(catalog, '_fetch_openrouter', lambda key: pytest.fail('no key'))
    cat = catalog.load()
    assert set(cat.raw['models']) == {'deepseek/flash', 'deepseek/v4-pro'}
    assert cat.raw['openrouter_catalog']['state'] == 'unavailable'


def test_corrupted_cache_cannot_activate_stored_free_flag(monkeypatch):
    install(monkeypatch, entry())
    path = paths.data_dir() / 'openrouter-models.json'
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved['models'][MID]['pricing']['image'] = '1'
    path.write_text(json.dumps(saved), encoding='utf-8')
    monkeypatch.delenv('OPENROUTER_API_KEY')
    assert catalog.load().model(MID) is None


def test_new_canonical_identity_cannot_inherit_general_or_browser_proof(monkeypatch, transport, providers_up):
    install(monkeypatch, entry())
    prove('agent.plan', MID)
    qualify(MID)
    assert llm.browser_quality(catalog.load().model(MID))['eligible']
    install(monkeypatch, entry(canonical_slug='vendor/new-version'))
    assert not llm.browser_quality(catalog.load().model(MID))['eligible']
    with pytest.raises(llm.NoEligibleModel):
        llm.complete('agent.plan', [], profile='zero_cost')
    assert not transport.calls


def test_removed_slug_is_not_executable_even_when_pinned(monkeypatch, transport):
    install(monkeypatch, entry())
    install(monkeypatch)
    with pytest.raises(llm.NoEligibleModel):
        llm.complete('agent.plan', [], profile='bench', pin_model=MID)
    assert not transport.calls


@pytest.mark.parametrize('profile', ['legacy', 'bench', 'zero_cost', 'low_cost', 'economical', 'flash_fallback', 'quality_first'])
def test_discovery_never_qualifies_browser_in_any_profile(monkeypatch, transport, providers_up, profile):
    install(monkeypatch, entry())
    prove('agent.react_step', MID)
    with pytest.raises(llm.NoEligibleModel):
        llm.complete('browser.react_step', [], profile=profile, pin_model=MID)
    assert not transport.calls


@pytest.mark.parametrize('cost,resolved,provider', [(None, 'vendor/arbitrary:free', 'fixture'),
    (.01, 'vendor/arbitrary:free', 'fixture'), (float('nan'), 'vendor/arbitrary:free', 'fixture'),
    (0., 'vendor/other:free', 'fixture'), (0., 'openrouter/free', 'fixture'),
    (0., 'vendor/arbitrary:free', None)])
def test_response_must_attest_zero_cost_and_exact_identity(monkeypatch, transport, cost, resolved, provider):
    install(monkeypatch, entry())
    transport.handler = lambda p, r: llm.TransportResult('{}', Usage(), r['model'], resolved, provider,
                                                       provider_cost_usd=cost)
    with pytest.raises(llm.GatewayError):
        llm.complete('agent.plan', [], profile='bench', pin_model=MID)
    assert journal.query('SELECT status FROM llm_calls')[0]['status'] == 'blocked'


def test_retired_ids_remain_history_only(monkeypatch, transport):
    cat = catalog.load()
    assert set(cat.raw['providers']) == {'openrouter', 'deepseek'} and cat.default_profile == 'zero_cost'
    for mid in ['ollama/qwen3.5-4b', 'lmstudio/model', 'groq/model', 'gemini/model',
                'cerebras/model', 'kilo/auto-free', 'omniroute/auto-free']:
        prove('historical', mid)
        assert journal.evidence('historical', mid, cat.evidence_rules())['eligible']
        with pytest.raises(llm.NoEligibleModel):
            llm.complete('agent.plan', [], profile='bench', pin_model=mid)
    assert not transport.calls


def test_cli_marks_technical_candidates_separately(monkeypatch, capsys):
    install(monkeypatch, entry())
    assert cmd_models(SimpleNamespace(refresh=False, browser_candidates=True)) == 0
    output = capsys.readouterr().out
    assert 'TECHNICAL' in output and 'browser=not-benchmarked' in output and MID in output
    assert catalog.OPENROUTER_SOURCE in output and 'age_s=' in output
    assert 'omniroute/' not in output


def test_economical_requires_real_general_proof(monkeypatch, transport, providers_up):
    install(monkeypatch, entry())
    transport.reply('{}')
    result = llm.complete('agent.plan', [], profile='economical')
    assert result.model == 'deepseek/flash'
    prove('octopus.plan', MID)
    result = llm.complete('agent.plan', [], profile='economical')
    assert result.model == MID and result.cost_usd == 0
