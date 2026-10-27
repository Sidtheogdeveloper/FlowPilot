import copy,json,math
import pytest
from sqlalchemy import select
from test_runtime import client
from test_table_runtime import upload,complete,aggregate
from test_product_tools import second
from app import models as db,artifacts
from app.tabular import compile_table_intent,execute_table
from app.partitioning import prepare_execution
from app.validator import validate_plan

def execute_graph(plan,ds):
    outputs={}
    for t in plan['tasks']:
        inputs={}
        for k,v in t['inputs'].items():
            if v.startswith('$dataset:'):
                d=next(d for d in ds if d.id==v.split(':',1)[1])
                with db.Session() as s:
                    part=s.get(db.DatasetPartition,(d.id,t['parameters'].get('chunk_size'),t['parameters'].get('index'))) if t['handler']=='table_load_chunk' else None
                inputs[k]=part.artifact if part else d.table_ref
            else:inputs[k]=outputs[v]
        outputs[t['id']]=execute_table(t['handler'],inputs,t['parameters'])['id']
    return outputs

def test_partitioned_weighted_mean_nulls_empty_and_all_functions(client):
    # Preserve group/null semantics for every decomposable function.
    key=upload(client,'mixed.csv',b'group,value\nA,1\nA,2\nA,3\nA,4\nB,5\nB,\n,8\n,9\n')
    metrics=[{'name':fn,'function':fn,**({} if fn=='rows' else {'column':'t0.c1'})} for fn in ('sum','mean','min','max','count','rows')]
    raw={'steps':[{'id':'agg','operation':'aggregate','sources':['t0'],'parameters':{'group_by':['t0.c0'],'metrics':metrics}}],'outputs':['agg']}
    with db.Session.begin() as s:
        ds=[s.get(db.Dataset,key)];plan,_=compile_table_intent(raw,ds)
        opt,record=prepare_execution(s,plan,ds,threshold=1)
    assert record['partitioned_aggregations']==['agg']
    baseline=artifacts.read_json(execute_graph(plan,ds)['agg'])
    actual=artifacts.read_json(execute_graph(opt,ds)['agg'])
    assert actual['rows']==baseline['rows']
    assert actual['rows'][0]['mean']==2.5
    assert validate_plan(opt)

@pytest.mark.parametrize('groups',[[],['t0.c0']])
@pytest.mark.parametrize('cutoff',[3,1000])
def test_multiple_real_partitions_match_original(client,groups,cutoff):
    rows=['group,value']+[('A' if i%3 else 'B')+','+(str(i%11) if i%7 else '') for i in range(50003)]
    key=upload(client,'large.csv',('\n'.join(rows)+'\n').encode())
    p={'group_by':groups,'metrics':[{'name':'average','function':'mean','column':'t0.c1'},{'name':'total','function':'sum','column':'t0.c1'},{'name':'n','function':'rows'}]}
    raw={'steps':[{'id':'filtered','operation':'filter','sources':['t0'],'parameters':{'column':'t0.c1','operator':'ge','value':cutoff}},{'id':'agg','operation':'aggregate','sources':['filtered'],'parameters':p}],'outputs':['agg']}
    with db.Session.begin() as s:
        ds=[s.get(db.Dataset,key)];plan,_=compile_table_intent(raw,ds);opt,record=prepare_execution(s,plan,ds)
    assert len([t for t in opt['tasks'] if t['handler']=='table_partial'])==2
    a=artifacts.read_json(execute_graph(plan,ds)['agg']);b=artifacts.read_json(execute_graph(opt,ds)['agg'])
    for left,right in zip(a['rows'],b['rows']):
        assert left.keys()==right.keys()
        for k in left:
            if isinstance(left[k],(int,float)):assert math.isclose(left[k],right[k],rel_tol=1e-12)
            else:assert left[k]==right[k]
    assert sum(x.get('excluded_rows',0) for x in a['audit'])==sum(x.get('excluded_rows',0) for x in b['audit'])
    broken=copy.deepcopy(opt);next(t for t in broken['tasks'] if t['handler']=='table_reduce')['parameters']['aggregation']['metrics'][0]['function']='median'
    with pytest.raises(ValueError):validate_plan(broken)

def test_global_fallback_for_non_decomposable_operation(client):
    key=upload(client,'x.csv',b'group,value\nA,1\nA,2\n')
    raw=aggregate();raw['steps'][0]['parameters']['metrics'][0]['function']='median'
    with db.Session.begin() as s:
        ds=[s.get(db.Dataset,key)];plan,_=compile_table_intent(raw,ds);opt,r=prepare_execution(s,plan,ds,threshold=1)
    assert opt==plan and not r['partitioned_aggregations']

def test_optional_editor_versions_validate_before_execution(client,monkeypatch):
    key=upload(client,'scores.csv',b'class,score\nA,2\nA,4\nB,9\n')
    monkeypatch.setattr('app.table_planning.propose_table',lambda *a,**k:(aggregate(),{}))
    f=client.post('/plans',json={'dataset_id':key,'request':'Sum scores by class','planner':'huggingface'}).json()
    info=client.get('/product/workflows/'+f['id']+'/editor')
    assert info.status_code==200 and info.json()['editable']
    changed=aggregate()['steps'][0]['parameters'];changed['metrics'][0]['function']='mean'
    r=client.post('/product/workflows/'+f['id']+'/edit',json={'changes':[{'id':'summary','parameters':changed}],'partitioned':False})
    assert r.status_code==200,r.text
    n=r.json();assert n['chat_id']==f['chat_id'] and n['revision']==2
    assert client.get('/workflows/'+f['id']).json()['status']=='cancelled'
    done=complete(client,n['id'])
    artifact=next(t['artifact'] for t in done['tasks'] if t['handler']=='table_aggregate')
    assert artifacts.read_json(artifact)['rows']==[{'t0.c0':'A','total':3},{'t0.c0':'B','total':9}]
    assert client.post('/product/workflows/'+n['id']+'/edit',json={}).status_code==409
    assert done['planner']['execution']['enabled']==False

def test_invalid_edit_is_atomic_and_added_filter_is_validated(client,monkeypatch):
    key=upload(client,'scores.csv',b'class,score\nA,2\nB,9\n')
    monkeypatch.setattr('app.table_planning.propose_table',lambda *a,**k:(aggregate(),{}))
    f=client.post('/plans',json={'dataset_id':key,'request':'Sum scores','planner':'huggingface'}).json()
    bad=client.post('/product/workflows/'+f['id']+'/edit',json={'changes':[{'id':'summary','parameters':{'group_by':['hallucinated'],'metrics':[{'name':'x','function':'rows'}]}}]})
    assert bad.status_code==422
    assert client.get('/workflows/'+f['id']).json()['status']=='planned'
    n=client.post('/product/workflows/'+f['id']+'/edit',json={'add_filter':{'source':'t0','parameters':{'column':'t0.c1','operator':'ge','value':5}}})
    assert n.status_code==200,n.text
    result=complete(client,n.json()['id']);ref=next(t['artifact'] for t in result['tasks'] if t['handler']=='table_aggregate')
    assert artifacts.read_json(ref)['rows']==[{'t0.c0':'B','total':9}]

def test_estimates_ownership_and_blocked_capacity(client):
    key=upload(client,'x.csv',b'x,y\nA,2\n')
    f=client.post('/plans',json={'dataset_id':key,'request':'overview'}).json()
    estimate=client.get('/product/workflows/'+f['id']+'/estimate').json()
    assert estimate['blocked_tasks'] and estimate['estimated_seconds'] is None
    assert estimate['largest_task_memory_mb']>=128
    assert client.get('/workflows/'+f['id']).json()['status']=='planned'
    other,_=second()
    for suffix in ('estimate','editor'):
        assert other.get('/product/workflows/'+f['id']+'/'+suffix).status_code==404
    assert other.post('/product/workflows/'+f['id']+'/edit',json={}).status_code==404
    other.close()

def test_drift_schema_missing_and_distribution(client):
    old=upload(client,'original.csv',('group,value,removed\n'+'\n'.join('A,'+str(i)+',x' for i in range(40))).encode())
    content=('value,group,added\n'+'\n'.join((str(i+100) if i<30 else '')+',A,x' for i in range(40))).encode()
    r=client.post('/product/datasets/'+old+'/versions',files={'file':('new.csv',content)})
    assert r.status_code==200,r.text
    report=r.json()['drift'];assert report['flagged']
    kinds={c['kind'] for c in report['schema_and_quality_changes']}
    assert {'added_column','removed_column','missing_rate_changed'}<=kinds
    assert report['distributions'][0]['flagged']
    assert client.get('/product/datasets/'+r.json()['id']+'/drift').json()==report
    assert client.get('/product/datasets/'+old+'/drift').json()['baseline_id'] is None

def test_estimate_excludes_foreign_execution_history(client):
    key=upload(client,'owner.csv',b'group,value\nA,2\n')
    f=client.post('/plans',json={'dataset_id':key,'request':'overview'}).json()
    complete(client,f['id'])
    other,_=second()
    data=upload(other,'other.csv',b'group,value\nB,9\n')
    preview=other.post('/plans',json={'dataset_id':data,'request':'overview'}).json()
    response=other.get('/product/workflows/'+preview['id']+'/estimate')
    assert response.status_code==200,response.text
    assert response.json()['tasks']
    other.close()

def test_visual_join_edit_requires_confirmation(client,monkeypatch):
    a=upload(client,'left.csv',b'key,value\nA,2\nB,4\n')
    b=upload(client,'right.csv',b'key,region\nA,North\n')
    raw={'steps':[{'id':'joined','operation':'join','sources':['t0','t1'],'parameters':{'left_keys':['t0.c0'],'right_keys':['t1.c0'],'how':'left'}},
                  {'id':'summary','operation':'aggregate','sources':['joined'],'parameters':{'group_by':['t1.c1'],'metrics':[{'name':'total','function':'sum','column':'t0.c1'}]}}],'outputs':['summary']}
    monkeypatch.setattr('app.table_planning.propose_table',lambda *a,**kw:(raw,{}))
    r=client.post('/plans',json={'dataset_ids':[a,b],'request':'Sum values by region','planner':'huggingface'}).json()
    f=client.post('/clarifications/'+r['clarification_id']+'/answer',json={'answer':'confirm'}).json()
    params=dict(raw['steps'][0]['parameters'],how='inner')
    payload={'changes':[{'id':'joined','parameters':params}]}
    assert client.post('/product/workflows/'+f['id']+'/edit',json=payload).status_code==422
    payload['confirm_joins']=True
    edited=client.post('/product/workflows/'+f['id']+'/edit',json=payload)
    assert edited.status_code==200,edited.text
    done=complete(client,edited.json()['id']);ref=next(t['artifact'] for t in done['tasks'] if t['handler']=='table_aggregate')
    assert artifacts.read_json(ref)['rows']==[{'t1.c1':'North','total':2}]
