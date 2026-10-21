import threading
import time
import json
import pytest
from sqlalchemy import select
from test_runtime import client
from app import models as db
from app.tabular import Clarification
from app.worker import run

def upload(c,name,content):
    result=c.post('/datasets',files={'file':(name,content)})
    assert result.status_code==200,result.text
    return result.json()['id']

def aggregate(source='t0',column='t0.c1',group='t0.c0'):
    return {'steps':[{'id':'summary','operation':'aggregate','sources':[source],'parameters':{'group_by':[group],'metrics':[{'name':'total','column':column,'function':'sum'}]}}],'outputs':['summary']}

def complete(c,wid):
    stop=threading.Event();threads=[threading.Thread(target=run,args=(stop,'table-worker-'+str(i),2,512),daemon=True) for i in range(2)]
    for t in threads:t.start()
    try:
        assert c.post('/workflows/'+wid+'/start').status_code==200
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            result=c.get('/workflows/'+wid).json()
            if result['status'] in ('completed','failed'):break
            time.sleep(.03)
        assert result['status']=='completed',result
        return result
    finally:
        stop.set()
        for thread in threads:thread.join(5)

def test_generic_overview_execution_and_profile_persistence(client):
    identity=upload(client,'students.csv',b'class,score\nA,2\nB,3\n')
    p=client.get('/datasets/'+identity+'/profile').json();assert p['columns'][1]['type']=='number'
    response=client.post('/plans',json={'dataset_id':identity,'request':'overview'})
    assert response.status_code==200,response.text
    assert response.json()['state']=='ready'
    flow=complete(client,response.json()['id'])
    assert flow['insights']['kind']=='tabular'
    assert client.get(flow['report_url'].replace('/api','',1)).status_code==200

def test_clarification_resume_and_consumption(client,monkeypatch):
    identity=upload(client,'students.csv',b'class,score\nA,2\nA,3\n')
    def propose(question,datasets,mode,previous=None,history=None):
        if not history:raise Clarification('Which score?', ['t0.c1'])
        return aggregate(),{'provider':'test'}
    monkeypatch.setattr('app.table_planning.propose_table',propose)
    response=client.post('/plans',json={'dataset_id':identity,'request':'average scores','planner':'huggingface'}).json()
    assert response['state']=='clarification_required'
    with db.Session() as s:assert not list(s.scalars(select(db.Workflow)))
    pending=response['clarification_id']
    assert client.get('/clarifications/'+pending).status_code==200
    response=client.post('/clarifications/'+pending+'/answer',json={'answer':'t0.c1'})
    assert response.status_code==200,response.text
    assert response.json()['state']=='ready'
    assert client.post('/clarifications/'+pending+'/answer',json={'answer':'t0.c1'}).status_code==409

def test_join_confirmation_execution_followup_and_history(client,monkeypatch):
    a=upload(client,'stock.csv',b'key,stock\n01,2\n02,7\n')
    b=upload(client,'locations.csv',b'key,region\n01,North\n02,South\n')
    raw=aggregate('joined','t0.c1','t1.c1')
    raw['steps'].insert(0,{'id':'joined','operation':'join','sources':['t0','t1'],'parameters':{'left_keys':['t0.c0'],'right_keys':['t1.c0'],'how':'left'}})
    monkeypatch.setattr('app.table_planning.propose_table',lambda *args,**kwargs:(raw,{'provider':'test'}))
    response=client.post('/plans',json={'dataset_ids':[a,b],'request':'Sum stock by region','planner':'huggingface'}).json()
    assert response['state']=='clarification_required'
    response=client.post('/clarifications/'+response['clarification_id']+'/answer',json={'answer':'confirm'})
    assert response.status_code==200,response.text
    flow=complete(client,response.json()['id'])
    assert len(flow['planner']['dataset_ids'])==2
    response=client.post('/workflows/'+flow['id']+'/followups',json={'request':'Repeat this analysis'})
    assert response.status_code==200,response.text
    if response.json().get('state')=='clarification_required':
        response=client.post('/clarifications/'+response.json()['clarification_id']+'/answer',json={'answer':'confirm'})
    revised=complete(client,response.json()['id'])
    assert revised['revision']==2 and len(revised['turns'])==2
    assert revised['turns'][0]['id']==flow['id']

def test_stale_profile_and_unknown_columns(client,monkeypatch):
    identity=upload(client,'students.csv',b'class,score\nA,2\n')
    monkeypatch.setattr('app.table_planning.propose_table',lambda *args,**kw:(_ for _ in ()).throw(Clarification('Which column?')))
    response=client.post('/plans',json={'dataset_id':identity,'request':'Sum scores','planner':'huggingface'}).json()
    with db.Session.begin() as s:
        p=s.get(db.PendingPlan,response['clarification_id']);value=dict(p.payload);value['profiles']={identity:'stale'};p.payload=value
    assert client.post('/clarifications/'+response['clarification_id']+'/answer',json={'answer':'score'}).status_code==409

def test_excel_sheet_api(client):
    import io
    from openpyxl import Workbook
    book=Workbook();book.active.append(['site','power']);book.active.append(['001',12]);data=io.BytesIO();book.save(data)
    response=client.post('/datasets',files={'file':('power.xlsx',data.getvalue())}).json()
    assert response['state']=='sheet_selection_required'
    response=client.post('/workbooks/'+response['workbook_id']+'/sheets',json={'sheet':'Sheet'})
    assert response.status_code==200,response.text
    assert response.json()['profile']['columns'][0]['leading_zero_identifier']

def test_wrong_worker_output_schema_is_not_accepted(client):
    from app import artifacts
    from app.coordinator import register_worker,command,tick,finish
    from app.catalogue import CATALOGUE
    from test_runtime import take
    identity=upload(client,'students.csv',b'class,score\nA,2\n')
    result=client.post('/plans',json={'dataset_id':identity,'request':'overview'}).json()
    register_worker('schema-worker','g',2,512,list(CATALOGUE));command(result['id'],'start');tick();job=take('schema-worker','g')
    fake=artifacts.put_json({'schema':{'invented':{'name':'Invented','type':'number'}},'rows':[{'invented':1}],'audit':[]})['id']
    with pytest.raises(ValueError,match='schema differs'):finish(job['attempt'],'g',fake)
    with db.Session() as s:
        task=s.scalar(select(db.TaskRow).where(db.TaskRow.workflow_id==result['id'],db.TaskRow.task_id=='t0'))
        assert task.artifact is None


def test_generic_followup_clarifies_scope_loss_and_resumes_without_overwriting(client,monkeypatch):
    identity=upload(client,'cohorts.csv',b'class,score\nA,2\nB,99\nA,10\n')
    raw=aggregate(source='selected')
    raw['steps'].insert(0,{'id':'selected','operation':'filter','sources':['t0'],'parameters':{'column':'t0.c0','operator':'eq','value':'A'}})
    monkeypatch.setattr('app.table_planning.propose_table',lambda *args,**kw:(raw,{'provider':'test'}))
    base=complete(client,client.post('/plans',json={'dataset_id':identity,'request':'Sum score for class A','planner':'huggingface'}).json()['id'])
    old_artifacts=[t['artifact'] for t in base['tasks']]
    replacement=aggregate();replacement['steps'][0]['parameters']['metrics'][0]['function']='median'
    monkeypatch.setattr('app.table_planning.propose_table',lambda *args,**kw:(replacement,{'provider':'test'}))
    response=client.post('/workflows/'+base['id']+'/followups',json={'request':'Use median instead of sum'})
    assert response.status_code==200,response.text
    pending=response.json();assert pending['state']=='clarification_required'
    with db.Session() as s:assert len(list(s.scalars(select(db.Workflow))))==1
    revised=json.loads(json.dumps(raw));revised['steps'][1]['parameters']['metrics'][0]['function']='median'
    monkeypatch.setattr('app.table_planning.propose_table',lambda *args,**kw:(revised,{'provider':'test'}))
    response=client.post('/clarifications/'+pending['clarification_id']+'/answer',json={'answer':'Keep previous filters'})
    assert response.status_code==200,response.text
    result=complete(client,response.json()['id'])
    assert result['revision']==2 and len(result['turns'])==2
    original=client.get('/workflows/'+base['id']).json()
    assert original['status']=='completed' and [t['artifact'] for t in original['tasks']]==old_artifacts


def test_concurrent_generic_followups_allow_only_one_new_revision(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    identity=upload(client,'stock.csv',b'location,stock\nA,2\nB,3\n')
    monkeypatch.setattr('app.table_planning.propose_table',lambda *args,**kw:(aggregate(),{'provider':'test'}))
    base=complete(client,client.post('/plans',json={'dataset_id':identity,'request':'Sum stock by location','planner':'huggingface'}).json()['id'])
    def followup(_):return client.post('/workflows/'+base['id']+'/followups',json={'request':'Repeat the same analysis'})
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(followup,range(2)))
    assert sorted(r.status_code for r in responses)==[200,409],[r.text for r in responses]
    with db.Session() as s:
        flows=list(s.scalars(select(db.Workflow)))
        assert len(flows)==2 and sorted(f.planner['revision'] for f in flows)==[1,2]
