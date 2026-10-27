"""Acceptance checks for the isolated compose.verify.yaml installation."""
from pathlib import Path
import hashlib
import json
import subprocess
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ['docker', 'compose', '--env-file', '.runtime/docker.verify.env', '-p', 'workflow-phase2-verify', '-f', 'compose.yaml', '-f', 'compose.verify.yaml']
client = httpx.Client(base_url='http://127.0.0.1:18000', trust_env=False, timeout=180)
request = 'Clean the transactions, calculate monthly sales and product performance, flag unusually large transactions using IQR multiplier 2, and generate a report.'
evidence = {'project': 'workflow-phase2-verify', 'api_port': 18000, 'model_port': 18001, 'runs': []}

def compose(*args):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, capture_output=True, text=True).stdout

def get_flow(wid):
    response = client.get('/workflows/' + wid)
    response.raise_for_status()
    return response.json()

def create(policy, planner='template', data=None):
    content = data if data is not None else (ROOT / 'samples/retail_transactions.csv').read_bytes()
    result = client.post('/datasets', files={'file': ('retail.csv', content, 'text/csv')})
    result.raise_for_status()
    dataset = result.json()['id']
    response = client.post('/plans', json={'dataset_id': dataset, 'request': request, 'planner': planner, 'policy': policy})
    response.raise_for_status()
    flow = response.json()
    assert next(t for t in flow['plan']['tasks'] if t['handler']=='flag_unusual_amounts')['parameters']['iqr_multiplier']==2
    return flow['id'], dataset

def wait(wid, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        flow = get_flow(wid)
        if flow['status'] in ('completed', 'failed', 'cancelled'):
            assert flow['status']=='completed', flow
            assert len(flow['tasks'])==6 and all(t['status']=='completed' for t in flow['tasks'])
            return flow
        time.sleep(.1)
    raise AssertionError('Workflow completion deadline exceeded')

def outputs(flow, check_fixture=True):
    hashes, decoded = {}, {}
    for task in flow['tasks']:
        result = client.get('/artifacts/' + task['artifact'])
        result.raise_for_status()
        hashes[task['handler']] = hashlib.sha256(result.content).hexdigest()
        if task['handler']=='build_report':
            assert 'Retail analysis report' in result.text
        else:
            decoded[task['handler']] = result.json()
    if check_fixture:
        assert len(decoded['clean_transactions']['rows'])==5
        assert len(decoded['clean_transactions']['rejected'])==3
        assert [r['revenue'] for r in decoded['monthly_sales']['rows']]==[50.0,590.0]
        flags=decoded['flag_unusual_amounts']['rows']
        assert all(r['threshold']==120 for r in flags)
        assert [r['transaction_id'] for r in flags if r['unusual']]==['T005']
    return hashes

baseline = None
for policy in ['greedy', 'heft', 'bounded']:
    wid, dataset = create(policy)
    client.post('/workflows/'+wid+'/start').raise_for_status()
    flow = wait(wid)
    hashes = outputs(flow)
    if baseline is None: baseline = hashes
    assert hashes == baseline
    evidence['runs'].append({'id':wid, 'policy':policy, 'planner':flow['planner'], 'metrics':flow['metrics'], 'workers':sorted({a['worker'] for t in flow['tasks'] for a in t['attempts']}), 'output_hashes':hashes})
    print('PASS: container workflow and verified downloads:', policy, flush=True)

negative = client.post('/plans', json={'dataset_id':dataset, 'request':'Predict next month sales and email the forecast.', 'planner':'huggingface', 'policy':'bounded'})
assert negative.status_code==422
evidence['unsupported_request_status'] = 422

deadline=time.monotonic()+300
while time.monotonic()<deadline:
    try:
        health=httpx.get('http://127.0.0.1:18001/health', trust_env=False, timeout=3)
        if health.status_code==200:
            evidence['container_model_health']=health.json()
            break
    except httpx.HTTPError: pass
    time.sleep(2)
else: raise AssertionError('Container model did not become ready')
wid, dataset=create('bounded','huggingface')
client.post('/workflows/'+wid+'/start').raise_for_status()
flow=wait(wid)
assert outputs(flow)==baseline
evidence['runs'].append({'id':wid,'policy':'bounded','planner':flow['planner'],'metrics':flow['metrics'],'output_hashes':baseline})
print('PASS: container-hosted model planning, requested parameter2, and complete report',flush=True)

compose('restart','database','api','worker_a','worker_b','worker_c')
deadline=time.monotonic()+60
while time.monotonic()<deadline:
    try:
        if client.get('/health').status_code==200: break
    except httpx.HTTPError: pass
    time.sleep(.5)
else: raise AssertionError('API did not recover after restart')
retained=get_flow(wid)
assert retained['status']=='completed' and outputs(retained)==baseline
evidence['restart_persistence']={'workflow':wid,'status':'completed','verified_artifacts':6,'restarted_services':['database','api','worker_a','worker_b','worker_c']}
print('PASS: container restart preserved workflow and six downloadable artifacts',flush=True)

# Kill an actual worker with an in-flight large-data attempt, then let the other worker recover it.
payload=b'transaction_id,timestamp,product_id,quantity,unit_price\n'+b''.join(f'R{i},2026-01-01,P001,1,10\n'.encode() for i in range(150000))
failure_id,_=create('greedy',data=payload)
client.post('/workflows/'+failure_id+'/start').raise_for_status()
deadline=time.monotonic()+30
victim=None
while time.monotonic()<deadline:
    current=get_flow(failure_id)
    victim=next((a for t in current['tasks'] for a in t['attempts'] if a['worker']=='worker-a' and a['status']=='running'),None)
    if victim:
        compose('kill','-s','SIGKILL','worker_a')
        break
    if current['status'] in ('completed','failed'): break
    time.sleep(.02)
assert victim is not None, 'No in-flight worker-a attempt observed for kill exercise'
try:
    recovered=wait(failure_id,180)
    outputs(recovered,False)
    expired=[a for t in recovered['tasks'] for a in t['attempts'] if a['status']=='expired']
    assert expired, 'Killed attempt must expire and retry; completion alone is insufficient recovery evidence'
    evidence['worker_failure']={'workflow':failure_id,'killed_service':'worker_a','observed_attempt':victim,'expired_attempts':expired,'final_status':recovered['status'],'tasks':recovered['tasks'],'metrics':recovered['metrics']}
    print('PASS: killed container worker expired, retried on surviving worker, and report completed',flush=True)
finally:
    compose('up','-d','worker_a')
evidence['container_status']=compose('ps','--format','json').splitlines()
(ROOT/'evidence/docker_execution.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print('Saved evidence/docker_execution.json',flush=True)
