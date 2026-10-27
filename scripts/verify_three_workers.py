"""Non-destructive live smoke test: all three deployed worker processes accept tasks."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,time
import httpx

ROOT=Path(__file__).resolve().parents[1]
def main():
    expected={'worker-a','worker-b','worker-c'}
    evidence={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'Live same-host three-worker smoke test; not a speedup benchmark','workflows':[]}
    with httpx.Client(base_url='http://127.0.0.1:18080/api',timeout=120) as c:
        from auth_client import sign_in
        sign_in(c)
        c.get('/health').raise_for_status()
        workers=c.get('/workers');workers.raise_for_status();roster=workers.json()
        live=[w for w in roster if w['id'] in expected and (datetime.now(timezone.utc)-datetime.fromisoformat(w['heartbeat'])).total_seconds()<20]
        assert {w['id'] for w in live}==expected,{'live_workers':[w['id'] for w in live]}
        evidence['workers']=[{k:w[k] for k in ('id','cpu','memory_mb','heartbeat')} for w in live]
        rows=30000
        data=('device,reading,site\n'+''.join(f'd{i%30},{i%100},s{i%4}\n' for i in range(rows))).encode()
        r=c.post('/datasets',files={'file':('three_worker_smoke_sensor.csv',data)});r.raise_for_status();dataset=r.json()
        assert dataset['rows']==rows
        flows=[]
        for _ in range(9):
            r=c.post('/plans',json={'dataset_id':dataset['id'],'generic':True,'request':'overview','policy':'bounded'});r.raise_for_status();flows.append(r.json()['id'])
        for flow in flows:c.post('/workflows/'+flow+'/start').raise_for_status()
        deadline=time.monotonic()+120;pending=set(flows);accepted=Counter()
        while pending and time.monotonic()<deadline:
            for flow in list(pending):
                r=c.get('/workflows/'+flow);r.raise_for_status();detail=r.json()
                if detail['status']=='failed':raise AssertionError({'failed_workflow':flow,'tasks':detail['tasks']})
                if detail['status']!='completed':continue
                ids=[]
                for task in detail['tasks']:
                    assert task['status']=='completed',task
                    reference=task['artifact'];response=c.get('/artifacts/'+reference);response.raise_for_status()
                    assert hashlib.sha256(response.content).hexdigest()==reference.split('.')[0]
                    ids.append(reference)
                    for attempt in task['attempts']:
                        if attempt['status']=='completed':accepted[attempt['worker']]+=1
                evidence['workflows'].append({'id':flow,'status':'completed','tasks':len(detail['tasks']),'artifacts_verified':ids})
                pending.remove(flow)
            if pending:time.sleep(.1)
        assert not pending,{'timed_out':sorted(pending)}
        assert expected.issubset(accepted),{'accepted_attempts':dict(accepted)}
        evidence['accepted_attempts_by_worker']=dict(accepted)
        evidence['dataset_rows']=rows;evidence['status']='passed'
    path=ROOT/'evidence/three_worker_verification_2026_10_22.json'
    path.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps({'status':'passed','workflows':len(flows),'accepted_attempts':dict(accepted),'evidence':str(path)}))
if __name__=='__main__':main()
