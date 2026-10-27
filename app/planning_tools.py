"""Owner-scoped estimates, explicit draft edits and version-to-version drift."""
import copy,json,math,uuid
from datetime import timedelta
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import select
from . import models as db,artifacts
from .product import workflow,dataset,revision,identity
router=APIRouter(prefix='/product',tags=['Planning tools'])

def drift_between(old,new):
    from .tables import ensure_metadata
    import numpy as np
    a=ensure_metadata(old);b=ensure_metadata(new);before={c['name']:c for c in a['columns']};after={c['name']:c for c in b['columns']}
    changes=[];distribution=[];old_rows=None;new_rows=None
    for name in before.keys()-after.keys():changes.append({'kind':'removed_column','column':name})
    for name in after.keys()-before.keys():changes.append({'kind':'added_column','column':name})
    for name in before.keys()&after.keys():
        x,y=before[name],after[name]
        if x['type']!=y['type']:changes.append({'kind':'type_changed','column':name,'before':x['type'],'after':y['type']})
        left=x['null_count']/max(1,a['rows']);right=y['null_count']/max(1,b['rows'])
        if abs(right-left)>=.10:changes.append({'kind':'missing_rate_changed','column':name,'before':left,'after':right,'percentage_points':round(100*(right-left),3)})
        if x['type']==y['type']=='number':
            if old_rows is None:old_rows=artifacts.read_json(old.table_ref)['rows'];new_rows=artifacts.read_json(new.table_ref)['rows']
            def vals(rows,key):
                return np.sort(np.array([float(r[key]) for r in rows if r.get(key) is not None and str(r[key]).strip()],dtype=float))
            v,w=vals(old_rows,x['id']),vals(new_rows,y['id'])
            if len(v)>=20 and len(w)>=20:
                points=np.union1d(v,w);ks=float(np.max(np.abs(np.searchsorted(v,points,side='right')/len(v)-np.searchsorted(w,points,side='right')/len(w))))
                distribution.append({'column':name,'ks_distance':ks,'flagged':ks>=.20,'before_count':len(v),'after_count':len(w)})
            else:distribution.append({'column':name,'flagged':False,'note':'At least 20 nonmissing numeric observations in each version are required'})
    return {'baseline_id':old.id,'current_id':new.id,'rows_before':a['rows'],'rows_after':b['rows'],'schema_and_quality_changes':sorted(changes,key=lambda c:(c['column'],c['kind'])),'distributions':sorted(distribution,key=lambda c:c['column']),'flagged':bool(changes or any(c['flagged'] for c in distribution)),'method':'Heuristics: absolute missing-rate change >=10 percentage points; numeric empirical CDF (KS) distance >=0.20. Not a significance test or a judgement that changed data is wrong.'}

@router.get('/datasets/{key}/drift')
def drift(key:str,against:str|None=None):
    with db.Session.begin() as s:
        current=dataset(s,key);base=revision(s,current)
        if current.kind!='tabular':raise HTTPException(422,'Drift checks require tabular versions')
        if against:
            old=dataset(s,against)
            if revision(s,old).family_id!=base.family_id:raise HTTPException(422,'Compare versions from the same dataset family')
        else:
            old=s.scalar(select(db.Dataset).join(db.DatasetRevision,db.DatasetRevision.dataset_id==db.Dataset.id).where(db.DatasetRevision.family_id==base.family_id,db.DatasetRevision.revision<base.revision).order_by(db.DatasetRevision.revision.desc()).limit(1))
        return drift_between(old,current) if old else {'baseline_id':None,'flagged':False,'message':'This is the first version. Upload an updated version to check drift.'}

@router.get('/workflows/{key}/estimate')
def estimate(key:str):
    from .estimation import task_estimates
    import networkx as nx
    with db.Session() as s:
        f=workflow(s,key);tasks={t['id']:t for t in f.plan['tasks']};predicted=task_estimates(s,f)
        workers=list(s.scalars(select(db.Worker).where(db.Worker.heartbeat>db.now()-timedelta(seconds=30))))
        graph=nx.DiGraph();graph.add_nodes_from(tasks)
        for t in tasks.values():
            for dep in t['depends_on']:graph.add_edge(dep,t['id'])
        done=set(s.scalars(select(db.TaskRow.task_id).where(db.TaskRow.workflow_id==key,db.TaskRow.status=='completed')))
        available={w.id:0. for w in workers};finish={};blocked=[];details=[];serial=0.
        for tid in nx.topological_sort(graph):
            t=tasks[tid];eligible=[w for w in workers if t['handler'] in w.handlers and w.cpu>=t['resources']['cpu'] and w.memory_mb>=t['resources']['memory_mb']]
            start=max((finish[d] for d in t['depends_on']),default=0.)
            if tid in done:
                finish[tid]=0.;predicted[tid]=0.
                details.append({'id':tid,'handler':t['handler'],'estimated_seconds':0.,'memory_mb':t['resources']['memory_mb'],'cpu':t['resources']['cpu'],'eligible_workers':[],'accepted_output':True})
                continue
            if not eligible:blocked.append(tid);finish[tid]=start+predicted[tid]
            else:
                w=min(eligible,key=lambda w:max(start,available[w.id])+predicted[tid]/w.speed)
                finish[tid]=max(start,available[w.id])+predicted[tid]/w.speed;available[w.id]=finish[tid]
            serial+=predicted[tid]
            details.append({'id':tid,'handler':t['handler'],'estimated_seconds':round(predicted[tid],4),'memory_mb':t['resources']['memory_mb'],'cpu':t['resources']['cpu'],'eligible_workers':[w.id for w in eligible]})
        seconds=max(finish.values(),default=0.)
        return {'workflow_id':key,'estimated_seconds':None if blocked else round(seconds,3),'illustrative_range_seconds':None,'serial_work_seconds':round(serial,3),'largest_task_memory_mb':max(t['resources']['memory_mb'] for t in tasks.values()),'workers':len(workers),'blocked_tasks':blocked,'tasks':details,'partitioning':f.planner.get('execution',{}),'method':'Advisory resource-feasible list simulation of remaining tasks; accepted outputs are skipped; per-row priors or accepted historical medians. Memory is a declaration, not a measurement. No calibrated timing interval is available. Excludes model planning, queued workflows, startup and variable storage overhead.'}

@router.get('/workflows/{key}/editor')
def editor(key:str):
    from .tabular import compile_table_intent,validate_generic_plan
    from .contracts import Plan
    from .tables import ensure_metadata
    with db.Session.begin() as s:
        f=workflow(s,key)
        if f.planner.get('kind')!='tabular':raise HTTPException(422,'Visual editing currently supports table workflows')
        ds=[dataset(s,i) for i in f.planner['dataset_ids']]
        plan,_=compile_table_intent(f.planner['intent'],ds,f.planner.get('confirmations',[]));schemas=validate_generic_plan(Plan.model_validate(plan))
        return {'workflow_id':key,'editable':f.status=='planned','intent':f.planner['intent'],'steps':[dict(step,columns=schemas[step['sources'][0]],right_columns=schemas[step['sources'][1]] if len(step['sources'])==2 else {}) for step in f.planner['intent']['steps']],'sources':[{'id':'t'+str(i),'name':d.filename,'columns':schemas['t'+str(i)]} for i,d in enumerate(ds)],'partitioned':f.planner.get('execution',{}).get('enabled',True)}

class Change(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str
    parameters:dict
class FilterAddition(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source:str
    parameters:dict
class EditRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    changes:list[Change]=Field(default_factory=list,max_length=20)
    add_filter:FilterAddition|None=None
    confirm_joins:bool=False
    confirm_many:bool=False
    partitioned:bool=True

@router.post('/workflows/{key}/edit')
def edit_plan(key:str,p:EditRequest):
    from .api import chat_flows,detail
    from .tabular import compile_table_intent,Clarification
    from .table_planning import preflight
    from .partitioning import prepare_execution
    from .validator import validate_plan
    from .tables import ensure_metadata
    from .reuse import executor_version
    try:
        with db.Session.begin() as s:
            original=workflow(s,key);root=original.planner.get('chat_id') or original.id;s.get(db.Workflow,root,with_for_update=True)
            original=s.get(db.Workflow,key,with_for_update=True,populate_existing=True);flows=chat_flows(s,root)
            if original.status!='planned' or flows[-1].id!=key:raise HTTPException(409,'Only the latest unstarted preview can be edited')
            if original.planner.get('kind')!='tabular':raise HTTPException(422,'A table plan is required')
            ds=[dataset(s,i) for i in original.planner['dataset_ids']]
            if {d.id:d.profile_ref for d in ds}!=original.planner['profiles']:raise HTTPException(409,'Dataset profiles changed; create a new request')
            intent=copy.deepcopy(original.planner['intent']);by={t['id']:t for t in intent['steps']};seen=set();confirmations=list(original.planner.get('confirmations',[]))
            for change in p.changes:
                if change.id in seen or change.id not in by:raise ValueError('Unknown or duplicate edited step')
                seen.add(change.id);step=by[change.id]
                if step['operation'] not in ('filter','aggregate','join'):raise ValueError('Only filters, aggregation and joins are visually editable')
                step['parameters']=change.parameters
                if step['operation']=='join':
                    if not p.confirm_joins:raise ValueError('Confirm the edited join keys and semantics')
                    signature=json.dumps({k:change.parameters[k] for k in ('left_keys','right_keys','how')},sort_keys=True)
                    confirmations.append(signature)
                    if p.confirm_many:confirmations.append('many:'+signature)
            if p.add_filter:
                sources={'t'+str(i) for i in range(len(ds))}
                if p.add_filter.source not in sources:raise ValueError('Select a dataset source for the new filter')
                name='manual_filter'
                while name in by:name+='x'
                for step in intent['steps']:step['sources']=[name if v==p.add_filter.source else v for v in step['sources']]
                intent['steps'].insert(0,{'id':name,'operation':'filter','sources':[p.add_filter.source],'parameters':p.add_filter.parameters})
            plan,profiles=compile_table_intent(intent,ds,confirmations);question=original.planner['request']
            plan['tasks'][-1]['parameters']['question']=question+' (explicitly edited analysis; see workflow transformations)'
            validate_plan(plan);preflight(plan,ds);plan,execution=prepare_execution(s,plan,ds,p.partitioned)
            wid=str(uuid.uuid4());record=dict(original.planner,intent=intent,profiles=profiles,confirmations=confirmations,execution=execution,executor_version=executor_version(),revision=len(flows)+1,parent_workflow_id=key,manual_edits={'changes':[c.model_dump() for c in p.changes],'added_filter':p.add_filter.model_dump() if p.add_filter else None},request=question+' [edited plan]',chat_id=root)
            f=db.Workflow(id=wid,dataset_id=original.dataset_id,plan=plan,planner=record,policy=original.policy);s.add(f);s.flush()
            for t in plan['tasks']:s.add(db.TaskRow(id=wid+':'+t['id'],workflow_id=wid,task_id=t['id'],spec=t))
            original.status='cancelled';original.finished=db.now()
            for old in s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==key,db.TaskRow.status=='pending')):old.status='cancelled';old.generation+=1
            s.add(db.Event(workflow_id=original.id,kind='preview_superseded',data={'replacement':wid}))
            s.add(db.Event(workflow_id=wid,kind='plan_edited',data={'previous':key,'user':identity()}))
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc))
    return detail(wid)
