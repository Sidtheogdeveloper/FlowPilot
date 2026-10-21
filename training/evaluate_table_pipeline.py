"""Development diagnostics for the complete corrected planning path, not raw LLMs."""
import json
import os
import time
from app import artifacts
from app.table_planning import propose_table
from app.tabular import Clarification,compile_table_intent
from training.compare_table_models import ROOT,fixtures
from training.train_tables import execute_intent,semantic_equal

def main():
    os.environ['TABLE_INFERENCE_URL']='http://127.0.0.1:8003/v1/chat/completions'
    os.environ['TABLE_MODEL']='flowpilot-tables';os.environ['TABLE_JSON_MODE']='1'
    artifacts.ROOT=ROOT/'fixtures';results=[]
    for case in fixtures():
        start=time.monotonic();valid=False;semantic=False;error=None;record={};intent=None
        try:
            intent,record=propose_table(case['question'],case['datasets'],'huggingface')
            if case['target'] is None:
                try:compile_table_intent(intent,case['datasets'],[])
                except Clarification:valid=semantic=True
            else:
                actual=execute_intent(intent,case['datasets']);valid=True
                semantic=semantic_equal(intent,case['target'],actual,execute_intent(case['target'],case['datasets']))
        except Clarification as exc:
            valid=semantic=case['target'] is None;error=str(exc)
        except Exception as exc:
            error=str(exc)
            record={'raw_outputs':getattr(exc,'raw_outputs',[]),'validation_findings':getattr(exc,'findings',[])}
        results.append({'id':case['id'],'question':case['question'],'intent':intent,'record':record,'valid':valid,'semantic':semantic,'error':error,'seconds':time.monotonic()-start})
        (ROOT/'pipeline.json').write_text(json.dumps({'results':results},indent=2),encoding='utf-8')
        print(case['id'],'valid',valid,'semantic',semantic,flush=True)
    report={'count':len(results),'valid_or_correct_clarification':sum(r['valid'] for r in results),'semantic':sum(r['semantic'] for r in results),'limitations':'20 development diagnostics used to identify integration failures; not an untouched test set or a general accuracy estimate. Execution comparison assumes confirmation of correct proposed join keys; actual confirmation persistence is tested separately.'}
    (ROOT/'pipeline-summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
if __name__=='__main__':main()
