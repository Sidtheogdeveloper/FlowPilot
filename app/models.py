import os
from datetime import datetime,timezone
from sqlalchemy import create_engine,String,JSON,DateTime,Integer,Float,ForeignKey
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column,sessionmaker

def now():return datetime.now(timezone.utc)
class Base(DeclarativeBase):pass
class Dataset(Base):
    __tablename__='datasets'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    filename:Mapped[str]=mapped_column(String)
    artifact:Mapped[str]=mapped_column(String)
    rows:Mapped[int]=mapped_column(Integer)
    profile_ref:Mapped[str|None]=mapped_column(String,nullable=True)
    table_ref:Mapped[str|None]=mapped_column(String,nullable=True)
    sheet:Mapped[str|None]=mapped_column(String,nullable=True)
class PendingPlan(Base):
    __tablename__='pending_plans'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    payload:Mapped[dict]=mapped_column(JSON)
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class Workflow(Base):
    __tablename__='workflows'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    dataset_id:Mapped[str]=mapped_column(ForeignKey('datasets.id'))
    status:Mapped[str]=mapped_column(String,default='planned')
    plan:Mapped[dict]=mapped_column(JSON)
    planner:Mapped[dict]=mapped_column(JSON)
    policy:Mapped[str]=mapped_column(String)
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    started:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    finished:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class TaskRow(Base):
    __tablename__='tasks'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    workflow_id:Mapped[str]=mapped_column(ForeignKey('workflows.id'))
    task_id:Mapped[str]=mapped_column(String)
    spec:Mapped[dict]=mapped_column(JSON)
    status:Mapped[str]=mapped_column(String,default='pending')
    generation:Mapped[int]=mapped_column(Integer,default=0)
    artifact:Mapped[str|None]=mapped_column(String,nullable=True)
class Worker(Base):
    __tablename__='workers'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    generation:Mapped[str]=mapped_column(String)
    cpu:Mapped[int]=mapped_column(Integer)
    memory_mb:Mapped[int]=mapped_column(Integer)
    handlers:Mapped[list]=mapped_column(JSON)
    heartbeat:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    speed:Mapped[float]=mapped_column(Float,default=1)
class Attempt(Base):
    __tablename__='attempts'
    id:Mapped[str]=mapped_column(String,primary_key=True)
    task_key:Mapped[str]=mapped_column(ForeignKey('tasks.id'))
    generation:Mapped[int]=mapped_column(Integer)
    worker_id:Mapped[str]=mapped_column(ForeignKey('workers.id'))
    worker_generation:Mapped[str]=mapped_column(String)
    status:Mapped[str]=mapped_column(String,default='queued')
    lease:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    started:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    finished:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    error:Mapped[str|None]=mapped_column(String,nullable=True)
class Event(Base):
    __tablename__='events'
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    workflow_id:Mapped[str]=mapped_column(String,index=True)
    kind:Mapped[str]=mapped_column(String)
    data:Mapped[dict]=mapped_column(JSON)
    created:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
ENGINE=None;Session=None
def init_db(url=None):
    global ENGINE,Session
    url=url or os.getenv('DATABASE_URL','postgresql+psycopg://workflow:workflow@localhost:5432/workflow')
    if not url.startswith('postgresql'):raise ValueError('PostgreSQL is required; SQLite is not a concurrency substitute')
    ENGINE=create_engine(url,pool_pre_ping=True);Session=sessionmaker(ENGINE,expire_on_commit=False);Base.metadata.create_all(ENGINE)
    from sqlalchemy import text
    with ENGINE.begin() as connection:
        for name in ('profile_ref','table_ref','sheet'):
            connection.execute(text('ALTER TABLE datasets ADD COLUMN IF NOT EXISTS '+name+' VARCHAR'))
