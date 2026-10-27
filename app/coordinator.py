import uuid,threading,time
from datetime import timedelta
from sqlalchemy import select,func
from . import models as db,artifacts
from .scheduling import schedule
from .catalogue import CATALOGUE
LEASE_SECONDS=30;MAX_ATTEMPTS=3

def event(s,wid,kind,data):s.add(db.Event(workflow_id=wid,kind=kind,data=data))
def register_worker(id,generation,cpu,memory_mb,handlers,speed=1):
    import math
    if cpu<1 or memory_mb<128 or not math.isfinite(speed) or speed<=0 or not set(handlers)<=set(CATALOGUE):raise ValueError('Invalid worker capabilities')
    with db.Session.begin() as s:
        w=s.get(db.Worker,id,with_for_update=True)
        if w is None:s.add(db.Worker(id=id,generation=generation,cpu=cpu,memory_mb=memory_mb,handlers=handlers,speed=speed))
        else:
            w.generation=generation;w.cpu=cpu;w.memory_mb=memory_mb;w.handlers=handlers;w.speed=speed;w.heartbeat=db.now()
def heartbeat(id,generation):
    with db.Session.begin() as s:
        w=s.get(db.Worker,id,with_for_update=True)
        if not w or w.generation!=generation:return False
        w.heartbeat=db.now()
        for a in s.scalars(select(db.Attempt).where(db.Attempt.worker_id==id,db.Attempt.worker_generation==generation,db.Attempt.status.in_(['queued','running']))):a.lease=db.now()+timedelta(seconds=LEASE_SECONDS)
    return True

def tick():
    with db.Session.begin() as s:
        # Single durable coordinator: transaction advisory lock also fences accidental duplicate loops.
        s.execute(__import__('sqlalchemy').text('SELECT pg_advisory_xact_lock(718234)'))
        workers=list(s.scalars(select(db.Worker).order_by(db.Worker.id).with_for_update()))
        active=list(s.scalars(select(db.Attempt).where(db.Attempt.status.in_(['queued','running'])).with_for_update()))
        now=db.now()
        for a in active:
            w=next((w for w in workers if w.id==a.worker_id),None)
            if a.lease<now or not w or w.generation!=a.worker_generation or w.heartbeat<now-timedelta(seconds=LEASE_SECONDS):
                a.status='expired';a.finished=now
                t=s.get(db.TaskRow,a.task_key,with_for_update=True);t.status='pending' if a.generation<MAX_ATTEMPTS else 'failed'
                event(s,t.workflow_id,'attempt_expired',{'attempt':a.id,'task':t.task_id})
        s.flush()
        active=[a for a in active if a.status in ('queued','running')]
        for flow in s.scalars(select(db.Workflow).where(db.Workflow.status=='running').order_by(db.Workflow.created).with_for_update()):
            tasks=list(s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==flow.id).with_for_update()))
            by={t.task_id:t for t in tasks}
            if any(t.status=='failed' for t in tasks):
                flow.status='failed';flow.finished=now
                for other in tasks:
                    if other.status not in ('completed','failed'):other.status='cancelled';other.generation+=1
                for a in active:
                    if a.task_key in {t.id for t in tasks} and a.status in ('queued','running'):a.status='cancelled';a.finished=now
                event(s,flow.id,'workflow_failed',{'reason':'Retry limit reached; remaining reservations released'})
                continue
            if all(t.status=='completed' for t in tasks):flow.status='completed';flow.finished=now;event(s,flow.id,'workflow_completed',{});continue
            ready=[t for t in tasks if t.status=='pending' and all(by[d].status=='completed' for d in t.spec['depends_on'])]
            from .estimation import estimates
            predicted=estimates(s,s.get(db.Dataset,flow.dataset_id).rows)
            snapshot=[]
            for w in workers:
                if w.heartbeat<now-timedelta(seconds=LEASE_SECONDS):continue
                reservations=[s.get(db.TaskRow,a.task_key).spec['resources'] for a in active if a.worker_id==w.id and a.status in ('queued','running')]
                snapshot.append({'id':w.id,'cpu':w.cpu-sum(r['cpu'] for r in reservations),'memory_mb':w.memory_mb-sum(r['memory_mb'] for r in reservations),'handlers':w.handlers,'speed':w.speed,'queue_seconds':sum(max(.001,predicted.get(s.get(db.TaskRow,a.task_key).spec['handler'],1)/w.speed-((now-a.started).total_seconds() if a.started else 0)) for a in active if a.worker_id==w.id and a.status in ('queued','running'))})
            import networkx as nx
            g=nx.DiGraph();g.add_nodes_from(by)
            for t in tasks:
                for dep in t.spec['depends_on']:g.add_edge(dep,t.task_id)
            ranks={}
            for key in reversed(list(nx.topological_sort(g))):ranks[key]=predicted[by[key].spec['handler']]+max((ranks[n] for n in g.successors(key)),default=0)
            order={t['id']:i for i,t in enumerate(flow.plan['tasks'])}
            ordered=sorted(ready,key=lambda t:(-ranks[t.task_id],t.task_id)) if flow.policy in ('heft','bounded') else sorted(ready,key=lambda t:order[t.task_id])
            specs=[dict(t.spec,rank=ranks[t.task_id],estimate=predicted[t.spec['handler']]) for t in ordered]
            decision=schedule(specs,snapshot,flow.policy)
            if ready:event(s,flow.id,'scheduling',decision.metrics)
            for tid,wid in decision.assignments.items():
                t=by[tid];w=next(w for w in workers if w.id==wid);t.generation+=1;t.status='queued'
                a=db.Attempt(id=str(uuid.uuid4()),task_key=t.id,generation=t.generation,worker_id=wid,worker_generation=w.generation,lease=now+timedelta(seconds=LEASE_SECONDS));s.add(a);active.append(a)
                event(s,flow.id,'reserved',{'task':tid,'attempt':a.id,'worker':wid})
            s.flush()

def claim(worker_id,generation):
    with db.Session.begin() as s:
        s.execute(__import__('sqlalchemy').text('SELECT pg_advisory_xact_lock(718234)'))
        a=s.scalar(select(db.Attempt).where(db.Attempt.worker_id==worker_id,db.Attempt.worker_generation==generation,db.Attempt.status=='queued').order_by(db.Attempt.created).with_for_update(skip_locked=True))
        if not a:return None
        t=s.get(db.TaskRow,a.task_key);flow=s.get(db.Workflow,t.workflow_id)
        if flow.status!='running':return None
        a.status='running';a.started=db.now();a.lease=db.now()+timedelta(seconds=LEASE_SECONDS);t.status='running'
        dataset=s.get(db.Dataset,flow.dataset_id)
        sources={x.task_id:x.artifact for x in s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==flow.id))}
        def resolve(source):
            if source=='$dataset':return dataset.artifact
            if source.startswith('$dataset:'):
                identity=source.split(':',1)[1]
                if identity not in (flow.planner or {}).get('dataset_ids',[]):raise ValueError('Dataset is not bound to workflow')
                bound=s.get(db.Dataset,identity)
                if not bound or not bound.table_ref:raise ValueError('Missing profiled table')
                return bound.table_ref
            return sources[source]
        return {'attempt':a.id,'owner_id':flow.owner_id,'handler':t.spec['handler'],'parameters':t.spec['parameters'],'inputs':{name:resolve(source) for name,source in t.spec['inputs'].items()}}

def finish(attempt_id,worker_generation,artifact=None,error=None):
    if artifact is not None:artifacts.read(artifact) # checksum and path validation before DB acceptance
    with db.Session.begin() as s:
        preview=s.get(db.Attempt,attempt_id)
        if not preview:return False
        w=s.get(db.Worker,preview.worker_id,with_for_update=True)
        a=s.get(db.Attempt,attempt_id,with_for_update=True,populate_existing=True)
        t=s.get(db.TaskRow,a.task_key,with_for_update=True);flow=s.get(db.Workflow,t.workflow_id)
        if a.status!='running' or a.generation!=t.generation or a.worker_generation!=worker_generation or w.generation!=worker_generation or a.lease<db.now() or flow.status=='cancelled':return False
        a.finished=db.now()
        if error:
            a.status='failed';a.error=str(error)[:2000];t.status='pending' if a.generation<MAX_ATTEMPTS else 'failed'
            event(s,flow.id,'attempt_failed',{'task':t.task_id,'error':a.error})
        else:
            if not artifact:raise ValueError('Successful completion requires artifact')
            if CATALOGUE[t.spec['handler']]['output']=='report' and not artifact.endswith('.html'):raise ValueError('Report artifact must be HTML')
            if CATALOGUE[t.spec['handler']]['output']!='report':
                output=artifacts.read_json(artifact)
                from .result_contracts import validate_output
                validate_output(t.spec['handler'],output)
                if t.spec['handler'].startswith('table_'):
                    from .tabular import validate_generic_plan
                    from .contracts import Plan
                    expected=validate_generic_plan(Plan.model_validate(flow.plan))[t.task_id]
                    if output['schema']!=expected:raise ValueError('Result schema differs from validated task output')
            if flow.owner_id:
                from sqlalchemy.dialects.postgresql import insert
                s.execute(insert(db.ArtifactGrant).values(owner_id=flow.owner_id,artifact=artifact,size=len(artifacts.read(artifact))).on_conflict_do_nothing())
            a.status='completed';t.status='completed';t.artifact=artifact;event(s,flow.id,'accepted',{'task':t.task_id,'artifact':artifact})
        return True

def command(id,action):
    with db.Session.begin() as s:
        s.execute(__import__('sqlalchemy').text('SELECT pg_advisory_xact_lock(718234)'))
        list(s.scalars(select(db.Worker).order_by(db.Worker.id).with_for_update()))
        f=s.get(db.Workflow,id,with_for_update=True)
        if not f:raise KeyError(id)
        allowed={'start':('planned','running'),'pause':('running','paused'),'resume':('paused','running')}
        if action=='cancel':
            if f.status in ('completed','failed','cancelled'):raise ValueError('Workflow is terminal')
            f.status='cancelled';f.finished=db.now()
            for t in s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==id).with_for_update()):
                if t.status!='completed':t.generation+=1;t.status='cancelled'
            for a in s.scalars(select(db.Attempt).join(db.TaskRow,db.Attempt.task_key==db.TaskRow.id).where(db.TaskRow.workflow_id==id,db.Attempt.status.in_(['queued','running'])).with_for_update(of=db.Attempt)):a.status='cancelled';a.finished=db.now()
        else:
            old,new=allowed[action]
            if f.status!=old:raise ValueError('Invalid workflow transition')
            f.status=new
            if action=='start':f.started=db.now()
        event(s,id,action,{})

def loop(stop):
    while not stop.is_set():
        try:tick()
        except Exception:
            import logging;logging.exception('Coordinator tick failed')
        stop.wait(.1)
