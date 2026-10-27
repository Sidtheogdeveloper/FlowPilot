import os
from statistics import median
from sqlalchemy import select
from . import models as db
# Cold-start per-row cost priors, overwritten by accepted runtime samples.
COST={'validate_dataset':.00002,'clean_transactions':.00008,'filter_transactions':.00001,'monthly_sales':.00002,'product_summary':.00001,'flag_unusual_amounts':.00002,'build_report':.000015}
from .catalogue import CATALOGUE
COST.update({k:.00004 for k in CATALOGUE if k.startswith('table_')})
COST.update({k:.0001 for k in CATALOGUE if k.startswith('document_')})

def estimates(session,rows):
    if os.getenv('ESTIMATE_HISTORY','1')=='0':return {k:max(.01,rows*v) for k,v in COST.items()}
    samples={k:[] for k in COST}
    query=(select(db.Attempt,db.TaskRow,db.Workflow,db.Dataset,db.Worker)
           .join(db.TaskRow,db.TaskRow.id==db.Attempt.task_key)
           .join(db.Workflow,db.Workflow.id==db.TaskRow.workflow_id)
           .join(db.Dataset,db.Dataset.id==db.Workflow.dataset_id)
           .join(db.Worker,db.Worker.id==db.Attempt.worker_id)
           .where(db.Attempt.status=='completed').order_by(db.Attempt.finished.desc()).limit(100))
    # Including owner-scoped Workflow/Dataset in the join excludes foreign history.
    for a,t,f,d,w in session.execute(query):
        if not a.started or not a.finished:continue
        n=(f.planner or {}).get('execution',{}).get('row_estimates',{}).get(t.task_id,d.rows)
        samples.setdefault(t.spec['handler'],[]).append((a.finished-a.started).total_seconds()*w.speed/max(n,1))
    return {k:max(.01,rows*(median(samples[k]) if samples[k] else v)) for k,v in COST.items()}


def task_estimates(session,flow):
    dataset=session.get(db.Dataset,flow.dataset_id)
    base=estimates(session,dataset.rows)
    counts=(flow.planner or {}).get('execution',{}).get('row_estimates',{})
    return {t['id']:max(.01,base[t['handler']]*counts.get(t['id'],dataset.rows)/max(1,dataset.rows)) for t in flow.plan['tasks']}
