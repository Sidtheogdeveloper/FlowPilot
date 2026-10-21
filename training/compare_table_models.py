"""Compare the trained small model and a stronger downloaded model on broader tasks.

Raw first responses, semantic intent and executed output checks are retained.
This diagnostic complements, rather than replaces, the 100-case adaptation study.
"""
import gc
import json
import time
from pathlib import Path
from types import SimpleNamespace
import torch
from transformers import AutoModelForCausalLM,AutoTokenizer
from app import artifacts
from app.tables import materialize
from app.table_planning import metadata,system_prompt
from app.tabular import TableIntent
from training.train_tables import execute_intent,semantic_equal
from training.lora import load_adapter

ROOT=Path(__file__).resolve().parent/'artifacts'/'model-comparison'

def fixtures():
    def dataset(name,raw):
        frame,_,table,profile=materialize(raw.encode(),name+'.csv')
        return SimpleNamespace(id=name,filename=name+'.csv',rows=len(frame),table_ref=table,profile_ref=profile)
    education=dataset('education','classroom,score,attempts\nA,62,2\nB,91,3\nA,74,0\nB,88,4\n')
    inventory=dataset('inventory','warehouse,stock\nEast,24\nWest,18\nEast,30\nNorth,7\n')
    sensor=dataset('sensor','date,reading,site\n2026-01-31,10,A\n2026-02-01,12,A\n2026-02-02,100,B\n')
    support=dataset('support','queue,minutes,status\nA,4,open\nB,,closed\nA,10,open\n')
    purchases=dataset('purchases','customer_id,net_amount\n001,120\n002,80\n001,35\n003,210\n')
    customers=dataset('customers','customer_id,region\n001,North\n002,South\n003,North\n')
    ambiguous=dataset('scores','classroom,midterm_score,final_score\nA,60,80\nB,75,90\n')
    def step(identity,operation,parameters,sources=None):return {'id':identity,'operation':operation,'sources':sources or ['t0'],'parameters':parameters}
    def agg(function,column='t0.c1',group=None):return step('summary','aggregate',{'group_by':group if group is not None else ['t0.c0'],'metrics':[{'name':'total','function':function,**({'column':column} if function!='rows' else {})}]})
    cases=[]
    def add(question,datasets,steps=None,outputs=None):cases.append({'id':'diagnostic_'+str(len(cases)+1),'question':question,'datasets':datasets,'target':{'steps':steps,'outputs':outputs or [steps[-1]['id']]} if steps else None})
    add('Calculate the mean of score grouped by classroom.',[education],[agg('mean')])
    add('Calculate the median of score grouped by classroom.',[education],[agg('median')])
    add('Sum stock grouped by warehouse.',[inventory],[agg('sum')])
    add('Show the two rows with the highest stock, descending.',[inventory],[step('ranked','sort',{'column':'t0.c1','descending':True,'limit':2})])
    add('Keep stock greater than 20, then sum stock by warehouse.',[inventory],[step('filtered','filter',{'column':'t0.c1','operator':'gt','value':20}),{**agg('sum'),'sources':['filtered']}])
    add('Create a column named ratio equal to score divided by attempts. Preserve all rows.',[education],[step('ratio_step','derive',{'name':'ratio','expression':{'op':'/','left':{'column':'t0.c1'},'right':{'column':'t0.c2'}}})])
    add('Flag unusual stock values using two-sided IQR with multiplier 1.5. Keep every row.',[inventory],[step('flags','outliers',{'column':'t0.c1','multiplier':1.5})])
    add('Create a month column named month from date, then sum reading by month.',[sensor],[step('months','time',{'column':'t0.c0','bucket':'month','name':'month'}),{**agg('sum',group=['month']),'sources':['months']}])
    add('Keep rows dated on or after 2026-02-01.',[sensor],[step('filtered','filter',{'column':'t0.c0','operator':'ge','value':'2026-02-01'})])
    add('Count rows grouped by status.',[support],[agg('rows',group=['t0.c2'])])
    add('Keep rows where status equals open.',[support],[step('filtered','filter',{'column':'t0.c2','operator':'eq','value':'open'})])
    add('Keep rows with missing minutes.',[support],[step('filtered','filter',{'column':'t0.c1','operator':'is_null'})])
    add('Fill missing minutes with 0.',[support],[step('filled','fill_missing',{'column':'t0.c1','value':0})])
    add('Remove duplicate classroom rows using classroom only, keep the first row.',[education],[step('unique','deduplicate',{'columns':['t0.c0']})])
    add('How many rows are in the education dataset?',[education],[agg('rows',group=[])])
    add('Inner join purchases to customers on customer_id, then sum net_amount by region.',[purchases,customers],[step('joined','join',{'left_keys':['t0.c0'],'right_keys':['t1.c0'],'how':'inner'},['t0','t1']),{**agg('sum',group=['t1.c1']),'sources':['joined']}])
    add('Left join purchases to customers on customer_id. Keep all purchase rows.',[purchases,customers],[step('joined','join',{'left_keys':['t0.c0'],'right_keys':['t1.c0'],'how':'left'},['t0','t1'])])
    add('Return the sum of score and the mean of attempts by classroom as two separate analyses.',[education],[agg('sum'),{**agg('mean','t0.c2'),'id':'attempt_summary'}],['summary','attempt_summary'])
    add('Calculate average score by classroom. Ask which score column to use if ambiguous.',[ambiguous])
    add('Join purchases and customers, but ask me to choose the keys and join type first.',[purchases,customers])
    return cases

def evaluate(model,tokenizer,cases,label):
    results=[]
    for case in cases:
        messages=[{'role':'system','content':system_prompt(metadata(case['datasets']))},{'role':'user','content':json.dumps({'question':case['question'],'previous_intent':None,'clarification_history':[]})}]
        text=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
        encoded=tokenizer(text,return_tensors='pt');start=time.monotonic()
        with torch.inference_mode():output=model.generate(**encoded,max_new_tokens=768,do_sample=False,pad_token_id=tokenizer.eos_token_id)
        content=tokenizer.decode(output[0,encoded['input_ids'].shape[1]:],skip_special_tokens=True);valid=False;semantic=False;error=None
        try:
            raw=json.loads(content.strip())
            if case['target'] is None:
                valid=semantic=isinstance(raw,dict) and isinstance(raw.get('clarification'),str) and bool(raw['clarification'].strip())
            else:
                intent=TableIntent.model_validate(raw).model_dump();actual=execute_intent(intent,case['datasets']);valid=True
                semantic=semantic_equal(intent,case['target'],actual,execute_intent(case['target'],case['datasets']))
        except Exception as exc:error=str(exc)
        results.append({'id':case['id'],'question':case['question'],'raw':content,'valid':valid,'semantic':semantic,'error':error,'seconds':time.monotonic()-start})
        (ROOT/(label+'.json')).write_text(json.dumps({'results':results},indent=2),encoding='utf-8')
        print(label,case['id'],'valid',valid,'semantic',semantic,flush=True)
    return {'count':len(results),'valid':sum(r['valid'] for r in results),'semantic':sum(r['semantic'] for r in results),'mean_seconds':sum(r['seconds'] for r in results)/len(results)}

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--small-only',action='store_true');args=parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True);artifacts.ROOT=ROOT/'fixtures';cases=fixtures();torch.set_num_threads(8)
    workspace=Path(__file__).resolve().parents[2]
    small=workspace/'.local_services/hf_cache/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/7ae557604adf67be50417f59c2c2f167def9a775'
    tokenizer=AutoTokenizer.from_pretrained(small,local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(small,local_files_only=True,dtype=torch.float32,attn_implementation='sdpa').to('cpu').eval();load_adapter(model,ROOT.parent/'table-lora')
    current=evaluate(model,tokenizer,cases,'trained-small');del model,tokenizer;gc.collect()
    if args.small_only:
        (ROOT/'trained-small-summary.json').write_text(json.dumps(current,indent=2));print(json.dumps(current),flush=True);return
    manifest=json.loads((workspace/'workflow_application/evidence/stronger_model_download.json').read_text())
    tokenizer=AutoTokenizer.from_pretrained(manifest['snapshot'],local_files_only=True)
    model=AutoModelForCausalLM.from_pretrained(manifest['snapshot'],local_files_only=True,dtype=torch.float32,attn_implementation='sdpa').to('cpu').eval()
    # Dynamic int8 on CPU reduces memory and matrix multiplication cost. Keep the
    # tied embedding/output projection unmodified.
    names={name for name,module in model.named_modules() if isinstance(module,torch.nn.Linear) and name!='lm_head'}
    model=torch.ao.quantization.quantize_dynamic(model,qconfig_spec=names,dtype=torch.qint8,inplace=True)
    candidate=evaluate(model,tokenizer,cases,'qwen3-int8')
    report={'trained_small':current,'stronger_candidate':candidate,'candidate_model':manifest['model'],'candidate_revision':manifest['revision'],'candidate_quantization':'CPU dynamic int8; embedding and tied output projection float32','recommend_stronger':candidate['semantic']>current['semantic'] and candidate['valid']>=current['valid'],'limitations':'20 synthetic diagnostics including 2 clarification cases; broader than the training templates but not statistical proof or the 100-case acceptance benchmark.'}
    (ROOT/'comparison.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
if __name__=='__main__':main()
