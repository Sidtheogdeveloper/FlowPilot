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
    recent=list(session.scalars(select(db.Attempt).where(db.Attempt.status=='completed').order_by(db.Attempt.finished.desc()).limit(100)))
    for a in recent:
        if not a.started or not a.finished:continue
        t=session.get(db.TaskRow,a.task_key);f=session.get(db.Workflow,t.workflow_id);d=session.get(db.Dataset,f.dataset_id);w=session.get(db.Worker,a.worker_id)
        samples[t.spec['handler']].append((a.finished-a.started).total_seconds()*w.speed/max(d.rows,1))
    return {k:max(.01,rows*(median(samples[k]) if samples[k] else v)) for k,v in COST.items()}

