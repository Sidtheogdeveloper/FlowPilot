import copy,json
import pytest
from app.planner import template_plan,propose
from app.validator import validate_plan
from app.scheduling import schedule
from app import artifacts
from app.handlers import execute
from hypothesis import given,strategies as st

def test_reference_plan():assert len(validate_plan(template_plan()).tasks)==6
@pytest.mark.parametrize('mutation',[lambda p:p['tasks'].append(copy.deepcopy(p['tasks'][0])),lambda p:p['tasks'][0].update(handler='exec'),lambda p:p['tasks'][1]['inputs'].update(validated='missing'),lambda p:p['tasks'][2]['resources'].update(memory_mb=1),lambda p:p['tasks'][4].update(parameters={'iqr_multiplier':-1}),lambda p:p['tasks'][2].update(depends_on=[]),lambda p:p['tasks'][2]['inputs'].update(cleaned='validate')])
def test_invalid_plans(mutation):
    p=template_plan();mutation(p)
    with pytest.raises(ValueError):validate_plan(p)
def test_cycle():
    p=template_plan();p['tasks'][0]['depends_on']=['report'];p['tasks'][0]['inputs']={'dataset':'report'}
    with pytest.raises(ValueError):validate_plan(p)
def test_template_unsupported():
    with pytest.raises(ValueError):propose('train model to forecast','template')

def test_handlers_and_escaping(tmp_path,monkeypatch):
    monkeypatch.setattr(artifacts,'ROOT',tmp_path)
    raw=b'transaction_id,timestamp,product_id,quantity,unit_price\n1,2026-01-01,p,2,10\n2,2026-02-01,<script>,1,30\n1,2026-01-01,p,2,10\n3,bad,p,-1,2\n'
    data=artifacts.put(raw,'.csv')['id'];v=execute('validate_dataset',{'dataset':data},{})['id'];c=execute('clean_transactions',{'validated':v},{})['id']
    cleaned=artifacts.read_json(c);assert len(cleaned['rows'])==2;assert len(cleaned['rejected'])==2
    m=execute('monthly_sales',{'cleaned':c},{})['id'];p=execute('product_summary',{'cleaned':c},{})['id'];f=execute('flag_unusual_amounts',{'cleaned':c},{})['id']
    assert [r['revenue'] for r in artifacts.read_json(m)['rows']]==[20,30]
    report=execute('build_report',{'monthly':m,'products':p,'flags':f},{})['id'];content=artifacts.read(report).decode();assert '&lt;script&gt;' in content;assert '<script>' not in content

def test_artifact_tampering_and_path(tmp_path,monkeypatch):
    monkeypatch.setattr(artifacts,'ROOT',tmp_path);id=artifacts.put(b'hello')['id'];(tmp_path/id).write_bytes(b'changed')
    with pytest.raises(ValueError):artifacts.read(id)
    with pytest.raises(ValueError):artifacts.read('../secret')

@given(cpu=st.lists(st.integers(min_value=1,max_value=4),min_size=1,max_size=8))
@pytest.mark.parametrize('policy',['greedy','heft','bounded'])
def test_scheduler_capacity(cpu,policy):
    tasks=[{'id':str(i),'handler':'x','resources':{'cpu':v,'memory_mb':128},'estimate':i+1} for i,v in enumerate(cpu)]
    workers=[{'id':'a','cpu':4,'memory_mb':512,'handlers':['x'],'speed':1},{'id':'b','cpu':2,'memory_mb':256,'handlers':['x'],'speed':2}]
    result=schedule(tasks,workers,policy)
    for w in workers:
        assigned=[t for t in tasks if result.assignments.get(t['id'])==w['id']]
        assert sum(t['resources']['cpu'] for t in assigned)<=w['cpu'];assert len(assigned)*128<=w['memory_mb']
def test_bounded_keeps_incumbent():
    tasks=[{'id':'t','handler':'x','resources':{'cpu':1,'memory_mb':128}}];workers=[{'id':'a','cpu':1,'memory_mb':128,'handlers':['x']}]
    assert schedule(tasks,workers,'bounded',max_states=0).assignments=={'t':'a'}
def test_incompatible_worker():
    assert not schedule([{'id':'t','handler':'x','resources':{'cpu':1,'memory_mb':128}}],[{'id':'a','cpu':1,'memory_mb':128,'handlers':['y']}]).assignments

def test_hf_adapter_repair(monkeypatch):
    import app.planner as planner
    answers=['{"tasks":[]}',json.dumps(template_plan())];requests=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':answers.pop(0)}}]}
    def post(url,**kwargs):requests.append(kwargs['json']);return Response()
    monkeypatch.setattr(planner.httpx,'post',post)
    raw,record=planner.propose('retail monthly report','huggingface');assert len(validate_plan(raw).tasks)==6;assert record['repaired'];assert len(requests)==2

def test_hf_adapter_never_silently_falls_back(monkeypatch):
    import app.planner as planner
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':'not json'}}]}
    monkeypatch.setattr(planner.httpx,'post',lambda *args,**kwargs:Response())
    with pytest.raises(ValueError):planner.propose('retail report','huggingface')


def test_greedy_and_heft_are_distinct():
    tasks=[{'id':'short','handler':'x','rank':1,'estimate':1,'resources':{'cpu':1,'memory_mb':128}},{'id':'critical','handler':'x','rank':10,'estimate':10,'resources':{'cpu':1,'memory_mb':128}}]
    workers=[{'id':'only','cpu':1,'memory_mb':128,'handlers':['x']}]
    assert schedule(tasks,workers,'greedy').assignments=={'short':'only'}
    assert schedule(tasks,workers,'heft').assignments=={'critical':'only'}

def test_baselines_not_search_capped():
    tasks=[{'id':str(i),'handler':'x','resources':{'cpu':1,'memory_mb':128}} for i in range(8)]
    workers=[{'id':'a','cpu':8,'memory_mb':1024,'handlers':['x']}]
    assert len(schedule(tasks,workers,'greedy').assignments)==8
    assert len(schedule(tasks,workers,'heft').assignments)==8
    assert len(schedule(tasks,workers,'bounded').assignments)==6

def test_result_schema_rejects_wrong_fields():
    from app.result_contracts import validate_output
    with pytest.raises(ValueError):validate_output('monthly_sales',{'rows':[{'foo':1}]})
    with pytest.raises(ValueError):validate_output('monthly_sales',{'rows':[{'month':'2026-01','revenue':float('inf'),'transactions':1}]})
    assert validate_output('monthly_sales',{'rows':[{'month':'2026-01','revenue':20,'transactions':1}]})

def test_bounded_improves_joint_fixture_and_matches_oracle():
    import itertools
    tasks=[{'id':'short','handler':'x','estimate':1,'resources':{'cpu':1,'memory_mb':128}},{'id':'long','handler':'x','estimate':10,'resources':{'cpu':1,'memory_mb':128}}]
    workers=[{'id':'fast','cpu':1,'memory_mb':128,'handlers':['x'],'speed':10},{'id':'slow','cpu':1,'memory_mb':128,'handlers':['x'],'speed':1}]
    greedy=schedule(tasks,workers,'greedy');ours=schedule(tasks,workers,'bounded',budget_ms=1000)
    assert greedy.assignments=={'short':'fast','long':'slow'}
    feasible=[]
    for values in itertools.product([None,'fast','slow'],repeat=2):
        if any(values.count(w['id'])>w['cpu'] for w in workers):continue
        durations=[t['estimate']/next(w['speed'] for w in workers if w['id']==a) for t,a in zip(tasks,values) if a]
        feasible.append((-len(durations),max(durations,default=0),sum(durations)))
    assert tuple(ours.metrics['objective'])==min(feasible)
    assert ours.assignments=={'short':'slow','long':'fast'}

def test_estimates_frozen_cold_start(monkeypatch):
    from app.estimation import estimates
    monkeypatch.setenv('ESTIMATE_HISTORY','0')
    values=estimates(None,10000);assert values['clean_transactions']>values['product_summary'];assert all(v>0 for v in values.values())

def test_ambiguous_request_rejected():
    with pytest.raises(ValueError):propose('Do something interesting','template')

def test_intent_compiler_adds_trusted_dependencies():
    from app.planner import compile_intent
    plan=compile_intent({'operations':['monthly_sales','build_report'],'iqr_multiplier':2})
    assert len(validate_plan(plan).tasks)==6
    assert next(t for t in plan['tasks'] if t['handler']=='flag_unusual_amounts')['parameters']=={'iqr_multiplier':2.0}

def test_intent_compiler_rejects_invalid_operations():
    from app.planner import compile_intent
    for operations in (['exec','build_report'],['build_report','build_report'],['monthly_sales']):
        with pytest.raises(ValueError):compile_intent({'operations':operations})

def test_worker_retries_transient_db_connection(monkeypatch):
    import threading
    import app.worker as worker
    from sqlalchemy.exc import DBAPIError
    stop=threading.Event();claims=[];completions=[]
    monkeypatch.setattr(worker,'register_worker',lambda *args:None);monkeypatch.setattr(worker,'heartbeat',lambda *args:True)
    def take(*args):
        claims.append(1)
        if len(claims)==1:raise DBAPIError(None,None,RuntimeError('temporary connection'),False)
        return {'attempt':'a','handler':'monthly_sales','inputs':{},'parameters':{}}
    def done(*args,**kwargs):
        completions.append(kwargs)
        if len(completions)==1:raise DBAPIError(None,None,RuntimeError('temporary connection'),False)
        stop.set();return True
    monkeypatch.setattr(worker,'claim',take);monkeypatch.setattr(worker,'execute',lambda *args:{'id':'immutable.json'});monkeypatch.setattr(worker,'finish',done)
    worker.run(stop,'test',1,128);assert len(claims)==2;assert completions==[{'artifact':'immutable.json'},{'artifact':'immutable.json'}]

@pytest.mark.parametrize('text,value',[('IQR multiplier 2',2),('IQR multiplier of 2.0',2),('IQR-multiplier: .5',.5),('IQR multiplier=10',10),('IQR multiplier is 0.1',.1),('IQR multiplier 2e0',2),('IQR multiplier 2 and IQR multiplier 2.0',2)])
def test_explicit_iqr_request_constraint(text,value):
    from app.request_constraints import extract_constraints
    assert extract_constraints(text)=={'iqr_multiplier':value}
    plan,record=propose('Retail report using '+text,'template')
    assert next(t['parameters'] for t in plan['tasks'] if t['handler']=='flag_unusual_amounts')['iqr_multiplier']==value
    assert record['explicit_parameter_source']=='request'

@pytest.mark.parametrize('text',['IQR multiplier 2O','IQR mulitplier 2','IQR multiplier two','IQR multiplier','IQR multiplier 2 or 3','IQR multiplier 2,3','IQR multiplier 2 and IQR multiplier 3','IQR multiplier 0','IQR multiplier 10.1','IQR multiplier 1e309'])
def test_ambiguous_invalid_iqr_request_rejected(text):
    with pytest.raises(ValueError):propose('Retail report using '+text,'template')

def test_repair_cannot_override_explicit_user_parameter(monkeypatch):
    import app.planner as planner
    answers=[json.dumps({'operations':['execute_shell','build_report'],'iqr_multiplier':2}),json.dumps({'operations':[k for k in planner.CATALOGUE if not k.startswith('table_')],'iqr_multiplier':1.5})];messages=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':answers.pop(0)}}]}
    def post(url,**kwargs):messages.append(json.loads(json.dumps(kwargs['json']['messages'])));return Response()
    monkeypatch.setattr(planner.httpx,'post',post)
    plan,record=planner.propose('Retail report using IQR multiplier 2','huggingface')
    assert next(t['parameters'] for t in plan['tasks'] if t['handler']=='flag_unusual_amounts')['iqr_multiplier']==2
    assert record['repaired'] and record['explicit_parameter_source']=='request';assert record['enforced_parameters']=={'iqr_multiplier':2}
    assert 'IQR multiplier 2' in messages[1][-1]['content'];assert 'must preserve' in messages[1][-1]['content']

def test_direct_dag_model_cannot_override_explicit_parameter(monkeypatch):
    import app.planner as planner
    wrong=template_plan();next(t for t in wrong['tasks'] if t['handler']=='flag_unusual_amounts')['parameters']={'iqr_multiplier':1.5}
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':json.dumps(wrong)}}]}
    monkeypatch.setattr(planner.httpx,'post',lambda *args,**kwargs:Response());monkeypatch.setenv('HF_PLANNING_MODE','dag')
    plan,record=planner.propose('Retail report using IQR multiplier 2','huggingface')
    assert next(t['parameters'] for t in plan['tasks'] if t['handler']=='flag_unusual_amounts')['iqr_multiplier']==2;assert record['explicit_parameter_source']=='request'

@pytest.mark.parametrize('text',['IQR multiplier2','IQRmultiplier 2'])
def test_iqr_missing_separator_rejected(text):
    with pytest.raises(ValueError):propose('Retail report using '+text,'template')

def test_intent_aliases_are_trusted_catalogue_normalization():
    from app.planner import compile_intent
    plan=compile_intent({'operations':['calculate_monthly_sales','generate_report']})
    assert len(validate_plan(plan).tasks)==6
    assert set(t['handler'] for t in plan['tasks'])==set(__import__('app.planner',fromlist=['BASE_OPERATIONS']).BASE_OPERATIONS)

def test_alias_and_canonical_duplicate_rejected():
    from app.planner import compile_intent
    with pytest.raises(ValueError):compile_intent({'operations':['monthly_sales','calculate_monthly_sales','build_report']})
