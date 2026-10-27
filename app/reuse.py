"""Deterministic subtree reuse within an owner's existing conversation."""
import hashlib,json,importlib.metadata
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from . import models as db,artifacts

def executor_version():
    root=Path(__file__).parent
    code=b''.join((root/name).read_bytes() for name in ('tables.py','tabular.py','catalogue.py','result_contracts.py','document_analysis.py') if (root/name).exists())
    versions='|'.join(importlib.metadata.version(name) for name in ('pandas','networkx','openpyxl','pypdf','reportlab'))
    return hashlib.sha256(code+versions.encode()).hexdigest()

def fingerprints(session,flow):
    nodes={task['id']:task for task in flow.plan['tasks']};memo={};version=(flow.planner or {}).get('executor_version') or executor_version()
    def fingerprint(identity):
        if identity in memo:return memo[identity]
        task=nodes[identity];sources={}
        for key,source in task['inputs'].items():
            if source.startswith('$dataset'):
                dataset=session.get(db.Dataset,source.split(':',1)[1] if ':' in source else flow.dataset_id)
                if not dataset:raise ValueError('Input dataset is unavailable')
                sources[key]=dataset.table_ref or dataset.artifact
            else:sources[key]=fingerprint(source)
        material={'executor':version,'handler':task['handler'],'version':task.get('version','1'),'parameters':task.get('parameters',{}),'inputs':sources}
        memo[identity]=hashlib.sha256(json.dumps(material,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        return memo[identity]
    for identity in nodes:fingerprint(identity)
    return memo

def reuse_completed(session,flow,parent):
    # Sharing never changes this boundary: reuse requires the same owner and chat.
    if not parent or parent.owner_id!=flow.owner_id or parent.status!='completed':return []
    if (parent.planner.get('chat_id') or parent.id)!=(flow.planner.get('chat_id') or flow.id):return []
    if not (parent.planner or {}).get('executor_version') or parent.planner['executor_version']!=(flow.planner or {}).get('executor_version'):return []
    previous=fingerprints(session,parent);current=fingerprints(session,flow)
    candidates={previous[t.task_id]:t for t in session.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==parent.id,db.TaskRow.status=='completed')) if t.artifact and not t.spec['handler'].endswith('_report')}
    reused=[]
    from .result_contracts import validate_output
    for row in session.scalars(select(db.TaskRow).where(db.TaskRow.workflow_id==flow.id)):
        original=candidates.get(current[row.task_id])
        if not original or row.spec['handler'].endswith('_report'):continue
        try:validate_output(row.spec['handler'],artifacts.read_json(original.artifact))
        except (ValueError,FileNotFoundError):continue
        row.status='completed';row.artifact=original.artifact
        if flow.owner_id:session.execute(insert(db.ArtifactGrant).values(owner_id=flow.owner_id,artifact=row.artifact,size=len(artifacts.read(row.artifact))).on_conflict_do_nothing())
        session.add(db.Event(workflow_id=flow.id,kind='task_reused',data={'task':row.task_id,'from_workflow':parent.id,'from_task':original.task_id,'fingerprint':current[row.task_id]}))
        reused.append(row.task_id)
    return reused
