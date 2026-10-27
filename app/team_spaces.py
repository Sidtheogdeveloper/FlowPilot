"""Explicit invitations and immutable, item-scoped snapshots; no tenancy bypass."""
import copy,uuid
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field
from sqlalchemy import select,delete
from . import models as db,artifacts
from .product import identity,dataset,workflow
router=APIRouter(prefix='/product',tags=['Team spaces'])
def member(s,team,roles=None):
    m=s.get(db.TeamMember,(team,identity()))
    if not m or roles and m.role not in roles:raise HTTPException(404,'Team or permission not found')
    return m
def shared(s,key,roles=None):
    row=s.get(db.TeamShare,key)
    if not row:raise HTTPException(404,'Shared item not found')
    return row,member(s,row.team_id,roles)
class TeamRequest(BaseModel):name:str=Field(min_length=1,max_length=80)
class InviteRequest(BaseModel):
    email:str=Field(min_length=3,max_length=254)
    role:Literal['viewer','editor']='viewer'
class ShareRequest(BaseModel):
    kind:Literal['dataset','chat']
    item_id:str
@router.post('/teams',status_code=201)
def create_team(p:TeamRequest):
    if not p.name.strip():raise HTTPException(422,'Enter a team name')
    with db.Session.begin() as s:
        t=db.Team(id=str(uuid.uuid4()),owner_id=identity(),name=p.name.strip());s.add(t);s.flush()
        s.add(db.TeamMember(team_id=t.id,user_id=identity(),role='owner'))
        return {'id':t.id,'name':t.name}
@router.get('/teams')
def teams():
    with db.Session() as s:
        return [{'id':t.id,'name':t.name,'role':m.role} for t,m in s.execute(select(db.Team,db.TeamMember).join(db.TeamMember,db.TeamMember.team_id==db.Team.id).where(db.TeamMember.user_id==identity()))]
@router.post('/teams/{key}/invites',status_code=201)
def invite(key:str,p:InviteRequest):
    from .auth import email as normalize_email
    email=normalize_email(p.email)
    with db.Session.begin() as s:
        member(s,key,['owner'])
        row=db.TeamInvite(id=str(uuid.uuid4()),team_id=key,email=email,role=p.role,expires=db.now()+timedelta(days=7));s.add(row)
        return {'message':'Invitation available to this email address in Team spaces for seven days.'}
@router.get('/team-invites')
def invites():
    with db.Session() as s:
        user=s.get(db.User,identity())
        return [{'id':i.id,'team':t.name,'role':i.role} for i,t in s.execute(select(db.TeamInvite,db.Team).join(db.Team,db.Team.id==db.TeamInvite.team_id).where(db.TeamInvite.email==user.email,db.TeamInvite.consumed==False,db.TeamInvite.expires>db.now()))]
@router.post('/team-invites/{key}/accept')
def accept(key:str):
    with db.Session.begin() as s:
        i=s.get(db.TeamInvite,key,with_for_update=True);u=s.get(db.User,identity())
        if not i or i.email!=u.email or i.consumed or i.expires<=db.now():raise HTTPException(404,'Invitation not available')
        m=s.get(db.TeamMember,(i.team_id,u.id))
        if not m:s.add(db.TeamMember(team_id=i.team_id,user_id=u.id,role=i.role))
        elif m.role!='owner':m.role=i.role
        i.consumed=True
    return {'ok':True}
@router.get('/teams/{key}/members')
def members(key:str):
    with db.Session() as s:
        member(s,key)
        return [{'id':u.id,'name':u.name,'email':u.email,'role':m.role} for m,u in s.execute(select(db.TeamMember,db.User).join(db.User,db.User.id==db.TeamMember.user_id).where(db.TeamMember.team_id==key))]
@router.delete('/teams/{key}/members/{user}')
def revoke(key:str,user:str):
    with db.Session.begin() as s:
        member(s,key,['owner']);m=s.get(db.TeamMember,(key,user))
        if not m or m.role=='owner':raise HTTPException(422,'Cannot remove the team owner')
        s.delete(m)
    return {'ok':True}
def dataset_snapshot(d):
    return {k:getattr(d,k) for k in ('id','filename','kind','rows','artifact','table_ref','profile_ref','sheet')}
@router.post('/teams/{key}/shares',status_code=201)
def share(key:str,p:ShareRequest):
    from .api import detail,chat_flows
    with db.Session.begin() as s:
        member(s,key,['owner','editor']);refs=set()
        if p.kind=='dataset':
            d=dataset(s,p.item_id);ds=[dataset_snapshot(d)];title=d.filename;payload={'kind':'dataset','datasets':ds}
        else:
            f=workflow(s,p.item_id)
            if f.status!='completed':raise HTTPException(409,'Share a completed analysis')
            flows=chat_flows(s,(f.planner or {}).get('chat_id') or f.id)
            if flows[-1].id!=f.id:raise HTTPException(409,'Select the latest completed conversation')
            ids=list(dict.fromkeys(i for w in flows for i in w.planner.get('dataset_ids',[w.dataset_id])))
            ds=[dataset_snapshot(dataset(s,i)) for i in ids]
            title=f.planner.get('chat_title') or f.planner.get('request','Analysis')
            payload={'kind':'chat','datasets':ds,'workflow':detail(f.id),'plan':f.plan,'planner':f.planner,'policy':f.policy}
            for w in flows:
                for task in s.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==w.id,db.TaskRow.status=='completed')):
                    if task.artifact:
                        refs.add(task.artifact)
                        if task.artifact.endswith('.html'):
                            # Narrative metadata includes calculated section artifacts.
                            from .api import workflow_detail
                            info=workflow_detail(s,w)
                            for section in (info.get('insights') or {}).get('sections',[]):
                                if section.get('artifact'):refs.add(section['artifact'])
        for d in ds:refs.update(d[k] for k in ('artifact','table_ref','profile_ref') if d.get(k))
        row=db.TeamShare(id=str(uuid.uuid4()),team_id=key,owner_id=identity(),title=title,snapshot=payload,allowed_artifacts=sorted(refs));s.add(row);s.flush()
        return {'id':row.id,'title':title}
@router.get('/teams/{key}/shares')
def shares(key:str):
    with db.Session() as s:
        member(s,key)
        return [{'id':r.id,'title':r.title,'kind':r.snapshot['kind'],'owner_id':r.owner_id} for r in s.scalars(select(db.TeamShare).where(db.TeamShare.team_id==key).order_by(db.TeamShare.created.desc()))]
@router.get('/shares/{key}')
def read_share(key:str):
    with db.Session() as s:
        r,m=shared(s,key)
        return {'id':r.id,'title':r.title,'snapshot':r.snapshot,'artifacts':r.allowed_artifacts,'can_fork':m.role in ('owner','editor')}
@router.get('/shares/{key}/artifacts/{ref}')
def download_share(key:str,ref:str):
    with db.Session() as s:
        r,m=shared(s,key)
        if ref not in r.allowed_artifacts:raise HTTPException(404,'Artifact not shared')
        body=artifacts.read(ref)
        return Response(body,media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="'+ref+'"'})
@router.delete('/shares/{key}')
def unshare(key:str):
    with db.Session.begin() as s:
        r,m=shared(s,key)
        if r.owner_id!=identity() and m.role!='owner':raise HTTPException(404,'Permission not found')
        s.delete(r)
    return {'ok':True}
@router.post('/shares/{key}/copy')
def fork_share(key:str):
    from .tables import ensure_metadata
    from .tabular import compile_table_intent
    from .validator import validate_plan
    from .reuse import executor_version
    from .api import detail
    from .document_analysis import compile_document_plan
    with db.Session.begin() as s:
        r,m=shared(s,key,['owner','editor']);payload=r.snapshot;mapping={};clones=[]
        for source in payload['datasets']:
            values=copy.deepcopy(source);old=values.pop('id');new=str(uuid.uuid4())
            if values['kind']=='document':
                body=artifacts.read_json(values['table_ref']);body['document_id']=new
                values['table_ref']=artifacts.put_json(body)['id']
            clone=db.Dataset(id=new,**values);s.add(clone);s.flush();mapping[old]=new;clones.append(clone)
            for field in ('artifact','table_ref','profile_ref'):
                if values.get(field):
                    from sqlalchemy.dialects.postgresql import insert
                    s.execute(insert(db.ArtifactGrant).values(owner_id=identity(),artifact=values[field],size=len(artifacts.read(values[field]))).on_conflict_do_nothing())
        if payload['kind']=='dataset':return {'dataset_ids':[d.id for d in clones],'message':'Private dataset copy created'}
        record=copy.deepcopy(payload['planner']);ids=[mapping[i] for i in record.get('dataset_ids',[])];selected=[next(d for d in clones if d.id==i) for i in ids]
        if record.get('kind')=='document':plan=compile_document_plan(selected,record['request'])
        else:
            plan,profiles=compile_table_intent(record['intent'],selected,record.get('confirmations',[]))
            plan['tasks'][-1]['parameters']['question']=record.get('request','')
            validate_plan(plan)
            from .table_planning import preflight
            preflight(plan,selected)
            record['profiles']=profiles
            from .partitioning import prepare_execution
            plan,record['execution']=prepare_execution(s,plan,selected,record.get('execution',{}).get('enabled',True))
        wid=str(uuid.uuid4());record.update(chat_id=wid,revision=1,parent_workflow_id=None,dataset_ids=ids,source_share=key,executor_version=executor_version())
        f=db.Workflow(id=wid,dataset_id=ids[0],plan=plan,planner=record,policy=payload['policy']);s.add(f);s.flush()
        for t in plan['tasks']:s.add(db.TaskRow(id=wid+':'+t['id'],workflow_id=wid,task_id=t['id'],spec=t))
    return detail(wid)
