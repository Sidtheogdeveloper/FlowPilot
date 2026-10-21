"""Evaluate the CPU GGUF runtime against the saved small-model diagnostics."""
import json
import time
from pathlib import Path
import httpx
from app import artifacts
from app.table_planning import metadata,system_prompt
from app.tabular import TableIntent
from training.compare_table_models import ROOT,fixtures
from training.train_tables import execute_intent,semantic_equal

def main():
    ROOT.mkdir(parents=True,exist_ok=True);artifacts.ROOT=ROOT/'fixtures';cases=fixtures();results=[]
    with httpx.Client(timeout=240,trust_env=False) as client:
        for case in cases:
            messages=[{'role':'system','content':system_prompt(metadata(case['datasets']))},{'role':'user','content':json.dumps({'question':case['question'],'previous_intent':None,'clarification_history':[]})}]
            start=time.monotonic()
            response=client.post('http://127.0.0.1:8003/v1/chat/completions',json={'model':'flowpilot-tables','messages':messages,'temperature':0,'max_tokens':1200,'response_format':{'type':'json_object'}})
            response.raise_for_status();content=response.json()['choices'][0]['message']['content'];valid=False;semantic=False;error=None
            try:
                raw=json.loads(content.strip())
                if case['target'] is None:valid=semantic=isinstance(raw,dict) and isinstance(raw.get('clarification'),str) and bool(raw['clarification'].strip())
                else:
                    intent=TableIntent.model_validate(raw).model_dump();actual=execute_intent(intent,case['datasets']);valid=True
                    semantic=semantic_equal(intent,case['target'],actual,execute_intent(case['target'],case['datasets']))
            except Exception as exc:error=str(exc)
            results.append({'id':case['id'],'question':case['question'],'raw':content,'valid':valid,'semantic':semantic,'error':error,'seconds':time.monotonic()-start})
            (ROOT/'qwen4b-gguf.json').write_text(json.dumps({'results':results},indent=2),encoding='utf-8')
            print(case['id'],'valid',valid,'semantic',semantic,flush=True)
    small=json.loads((ROOT/'trained-small.json').read_text())['results']
    summary=lambda rows:{'count':len(rows),'valid':sum(r['valid'] for r in rows),'semantic':sum(r['semantic'] for r in rows),'mean_seconds':sum(r['seconds'] for r in rows)/len(rows)}
    before,after=summary(small),summary(results)
    report={'trained_small':before,'stronger_runtime':after,'recommend_stronger':after['semantic']>before['semantic'] and after['valid']>=before['valid'],'runtime':'Qwen3-4B-Instruct-2507 Q4_K_M, Ollama CPU, JSON-object decoding','model_manifest':'evidence/stronger_4b_download.json','limitations':'20 synthetic diagnostics including two clarification cases. This compares complete runtime configurations; JSON-constrained decoding differs from the Transformers runtime. No broad accuracy claim.'}
    (ROOT/'comparison-4b.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
if __name__=='__main__':main()
