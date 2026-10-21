import os
from app import models as db
from sqlalchemy import create_engine,text
# Lightweight initial schema runner; rejects non-PostgreSQL URLs.
db.init_db()
print('PostgreSQL application schema ready')
