"""Live model → persisted DAG → Docker workers → independently checked answers."""
import json
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]

def run(c,preview):
    assert preview['state']=='ready',preview
    identity=preview['id'];c.post('/workflows/'+identity+'/start').raise_for_status();deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        result=c.get('/workflows/'+identity).json()
        if result['status'] in ('completed','failed'):break
        time.sleep(.05)
    assert result['status']=='completed',result
    return result

def summary_rows(c,flow):
    task=next(t for t in flow['tasks'] if t['handler']=='table_aggregate')
    response=c.get('/artifacts/'+task['artifact']);response.raise_for_status();return response.json()['rows']

def main():
    evidence={}
    with httpx.Client(base_url='http://127.0.0.1:18080/api',timeout=240) as c:
        def upload(name):
            response=c.post('/datasets',files={'file':(name,(ROOT/'samples/general_tables'/name).read_bytes())});response.raise_for_status();return response.json()['id']
        education=upload('education.csv')
        response=c.post('/plans',json={'dataset_id':education,'generic':True,'planner':'huggingface','request':'Calculate the mean of exam_score grouped by classroom.'});response.raise_for_status()
        original=run(c,response.json());rows=summary_rows(c,original)
        values={r['t0.c0']:next(v for k,v in r.items() if k!='t0.c0') for r in rows}
        assert values=={'A':68.0,'B':89.5},values
        evidence['education']={'workflow':original['id'],'values':values,'planner':original['planner'],'tasks':original['tasks']}
        response=c.post('/workflows/'+original['id']+'/followups',json={'request':'Use median instead of mean, retaining the classroom grouping.'});response.raise_for_status()
        revision=run(c,response.json());assert revision['revision']==2 and len(revision['turns'])==2
        assert any(m['function']=='median' for step in revision['planner']['intent']['steps'] if step['operation']=='aggregate' for m in step['parameters']['metrics'])
        evidence['followup']={'workflow':revision['id'],'revision':revision['revision'],'rows':summary_rows(c,revision)}
        purchases,customers=upload('purchases.csv'),upload('customers.csv')
        response=c.post('/plans',json={'dataset_ids':[purchases,customers],'planner':'huggingface','request':'Inner join purchases to customers on customer_id, then sum net_amount by region.'});response.raise_for_status();pending=response.json()
        assert pending['state']=='clarification_required',pending
        response=c.post('/clarifications/'+pending['clarification_id']+'/answer',json={'answer':'confirm'});response.raise_for_status()
        joined=run(c,response.json());rows=summary_rows(c,joined)
        values={r['t1.c1']:next(v for k,v in r.items() if k!='t1.c1') for r in rows}
        assert values=={'North':365.0,'South':80.0},values
        evidence['joined']={'workflow':joined['id'],'values':values,'confirmation':pending,'planner':joined['planner'],'tasks':joined['tasks']}
    (ROOT/'evidence/table_live_analysis.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print('Live unseen education analysis, median follow-up, confirmed joined-table analysis: PASS',flush=True)
if __name__=='__main__':main()
