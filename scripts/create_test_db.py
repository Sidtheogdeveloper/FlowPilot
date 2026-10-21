import os
from sqlalchemy import create_engine,text
url=os.environ['DATABASE_URL'];engine=create_engine(url,isolation_level='AUTOCOMMIT')
with engine.connect() as c:
    exists=c.execute(text("SELECT 1 FROM pg_database WHERE datname='workflow_test'")).scalar()
    if not exists:c.execute(text('CREATE DATABASE workflow_test'))
print('Disposable workflow_test database ready')
