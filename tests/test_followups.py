import re
import pytest
from app import artifacts
from app.followups import propose_followup
from app.planner import template_plan
from app.handlers import execute
from app.validator import validate_plan


def test_followup_changes_graph_and_preserves_parent():
    previous=template_plan()
    plan,record=propose_followup('Which product has the highest revenue in February 2026?',previous)
    assert [task['handler'] for task in plan['tasks']]==['validate_dataset','clean_transactions','filter_transactions','product_summary','build_report']
    assert plan['tasks'][2]['parameters']=={'month':'2026-02'}
    assert len(previous['tasks'])==6 and 'filter_transactions' not in [task['handler'] for task in previous['tasks']]
    assert record['resolved_intent']['sections']==['products']
    assert validate_plan(plan)


def test_followup_inherits_scope_and_changes_threshold():
    first,_=propose_followup('Show unusual transactions for product P1 in 2026-02 with IQR multiplier 2',template_plan())
    second,_=propose_followup('Use IQR multiplier 3 instead.',first)
    assert next(task['parameters'] for task in second['tasks'] if task['handler']=='filter_transactions')=={'month':'2026-02','product_id':'P1'}
    assert next(task['parameters']['iqr_multiplier'] for task in second['tasks'] if task['handler']=='flag_unusual_amounts')==3
    third,_=propose_followup('Show monthly sales across the entire dataset',second)
    assert 'filter_transactions' not in [task['handler'] for task in third['tasks']]
    assert [task['handler'] for task in third['tasks']]==['validate_dataset','clean_transactions','monthly_sales','build_report']

def test_followup_add_remove_and_clear_one_scope_dimension():
    first,_=propose_followup('Which product has the highest revenue for product P1 in 2026-02?',template_plan())
    second,_=propose_followup('Also include monthly sales',first)
    assert {'monthly_sales','product_summary'} <= {task['handler'] for task in second['tasks']}
    third,_=propose_followup('Remove monthly sales',second)
    assert 'monthly_sales' not in {task['handler'] for task in third['tasks']}
    fourth,_=propose_followup('Show all products',third)
    assert next(task['parameters'] for task in fourth['tasks'] if task['handler']=='filter_transactions')=={'month':'2026-02'}


@pytest.mark.parametrize('question',['Forecast sales next year','Which product performed best in February?','Show 2026-13 sales','Show product sales for 2026-01 and 2026-02','Hello there'])
def test_unsupported_or_ambiguous_followup_rejected(question):
    with pytest.raises(ValueError):propose_followup(question,template_plan())


def test_report_is_grounded_narrative_and_escaped(tmp_path,monkeypatch):
    monkeypatch.setattr(artifacts,'ROOT',tmp_path)
    raw=b'transaction_id,timestamp,product_id,quantity,unit_price\n1,2026-01-01,A,1,20\n2,2026-02-01,<script>,2,30\n3,2026-02-02,A,1,10\n4,bad,A,1,10\n'
    dataset=artifacts.put(raw,'.csv')['id'];plan,_=propose_followup('Which product has the highest revenue in February 2026?',template_plan());outputs={}
    for task in plan['tasks']:
        inputs={key:dataset if source=='$dataset' else outputs[source] for key,source in task['inputs'].items()}
        outputs[task['id']]=execute(task['handler'],inputs,task['parameters'])['id']
    content=artifacts.read(outputs['report']).decode()
    assert '<table' not in content and '<script>' not in content
    assert '&lt;script&gt;' in content and '60.00' in content and '70.00' in content and '85.7%' in content
    reference=re.search(r'name="workflow-insights" content="([a-f0-9]+\.json)"',content).group(1)
    insights=artifacts.read_json(reference)
    assert '2 accepted transactions' in insights['summary']
    assert any('1 input rows were rejected' in finding['text'] for finding in insights['findings'])
    assert any('month: 2026-02' in finding['text'] for finding in insights['findings'])


def test_empty_filter_does_not_invent_findings(tmp_path,monkeypatch):
    monkeypatch.setattr(artifacts,'ROOT',tmp_path)
    cleaned=artifacts.put_json({'rows':[],'rejected':[],'scope':{'month':'2026-02'},'excluded_rows':1})['id']
    products=artifacts.put_json({'rows':[]})['id']
    from app.insights import build_insights
    result=build_insights({'cleaned':cleaned,'products':products},{})
    assert '0 accepted transactions' in result['summary']
    assert any('No accepted transactions matched' in item['text'] for item in result['findings'])


def test_report_and_filter_bindings_still_validated():
    plan,_=propose_followup('Show monthly sales for 2026-02',template_plan())
    report=plan['tasks'][-1];report['inputs']={'cleaned':'filter'};report['depends_on']=['filter']
    with pytest.raises(ValueError,match='analytical input'):validate_plan(plan)
    plan,_=propose_followup('Show monthly sales for 2026-02',template_plan())
    plan['tasks'][2]['parameters']['month']='2026-99'
    with pytest.raises(ValueError,match='invalid month filter'):validate_plan(plan)


def test_live_model_legacy_scope_response_no_longer_blocks_followup(monkeypatch):
    import json
    from app import planner
    output={'operations':planner.BASE_OPERATIONS,'iqr_multiplier':1.5,
            'section':'products','scope':{'month':'2026-02','year':'2026'}}
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'model':'local-trained-or-base','choices':[{'message':{'content':json.dumps(output)}}]}
    monkeypatch.setattr(planner.httpx,'post',lambda *args,**kwargs:Response())
    parent=template_plan()
    next(t for t in parent['tasks'] if t['handler']=='flag_unusual_amounts')['parameters']={'iqr_multiplier':3}
    plan,record=propose_followup('Which product has the highest revenue in February 2026?',parent,'huggingface')
    assert record['repaired'] is False
    assert record['resolved_intent']=={'sections':['products'],'scope':{'month':'2026-02'},'iqr_multiplier':3}
    assert plan['tasks'][2]['parameters']=={'month':'2026-02'}
    assert [t['handler'] for t in plan['tasks']]==['validate_dataset','clean_transactions','filter_transactions','product_summary','build_report']


@pytest.mark.parametrize('extra',[
    {'sections':['products'],'scope':{'month':'2026-03'}},
    {'section':'products','scope':{'month':'2026-02','year':'2025'}},
    {'section':'products','sections':['flags'],'scope':{'month':'2026-02'}},
    {'sections':['products'],'scope':{'month':'2026-02','execute':'shell'}},
    {'sections':['products'],'scope':{'month':'2026-02'},'code':'print(1)'},
])
def test_followup_model_mismatch_or_unknown_keys_rejected(monkeypatch,extra):
    import json
    from app import planner
    output={'operations':planner.BASE_OPERATIONS,'iqr_multiplier':1.5,**extra}
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'message':{'content':json.dumps(output)}}]}
    monkeypatch.setattr(planner.httpx,'post',lambda *args,**kwargs:Response())
    with pytest.raises(ValueError,match='failed validation'):
        propose_followup('Which product has the highest revenue in February 2026?',template_plan(),'huggingface')


def test_contextual_repair_example_uses_context_schema(monkeypatch):
    import json
    from app import planner
    outputs=['not JSON',json.dumps({'operations':planner.BASE_OPERATIONS,'iqr_multiplier':3,
                                  'sections':['products'],'scope':{'month':'2026-02'}})]
    messages=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'training':{'fine_tuned':True,'adapter_sha256':'test'},'choices':[{'message':{'content':outputs.pop(0)}}]}
    def post(*args,**kwargs):
        messages.append(json.loads(json.dumps(kwargs['json']['messages'])))
        return Response()
    monkeypatch.setattr(planner.httpx,'post',post)
    _,record=propose_followup('Which product has the highest revenue in February 2026?',template_plan(),'huggingface')
    assert record['repaired'] and record['training']['fine_tuned']
    assert '"sections": ["products"]' in messages[1][-1]['content']
    assert '"scope": {"month": "2026-02"}' in messages[1][-1]['content']


@pytest.mark.parametrize('question,expected',[
    ('Remove the month filter',{'product_id':'P1'}),
    ('Clear the product filter',{'month':'2026-02'}),
])
def test_clear_specific_filter_keeps_other_dimension(question,expected):
    first,_=propose_followup('Show monthly sales for product P1 in 2026-02',template_plan())
    next_plan,_=propose_followup(question,first)
    assert next(t['parameters'] for t in next_plan['tasks'] if t['handler']=='filter_transactions')==expected
