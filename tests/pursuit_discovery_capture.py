"""Identical synthetic niche-heavy state, no network/provider; structural measurement only."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
with tempfile.TemporaryDirectory() as data:
    os.environ.update(OCTOPUS_HOME=data, OCTOPUS_DB=str(Path(data)/'fixture.db'), PODALUX_ROOT=data)
    from agents import agent_browser, runtime, task_handlers
    from octopus import supervisor, tasks, strategy, capability_acquisition as acquisition
    from octopus import strategy_separation as separation
    niche = 'API-cost monitoring : offres gratuites et open-source observées ; achat inconnu. '
    conclusion = 'API-cost : recherches publiques répétitives ; information marginale faible, aucune intention d’achat trouvée ; pas de réfutation économique.'
    raw = {'rapport': niche*60, 'reason': conclusion, 'decision':'pause',
           'execution_status':'completed', 'determination':{'action':'pause','reason':conclusion,'next_goal':'','permission':''},
           'results':[{'steps':[{'tool':'browser_navigate','result':niche*20} for _ in range(12)]}]}
    previous={'id':1,'business':'octopus','status':'done','input':{},'output':raw}
    hypotheses=[{'hypothesis_id':i+1,'statement':niche*6,'economic_justification':niche*12,
                 'strategic_state':'retained' if not i else 'candidate','hypothesis_status':'proposed',
                 'economic_rank':i+1,'required_capabilities':['api_cost_monitoring'],'evidence_ids':[]} for i in range(3)]
    decisions=[{'id':i+1,'rationale':conclusion+' '+niche*5,'resulting_action':niche*6,'decision':'pause'} for i in range(5)]
    studies=[{'id':i+1,'strategy':niche*6,'reason':niche*14} for i in range(6)]
    objective={'id':1,'statement':supervisor.FINALITY,'created_by':'octopus'}
    captured={}
    ctx=SimpleNamespace(id=2, owner='fixture', business='octopus', input={'previous_id':1,'round':1,'goal':'Réexaminer l’état','profile':'economical'})
    with patch('octopus.enabled',return_value=True), patch.object(tasks,'get',return_value=previous), \
         patch.object(tasks,'step_value',return_value={}), patch.object(tasks,'save_step'), patch.object(tasks,'answer_for',return_value=None), \
         patch.object(strategy,'learning_context',return_value={'lessons':[],'invalidated_hypotheses':[],'available_evidence_ids':[]}), \
         patch.object(strategy,'list_items',side_effect=lambda kind,*a,**k: copy.deepcopy(decisions) if kind=='decision' else []), \
         patch.object(separation,'recorded_strategies',return_value=copy.deepcopy(hypotheses)), \
         patch.object(acquisition,'recorded',return_value=copy.deepcopy(studies)), patch.object(acquisition,'study_context',side_effect=lambda x:x), \
         patch.object(agent_browser,'availability',return_value={'ready':True}), \
         patch.object(task_handlers,'_run',side_effect=lambda ctx,fn:fn()), \
         patch.object(runtime,'run_mission',side_effect=lambda goal,**kwargs:captured.update(goal=goal,kwargs={k:v for k,v in kwargs.items() if k!='checkpoint'}) or {}):
        supervisor._pursuit_mission(ctx,objective)
    state=json.loads(captured['goal'].rsplit('\n',1)[-1])
    def serialized(value):return json.dumps(value,ensure_ascii=False,default=str)
    families=('travail_précédent','stratégies_enregistrées','décisions','écarts_de_capacités','expériences_antérieures')
    a_chars=sum(len(serialized(state.get(k))) for k in families if 'API-cost' in serialized(state.get(k)))
    total=len(serialized(state))
    metrics={'goal_chars':len(captured['goal']),'estimated_tokens':round(len(captured['goal'])/4),
             'state_chars':total,'niche_field_chars':a_chars,'niche_field_share_pct':round(100*a_chars/total,1),
             'niche_mentions':captured['goal'].count('API-cost'),
             'field_chars':{k:len(serialized(state.get(k))) for k in families},
             'business_signal_focus':captured['kwargs'].get('business_signal_focus',False),
             'business_signal_target':captured['kwargs'].get('business_signal_target'),
             'provider_calls':0,'quality_benchmark':False}
    out=Path(sys.argv[2]);out.mkdir(exist_ok=True)
    (out/'capture.json').write_text(serialized(captured))
    (out/'metrics.json').write_text(serialized(metrics))
    print(serialized(metrics))
