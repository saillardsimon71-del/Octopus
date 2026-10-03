"""Bounded computer-use trajectories on hermetic local pages; no model scripted UI paths.

Uses the production Workspace, tool schemas, multimodal loader, gateway and bench_results.
Code controls fixture truth/scoring only. Run explicitly; never bench a real account.
"""
from __future__ import annotations

import html
import json
import math
import os
import secrets
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import bench, browser_workspace as bw, catalog, economy, journal, llm, paths
from agents import agent_browser, web_guard

MAX_STEPS = 12


class RequestBudget:
    def __init__(self, maximum):
        self.maximum, self.used = maximum, 0

    def reserve(self):
        if self.used >= self.maximum:
            raise llm.RequestLimitExceeded('browser benchmark request ceiling reached')
        self.used += 1


class Fixture:
    def __init__(self, scenario):
        if scenario not in llm.BROWSER_SCENARIOS:
            raise ValueError('unknown browser scenario')
        self.scenario = scenario
        self.token = secrets.token_hex(4).upper()
        self.routes = {k: '/' + secrets.token_hex(5) for k in ('home', 'manage', 'orders', 'performance', 'done', 'wrong')}
        self.hits = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path not in fixture.routes.values():
                    self.send_error(404)
                    return
                fixture.hits.append(self.path)
                body = fixture.render(self.path).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def origin(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}'

    @property
    def start_url(self):
        key = 'wrong' if self.scenario == 'recovery' else 'home'
        return self.origin + self.routes[key]

    def render(self, path):
        route = next(k for k, v in self.routes.items() if v == path)
        fr = self.scenario == 'language_layout'
        names = {'home': 'Accueil' if fr else 'Home', 'manage': 'Mon espace professionnel' if fr else 'Account management',
                 'orders': 'Commandes' if fr else 'Orders', 'performance': 'Indicateurs' if fr else 'Performance',
                 'done': 'État opérationnel' if fr else 'Operational state', 'wrong': 'Catalogue produits' if fr else 'Product catalogue'}
        def link(key):
            return f'<a href="{self.routes[key]}">{names[key]}</a>'
        head = f'<!doctype html><meta charset=utf-8><title>{names[route]}</title>'
        if route == 'wrong':
            body = '<h1>' + names[route] + '</h1><p>This section only lists products, no account information.</p>' + link('home')
        elif route == 'done':
            if self.scenario == 'vision':
                # Essential information never appears as text, accessible name or href.
                body = '<h1>Operational state</h1><canvas id=c width=700 height=170 aria-label="Operational summary image"></canvas>' + self._canvas('Account evidence code: ' + self.token)
            else:
                body = '<h1>' + names[route] + '</h1><p>Account evidence code: ' + self.token + '</p>'
            body += link('home')
        elif route in ('manage', 'orders', 'performance'):
            next_key = {'manage': 'orders', 'orders': 'performance', 'performance': 'done'}[route] if self.scenario == 'multi_screen' else 'done'
            body = '<h1>' + names[route] + '</h1>' + link(next_key) + link('home')
        else:
            search = '<label>Find products <input type=search></label><button type=button>Search products</button>'
            if self.scenario == 'ambiguous_dom':
                # Same accessible labels. The canvas tells which visible control opens account.
                body = '<canvas id=c width=800 height=180></canvas>' + self._canvas('LEFT: products          RIGHT: account operations')
                body += ''.join(f'<button aria-label="Open section" style="margin:20px 90px" onclick="location.href=\'{self.routes[k]}\'">→</button>' for k in ('wrong', 'manage'))
            elif self.scenario in ('dynamic_menu', 'affordance', 'language_layout'):
                avatar = 'Camille · compte' if fr else 'Camille · user menu'
                body = (f'<button type=button aria-expanded=false aria-haspopup=menu onclick="let m=document.getElementById(\'menu\');m.hidden=!m.hidden;this.setAttribute(\'aria-expanded\',!m.hidden)">{avatar}</button>'
                        f'<nav id=menu hidden>{link("manage")}{link("wrong")}</nav>{search}')
                if self.scenario == 'affordance':
                    body += link('manage')  # An alternative direct observed path is equally valid.
            else:
                body = search + link('wrong') + link('manage')
            if fr:
                body = '<aside style="float:right;width:70%">' + body + '</aside>'
        return head + '<body style="font:22px sans-serif">' + body + '</body>'

    def _canvas(self, text):
        return '<script>const ctx=document.getElementById("c").getContext("2d");ctx.fillStyle="#fff";ctx.fillRect(0,0,800,180);ctx.fillStyle="#111";ctx.font="28px sans-serif";ctx.fillText(' + json.dumps(text) + ',15,75)</script>'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()


@contextmanager
def local_workspace(fixture):
    previous = os.environ.get(bw.LAB_ORIGINS_ENV)
    os.environ[bw.LAB_ORIGINS_ENV] = fixture.origin
    business = 'browser-benchmark'
    # Fixture-only act channel, existing authorization path. No account/profile/mandate attached.
    cid = economy.add_channel(business, 'website', 'Hermetic computer-use fixture', created_by='human',
        locator=fixture.origin + '/', capabilities=[bw.CAPABILITY], access='act', nature='observed', source_ref=fixture.origin)
    economy.update_channel(business, cid, actor='human', status='active')
    space = bw.Workspace(bw.Scope('bench-' + secrets.token_hex(8), business, None), web_guard.BrowseState())
    try:
        yield space
    finally:
        space.close()
        economy.update_channel(business, cid, actor='human', status='suspended')
        if previous is None:
            os.environ.pop(bw.LAB_ORIGINS_ENV, None)
        else:
            os.environ[bw.LAB_ORIGINS_ENV] = previous


def trajectory(model_id, fixture, space, *, decide=None, request_budget=None):
    """Every next action is model-authored; tests may supply a fake, never a quality claim."""
    from agents.runtime import TOOLS, _tool_result_view
    from agents.tool_registry import ToolRegistry
    allowed = {'browser_navigate', 'browser_snapshot', 'browser_click', 'browser_scroll', 'browser_back', 'browser_screenshot'}
    registry = ToolRegistry({name: {**TOOLS[name], 'fn': lambda args, name=name: bw.call_on(space, name[8:], **args)} for name in allowed})
    descriptions = registry.describe(allowed, browser_details=True)
    messages = [{'role': 'system', 'content': 'Observe this local interface and choose your own actions. '
        'Do not invent internal routes or evidence. Return JSON {"tool":"name","args":{...}} or {"final":"report"}. '
        'Report the account evidence code once you actually obtain it. Use screenshots when useful.\n' + descriptions},
        {'role': 'user', 'content': 'Reach the operational state of the account and report its evidence code. Start at ' + fixture.start_url}]
    monitor = bw.BrowserTrajectory()
    records, identities, call_ids = [], set(), []
    final = ''
    injection_done = False
    screenshot_used = False
    recovered = fixture.scenario not in ('stale_refs', 'invalid_args', 'recovery')
    for i in range(MAX_STEPS):
        if decide is None:
            c = llm.complete('browser.bench_step', messages, agent='BROWSER_BENCH', business='browser-benchmark',
                pin_model=model_id, profile='bench', json_mode=True, max_tokens=1200, validate=llm.parse_json,
                before_request=request_budget.reserve if request_budget else None)
            action = c.data
            identity = None
            if c.resolved_model:
                identity = ('browser.model:' + c.justification['evidence_identity'] if c.provider == 'openrouter'
                            else llm.browser_identity(catalog.load().model(model_id), c.resolved_model))
            identities.add(identity)
            call_ids.append(c.call_id)
        else:
            action = decide(messages)
        if 'final' in action:
            final = str(action['final'])
            break
        tool, args = action.get('tool'), action.get('args', {})
        if tool not in allowed:
            result = {'ok': False, 'refused': True, 'reason': 'forbidden tool'}
        elif fixture.scenario == 'stale_refs' and tool == 'browser_click' and not injection_done:
            injection_done = True
            space._refs.clear()
            result = bw.call_on(space, 'click', **args)
        elif fixture.scenario == 'invalid_args' and tool == 'browser_click' and not injection_done:
            injection_done = True
            result = {'ok': False, 'refused': True, 'error_code': 'invalid_tool_arguments',
                      'reason': 'invalid_tool_arguments: fixture rejected arguments; ref must match @eN; effect contact|publish|edit|omitted. Reobserve or correct.'}
        else:
            reason, result = registry.dispatch(tool, args, allowed)
            if reason:
                result = {'ok': False, 'refused': True, 'error_code': 'invalid_tool_arguments', 'reason': reason}
        progress = monitor.record(tool, args, result)
        if injection_done and progress['progress'] and result.get('ok'):
            recovered = True
        if fixture.scenario == 'recovery' and result.get('url') != fixture.start_url and progress['progress']:
            recovered = True
        messages.append({'role': 'assistant', 'content': json.dumps(action, ensure_ascii=False)})
        content = _tool_result_view(str(tool), result)
        if isinstance(result, dict) and result.get('image'):
            part = bw.image_part(result['image'], business=space.scope.business, scope_key=space.scope.key)
            content = [{'type': 'text', 'text': content}, part]
            screenshot_used = True  # The next model request receives the actual PNG.
        messages.append({'role': 'user', 'content': content})
        records.append({'tool': tool, 'args': bw.trace_args(args), 'result': result, **progress})
        if monitor.no_progress >= 5:
            break
    # DOM truth is observed separately from the model's report, never an exact-path score.
    attained = space._current_url() == fixture.origin + fixture.routes['done']
    observed = any(fixture.token in str(row['result'].get('snapshot', '')) for row in records)
    visual = fixture.scenario in ('vision', 'ambiguous_dom')
    evidence = fixture.token in final and (observed or visual and screenshot_used)
    forbidden = any(row['result'].get('reason') == 'forbidden tool' for row in records)
    valid = sum(row['tool_ok'] for row in records) / max(1, len(records))
    complete = attained and evidence and recovered and not forbidden and (not visual or screenshot_used)
    score = (.6 * (attained and evidence) + .15 * valid + .1 * recovered + .1 * (len(records) <= 9) + .05 * (not visual or screenshot_used))
    return {'passed': int(complete and score >= .9), 'score': score, 'checks': {'goal': attained,
        'evidence': evidence, 'recovery': recovered, 'tool_valid_rate': valid, 'forbidden': forbidden,
        'vision': screenshot_used, 'steps': len(records)}, 'identities': identities, 'call_ids': call_ids,
        'trace': records, 'final': final}


def run(models, *, repeats=2, allow_paid=False, max_cost_usd=.05, max_requests=240, out_dir=None, log=print):
    if not math.isfinite(max_cost_usd) or max_cost_usd < 0:
        raise ValueError('max_cost_usd must be finite and nonnegative')
    if repeats < 1 or repeats > 5:
        raise ValueError('repeats: 1..5')
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
        raise ValueError('max_requests must be a positive integer')
    if not models or len(models) != len(set(models)):
        raise ValueError('explicit nonempty shortlist without duplicates required')
    cat = catalog.load()
    rows, skipped = [], {}
    runnable = []
    for mid in models:
        model = cat.model(mid)
        if not model:
            skipped[mid] = 'unknown model'
        elif model['cost_class'] == 'paid' and not allow_paid:
            skipped[mid] = 'paid evaluation requires --allow-paid'
        else:
            runnable.append(mid)
    preflight = {'models': runnable, 'scenarios': len(llm.BROWSER_SCENARIOS), 'repeats': repeats,
        'trajectories': len(runnable) * len(llm.BROWSER_SCENARIOS) * repeats,
        'step_requests_upper_bound': len(runnable) * len(llm.BROWSER_SCENARIOS) * repeats * MAX_STEPS,
        'structured_requests_upper_bound': sum(len(llm._structured_methods(cat.model(mid), True, None, None))
            for mid in runnable) * len(llm.BROWSER_SCENARIOS) * repeats * MAX_STEPS,
        'max_requests': max_requests, 'max_cost_usd': max_cost_usd}
    log('Preflight: ' + json.dumps(preflight))
    agent_browser.require_backend()
    budget = RequestBudget(max_requests)
    stopped = False
    with journal.run('browser-benchmark', 'bench', label='browser-v1', budget_usd=max_cost_usd, profile='bench') as ctx:
        for mid in runnable:
            if stopped:
                break
            for scenario in sorted(llm.BROWSER_SCENARIOS):
                if stopped:
                    break
                for rep in range(repeats):
                    started = time.monotonic()
                    first = journal.query('SELECT COALESCE(MAX(id),0) id FROM llm_calls')[0]['id']
                    outcome, error = None, None
                    try:
                        with Fixture(scenario) as fixture, local_workspace(fixture) as space:
                            outcome = trajectory(mid, fixture, space, request_budget=budget)
                    except llm.RequestLimitExceeded:
                        stopped, error = True, 'request ceiling reached; benchmark incomplete'
                    except llm.BudgetExceeded:
                        stopped, error = True, 'cost ceiling reached; benchmark incomplete'
                    except Exception as exc:
                        error = type(exc).__name__  # No provider echo or image bytes in benchmark error.
                    calls = journal.query('SELECT * FROM llm_calls WHERE id>? AND run_id=?', (first, ctx.id))
                    identities = outcome['identities'] if outcome else set()
                    identity = next(iter(identities)) if len(identities) == 1 and None not in identities else None
                    if not outcome:
                        identity = llm.browser_identity(cat.model(mid))
                    passed = bool(outcome and outcome['passed'] and identity)
                    row = {'ts': time.time(), 'bench_run_id': ctx.id, 'suite': 'octopus.browser_bench',
                        'task': llm.BROWSER_BENCH_TASK, 'item': scenario, 'model': identity or 'browser.unresolved:' + mid,
                        'repeat': rep, 'prompt_version': llm.BROWSER_BENCH_VERSION, 'passed': int(passed),
                        'score': outcome['score'] if outcome and identity else 0., 'value': None,
                        'checks': json.dumps({**(outcome['checks'] if outcome else {}), 'requested_model': mid,
                            'benchmark_incomplete': stopped, 'calls': len(calls),
                            'prompt_tokens': sum(c['prompt_tokens'] or 0 for c in calls),
                            'completion_tokens': sum(c['completion_tokens'] or 0 for c in calls),
                            'trajectory': [{k: v for k, v in st.items() if k != 'result'} for st in outcome['trace']] if outcome else [],
                            'resolved': [{'model': c['resolved_model'], 'provider': c['resolved_provider']} for c in calls]}, ensure_ascii=False),
                        'latency_ms': int((time.monotonic() - started) * 1000),
                        'cost_usd': sum(c['cost_usd'] for c in calls), 'llm_call_id': calls[-1]['id'] if calls else None,
                        'error': error or (None if identity else 'resolved identity missing or changed'),
                        'output_preview': 'trajectory completed' if passed else 'trajectory incomplete'}
                    journal.record_bench_result(row)
                    rows.append(row)
                    log(f'{mid} {scenario} {rep}: {"PASS" if passed else "FAIL"} ; {len(calls)} calls ; {row["cost_usd"]:.6f} USD')
                    if stopped:
                        break
        run_id = ctx.id
    # Existing CSV output helper; identity keys are deliberately not virtual catalog aliases.
    files = bench.write_outputs([], rows, Path(out_dir) if out_dir else paths.data_dir() / 'bench' / str(run_id))
    summaries = []
    for identity in sorted(set(r['model'] for r in rows)):
        group = [r for r in rows if r['model'] == identity]
        completed = sum(r['passed'] for r in group)
        cost = sum(r['cost_usd'] for r in group)
        summaries.append({'identity': identity, 'trajectories': len(group), 'completed': completed,
            'cost_usd': cost, 'cost_per_completed_objective_usd': cost / completed if completed else None})
    return {'bench_run_id': run_id, 'rows': rows, 'summaries': summaries, 'skipped': skipped, 'files': files,
        'preflight': preflight, 'requests_used': budget.used, 'incomplete': stopped}
