"""HTTP owner scope. Internal coordinator/worker processes have no implicit user scope."""
from contextvars import ContextVar
from sqlalchemy import event
from sqlalchemy.orm import Session,with_loader_criteria
from . import models as db

owner=ContextVar('flowpilot_owner',default=None)
_installed=False
def install():
    global _installed
    if _installed:return
    _installed=True
    @event.listens_for(Session,'do_orm_execute')
    def scope(state):
        identity=owner.get()
        if identity and (state.is_select or state.is_update or state.is_delete):
            for model in (db.Dataset,db.Workflow,db.PendingPlan,db.ArtifactGrant):
                state.statement=state.statement.options(with_loader_criteria(model,model.owner_id==identity,include_aliases=True))
    @event.listens_for(Session,'before_flush')
    def stamp(session,context,instances):
        identity=owner.get()
        if not identity:return
        for value in session.new:
            if isinstance(value,(db.Dataset,db.Workflow,db.PendingPlan,db.ArtifactGrant)):
                if value.owner_id and value.owner_id!=identity:raise ValueError('Cross-account ownership is not allowed')
                value.owner_id=identity
        for value in session.dirty:
            if isinstance(value,(db.Dataset,db.Workflow,db.PendingPlan,db.ArtifactGrant)) and value.owner_id!=identity:
                raise ValueError('Cross-account ownership is not allowed')

def grant(reference,size=0):
    identity=owner.get()
    if not identity:return
    from sqlalchemy.dialects.postgresql import insert
    with db.Session.begin() as s:
        s.execute(insert(db.ArtifactGrant).values(owner_id=identity,artifact=reference,size=size).on_conflict_do_nothing())

def owns_artifact(reference):
    identity=owner.get()
    if not identity:return False
    with db.Session() as s:return s.get(db.ArtifactGrant,(identity,reference)) is not None
