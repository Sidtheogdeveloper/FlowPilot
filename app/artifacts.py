import os,hashlib,json,uuid
from pathlib import Path
ROOT=Path(os.getenv('ARTIFACT_ROOT','data/artifacts')).resolve()

def put(data:bytes,suffix='.json'):
    ROOT.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(data).hexdigest();path=ROOT/(digest+suffix)
    if not path.exists():
        temp=ROOT/(str(uuid.uuid4())+'.tmp');temp.write_bytes(data);os.replace(temp,path)
    return {'id':digest+suffix,'sha256':digest,'size':len(data),'path':str(path)}
def read(id):
    path=(ROOT/id).resolve()
    if path.parent!=ROOT:raise ValueError('Invalid artifact ID')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=id.split('.')[0]:raise ValueError('Artifact checksum mismatch')
    return data
def put_json(obj):return put(json.dumps(obj,allow_nan=False).encode())
def read_json(id):return json.loads(read(id))
