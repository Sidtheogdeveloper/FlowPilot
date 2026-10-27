from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.api import app
from app import models as db
from app.auth import COOKIE, digest
from test_runtime import client

PASSWORD='A synthetic testing passphrase 123!'

def other():
    c=TestClient(app)
    response=c.post('/auth/register',json={'name':'Other Analyst','email':'other@example.org','password':PASSWORD})
    assert response.status_code==201,response.text
    c.headers['X-FlowPilot-CSRF']=response.json()['csrf_token']
    return c

def test_anonymous_boundary(client):
    anonymous=TestClient(app)
    assert anonymous.get('/health').status_code==200
    for path in ['/datasets','/workflows','/workers','/auth/me','/openapi.json']:
        assert anonymous.get(path).status_code==401
    assert anonymous.post('/datasets').status_code==401

def test_password_and_cookie_storage(client):
    raw=client.cookies.get(COOKIE)
    with db.Session() as s:
        user=s.scalar(select(db.User))
        session=s.scalar(select(db.UserSession))
        assert user.password_hash.startswith('$argon2id$') and PASSWORD not in user.password_hash
        assert session.token_hash==digest(raw) and raw!=session.token_hash
    c=TestClient(app)
    response=c.post('/auth/login',json={'email':'TEST@example.org','password':PASSWORD})
    assert response.status_code==200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']

def test_csrf_and_cross_origin(client):
    assert client.post('/auth/logout',headers={'X-FlowPilot-CSRF':''}).status_code==403
    assert client.post('/auth/logout',headers={'Origin':'https://attacker.invalid'}).status_code==403
    c=TestClient(app)
    assert c.post('/auth/login',headers={'Origin':'https://attacker.invalid'},json={'email':'test@example.org','password':PASSWORD}).status_code==403
    assert client.get('/auth/me').status_code==200

def test_dataset_workflow_artifact_isolation(client):
    first=client.post('/datasets',files={'file':('scores.csv',b'class,score\nA,2\nB,3\n','text/csv')}).json()
    flow=client.post('/plans',json={'dataset_id':first['id'],'request':'overview'}).json()
    c=other()
    assert c.get('/datasets').json()==[] and c.get('/workflows').json()==[]
    for path in ['/datasets/'+first['id']+'/profile','/workflows/'+flow['id'],'/artifacts/'+first['artifact']]:
        assert c.get(path).status_code==404
    assert c.post('/workflows/'+flow['id']+'/start').status_code==404
    assert c.post('/plans',json={'dataset_id':first['id'],'request':'overview'}).status_code==404
    assert client.get('/artifacts/'+first['artifact']).status_code==200
    same=c.post('/datasets',files={'file':('copy.csv',b'class,score\nA,2\nB,3\n','text/csv')}).json()
    assert same['artifact']==first['artifact']
    assert c.get('/artifacts/'+same['artifact']).status_code==200
    assert len(c.get('/datasets').json())==1

def test_clarifications_are_private(client,monkeypatch):
    from app.tabular import Clarification
    def ambiguous(*args,**kwargs):raise Clarification('Which score?', ['t0.c1'])
    monkeypatch.setattr('app.table_planning.propose_table',ambiguous)
    d=client.post('/datasets',files={'file':('scores.csv',b'score\n2\n3\n')}).json()
    p=client.post('/plans',json={'dataset_id':d['id'],'request':'analyse score','planner':'huggingface'}).json()
    assert p['state']=='clarification_required'
    c=other()
    assert c.get('/clarifications/'+p['clarification_id']).status_code==404
    assert c.post('/clarifications/'+p['clarification_id']+'/answer',json={'answer':'t0.c0'}).status_code==409

def test_session_logout_and_idle_expiry(client):
    raw=client.cookies.get(COOKIE)
    assert client.post('/auth/logout').status_code==200
    client.cookies.set(COOKIE,raw,domain='testserver.local',path='/')
    assert client.get('/auth/me').status_code==401
    r=client.post('/auth/login',json={'email':'test@example.org','password':PASSWORD})
    assert r.status_code==200
    with db.Session.begin() as s:
        row=s.scalar(select(db.UserSession).where(db.UserSession.token_hash==digest(client.cookies.get(COOKIE))))
        row.last_seen=db.now()-timedelta(hours=2)
    assert client.get('/auth/me').status_code==401

def test_sessions_owned_and_revoked(client):
    mine=client.get('/auth/sessions').json()[0]['id']
    c=other()
    assert c.delete('/auth/sessions/'+mine).status_code==404
    assert client.post('/auth/logout-all').status_code==200
    assert client.get('/datasets').status_code==401

def test_login_throttle_and_validation(client):
    c=TestClient(app)
    assert c.post('/auth/register',json={'email':'a@example.org','name':'A','password':'short'}).status_code==422
    for _ in range(10):
        assert c.post('/auth/login',json={'email':'test@example.org','password':'wrong'}).status_code==401
    assert c.post('/auth/login',json={'email':'test@example.org','password':PASSWORD}).status_code==429

def test_legacy_records_not_claimed(client):
    with db.Session.begin() as s:s.add(db.Dataset(id='legacy-unassigned',filename='private.csv',artifact='not-a-reference',rows=0))
    assert client.get('/datasets').json()==[]
    assert other().get('/datasets').json()==[]

def test_worker_outputs_are_private(client):
    from test_table_runtime import complete
    d=client.post('/datasets',files={'file':('scores.csv',b'class,score\nA,2\nB,3\n')}).json()
    p=client.post('/plans',json={'dataset_id':d['id'],'request':'overview'}).json()
    flow=complete(client,p['id'])
    url=flow['report_url'].replace('/api','',1)
    assert client.get(url).status_code==200
    assert other().get(url).status_code==404
