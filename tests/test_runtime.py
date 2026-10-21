import os,uuid,time,threading
from datetime import timedelta
import pytest
from sqlalchemy import select,text
from fastapi.testclient import TestClient
from app import models as db,artifacts
from app.api import app
from app.coordinator import tick,register_worker,claim,finish,heartbeat,command
from app.worker import run
from app.handlers import execute
@pytest.fixture()
def client(tmp_path,monkeypatch):
    if not os.getenv('DATABASE_URL'):pytest.skip('Real PostgreSQL DATABASE_URL required')
    from sqlalchemy.engine import make_url
    if make_url(os.environ['DATABASE_URL']).database!='workflow_test':
        pytest.fail('Integration tests require the dedicated workflow_test database; refusing to reset application data')
    monkeypatch.setattr(artifacts,'ROOT',tmp_path)
    db.init_db()
    with db.Session.begin() as s:s.execute(text('TRUNCATE pending_plans,events,attempts,tasks,workflows,datasets,workers RESTART IDENTITY CASCADE'))
    with TestClient(app) as c:
        yield c

def take(worker,generation):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        job=claim(worker,generation)
        if job:return job
        time.sleep(.005)
    return None

def create(c,policy='greedy'):
    data=c.post('/datasets',files={'file':('retail.csv',b'transaction_id,timestamp,product_id,quantity,unit_price\n1,2026-01-01,p,2,10\n2,2026-02-01,q,3,5\n','text/csv')});assert data.status_code==200
    result=c.post('/plans',json={'dataset_id':data.json()['id'],'request':'clean sales and products, flag unusual transactions and report','policy':policy});assert result.status_code==200,result.text
    return result.json()['id']
@pytest.mark.parametrize('policy',['greedy','heft','bounded'])
def test_end_to_end(client,policy):
    wid=create(client,policy);stop=threading.Event();threads=[threading.Thread(target=run,args=(stop,'w'+str(i),2,512),daemon=True) for i in range(2)]
    for t in threads:t.start()
    try:
        assert client.post('/workflows/'+wid+'/start').status_code==200
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            result=client.get('/workflows/'+wid).json()
            if result['status'] in ('completed','failed'):break
            time.sleep(.05)
        assert result['status']=='completed',result
        assert len(result['tasks'])==6;assert all(t['status']=='completed' for t in result['tasks'])
        report=next(t['artifact'] for t in result['tasks'] if t['handler']=='build_report');assert b'2026-01' in client.get('/artifacts/'+report).content
        assert result['metrics']['completion_seconds']>0
    finally:
        stop.set()
        for t in threads:t.join(5)

def test_fenced_retry_and_duplicate(client):
    wid=create(client);register_worker('a','g1',1,128,list(__import__('app.catalogue',fromlist=['CATALOGUE']).CATALOGUE));command(wid,'start');tick();job=take('a','g1');assert job
    artifact=execute(job['handler'],job['inputs'],job['parameters'])['id']
    with db.Session.begin() as s:s.get(db.Attempt,job['attempt']).lease=db.now()-timedelta(seconds=1)
    tick();assert finish(job['attempt'],'g1',artifact)==False
    retry=take('a','g1');assert retry and retry['attempt']!=job['attempt'];assert finish(retry['attempt'],'g1',artifact);assert not finish(retry['attempt'],'g1',artifact)
    with db.Session() as s:assert s.scalar(select(db.TaskRow).where(db.TaskRow.workflow_id==wid,db.TaskRow.task_id=='validate')).artifact==artifact

def test_pause_cancel_and_generation(client):
    wid=create(client);assert client.post('/workflows/'+wid+'/pause').status_code==409
    command(wid,'start');command(wid,'pause');register_worker('a','g',1,128,list(__import__('app.catalogue',fromlist=['CATALOGUE']).CATALOGUE));tick();assert claim('a','g') is None
    command(wid,'resume');tick();job=take('a','g');assert job
    command(wid,'cancel');artifact=execute(job['handler'],job['inputs'],job['parameters'])['id'];assert not finish(job['attempt'],'g',artifact)
    assert client.get('/workflows/'+wid).json()['status']=='cancelled'

def test_worker_restart_and_lease(client):
    wid=create(client);register_worker('a','g1',1,128,list(__import__('app.catalogue',fromlist=['CATALOGUE']).CATALOGUE));command(wid,'start');tick();job=take('a','g1');register_worker('a','g2',1,128,list(__import__('app.catalogue',fromlist=['CATALOGUE']).CATALOGUE));tick()
    assert not heartbeat('a','g1');retry=take('a','g2');assert retry and retry['attempt']!=job['attempt']

def test_api_validation(client):
    assert client.post('/datasets',files={'file':('bad.csv',b'wrong,wrong\n1,2')}).status_code==422
    assert client.post('/plans',json={'dataset_id':'missing','request':'retail report'}).status_code==404
    assert client.get('/workflows/missing').status_code==404

def complete_chat(c,wid):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        item=c.get('/workflows/'+wid).json()
        if item['status'] in ('completed','failed'):break
        time.sleep(.03)
    assert item['status']=='completed',item
    return item

def test_same_chat_followup_revisions_execute_and_preserve_outputs(client):
    wid=create(client);stop=threading.Event();thread=threading.Thread(target=run,args=(stop,'followup-worker',2,512),daemon=True);thread.start()
    try:
        assert client.post('/workflows/'+wid+'/start').status_code==200
        original=complete_chat(client,wid)
        request='Which product has the highest revenue in February 2026?'
        response=client.post('/workflows/'+wid+'/followups',json={'request':request})
        assert response.status_code==200,response.text
        next_version=response.json();assert next_version['id']!=wid and next_version['chat_id']==wid and next_version['revision']==2
        assert next_version['parent_workflow_id']==wid and next_version['request']==request
        result=complete_chat(client,next_version['id'])
        assert len(result['tasks'])==5 and len(result['turns'])==2
        assert any('Product q generated 15.00' in finding['text'] for finding in result['insights']['findings'])
        history=client.get('/workflows').json();assert len(history)==1 and history[0]['id']==next_version['id']
        assert original['request'] in history[0]['search_text'] and request in history[0]['search_text']
        old=client.get('/workflows/'+wid).json();assert old['plan']==original['plan'] and old['tasks']==original['tasks']
        assert old['latest_workflow_id']==next_version['id'] and len(old['turns'])==2
        report=client.get('/workflows/'+next_version['id']+'/report');assert report.status_code==200 and b'<table' not in report.content
        assert b'February 2026' in report.content
        assert client.post('/workflows/'+wid+'/followups',json={'request':'Monthly sales'}).status_code==409
        assert client.post('/workflows/'+next_version['id']+'/followups',json={'request':'Predict weather'}).status_code==422
        assert len(client.get('/workflows/'+next_version['id']).json()['turns'])==2
    finally:stop.set();thread.join(5)

def test_followup_requires_terminal_current_version(client):
    wid=create(client)
    assert client.post('/workflows/'+wid+'/followups',json={'request':'Monthly sales'}).status_code==409
    assert client.post('/workflows/missing/followups',json={'request':'Monthly sales'}).status_code==404
    assert client.get('/workflows/'+wid+'/report').status_code==409

def test_concurrent_followups_cannot_create_two_current_versions(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app import followups
    wid=create(client);client.post('/workflows/'+wid+'/cancel')
    original=followups.propose_followup;barrier=threading.Barrier(2)
    def simultaneous(*args,**kwargs):
        result=original(*args,**kwargs);barrier.wait(timeout=5);return result
    monkeypatch.setattr(followups,'propose_followup',simultaneous)
    def submit(question):return client.post('/workflows/'+wid+'/followups',json={'request':question}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(submit,['Show monthly sales','Show product performance']))
    assert sorted(statuses)==[200,409]
    history=client.get('/workflows').json();assert len(history)==1
    detail=client.get('/workflows/'+history[0]['id']).json();assert len(detail['turns'])==2 and detail['revision']==2

def test_chat_history_keeps_separate_dags_and_reusable_dataset(client):
    first=create(client)
    original=client.get('/workflows/'+first).json()
    dataset_id=original['dataset_id']
    request='Monthly sales and product report with IQR multiplier 2'
    response=client.post('/plans',json={'dataset_id':dataset_id,'request':request,'planner':'template','policy':'bounded'})
    assert response.status_code==200,response.text
    second=response.json()
    assert second['id']!=first and second['dataset_id']==dataset_id
    assert second['request']==request and second['planner_mode']=='template' and second['policy']=='bounded'
    assert second['dataset']['filename']=='retail.csv' and second['dataset']['rows']==2
    flags=next(t for t in second['plan']['tasks'] if t['handler']=='flag_unusual_amounts')
    assert flags['parameters']['iqr_multiplier']==2
    assert client.get('/workflows/'+first).json()['plan']==original['plan']
    # Reopening sessions must retain the stored request and each graph independently.
    with db.Session() as s:
        flows=[s.get(db.Workflow,id) for id in (first,second['id'])]
        assert flows[1].planner['request']==request
        keys=[set(s.scalars(select(db.TaskRow.id).where(db.TaskRow.workflow_id==flow.id))) for flow in flows]
        assert len(keys[0])==len(keys[1])==6 and keys[0].isdisjoint(keys[1])
    history=client.get('/workflows').json()
    assert [item['id'] for item in history]==[second['id'],first]
    assert history[0]['request']==request and history[0]['dataset_filename']=='retail.csv'
    saved=client.get('/datasets').json()
    assert len(saved)==1 and saved[0]['id']==dataset_id and saved[0]['workflow_count']==2
    assert saved[0]['last_used'] and saved[0]['columns']==['transaction_id','timestamp','product_id','quantity','unit_price']

def test_legacy_chat_history_has_honest_missing_request(client):
    wid=create(client)
    with db.Session.begin() as s:
        flow=s.get(db.Workflow,wid)
        flow.planner={key:value for key,value in flow.planner.items() if key not in ('request','planner_mode')}
    item=client.get('/workflows').json()[0]
    assert item['request'] is None and item['title']=='retail.csv analysis'
    reopened=client.get('/workflows/'+wid).json()
    assert reopened['request'] is None and len(reopened['plan']['tasks'])==6

def test_failed_plan_does_not_overwrite_saved_chat(client):
    wid=create(client)
    saved=client.get('/workflows/'+wid).json()
    response=client.post('/plans',json={'dataset_id':saved['dataset_id'],'request':'predict weather and train model'})
    assert response.status_code==422
    assert client.get('/workflows/'+wid).json()['request']==saved['request']
    assert len(client.get('/workflows').json())==1
    assert client.get('/datasets').json()[0]['workflow_count']==1

def test_parallel_branches_capacity_and_reconciliation(client):
    from app.catalogue import CATALOGUE
    wid=create(client);register_worker('a','g',1,128,list(CATALOGUE));register_worker('b','h',1,128,list(CATALOGUE));command(wid,'start')
    for expected in ('validate_dataset','clean_transactions'):
        tick();job=take('a','g') or take('b','h');assert job,client.get('/workflows/'+wid).json();assert job['handler']==expected
        gen='g' if db_attempt_worker(job['attempt'])=='a' else 'h'
        artifact=execute(job['handler'],job['inputs'],job['parameters'])['id'];assert finish(job['attempt'],gen,artifact)
    tick();jobs=[take('a','g'),take('b','h')];assert all(jobs),client.get('/workflows/'+wid).json();assert len(set(j['handler'] for j in jobs))==2
    with db.Session() as s:
        completed=list(s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==wid,db.TaskRow.status=='completed')));assert len(completed)==2
        saved={t.task_id:t.artifact for t in completed}
    # Simulate loss plus coordinator restart by rebuilding sessions while state persists.
    with db.Session.begin() as s:s.get(db.Worker,'a').heartbeat=db.now()-timedelta(seconds=60)
    db.init_db();tick()
    with db.Session() as s:
        assert {t.task_id:t.artifact for t in s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==wid,db.TaskRow.status=='completed'))}==saved
        reservations=list(s.scalars(select(db.Attempt).where(db.Attempt.worker_id=='b',db.Attempt.status.in_(['queued','running']))));assert len(reservations)<=1
    old=jobs[0];output=execute(old['handler'],old['inputs'],old['parameters'])['id'];assert not finish(old['attempt'],'g',output)

def db_attempt_worker(id):
    with db.Session() as s:return s.get(db.Attempt,id).worker_id

def test_retry_bound(client):
    from app.catalogue import CATALOGUE
    wid=create(client);register_worker('a','g',1,128,list(CATALOGUE));command(wid,'start')
    for i in range(3):
        tick();job=take('a','g');assert job;assert finish(job['attempt'],'g',error='controlled handler failure')
    tick();assert client.get('/workflows/'+wid).json()['status']=='failed';assert claim('a','g') is None

def test_queued_lease_renewal_and_contract_acceptance(client):
    from app.catalogue import CATALOGUE
    wid=create(client);register_worker('a','g',2,256,list(CATALOGUE));command(wid,'start');tick()
    with db.Session.begin() as s:
        a=s.scalar(select(db.Attempt).where(db.Attempt.status=='queued'));a.lease=db.now()+timedelta(seconds=1);aid=a.id
    assert heartbeat('a','g')
    with db.Session() as s:assert s.get(db.Attempt,aid).lease>db.now()+timedelta(seconds=20)
    job=take('a','g');wrong=artifacts.put_json({'rows':[{'wrong':'value'}]})['id']
    with pytest.raises(ValueError):finish(job['attempt'],'g',wrong)
    with db.Session() as s:assert s.get(db.Attempt,aid).status=='running'



def test_terminal_failure_releases_other_reservations(client):
    from app.catalogue import CATALOGUE
    wid=create(client);register_worker('a','g',2,256,list(CATALOGUE));command(wid,'start')
    for _ in range(2):
        tick();job=take('a','g');artifact=execute(job['handler'],job['inputs'],job['parameters'])['id'];assert finish(job['attempt'],'g',artifact)
    tick()
    with db.Session.begin() as s:
        task=s.scalar(select(db.TaskRow).where(db.TaskRow.workflow_id==wid,db.TaskRow.task_id=='monthly'));task.status='failed'
    tick()
    with db.Session() as s:
        assert s.get(db.Workflow,wid).status=='failed'
        attempts=list(s.scalars(select(db.Attempt).join(db.TaskRow,db.Attempt.task_key==db.TaskRow.id).where(db.TaskRow.workflow_id==wid,db.Attempt.status.in_(['queued','running']))));assert not attempts

def test_cancel_and_completion_concurrency(client):
    from concurrent.futures import ThreadPoolExecutor
    from app.catalogue import CATALOGUE
    register_worker('a','g',1,128,list(CATALOGUE))
    for _ in range(5):
        wid=create(client);command(wid,'start');tick();job=take('a','g');assert job
        output=execute(job['handler'],job['inputs'],job['parameters'])['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            completed=pool.submit(finish,job['attempt'],'g',output)
            cancelled=pool.submit(command,wid,'cancel')
            assert completed.result(timeout=5) in (True,False);cancelled.result(timeout=5)
        with db.Session() as s:
            assert s.get(db.Workflow,wid).status=='cancelled'
            assert s.get(db.Attempt,job['attempt']).status in ('completed','cancelled')
            active=list(s.scalars(select(db.Attempt).join(db.TaskRow,db.Attempt.task_key==db.TaskRow.id).where(db.TaskRow.workflow_id==wid,db.Attempt.status.in_(['queued','running']))));assert not active

def test_api_preserves_explicit_parameter_and_rejects_ambiguity(client):
    wid=create(client)
    with db.Session() as s:dataset_id=s.get(db.Workflow,wid).dataset_id
    result=client.post('/plans',json={'dataset_id':dataset_id,'request':'Retail report with IQR multiplier 2','planner':'template'})
    assert result.status_code==200;body=result.json()
    assert next(t['parameters'] for t in body['plan']['tasks'] if t['handler']=='flag_unusual_amounts')['iqr_multiplier']==2
    assert body['planner']['explicit_parameter_source']=='request'
    rejected=client.post('/plans',json={'dataset_id':dataset_id,'request':'Retail report with IQR multiplier 2 or 3','planner':'template'})
    assert rejected.status_code==422
