"""Real separate-process failure exercise. Uses dedicated workflow_test database."""
import os,sys,subprocess,time,json
from pathlib import Path
from fastapi.testclient import TestClient
from app.api import app
from app import models as db
from sqlalchemy import select,text

def main():
    out=Path('evidence');out.mkdir(exist_ok=True)
    if not os.environ.get('DATABASE_URL','').endswith('/workflow_test'):raise ValueError('Dedicated workflow_test database required')
    db.init_db()
    with db.Session.begin() as s:s.execute(text('TRUNCATE pending_plans,events,attempts,tasks,workflows,datasets,workers,artifact_grants,user_sessions,account_tokens,auth_throttles,users RESTART IDENTITY CASCADE'))
    env=os.environ.copy();env['WORKER_ID']='process-killed';env['WORKER_CPU']='1';env['WORKER_MEMORY_MB']='512'
    worker=subprocess.Popen([sys.executable,'-m','app.worker'],env=env,stdout=subprocess.DEVNULL,stderr=(out/'worker-killed.log').open('w'))
    replacement=None
    try:
        with TestClient(app) as c:
            registered=c.post('/auth/register',json={'name':'Benchmark Test','email':'benchmark@example.invalid','password':'Synthetic benchmark passphrase 2026!'})
            registered.raise_for_status()
            c.headers['X-FlowPilot-CSRF']=registered.json()['csrf_token']
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if any(w['id']=='process-killed' for w in c.get('/workers').json()):break
                time.sleep(.05)
            rows=100000;data=('transaction_id,timestamp,product_id,quantity,unit_price\n'+''.join(f'{i},2026-01-01,p{i%20},2,10\n' for i in range(rows))).encode()
            dataset=c.post('/datasets',files={'file':('process.csv',data)}).json()
            plan=c.post('/plans',json={'dataset_id':dataset['id'],'request':'retail report','policy':'bounded'}).json();wid=plan['id'];c.post('/workflows/'+wid+'/start')
            deadline=time.monotonic()+30;killed=None
            while time.monotonic()<deadline:
                detail=c.get('/workflows/'+wid).json()
                running=[a for t in detail['tasks'] for a in t['attempts'] if a['status']=='running' and a['worker']=='process-killed']
                if running:
                    killed=running[0]['id'];worker.kill();worker.wait();break
                time.sleep(.005)
            if not killed:raise RuntimeError('Did not catch running worker attempt for real kill')
            env['WORKER_ID']='process-replacement'
            replacement=subprocess.Popen([sys.executable,'-m','app.worker'],env=env,stdout=subprocess.DEVNULL,stderr=(out/'worker-replacement.log').open('w'))
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                detail=c.get('/workflows/'+wid).json()
                if detail['status'] in ('completed','failed'):break
                time.sleep(.1)
            assert detail['status']=='completed',detail
            assert any(e['kind']=='attempt_expired' and e['data']['attempt']==killed for e in detail['events'])
            assert any(a['worker']=='process-replacement' and a['status']=='completed' for t in detail['tasks'] for a in t['attempts'])
            (out/'process_failure.json').write_text(json.dumps(detail,indent=2));print('Real worker process kill, lease expiry, replacement retry and CSV-to-report: PASS')
    finally:
        for p in (worker,replacement):
            if p and p.poll() is None:p.terminate();p.wait(10)
if __name__=='__main__':main()
