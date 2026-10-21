"""Separate table LoRA training and reproducible unseen-schema executable evaluation.

python -m training.train_tables --model-cache <local snapshot>
"""
import argparse
import hashlib
import json
import random
import time
from pathlib import Path
from types import SimpleNamespace
import torch
from transformers import AutoTokenizer,AutoModelForCausalLM
from app import artifacts
from app.tables import materialize
from app.table_planning import metadata,system_prompt
from app.tabular import TableIntent,compile_table_intent,execute_table
from app.validator import validate_plan
from training.lora import attach_lora,adapter_state,load_adapter

ROOT=Path(__file__).resolve().parent/'artifacts'/'table-lora'

def corpus(split,count):
    domains=[('sales','region','net_amount'),('inventory','warehouse','stock'),('education','classroom','score'),('sensors','device','reading'),('support','queue','duration')]
    held=[('logistics','destination','weight'),('energy','plant','output'),('libraries','shelf','loans'),('research','cohort','measurement'),('manufacturing','line','defects')]
    for i in range(count):
        domain,group,value=(domains if split=='train' else held)[i%5]
        suffix=f'_{i}';group+=suffix;value+=suffix;reverse=i%2==1
        header=[value,group] if reverse else [group,value]
        lines=[','.join(header)]+[','.join([str(n),g] if reverse else [g,str(n)]) for g,n in [('A',2),('B',7),('A',4),('C',13)]]
        raw=('\n'.join(lines)+'\n').encode();frame,desc,table_ref,profile_ref=materialize(raw,domain+'.csv')
        d=SimpleNamespace(id='dataset_'+str(i),filename=domain+'.csv',rows=4,profile_ref=profile_ref,table_ref=table_ref)
        datasets=[d];gid='t0.c'+str(1 if reverse else 0);vid='t0.c'+str(0 if reverse else 1)
        operation=i%5
        if operation==0:
            question=f'How many rows are in {domain}?'
            steps=[{'id':'summary','operation':'aggregate','sources':['t0'],'parameters':{'group_by':[],'metrics':[{'name':'total','function':'rows'}]}}]
        elif operation in (1,2):
            fn='sum' if operation==1 else 'mean'
            question=f'Calculate the {fn} of {value} grouped by {group}.'
            steps=[{'id':'summary','operation':'aggregate','sources':['t0'],'parameters':{'group_by':[gid],'metrics':[{'name':'total','function':fn,'column':vid}]}}]
        elif operation==3:
            question=f'Keep rows where {value} is greater than 5, then sum {value} by {group}.'
            steps=[{'id':'filtered','operation':'filter','sources':['t0'],'parameters':{'column':vid,'operator':'gt','value':5}}, {'id':'summary','operation':'aggregate','sources':['filtered'],'parameters':{'group_by':[gid],'metrics':[{'name':'total','function':'sum','column':vid}]}}]
        else:
            other=f'{group},description_{i}\nA,Alpha\nB,Beta\nC,Gamma\n'.encode()
            _,desc2,ref2,prof2=materialize(other,'lookup.csv')
            datasets.append(SimpleNamespace(id='lookup_'+str(i),filename='lookup.csv',rows=3,profile_ref=prof2,table_ref=ref2))
            question=f'Inner join {domain} to lookup on {group}, then sum {value} by description_{i}. Join keys are explicitly {gid} and t1.c0.'
            steps=[{'id':'joined','operation':'join','sources':['t0','t1'],'parameters':{'left_keys':[gid],'right_keys':['t1.c0'],'how':'inner'}},{'id':'summary','operation':'aggregate','sources':['joined'],'parameters':{'group_by':['t1.c1'],'metrics':[{'name':'total','function':'sum','column':vid}]}}]
        target={'steps':steps,'outputs':['summary']}
        context=metadata(datasets)
        yield {'id':split+'_'+str(i),'domain':domain,'messages':[{'role':'system','content':system_prompt(context)},{'role':'user','content':json.dumps({'question':question,'previous_intent':None,'clarification_history':[]})}], 'target':target,'datasets':datasets}

def execute_intent(intent,datasets):
    confirmations=[json.dumps({k:s['parameters'][k] for k in ('left_keys','right_keys','how')},sort_keys=True) for s in intent['steps'] if s['operation']=='join']
    raw,_=compile_table_intent(intent,datasets,confirmations);plan=validate_plan(raw);refs={}
    for t in plan.tasks:
        if t.handler=='table_report':continue
        inputs={}
        for key,source in t.inputs.items():
            inputs[key]=next(d.table_ref for d in datasets if d.id==source.split(':',1)[1]) if source.startswith('$dataset:') else refs[source]
        refs[t.id]=execute_table(t.handler,inputs,t.parameters)['id']
    return [artifacts.read_json(refs[o])['rows'] for o in intent['outputs']]

def semantic_signature(intent):
    """Normalize step IDs and output labels without discarding operations/scope."""
    sources={};columns={};outputs={}
    for step in intent['steps']:
        p=json.loads(json.dumps(step['parameters']))
        def replace(value):
            if isinstance(value,str):return columns.get(value,value)
            if isinstance(value,list):return [replace(v) for v in value]
            if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
            return value
        p=replace(p)
        if step['operation']=='aggregate':
            for old,metric in zip(step['parameters']['metrics'],p['metrics']):
                label=metric['function']+'('+str(metric.get('column','*'))+')'
                columns[old['name']]=label;metric['name']=label
        signature={'operation':step['operation'],'sources':[sources.get(s,s) for s in step['sources']],'parameters':p}
        sources[step['id']]=signature
        outputs[step['id']]=dict(columns)
    return [sources[o] for o in intent['outputs']], [outputs[o] for o in intent['outputs']]

def semantic_equal(raw,target,actual,expected):
    a,anames=semantic_signature(raw);b,bnames=semantic_signature(target)
    if a!=b:return False
    def normalize(results,maps):
        return [sorted([json.dumps({names.get(k,k):v for k,v in row.items()},sort_keys=True) for row in rows]) for rows,names in zip(results,maps)]
    return normalize(actual,anames)==normalize(expected,bnames)

def evaluate(model,tokenizer,rows,path,label):
    results=[];model.eval()
    for row in rows:
        start=time.monotonic();encoded=tokenizer(tokenizer.apply_chat_template(row['messages'],tokenize=False,add_generation_prompt=True),return_tensors='pt')
        with torch.inference_mode():out=model.generate(**encoded,max_new_tokens=600,do_sample=False,pad_token_id=tokenizer.eos_token_id)
        content=tokenizer.decode(out[0,encoded['input_ids'].shape[1]:],skip_special_tokens=True)
        valid=False;semantic=False;error=None
        try:
            raw=TableIntent.model_validate_json(content.strip()).model_dump()
            actual=execute_intent(raw,row['datasets']);valid=True
            # Both a requested-operation match and execution equivalence are required:
            # coincidentally equal outputs alone do not count as intent correctness.
            semantic=semantic_equal(raw,row['target'],actual,execute_intent(row['target'],row['datasets']))
        except Exception as exc:error=str(exc)
        results.append({'id':row['id'],'domain':row['domain'],'output':content,'valid':valid,'semantic':semantic,'error':error,'seconds':round(time.monotonic()-start,3)})
        path.write_text(json.dumps({'label':label,'results':results},indent=2),encoding='utf-8')
        if len(results)%10==0:print(label,len(results),'valid',sum(x['valid'] for x in results),'semantic',sum(x['semantic'] for x in results),flush=True)
    return {'count':len(rows),'valid':sum(x['valid'] for x in results),'exact':sum(x['semantic'] for x in results),'semantic':sum(x['semantic'] for x in results)}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model-cache',required=True);parser.add_argument('--steps',type=int,default=200);parser.add_argument('--threads',type=int,default=8);args=parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True);artifacts.ROOT=ROOT/'fixtures'
    torch.set_num_threads(args.threads);torch.manual_seed(20261020);random.seed(20261020)
    train=list(corpus('train',200));heldout=list(corpus('heldout',100))
    for name,rows in [('train',train),('heldout',heldout)]:
        (ROOT/(name+'.jsonl')).write_text('\n'.join(json.dumps({k:v for k,v in r.items() if k!='datasets'}) for r in rows),encoding='utf-8')
    tokenizer=AutoTokenizer.from_pretrained(args.model_cache,local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(args.model_cache,local_files_only=True,dtype=torch.float32,attn_implementation='sdpa').to('cpu')
    before=evaluate(model,tokenizer,heldout,ROOT/'before.json','base')
    attach_lora(model,rank=8,alpha=16);parameters=[p for p in model.parameters() if p.requires_grad];optimizer=torch.optim.AdamW(parameters,lr=.0005,weight_decay=0)
    encoded=[]
    for row in train:
        prompt=tokenizer.apply_chat_template(row['messages'],tokenize=False,add_generation_prompt=True)
        full=tokenizer.apply_chat_template(row['messages']+[{'role':'assistant','content':json.dumps(row['target'],separators=(',',':'))}],tokenize=False)
        x=tokenizer(full,return_tensors='pt');y=x['input_ids'].clone();y[:,:len(tokenizer(prompt)['input_ids'])]=-100;encoded.append((x,y))
    model.train();model.config.use_cache=False;start=time.monotonic();losses=[]
    order=list(range(len(encoded)));random.shuffle(order)
    for step in range(args.steps):
        x,y=encoded[order[step%len(order)]];optimizer.zero_grad(set_to_none=True);loss=model(**x,labels=y).loss
        if not torch.isfinite(loss):raise ValueError('Non-finite loss')
        loss.backward();torch.nn.utils.clip_grad_norm_(parameters,1);optimizer.step();losses.append(float(loss.detach()))
        if (step+1)%10==0:print('training',step+1,'loss',losses[-1],flush=True)
    torch.save(adapter_state(model),ROOT/'adapter.pt')
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    metadata={'base_model':'Qwen/Qwen2.5-0.5B-Instruct','base_snapshot':Path(args.model_cache).name,'method':'general-table attention LoRA','rank':8,'alpha':16,'steps':args.steps,'trainable_parameters':sum(p.numel() for p in parameters),'training_seconds':time.monotonic()-start,'adapter_sha256':digest(ROOT/'adapter.pt'),'train_sha256':digest(ROOT/'train.jsonl'),'heldout_sha256':digest(ROOT/'heldout.jsonl'),'training_examples':len(train),'heldout_examples':len(heldout),'losses':losses,'seed':20261020,'task':'tabular'}
    (ROOT/'adapter_config.json').write_text(json.dumps(metadata,indent=2))
    model.config.use_cache=True;after=evaluate(model,tokenizer,heldout,ROOT/'after.json','adapter')
    active=after['semantic']>before['semantic'] and after['valid']>=before['valid'] and after['semantic']>=90
    report={'before':before,'after':after,'adapter_sha256':metadata['adapter_sha256'],'activation_recommended':active,'target_met':after['semantic']>=90,'limitations':'100 synthetic held-out cases in five domains excluded from training; strict structural plus output equivalence, not a general-purpose data-agent benchmark.'}
    (ROOT/'evaluation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
if __name__=='__main__':main()
