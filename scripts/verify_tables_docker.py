"""Real local API + two Docker workers: overview, process failure and restart."""
import json
import subprocess
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
COMMAND=['docker','compose','--env-file','.runtime/docker.verify.env','-p','workflow-phase2-verify','-f','compose.yaml','-f','compose.verify.yaml','-f','compose.tables.yaml']
def compose(*args):subprocess.run(COMMAND+list(args),cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
def wait(c,identity,seconds=90):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        response=c.get('/workflows/'+identity);response.raise_for_status();value=response.json()
        if value['status'] in ('completed','failed'):return value
        time.sleep(.05)
    raise RuntimeError('Workflow did not finish')

def main():
    evidence={}
    with httpx.Client(base_url='http://127.0.0.1:18080/api',timeout=180) as c:
        raw=b'device,reading,site\n'+b''.join(f'd{i%30},{i%100},s{i%4}\n'.encode() for i in range(60000))
        upload=c.post('/datasets',files={'file':('sensor_unseen.csv',raw)});upload.raise_for_status();dataset=upload.json()
        preview=c.post('/plans',json={'dataset_id':dataset['id'],'generic':True,'request':'overview'});preview.raise_for_status();identity=preview.json()['id']
        compose('stop','worker_b')
        try:
            c.post('/workflows/'+identity+'/start').raise_for_status();victim=None;deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                detail=c.get('/workflows/'+identity).json()
                running=[a for t in detail['tasks'] for a in t['attempts'] if a['worker']=='worker-a' and a['status']=='running']
                if running:
                    victim=running[0]['id'];compose('kill','-s','SIGKILL','worker_a');break
                time.sleep(.005)
            if not victim:raise AssertionError('Could not observe running generic task')
            compose('start','worker_b');detail=wait(c,identity)
            assert detail['status']=='completed',detail
            assert any(e['kind']=='attempt_expired' and e['data']['attempt']==victim for e in detail['events'])
            evidence['worker_failure']={'workflow_id':identity,'expired_attempt':victim,'status':detail['status'],'tasks':detail['tasks']}
        finally:compose('start','worker_a','worker_b')
        references=[t['artifact'] for t in detail['tasks']]
        for reference in references:c.get('/artifacts/'+reference).raise_for_status()
        compose('restart','database','api','worker_a','worker_b')
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                if c.get('/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.5)
        restored=c.get('/workflows/'+identity);restored.raise_for_status()
        assert restored.json()['status']=='completed'
        for reference in references:c.get('/artifacts/'+reference).raise_for_status()
        evidence['restart']={'workflow_id':identity,'artifacts_verified':len(references),'status':'completed'}
        evidence['profiles']={'rows':dataset['rows'],'columns':dataset['profile']['columns']}
    (ROOT/'evidence'/'table_docker_execution.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print('Generic sensor workflow: real worker kill, lease retry, artifact verification and restart persistence PASS')
if __name__=='__main__':main()
