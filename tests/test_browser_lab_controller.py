import json

import pytest

from agents import runtime
from browser_lab import ObservationDecider


@pytest.mark.parametrize('resumed', [False, True])
@pytest.mark.parametrize('flat', [False, True])
def test_simulated_controller_reads_runtime_observations_and_recovery(resumed, flat):
    result = {'ok': True, 'url': 'http://127.0.0.1:8123/',
              'snapshot': '- textbox "Société" [ref=e1]'}
    action = {'tool': 'browser_navigate', **({'url': result['url']} if flat else {'args': {'url': result['url']}})}
    prefix = ('Observation déjà acquise, ne pas répéter : ' if resumed else 'Résultat de browser_navigate (step=1) : ')
    messages = [{'role': 'assistant', 'content': json.dumps(action)},
                {'role': 'user', 'content': prefix + runtime._tool_result_view('browser_navigate', result)}]
    controller = ObservationDecider(None, {'société': 'Nova'})
    assert controller.decide(messages) == {'tool': 'browser_type', 'args': {'ref': '@e1', 'text': 'Nova'}}


def test_simulated_controller_recognizes_completed_flat_edit():
    page = {'ok': True, 'snapshot': '- textbox "Société" [ref=e1]\n- button "Continuer" [ref=e2]'}
    messages = [{'role': 'assistant', 'content': json.dumps({'tool': 'browser_snapshot'})},
                {'role': 'user', 'content': 'Résultat de browser_snapshot (step=1) : ' + runtime._tool_result_view('browser_snapshot', page)},
                {'role': 'assistant', 'content': json.dumps({'tool': 'browser_type', 'ref': '@e1', 'text': 'Nova'})},
                {'role': 'user', 'content': 'Résultat de browser_type (step=2) : ' + runtime._tool_result_view('browser_type', page)}]
    controller = ObservationDecider(None, {'société': 'Nova'})
    assert controller.decide(messages) == {'tool': 'browser_click', 'args': {'ref': '@e2'}}


def test_simulated_controller_reobserves_stale_ref_instead_of_repeating_it():
    page = {'ok': True, 'snapshot': '- button "Confirmer la demande" [ref=e1]'}
    error = {'ok': False, 'refused': True, 'reason': 'ref @e1 absente du dernier snapshot : la page a changé, observe-la à nouveau'}
    messages = [{'role': 'assistant', 'content': json.dumps({'tool': 'browser_snapshot'})},
                {'role': 'user', 'content': 'Résultat de browser_snapshot (step=1) : ' + runtime._tool_result_view('browser_snapshot', page)},
                {'role': 'assistant', 'content': json.dumps({'tool': 'browser_click', 'ref': '@e1'})},
                {'role': 'user', 'content': 'Résultat de browser_click (step=2) : ' + runtime._tool_result_view('browser_click', error)}]
    assert ObservationDecider(None, {}).decide(messages) == {'tool': 'browser_snapshot', 'args': {}}
