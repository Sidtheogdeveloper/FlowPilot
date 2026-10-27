import uuid,threading,json,re,httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI,UploadFile,File,HTTPException,Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import select,func,or_
from . import models as db,artifacts
from .contracts import PlanRequest,FollowupRequest
from .catalogue import CATALOGUE
from .handlers import inspect_csv,COLUMNS
from .planner import propose
from .validator import validate_plan
from .coordinator import loop,command
@asynccontextmanager
async def lifespan(app):
    from .account_features import validate_deployment
    validate_deployment()
    from .product_jobs import loop as jobs_loop
    db.init_db();stop=threading.Event();thread=threading.Thread(target=loop,args=(stop,),daemon=True);thread.start()
    jobs=threading.Thread(target=jobs_loop,args=(stop,),daemon=True);jobs.start()
    yield
    stop.set();thread.join(5);jobs.join(6)
app=FastAPI(title='FlowPilot',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://localhost:4173'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
from .auth import AuthenticationMiddleware,router as account_router
app.add_middleware(AuthenticationMiddleware)
app.include_router(account_router)
from .account_features import router as account_features_router
app.include_router(account_features_router)
@app.get('/health')
def health():return {'status':'ok'}
@app.get('/catalogue')
def catalogue():return CATALOGUE

def dataset_info(dataset):
    from .tables import ensure_metadata
    description=artifacts.read_json(dataset.profile_ref) if dataset.kind=='document' else ensure_metadata(dataset)
    return {'id':dataset.id,'kind':dataset.kind,'filename':dataset.filename,'rows':dataset.rows,'columns':[c['name'] for c in description['columns']],'artifact':dataset.artifact,'profile_ref':dataset.profile_ref,'profile':description,'sheet':dataset.sheet}

def workflow_info(flow,dataset):
    record=flow.planner or {}
    request=record.get('request')
    title=record.get('chat_title') or (' '.join(request.split()) if isinstance(request,str) and request.strip() else (dataset.filename+' analysis' if dataset else 'Previous analysis'))
    return {'id':flow.id,'kind':record.get('kind','retail'),'status':flow.status,'policy':flow.policy,'created':flow.created.isoformat(),
            'dataset_id':flow.dataset_id,'dataset_filename':dataset.filename if dataset else None,
            'request':request,'title':title[:87]+'…' if len(title)>90 else title,
            'planner_mode':record.get('planner_mode') or ('huggingface' if record.get('provider')=='huggingface' else 'template'),
            'chat_id':record.get('chat_id') or flow.id,'revision':record.get('revision',1),
            'parent_workflow_id':record.get('parent_workflow_id'),'effective_request':record.get('effective_request',request)}

@app.get('/datasets')
def datasets():
    with db.Session.begin() as s:
        query=(select(db.Dataset,func.count(db.Workflow.id),func.max(db.Workflow.created))
               .outerjoin(db.Workflow,db.Workflow.dataset_id==db.Dataset.id).where(db.Dataset.kind=='tabular').group_by(db.Dataset.id)
               .order_by(func.max(db.Workflow.created).desc().nullslast(),db.Dataset.filename,db.Dataset.id))
        return [dict(dataset_info(dataset),workflow_count=count,last_used=last.isoformat() if last else None)
                for dataset,count,last in s.execute(query)]
@app.post('/datasets')
async def upload(file:UploadFile=File(...),sheet:str|None=Form(None)):
    from .tables import worksheets,materialize
    data=await file.read(25*1024*1024+1)
    if len(data)>25*1024*1024:raise HTTPException(413,'Maximum upload is 25 MiB')
    name=file.filename or 'dataset.csv'
    try:
        if name.lower().endswith('.xlsx') and sheet is None:
            names=worksheets(data);reference=artifacts.put(data,'.xlsx')['id']
            return {'state':'sheet_selection_required','workbook_id':reference,'filename':name,'sheets':names}
        return save_table(data,name,sheet)
    except Exception as exc:raise HTTPException(422,'Could not parse dataset: '+str(exc)[:500])

def save_table(data,name,sheet=None):
    from .tables import materialize
    frame,description,table_ref,profile_ref=materialize(data,name,sheet)
    artifact=artifacts.put(data,'.xlsx' if name.lower().endswith('.xlsx') else '.csv');identity=str(uuid.uuid4())
    with db.Session.begin() as session:
        dataset=db.Dataset(id=identity,filename=name,artifact=artifact['id'],rows=len(frame),table_ref=table_ref,profile_ref=profile_ref,sheet=sheet)
        session.add(dataset);session.flush();return dataset_info(dataset)

@app.post('/workbooks/{identity}/sheets')
def select_sheet(identity:str,payload:dict):
    from .tenancy import owns_artifact
    if not owns_artifact(identity):raise HTTPException(404,'Workbook not found')
    try:
        if not re.fullmatch(r'[a-f0-9]{64}\.xlsx',identity):raise ValueError('Invalid workbook ID')
        name=payload.get('filename','workbook.xlsx')
        if not isinstance(name,str) or not name.lower().endswith('.xlsx'):raise ValueError('Workbook filename must end in .xlsx')
        return save_table(artifacts.read(identity),name,payload.get('sheet'))
    except Exception as exc:raise HTTPException(422,'Could not select worksheet: '+str(exc)[:500])

@app.get('/datasets/{identity}/profile')
def dataset_profile(identity:str):
    with db.Session.begin() as session:
        dataset=session.get(db.Dataset,identity)
        if not dataset:raise HTTPException(404,'Dataset not found')
        from .tables import ensure_metadata
        return dict(artifacts.read_json(dataset.profile_ref) if dataset.kind=='document' else ensure_metadata(dataset),profile_ref=dataset.profile_ref)

@app.post('/clarifications/{identity}/answer')
def answer_clarification(identity:str,payload:dict):
    from .table_planning import create_table_plan
    answer=payload.get('answer')
    if not isinstance(answer,str) or not 1<=len(answer.strip())<=2000:raise HTTPException(422,'Provide a nonempty clarification answer')
    try:return create_table_plan('',[],'huggingface','greedy',pending_id=identity,answer=answer)
    except KeyError as exc:raise HTTPException(404,str(exc))
    except httpx.HTTPError as exc:raise HTTPException(503,'Local table model is unavailable or timed out; your pending request is preserved')
    except ValueError as exc:raise HTTPException(409,str(exc))

@app.get('/clarifications/{identity}')
def get_clarification(identity:str):
    with db.Session() as session:
        pending=session.get(db.PendingPlan,identity)
        if not pending:raise HTTPException(404,'Clarification not found')
        return dict(pending.payload,clarification_id=identity,state='resolved' if pending.payload.get('consumed') else 'clarification_required')

@app.post('/plans')
def create_plan(req:PlanRequest):
    identities=req.dataset_ids or ([req.dataset_id] if req.dataset_id else [])
    if not identities:raise HTTPException(422,'Select at least one dataset')
    with db.Session.begin() as session:
        selected=[session.get(db.Dataset,i) for i in identities]
        if any(d is None for d in selected):raise HTTPException(404,'Dataset not found')
        if any(d.kind!='tabular' for d in selected):raise HTTPException(422,'Use document analysis for document files')
        generic=req.generic or len(identities)>1 or dataset_info(selected[0])['columns']!=COLUMNS
    if generic:
        from .table_planning import create_table_plan
        try:return create_table_plan(req.request,identities,req.planner,req.policy)
        except httpx.HTTPError as exc:raise HTTPException(503,'Local table model is unavailable or timed out; retry after the model service is ready')
        except (ValueError,TypeError) as exc:raise HTTPException(422,str(exc))
    req.dataset_id=identities[0]
    with db.Session() as s:
        if not s.get(db.Dataset,req.dataset_id):raise HTTPException(404,'Dataset not found')
    try:raw,record=propose(req.request,req.planner);plan=validate_plan(raw)
    except Exception as exc:raise HTTPException(422,str(exc))
    # Keep the exact submitted request with its immutable workflow snapshot.
    # Existing records remain readable without requiring a database migration.
    record=dict(record,request=req.request,planner_mode=req.planner)
    id=str(uuid.uuid4())
    with db.Session.begin() as s:
        s.add(db.Workflow(id=id,dataset_id=req.dataset_id,plan=plan.model_dump(),planner=record,policy=req.policy));s.flush()
        for t in plan.tasks:s.add(db.TaskRow(id=id+':'+t.id,workflow_id=id,task_id=t.id,spec=t.model_dump()))
    return detail(id)
@app.get('/workflows')
def workflows(view:str='active'):
    if view not in ('active','archived','trash'):raise HTTPException(422,'Unknown chat view')
    with db.Session() as s:
        latest={}
        for flow,dataset in s.execute(select(db.Workflow,db.Dataset).join(db.Dataset,db.Workflow.dataset_id==db.Dataset.id).order_by(db.Workflow.created.desc(),db.Workflow.id)):
            item=workflow_info(flow,dataset)
            if item['chat_id'] not in latest:
                item['search_text']=item['request'] or ''
                latest[item['chat_id']]=item
            else:latest[item['chat_id']]['search_text']+='\n'+(item['request'] or '')
        from .product import preferences
        return preferences(list(latest.values()),view)

def chat_flows(s,chat_id):
    return list(s.scalars(select(db.Workflow).where(or_(db.Workflow.id==chat_id,db.Workflow.planner['chat_id'].as_string()==chat_id)).order_by(db.Workflow.created,db.Workflow.id)))

def workflow_detail(s,f):
    dataset=s.get(db.Dataset,f.dataset_id)
    rows=list(s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==f.id)))
    tasks=[]
    for t in rows:
        attempts=[{'id':a.id,'status':a.status,'worker':a.worker_id,'generation':a.generation,'error':a.error} for a in s.scalars(select(db.Attempt).where(db.Attempt.task_key==t.id))]
        tasks.append({'id':t.task_id,'handler':t.spec['handler'],'status':t.status,'attempts':attempts,'artifact':t.artifact})
    events=[{'kind':e.kind,'data':e.data,'created':e.created.isoformat()} for e in s.scalars(select(db.Event).where(db.Event.workflow_id==f.id).order_by(db.Event.id))]
    sched=[e['data']['elapsed_ms'] for e in events if e['kind']=='scheduling']
    reused={e['data']['task'] for e in events if e['kind']=='task_reused'}
    for task in tasks:task['reused']=task['id'] in reused
    insights=None;report=next((t for t in rows if t.spec['handler'] in ('build_report','table_report','document_report') and t.status=='completed' and t.artifact),None)
    if report:
        from .insights import build_insights
        content=artifacts.read(report.artifact).decode('utf-8')
        reference=re.search(r'<meta name="workflow-insights" content="([a-f0-9]+\.json)">',content)
        if reference:insights=artifacts.read_json(reference.group(1))
        else:
            sources={t.task_id:t.artifact for t in rows}
            inputs={name:sources[source] for name,source in report.spec['inputs'].items()}
            # Legacy table artifacts remain immutable; derive a readable view from accepted outputs.
            if 'cleaned' not in inputs:
                clean=next((t.artifact for t in rows if t.spec['handler']=='clean_transactions' and t.artifact),None)
                if clean:inputs['cleaned']=clean
            insights=build_insights(inputs,report.spec['parameters'])
    capacity_warnings=[]
    if (f.planner or {}).get('kind')=='tabular':
        workers=list(s.scalars(select(db.Worker)))
        for task in rows:
            resources=task.spec['resources']
            if task.status=='pending' and not any(task.spec['handler'] in w.handlers and w.cpu>=resources['cpu'] and w.memory_mb>=resources['memory_mb'] for w in workers):
                capacity_warnings.append(f"{task.task_id} requires {resources['memory_mb']} MiB and {resources['cpu']} CPU; no registered compatible worker currently has that declared capacity.")
    return dict(workflow_info(f,dataset),dataset=dataset_info(dataset) if dataset else None,capacity_warnings=capacity_warnings,
                plan=f.plan,planner=f.planner,validation={'valid':True},tasks=tasks,events=events,insights=insights,
                report_url=f'/api/workflows/{f.id}/report' if report else None,
                metrics={'completion_seconds':(f.finished-f.started).total_seconds() if f.finished and f.started else None,'scheduling_ms':sum(sched)})
@app.get('/workflows/{id}')
def detail(id):
    with db.Session() as s:
        f=s.get(db.Workflow,id)
        if not f:raise HTTPException(404,'Workflow not found')
        item=workflow_detail(s,f);flows=chat_flows(s,item['chat_id'])
        from .tenancy import owner
        if owner.get():
            pref=s.get(db.ChatPreference,(owner.get(),item['chat_id']))
            if pref and pref.title:item['title']=pref.title
        item['turns']=[workflow_detail(s,flow) for flow in flows]
        item['latest_workflow_id']=flows[-1].id
        return item

@app.post('/workflows/{id}/followups')
def followup(id,req:FollowupRequest):
    with db.Session() as session:
        generic_parent=session.get(db.Workflow,id)
        if generic_parent and (generic_parent.planner or {}).get('kind')=='tabular':
            identities=req.dataset_ids or generic_parent.planner['dataset_ids'];mode=req.planner or generic_parent.planner.get('planner_mode','huggingface');policy=req.policy or generic_parent.policy
        else:identities=None
    if identities:
        from .table_planning import create_table_plan
        try:return create_table_plan(req.request,identities,mode,policy,parent_id=id,reuse_outputs=req.reuse_outputs)
        except httpx.HTTPError as exc:raise HTTPException(503,'Local table model is unavailable or timed out; retry after the model service is ready')
        except (ValueError,TypeError) as exc:raise HTTPException(409,str(exc))
    from .followups import propose_followup
    with db.Session() as s:
        parent=s.get(db.Workflow,id)
        if not parent:raise HTTPException(404,'Workflow not found')
        chat_id=(parent.planner or {}).get('chat_id') or parent.id
        flows=chat_flows(s,chat_id)
        if flows[-1].id!=id:raise HTTPException(409,'Open the latest analysis version before adding a follow-up')
        if parent.status not in ('completed','failed','cancelled'):raise HTTPException(409,'Finish or cancel the current analysis before adding a follow-up')
        planner=req.planner or workflow_info(parent,s.get(db.Dataset,parent.dataset_id))['planner_mode']
        policy=req.policy or parent.policy;previous=parent.plan;dataset_id=parent.dataset_id
        title=workflow_info(flows[0],s.get(db.Dataset,dataset_id))['title'];revision=len(flows)+1
    try:raw,record=propose_followup(req.request,previous,planner);plan=validate_plan(raw)
    except Exception as exc:raise HTTPException(422,str(exc))
    new_id=str(uuid.uuid4())
    with db.Session.begin() as s:
        # Serialise revisions on the root chat row; a concurrent newer submission is rejected.
        s.get(db.Workflow,chat_id,with_for_update=True)
        if chat_flows(s,chat_id)[-1].id!=id:raise HTTPException(409,'A newer analysis version already exists; reopen it before continuing')
        record=dict(record,request=req.request,planner_mode=planner,chat_id=chat_id,revision=revision,parent_workflow_id=id,chat_title=title)
        s.add(db.Workflow(id=new_id,dataset_id=dataset_id,plan=plan.model_dump(),planner=record,policy=policy,status='running',started=db.now()));s.flush()
        for task in plan.tasks:s.add(db.TaskRow(id=new_id+':'+task.id,workflow_id=new_id,task_id=task.id,spec=task.model_dump()))
        s.add(db.Event(workflow_id=new_id,kind='followup_started',data={'parent_workflow_id':id,'revision':revision}))
    return detail(new_id)

@app.get('/workflows/{id}/report')
def narrative_report(id):
    from .insights import render_report
    item=detail(id)
    if not item['insights']:raise HTTPException(409,'Report is not available yet')
    if item['insights'].get('kind') in ('tabular','document'):
        with db.Session() as session:
            row=session.scalar(select(db.TaskRow).where(db.TaskRow.workflow_id==id,db.TaskRow.spec['handler'].as_string().in_(['table_report','document_report'])))
            content=artifacts.read(row.artifact)
    else:content=render_report(item['insights'],'').encode()
    return Response(content,media_type='text/html',headers={'Content-Disposition':f'attachment; filename="dataset-insights-v{item["revision"]}.html"'})
@app.post('/workflows/{id}/{action}')
def control(id,action):
    if action not in ('start','pause','resume','cancel'):raise HTTPException(404,'Unknown command')
    try:command(id,action)
    except KeyError:raise HTTPException(404,'Workflow not found')
    except ValueError as exc:raise HTTPException(409,str(exc))
    return detail(id)
@app.get('/workers')
def workers():
    with db.Session() as s:return [{'id':w.id,'cpu':w.cpu,'memory_mb':w.memory_mb,'heartbeat':w.heartbeat.isoformat(),'generation':w.generation,'handlers':w.handlers,'speed':w.speed} for w in s.scalars(select(db.Worker))]
@app.get('/artifacts/{id}')
def download(id):
    from .tenancy import owns_artifact
    if not owns_artifact(id):raise HTTPException(404,'Artifact not found')
    try:data=artifacts.read(id)
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Artifact not found')
    media={'.html':'text/html','.json':'application/json','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.txt':'text/plain','.md':'text/markdown'}
    return Response(data,media_type=next((m for suffix,m in media.items() if id.endswith(suffix)),'text/csv'),headers={'Content-Disposition':'attachment; filename="'+id+'"'})

from .product import router as product_router
from .document_analysis import router as document_router
app.include_router(product_router)
app.include_router(document_router)

from .team_spaces import router as team_router
app.include_router(team_router)
