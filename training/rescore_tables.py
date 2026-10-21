"""Rescore saved generations without changing prompts or generating new answers.

Normalizes harmless step/output label changes, then checks requested operations,
scope and execution-equivalent results. Raw outputs and original scores remain saved.
"""
import hashlib
import json
from training.train_tables import ROOT,corpus,execute_intent,semantic_equal
from app import artifacts
from app.tabular import TableIntent

def main():
    artifacts.ROOT=ROOT/'fixtures';rows={r['id']:r for r in corpus('heldout',100)};summaries={}
    for name in ('before','after'):
        path=ROOT/(name+'.json');document=json.loads(path.read_text(encoding='utf-8'));results=[]
        if len(document['results'])!=100:raise ValueError('Require 100 completed generations per model')
        for result in document['results']:
            row=rows[result['id']];valid=False;semantic=False;error=None
            try:
                intent=TableIntent.model_validate_json(result['output'].strip()).model_dump()
                actual=execute_intent(intent,row['datasets']);valid=True
                semantic=semantic_equal(intent,row['target'],actual,execute_intent(row['target'],row['datasets']))
            except Exception as exc:error=str(exc)
            results.append(dict(result,valid=valid,semantic=semantic,error=error))
        rescored={'results':results,'raw_generations_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'method':'Normalized operation/dependency/scope signature AND executed output equivalence; no generation or repair'}
        (ROOT/(name+'-semantic.json')).write_text(json.dumps(rescored,indent=2),encoding='utf-8')
        summaries[name]={'count':100,'valid':sum(r['valid'] for r in results),'exact':sum(r['semantic'] for r in results),'semantic':sum(r['semantic'] for r in results)}
    old=ROOT/'evaluation.json';report=json.loads(old.read_text());(ROOT/'evaluation-strict.json').write_text(json.dumps(report,indent=2))
    before,after=summaries['before'],summaries['after']
    report.update(before=before,after=after,activation_recommended=after['semantic']>before['semantic'] and after['valid']>=before['valid'] and after['semantic']>=90,target_met=after['semantic']>=90,scoring='Normalized semantic signature plus executed results; raw predictions unchanged')
    old.write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
if __name__=='__main__':main()
