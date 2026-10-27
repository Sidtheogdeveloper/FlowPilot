import json,os,time,threading,csv,random,argparse,platform
from pathlib import Path
from fastapi.testclient import TestClient
from .api import app
from .worker import run
from . import models as db
from sqlalchemy import text

def main(repeats=3,rows=10000,concurrent=1):
    if not os.environ.get('DATABASE_URL','').endswith('/workflow_test'):raise ValueError('Benchmark reset requires dedicated workflow_test database')
    os.environ['ESTIMATE_HISTORY']='0'
    db.init_db()
    with db.Session.begin() as s:s.execute(text('TRUNCATE pending_plans,events,attempts,tasks,workflows,datasets,workers,artifact_grants,user_sessions,account_tokens,auth_throttles,users RESTART IDENTITY CASCADE'))
    out=Path('evidence')/('single' if concurrent==1 else f'concurrent_{concurrent}');out.mkdir(parents=True,exist_ok=True);results=[]
    (out/'environment.json').write_text(json.dumps({'python':platform.python_version(),'platform':platform.platform(),'workers':'2 threads on same host; capacities2CPU/512MB and1CPU/256MB; configured speedpriors1/.8','history_enabled':False,'warmup_excluded':True,'policy_order':'rotated per seed','concurrent':concurrent},indent=2))
    with TestClient(app) as c:
        registered=c.post('/auth/register',json={'name':'Benchmark Test','email':'benchmark@example.invalid','password':'Synthetic benchmark passphrase 2026!'})
        registered.raise_for_status()
        c.headers['X-FlowPilot-CSRF']=registered.json()['csrf_token']
        stop=threading.Event();threads=[threading.Thread(target=run,args=(stop,'benchmark-'+str(i),2 if i==0 else 1,512 if i==0 else 256,1 if i==0 else .8),daemon=True) for i in range(2)]
        for t in threads:t.start()
        try:
            deadline=time.monotonic()+10
            while len(c.get('/workers').json())<2:
                if time.monotonic()>deadline:raise RuntimeError('Workers failed registration')
                time.sleep(.01)
            for seed in range(-1,repeats):
                rng=random.Random(seed);lines=['transaction_id,timestamp,product_id,quantity,unit_price']+[f'{i},2026-{rng.randrange(1,13):02d}-01,p{rng.randrange(20)},{rng.randrange(1,10)},{rng.randrange(1,100)}' for i in range(rows)]
                data=('\n'.join(lines)+'\n').encode();policies=['greedy','heft','bounded'];policies=policies[seed%3:]+policies[:seed%3];expected_hashes=None
                if seed==-1:policies=['greedy']
                for policy in policies:
                    dataset=c.post('/datasets',files={'file':('benchmark.csv',data)}).json();ids=[]
                    for _ in range(concurrent):
                        planned=c.post('/plans',json={'dataset_id':dataset['id'],'request':'clean monthly product unusual report','policy':policy}).json();ids.append(planned['id'])
                    groupstart=time.perf_counter()
                    for wid in ids:c.post('/workflows/'+wid+'/start')
                    deadline=time.monotonic()+180
                    while time.monotonic()<deadline:
                        details=[c.get('/workflows/'+wid).json() for wid in ids]
                        if all(r['status'] in ('completed','failed') for r in details):break
                        time.sleep(.02)
                    elapsed=time.perf_counter()-groupstart
                    if any(r['status']!='completed' for r in details):raise RuntimeError(details)
                    hashes=sorted((t['handler'],t['artifact']) for t in details[0]['tasks'])
                    if expected_hashes is not None and hashes!=expected_hashes:raise AssertionError('Analysis output mismatch across policies')
                    expected_hashes=hashes
                    if seed>=0:
                        (out/f'{policy}-{seed}.json').write_text(json.dumps(details,indent=2))
                        results.append({'seed':seed,'rows':rows,'concurrent':concurrent,'policy':policy,'group_wall_seconds':elapsed,'completion_seconds':sum(r['metrics']['completion_seconds'] for r in details)/len(details),'scheduling_ms':sum(r['metrics']['scheduling_ms'] for r in details),'outputs_equal':True})
        finally:
            stop.set()
            for t in threads:t.join(5)
    with (out/'benchmark.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    print(json.dumps(results,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repeats',type=int,default=3);p.add_argument('--rows',type=int,default=10000);p.add_argument('--concurrent',type=int,default=1);a=p.parse_args();main(a.repeats,a.rows,a.concurrent)
