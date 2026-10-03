"""Synthetic evidence in isolated test journals, never a live qualification result."""
import json
import time
from octopus import catalog, journal, llm


def prove(task, mid, *, passed=5, total=5, age_days=0):
    model = catalog.load().model(mid)
    identity = model.get('evidence_identity', mid) if model else mid
    with journal.run('test', 'bench') as ctx:
        for i in range(total):
            journal.record_bench_result({'ts': time.time() - age_days * 86400,
                'bench_run_id': ctx.id, 'suite': 'offline-fixtures', 'task': task, 'item': str(i),
                'model': identity, 'passed': int(i < passed), 'score': 1. if i < passed else 0.})


def qualify(mid, *, fail=(), age=0, run_id=None, identity=None):
    cat = catalog.load()
    identity = identity or llm.browser_identity(cat.model(mid))
    with journal.run('test', 'bench', profile='bench') as ctx:
        for scenario in llm.BROWSER_SCENARIOS:
            journal.record_bench_result({'ts': time.time() - age, 'bench_run_id': run_id or ctx.id,
                'suite': 'test-fixtures', 'task': llm.BROWSER_BENCH_TASK, 'item': scenario,
                'model': identity, 'prompt_version': llm.BROWSER_BENCH_VERSION,
                'passed': int(scenario not in fail), 'score': 0. if scenario in fail else 1.,
                'checks': json.dumps({'forbidden': False})})
    return identity


def adapt_decider(decider):
    """Upgrade old simulated deciders to explicit browser handoff/final contract.

    Preserve the first intent when runtime asks a qualified controller before dispatch.
    This is a mock adapter, not a bypass of gateway qualification in production.
    """
    pending = []
    def adapted(agent, stage, model, messages, **kwargs):
        browser = kwargs.get('cognitive_task') == 'browser.react_step'
        if browser and pending:
            return pending.pop()
        value = decider(agent, stage, model, messages, **kwargs)
        if stage == 'action' and not browser and str(value.get('tool', '')).startswith('browser_'):
            pending.append(value)
        if stage == 'action' and browser and 'final' in value and 'objective_status' not in value:
            acquired = []
            count = 0
            for message in messages:
                content = message.get('content')
                if message.get('role') == 'assistant':
                    try:
                        if json.loads(content).get('tool'): count += 1
                    except (TypeError, ValueError): pass
                if isinstance(content, list):
                    content = next((p.get('text', '') for p in content if p.get('type') == 'text'), '')
                if isinstance(content, str) and 'PAGE (refs @eN) :\n' in content:
                    quote = content.split('PAGE (refs @eN) :\n', 1)[1].strip()[:100]
                    if quote: acquired.append({'step':count,'quote':quote})
            value = {**value,'objective_status':'completed' if acquired else 'incomplete',
                     'missing':[] if acquired else ['observation unavailable'], 'evidence':acquired[-1:]}
        return value
    return adapted
