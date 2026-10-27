"""Bounded document ingestion and extractive analysis with verifiable source passages."""
import io,json,re,math,html,uuid,zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree
from fastapi import APIRouter,UploadFile,File,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select
from . import models as db,artifacts
from .tenancy import owner

router=APIRouter(prefix='/product',tags=['Document analysis'])
MAX_CHARS=2_000_000
STOP={'the','and','for','with','that','this','from','what','which','where','when','does','are','was','were','has','have','about','into','their','they','your','you','can','how','its','not','but','our','all','any','document','please','tell','would','could','should','will','there','these','those','them'}
def words(text):return [w for w in re.findall(r'\w+',text.lower()) if len(w)>2 and w not in STOP]

def extract(data,filename):
    suffix=Path(filename).suffix.lower();pages=[];location='section'
    if suffix=='.pdf':
        from pypdf import PdfReader,filters
        # The parser's decompression guards are reduced for this bounded application.
        for key in ('ZLIB_MAX_OUTPUT_LENGTH','LZW_MAX_OUTPUT_LENGTH','RUN_LENGTH_MAX_OUTPUT_LENGTH','MAX_DECLARED_STREAM_LENGTH','MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH','FLATE_MAX_BUFFER_SIZE'):
            if hasattr(filters,key):setattr(filters,key,8_000_000)
        reader=PdfReader(io.BytesIO(data),strict=True)
        if reader.is_encrypted:raise ValueError('Encrypted PDFs are not supported')
        if len(reader.pages)>1000:raise ValueError('Maximum document length is 1000 pages')
        count=0
        for page in reader.pages:
            text=page.extract_text() or '';count+=len(text)
            if count>MAX_CHARS:raise ValueError('Extracted document text exceeds 2 million characters')
            pages.append(text)
        location='page'
    elif suffix=='.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info=archive.infolist()
            if sum(i.file_size for i in info)>25*1024*1024 or any(i.file_size>10*1024*1024 for i in info):raise ValueError('Expanded Word document exceeds safe limits')
            xml=archive.read('word/document.xml')
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():raise ValueError('XML entities are not supported')
        root=ElementTree.fromstring(xml);ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text='\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p',ns))
        pages=[text[i:i+6000] for i in range(0,len(text),6000)]
    elif suffix in ('.txt','.md'):
        text=data.decode('utf-8-sig')
        pages=[text[i:i+6000] for i in range(0,len(text),6000)]
    else:raise ValueError('Documents must be PDF, DOCX, TXT or Markdown')
    if sum(len(p) for p in pages)>MAX_CHARS:raise ValueError('Extracted text exceeds 2 million characters')
    chunks=[]
    for page,text in enumerate(pages,1):
        text=re.sub(r'[ \t]+',' ',text).strip()
        for start in range(0,len(text),1000):
            excerpt=text[start:start+1200].strip()
            if excerpt:chunks.append({'id':f'p{page}_c{start//1000+1}','page':page,'text':excerpt})
    if not chunks:raise ValueError('No extractable text found. Scanned PDFs need OCR before upload.')
    if len(chunks)>4000:raise ValueError('Document contains too many passages')
    return {'kind':'document_chunks','filename':filename,'location':location,'pages':len(pages),'characters':sum(len(p) for p in pages),'chunks':chunks}

def validate_document_output(handler,obj):
    if not isinstance(obj,dict):raise ValueError('Document output must be an object')
    if handler=='document_load':
        if obj.get('kind')!='document_chunks' or not isinstance(obj.get('chunks'),list) or not obj['chunks'] or len(obj['chunks'])>4000:raise ValueError('Invalid document chunks')
        for c in obj['chunks']:
            if not isinstance(c,dict) or not re.fullmatch(r'p\d+_c\d+',str(c.get('id',''))) or not isinstance(c.get('page'),int) or not isinstance(c.get('text'),str) or not 1<=len(c['text'])<=1200:raise ValueError('Invalid source passage')
    elif handler=='document_search':
        if obj.get('kind')!='document_evidence' or not isinstance(obj.get('citations'),list) or len(obj['citations'])>12:raise ValueError('Invalid document evidence')
        for c in obj['citations']:
            if not all(k in c for k in ('document_id','chunk_id','filename','page','text','location')) or not isinstance(c['text'],str):raise ValueError('Invalid citation')
    else:raise ValueError('Unsupported document output')
    return obj

def execute_document(handler,inputs,parameters):
    if handler=='document_load':return artifacts.put_json(validate_document_output(handler,artifacts.read_json(inputs['dataset'])))
    if handler=='document_search':
        question=parameters['question'];query=Counter(words(question));documents=[artifacts.read_json(ref) for ref in inputs.values()]
        ranked=[]
        for doc in documents:
            for chunk in doc['chunks']:
                tokens=Counter(words(chunk['text']));score=sum(min(tokens[w],3)*count for w,count in query.items())/max(1,math.sqrt(len(tokens)))
                if parameters['mode']=='summary':score=len(set(tokens))/max(1,math.sqrt(len(chunk['text'])))
                if score>0:ranked.append((score,doc,chunk))
        ranked.sort(key=lambda x:(-x[0],x[1]['filename'],x[2]['page'],x[2]['id']))
        selected=[];seen=set()
        # Include at least one matching passage from each selected document.
        for entry in ranked:
            key=entry[1]['document_id']
            if key not in seen:selected.append(entry);seen.add(key)
        selected=selected[:8]
        for entry in ranked:
            if len(selected)>=8:break
            if entry not in selected:selected.append(entry)
        cites=[{'document_id':doc['document_id'],'chunk_id':chunk['id'],'filename':doc['filename'],'page':chunk['page'],'text':chunk['text'],'location':doc['location']} for _,doc,chunk in selected]
        return artifacts.put_json(validate_document_output(handler,{'kind':'document_evidence','question':question,'mode':parameters['mode'],'citations':cites,'method':'keyword-ranked extractive evidence','document_count':len(documents)}))
    if handler=='document_report':
        evidence=validate_document_output('document_search',artifacts.read_json(inputs['evidence']))
        findings=['The following passages are quoted from the selected source documents.']+[f'“{c["text"]}” [{i+1}]' for i,c in enumerate(evidence['citations'])]
        if not evidence['citations']:findings=['No matching source passages were found. Try more specific terms; no unsupported answer has been generated.']
        insights={'kind':'document','title':'Document evidence','question':parameters['question'],'method':evidence['method'],'sections':[{'title':'Source-grounded passages','findings':findings,'citations':evidence['citations'],'artifact':inputs['evidence'],'audit':[{'operation':'extractive_document_analysis','method':evidence['method'],'documents':evidence['document_count']}]}]}
        reference=artifacts.put_json(insights)['id']
        body=''.join('<blockquote>'+html.escape(c['text'])+'</blockquote><p>['+str(i+1)+'] '+html.escape(c['filename'])+' · '+c['location']+' '+str(c['page'])+' · '+c['chunk_id']+'</p>' for i,c in enumerate(evidence['citations']))
        page='<!doctype html><html><head><meta charset="utf-8"><meta name="workflow-insights" content="'+reference+'"><title>FlowPilot document evidence</title></head><body><h1>Document evidence</h1><p>'+html.escape(parameters['question'])+'</p><p>Extractive source passages; no generated factual claims.</p>'+body+('' if body else '<p>No matching source passages were found.</p>')+'</body></html>'
        return artifacts.put(page.encode(),'.html')
    raise ValueError('Unsupported document handler')

def validate_document_plan(plan):
    for t in plan.tasks:
        if t.handler=='document_load':
            if t.parameters or len(t.inputs)!=1:raise ValueError('Document loading accepts no parameters')
        elif t.handler=='document_search':
            if set(t.parameters)!={'question','mode'} or not isinstance(t.parameters['question'],str) or not 3<=len(t.parameters['question'])<=2000 or t.parameters['mode'] not in ('search','summary'):raise ValueError('Invalid document search intent')
            if not 1<=len(t.inputs)<=8 or any(not re.fullmatch(r'd\d+',k) for k in t.inputs):raise ValueError('Invalid document source bindings')
        elif t.handler=='document_report':
            if set(t.parameters)!={'question'} or not isinstance(t.parameters['question'],str) or not 3<=len(t.parameters['question'])<=2000:raise ValueError('Invalid document report')
        else:raise ValueError('Mixed document and table plans are unsupported')

def compile_document_plan(datasets,question):
    tasks=[{'id':f'document_{i}','handler':'document_load','inputs':{'dataset':'$dataset:'+d.id},'depends_on':[],'parameters':{},'resources':{'cpu':1,'memory_mb':128}} for i,d in enumerate(datasets)]
    mode='summary' if re.search(r'\b(summary|summarize|overview|summarise)\b',question,re.I) else 'search'
    tasks.append({'id':'evidence','handler':'document_search','inputs':{f'd{i}':t['id'] for i,t in enumerate(tasks)},'depends_on':[t['id'] for t in tasks],'parameters':{'question':question,'mode':mode},'resources':{'cpu':1,'memory_mb':128+32*(len(datasets)-1)}})
    tasks.append({'id':'report','handler':'document_report','inputs':{'evidence':'evidence'},'depends_on':['evidence'],'parameters':{'question':question},'resources':{'cpu':1,'memory_mb':128}})
    from .validator import validate_plan
    return validate_plan({'tasks':tasks}).model_dump()

class DocumentRequest(BaseModel):
    document_ids:list[str]=Field(min_length=1,max_length=8)
    request:str=Field(min_length=3,max_length=2000)
    reuse_outputs:bool=True
def create_document_plan(ids,question,parent_id=None,reuse=True):
    from .api import chat_flows,detail
    from .reuse import executor_version,reuse_completed
    with db.Session.begin() as s:
        datasets=[s.get(db.Dataset,i) for i in ids]
        if any(not d or d.kind!='document' for d in datasets):raise HTTPException(404,'Document not found')
        if len(set(ids))!=len(ids):raise HTTPException(422,'Select each document once')
        parent=s.get(db.Workflow,parent_id) if parent_id else None
        if parent_id and (not parent or parent.planner.get('kind')!='document'):raise HTTPException(404,'Document workflow not found')
        key=str(uuid.uuid4());root=root_id=key;revision=1;title=question
        if parent:
            if parent.status not in ('completed','failed','cancelled'):raise HTTPException(409,'Finish the current analysis first')
            root=parent.planner.get('chat_id') or parent.id;s.get(db.Workflow,root,with_for_update=True);flows=chat_flows(s,root)
            if flows[-1].id!=parent.id:raise HTTPException(409,'Open the latest version before continuing')
            revision=len(flows)+1;title=flows[0].planner.get('chat_title') or flows[0].planner.get('request',question)
        record={'kind':'document','request':question,'chat_title':title,'chat_id':root,'revision':revision,'parent_workflow_id':parent_id,'dataset_ids':ids,'executor_version':executor_version(),'planner_mode':'template','method':'extractive'}
        plan=compile_document_plan(datasets,question);flow=db.Workflow(id=key,dataset_id=ids[0],plan=plan,planner=record,policy='greedy');s.add(flow);s.flush()
        for task in plan['tasks']:s.add(db.TaskRow(id=key+':'+task['id'],workflow_id=key,task_id=task['id'],spec=task))
        s.flush()
        if parent and reuse:reuse_completed(s,flow,parent)
    return dict(detail(key),state='ready')

@router.post('/documents',status_code=201)
async def upload_document(file:UploadFile=File(...)):
    data=await file.read(25*1024*1024+1)
    if len(data)>25*1024*1024:raise HTTPException(413,'Maximum upload is 25 MiB')
    from starlette.concurrency import run_in_threadpool
    name=Path(file.filename or 'document.txt').name
    try:body=await run_in_threadpool(extract,data,name)
    except Exception as exc:raise HTTPException(422,'Could not read document: '+str(exc)[:300])
    key=str(uuid.uuid4());body['document_id']=key;content=artifacts.put_json(body)['id'];source=artifacts.put(data,Path(name).suffix.lower())['id']
    description={'kind':'document','columns':[],'pages':body['pages'],'characters':body['characters'],'chunks':len(body['chunks']),'location':body['location']}
    profile=artifacts.put_json(description)['id']
    with db.Session.begin() as s:s.add(db.Dataset(id=key,filename=name,rows=body['pages'],artifact=source,table_ref=content,profile_ref=profile,kind='document'))
    return dict(description,id=key,filename=name)

@router.get('/documents')
def documents():
    with db.Session() as s:return [dict(artifacts.read_json(d.profile_ref),id=d.id,filename=d.filename) for d in s.scalars(select(db.Dataset).where(db.Dataset.kind=='document').order_by(db.Dataset.filename))]

@router.get('/documents/{key}/passages')
def passages(key:str,offset:int=0):
    if offset<0:raise HTTPException(422,'Invalid offset')
    with db.Session() as s:
        d=s.get(db.Dataset,key)
        if not d or d.kind!='document':raise HTTPException(404,'Document not found')
        body=artifacts.read_json(d.table_ref)
        return {'filename':d.filename,'location':body['location'],'total':len(body['chunks']),'passages':body['chunks'][offset:offset+20]}

@router.get('/documents/{key}/passages/{chunk}')
def passage(key:str,chunk:str):
    with db.Session() as s:
        d=s.get(db.Dataset,key)
        if not d or d.kind!='document':raise HTTPException(404,'Document not found')
        body=artifacts.read_json(d.table_ref);item=next((c for c in body['chunks'] if c['id']==chunk),None)
        if not item:raise HTTPException(404,'Source passage not found')
        return dict(item,filename=d.filename,location=body['location'],source_artifact=d.artifact)

@router.post('/document-plans')
def document_plan(payload:DocumentRequest):return create_document_plan(payload.document_ids,payload.request,reuse=payload.reuse_outputs)

@router.post('/document-workflows/{key}/followups')
def document_followup(key:str,payload:DocumentRequest):return create_document_plan(payload.document_ids,payload.request,parent_id=key,reuse=payload.reuse_outputs)
