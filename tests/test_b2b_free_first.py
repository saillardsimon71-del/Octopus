import json
import time

import pytest

from agents import runtime
from octopus import catalog, journal, llm, mandates, resources, supervisor, tasks
from octopus.pricing import Usage


def test_generic_health_is_reused_across_families(transport, providers_up):
    journal.record_llm_call({'ts': time.time(), 'business': 'b2b', 'task': 'agent.plan',
        'model': 'openrouter/fixture/text-gamma:free', 'provider': 'openrouter',
        'profile': 'economical', 'cost_class': 'free_quota', 'status': 'ok', 'cost_usd': 0, 'duration_ms': 10})
    transport.reply('{"summary":"ok"}')
    result = llm.complete('agent.synthesize', [{'role': 'user', 'content': 'Summarize.'}], profile='economical')
    assert result.model == 'openrouter/fixture/text-gamma:free' and result.cost_usd == 0


@pytest.mark.parametrize('task', ['agent.plan', 'agent.synthesize', 'agent.decision', 'web.inspect_page', 'agent.react_step'])
@pytest.mark.parametrize('profile', ['economical', 'zero_cost'])
def test_generic_free_route_without_benchmark(task, profile, transport, providers_up):
    transport.handler = lambda provider, request: llm.TransportResult(
        '{"ok":true}', Usage(prompt_tokens=10, completion_tokens=5), request['model'],
        request['model'], 'fixture-host', provider_cost_usd=0.)
    result = llm.complete(task, [{'role': 'user', 'content': 'Return JSON.'}], profile=profile, json_mode=True)
    assert result.provider == 'openrouter' and result.cost_usd == 0
    assert not journal.query('SELECT * FROM bench_results')


def test_free_tool_route_requires_tools_without_benchmark(transport, providers_up):
    tool = {'type': 'function', 'function': {'name': 'read', 'parameters': {'type': 'object'}}}
    transport.reply('{"ok":true}')
    result = llm.complete('development.step', [{'role': 'user', 'content': 'Use the tool.'}],
                         profile='economical', json_schema={'type': 'object'}, tool_schemas=[tool])
    assert result.model == 'openrouter/fixture/text-gamma:free'
    assert transport.models == ['fixture/text-gamma:free']
    assert transport.calls[0][1]['tools'] == [tool]
    assert not journal.query('SELECT * FROM bench_results')


def test_economical_failure_stops_before_paid_call(transport, providers_up):
    transport.handler = lambda provider, request: RuntimeError('fixture endpoint unavailable')
    with journal.run('b2b', 'fixture', profile='economical', budget_usd=0):
        with pytest.raises(llm.NoEligibleModel, match='payant') as error:
            llm.complete('agent.plan', [{'role': 'user', 'content': 'plan'}], profile='economical')
    assert error.value.human_required
    assert transport.models and all(m.endswith(':free') for m in transport.models)
    assert len(transport.calls) <= 3


def test_economical_rejects_paid_even_if_candidates_are_misconfigured(monkeypatch, transport, providers_up):
    cat = catalog.load()
    cat.raw['profiles']['economical']['allowed_cost_classes'].append('paid')
    cat.task('agent.plan')['candidates']['economical'] = ['deepseek/flash']
    monkeypatch.setattr(catalog, 'load', lambda: cat)
    with journal.run('b2b', 'fixture', profile='economical', budget_usd=0):
        with pytest.raises(llm.NoEligibleModel, match='payant') as error:
            llm.complete('agent.plan', [{'role': 'user', 'content': 'plan'}], profile='economical')
    assert error.value.human_required
    assert not transport.calls


def test_economical_no_free_route_reports_human_boundary(monkeypatch, transport, providers_up):
    monkeypatch.setattr(catalog, '_fetch_openrouter', lambda key: {'data': []})
    result = runtime.run_mission('fixture', business='b2b', profile='economical',
                                 allowed_tools={'search'}, max_steps_per_agent=1)
    assert result['execution_status'] == 'human_required'
    assert 'payant' in result['synthesis_error']
    assert not transport.calls


def test_connected_account_needs_each_effect_and_active_mandate():
    resources.configure_account('account', actor='human', provider='Unknown', label='Compte',
                                url='https://account.example/', domains=['account.example'], businesses=['b2b'])
    resources.set_account_session('account', 'connected')
    assert not mandates.account_authority('b2b', 'account', 'publish')
    mid = mandates.grant('b2b', 'Lecture', 'owned_account', ['read'], actor='human', resource_keys=['account'])
    assert mandates.account_authority('b2b', 'account', 'read')['id'] == mid
    assert not mandates.account_authority('b2b', 'account', 'publish')
    assert not mandates.account_authority('other', 'account', 'read')
    mandates.revoke('b2b', mid, actor='human')
    assert not mandates.account_authority('b2b', 'account', 'read')


def test_public_mandate_does_not_cover_other_effects():
    mandates.grant('b2b', 'Contact', 'public_business', ['contact'], actor='human')
    assert mandates.covering('b2b', 'public_business', 'contact')
    assert not mandates.covering('b2b', 'public_business', 'publish')


def test_account_inventory_hides_other_activity():
    resources.configure_account('old', actor='human', provider='Old', label='Old',
                                url='https://old.example/', domains=['old.example'], businesses=['other'])
    with journal.run('b2b', 'fixture'):
        result = runtime._resources_status({})
    assert not result['resources']


def test_skills_are_metadata_and_preserve_authority():
    before_tools = set(runtime.TOOLS)
    with journal.run('b2b', 'fixture'):
        result = runtime._resources_status({})
        prompt, _, _ = runtime.build_prompts('ORBIT', 'fixture', allowed_tools={'search', 'resources_status'})
    cards = result['skills']
    assert len(cards) >= 18
    assert all(c['prerequisites'] and c['outputs'] and c['evidence'] for c in cards)
    assert 'youtube_research' in prompt
    assert set(runtime.TOOLS) == before_tools
    assert not mandates.list_mandates('b2b')

def test_youtube_learning_is_read_only_deduplicated_and_provenanced(monkeypatch):
    monkeypatch.setattr(runtime, '_seen_this_session', lambda url: True)
    observation = json.dumps({'title': 'Fixture', 'channel': 'Fixture channel', 'date': '2026-01-01',
                              'summary': 'Hypothèse à tester, aucun résultat commercial prouvé.'})
    with journal.run('b2b', 'fixture'):
        first = runtime._record_observation({'source_type': 'external_learning',
            'source_ref': 'https://youtu.be/abcdefghijk', 'observation': observation})
        second = runtime._record_observation({'source_type': 'external_learning',
            'source_ref': 'https://www.youtube.com/watch?v=abcdefghijk', 'observation': observation})
    assert first['evidence_id'] == second['evidence_id']
    row = journal.query('SELECT * FROM strategy_evidence WHERE id=?', (first['evidence_id'],))[0]
    assert row['source_ref'] == 'https://www.youtube.com/watch?v=abcdefghijk'
    assert row['captured_at'] and row['nature'] == 'observed'
    assert not journal.query('SELECT * FROM channel_actions')
    assert not mandates.list_mandates('b2b')
