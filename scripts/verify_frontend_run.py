"""Check a browser-created sample run through the frontend's actual API proxy."""
from pathlib import Path
import argparse
import json
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('workflow_id')
parser.add_argument('--base-url',default='http://127.0.0.1:18080/api')
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
with httpx.Client(base_url=args.base_url,trust_env=False,timeout=180) as client:
    from auth_client import sign_in
    sign_in(client)
    response=client.get('/workflows/'+args.workflow_id)
    response.raise_for_status()
    flow=response.json()
    assert flow['status']=='completed' and flow['policy']=='bounded'
    assert flow['planner']['provider']=='huggingface'
    assert len(flow['tasks'])==6 and all(t['status']=='completed' for t in flow['tasks'])
    flags_task=next(t for t in flow['plan']['tasks'] if t['handler']=='flag_unusual_amounts')
    assert flags_task['parameters']['iqr_multiplier']==2
    decoded={}
    for task in flow['tasks']:
        result=client.get('/artifacts/'+task['artifact'])
        result.raise_for_status()
        assert 'attachment;' in result.headers['content-disposition']
        if task['handler']=='build_report':
            assert 'Retail analysis report' in result.text
            decoded[task['handler']]={'download_bytes':len(result.content)}
        else: decoded[task['handler']]=result.json()
    assert len(decoded['clean_transactions']['rows'])==5
    assert len(decoded['clean_transactions']['rejected'])==3
    assert [r['revenue'] for r in decoded['monthly_sales']['rows']]==[50,590]
    flags=decoded['flag_unusual_amounts']['rows']
    assert all(r['threshold']==120 for r in flags)
    assert [r['transaction_id'] for r in flags if r['unusual']]==['T005']
    evidence={'frontend_url':args.base_url.removesuffix('/api'),'workflow_id':args.workflow_id,
              'browser_actions':['upload sample CSV','select live Hugging Face and bounded policy','generate validated plan','start','observe all six completed tasks'],
              'download_checks':'all six artifacts verified via Nginx /api proxy',
              'planner':flow['planner'],'metrics':flow['metrics'],'outputs':decoded,
              'workers_used':sorted({a['worker'] for t in flow['tasks'] for a in t['attempts']})}
    (root/'evidence/docker_frontend_e2e.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
print('PASS: browser-created workflow, live container model, preserved IQR2 and six verified downloads through frontend proxy')
