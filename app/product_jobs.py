"""Durable scheduled reruns and in-app completion notifications."""
import uuid,logging
from datetime import timedelta
from sqlalchemy import select,text
from . import models as db
from .tenancy import owner

def replay(s,base,use_latest=True,scheduled=False,reuse=True):
    from .api import chat_flows
    from .validator import validate_plan
    from .reuse import executor_version,reuse_completed
    from .table_planning import compile_table_intent,preflight
    from .document_analysis import compile_document_plan
    from .product import revision,root_id
    root=root_id(base);s.get(db.Workflow,root,with_for_update=True);flows=chat_flows(s,root);previous=flows[-1]
    if previous.status not in ('completed','failed','cancelled'):raise ValueError('This conversation has an active workflow; the rerun will wait')
    ids=(base.planner or {}).get('dataset_ids') or [base.dataset_id];datasets=[]
    for key in ids:
        d=s.get(db.Dataset,key)
        if not d:raise ValueError('A saved input is unavailable')
        if use_latest:
            current=revision(s,d)
            newest=s.scalar(select(db.Dataset).join(db.DatasetRevision,db.DatasetRevision.dataset_id==db.Dataset.id).where(db.DatasetRevision.family_id==current.family_id).order_by(db.DatasetRevision.revision.desc()).limit(1))
            d=newest or d
        datasets.append(d)
    question=base.planner.get('request') or 'Repeat saved analysis'
    record=dict(base.planner,dataset_ids=[d.id for d in datasets],request=question,chat_id=root,revision=len(flows)+1,parent_workflow_id=previous.id,executor_version=executor_version(),replayed_from=base.id,scheduled=scheduled)
    if record['kind']=='document':plan=compile_document_plan(datasets,question)
    else:
        plan,profiles=compile_table_intent(record['intent'],datasets,record.get('confirmations',[]))
        plan['tasks'][-1]['parameters']['question']=question;validate_plan(plan);preflight(plan,datasets);record['profiles']=profiles
    if record['kind']=='tabular':
        from .partitioning import prepare_execution
        plan,record['execution']=prepare_execution(s,plan,datasets,record.get('execution',{}).get('enabled',True))
    key=str(uuid.uuid4());flow=db.Workflow(id=key,dataset_id=datasets[0].id,plan=plan,planner=record,policy=base.policy,status='running',started=db.now());s.add(flow);s.flush()
    for task in plan['tasks']:s.add(db.TaskRow(id=key+':'+task['id'],workflow_id=key,task_id=task['id'],spec=task))
    s.flush()
    if reuse:reuse_completed(s,flow,previous)
    s.add(db.Event(workflow_id=key,kind='scheduled_rerun' if scheduled else 'manual_rerun',data={'base_workflow':base.id,'latest_dataset_versions':use_latest}))
    return flow

def tick():
    with db.Session.begin() as s:
        if not s.scalar(text('SELECT pg_try_advisory_xact_lock(718235)')):return
        due=list(s.scalars(select(db.Schedule).where(db.Schedule.active==True,db.Schedule.next_run<=db.now()).with_for_update(skip_locked=True)))
        for schedule in due:
            token=owner.set(schedule.owner_id)
            try:
                user=s.get(db.User,schedule.owner_id)
                if not user or not user.active:schedule.active=False;continue
                base=s.get(db.Workflow,schedule.workflow_id)
                if not base:raise ValueError('Saved workflow no longer exists')
                with s.begin_nested():
                    flow=replay(s,base,schedule.use_latest,scheduled=True,reuse=False)
                    s.add(db.ScheduleRun(id=str(uuid.uuid4()),schedule_id=schedule.id,workflow_id=flow.id))
                schedule.error=None;schedule.next_run=db.now()+timedelta(minutes=schedule.interval_minutes)
            except Exception as exc:
                message=str(exc)[:500];schedule.error=message
                if 'active workflow' in message:schedule.next_run=db.now()+timedelta(minutes=1)
                else:
                    schedule.active=False
                    s.add(db.Notification(id=str(uuid.uuid4()),owner_id=schedule.owner_id,title='Scheduled analysis needs attention',body=message,workflow_id=schedule.workflow_id))
            finally:owner.reset(token)
        for run in s.scalars(select(db.ScheduleRun).where(db.ScheduleRun.notified==False).with_for_update(skip_locked=True)):
            flow=s.get(db.Workflow,run.workflow_id)
            if flow and flow.status in ('completed','failed','cancelled'):
                schedule=s.get(db.Schedule,run.schedule_id)
                s.add(db.Notification(id=str(uuid.uuid4()),owner_id=schedule.owner_id,title='Scheduled analysis '+flow.status,body=(flow.planner or {}).get('request','Your saved analysis')[:300],workflow_id=flow.id))
                run.notified=True

def loop(stop):
    while not stop.is_set():
        try:tick()
        except Exception:logging.exception('Scheduled analysis check failed')
        stop.wait(5)
