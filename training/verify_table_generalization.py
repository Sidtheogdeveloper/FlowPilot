"""Fresh 100-case acceptance probe with independent, explicitly specified answers.

Freeze the manifest before inference. Never use target intents or answers in
prompts. Cases cover 18 operation patterns and two ambiguities in five schemas.
This is synthetic coverage, not a population accuracy or real-user benchmark.
"""
import csv
import argparse
import hashlib
import io
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from app import artifacts
from app.tables import materialize
from app.table_planning import propose_table
from app.tabular import Clarification
from training.train_tables import execute_intent, semantic_signature

ROOT=Path(__file__).resolve().parent/'artifacts'/'generalization-v2'
DOMAINS=[
    ('agriculture',['field_name','harvest_kg','plots','harvest_date','grade']),
    ('transport',['route_code','load_kg','trips','departure_date','status']),
    ('energy',['turbine','energy_kwh','hours','reading_date','state']),
    ('research',['sample_group','response_value','trials','measured_at','condition']),
    ('public_services',['district','cases_processed','staff','recorded_at','status']),
]

def matches_answer(intent,target,actual,expected):
    signature,names=semantic_signature(intent);wanted,wanted_names=semantic_signature(target)
    if signature!=wanted:return False
    def normalize(outputs,labels):
        return [[{mapping.get(k,k):float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) else v for k,v in row.items()} for row in rows] for rows,mapping in zip(outputs,labels)]
    found,reference=normalize(actual,names),normalize(expected,wanted_names)
    if any(s['operation']=='sort' for s in target['steps']):return found==reference
    ordered=lambda rows:sorted(rows,key=lambda row:json.dumps(row,sort_keys=True))
    return [ordered(rows) for rows in found]==[ordered(rows) for rows in reference]

def cases():
    result=[]
    def dataset(name,headers,rows):
        buffer=io.StringIO();writer=csv.writer(buffer);writer.writerow(headers);writer.writerows(rows)
        data=buffer.getvalue();frame,_,table,profile=materialize(data.encode(),name+'.csv')
        d=SimpleNamespace(id=name,filename=name+'.csv',rows=len(frame),table_ref=table,profile_ref=profile)
        return d,{'id':name,'filename':d.filename,'csv':data}
    def step(identity,operation,parameters,sources=None):return {'id':identity,'operation':operation,'sources':sources or ['t0'],'parameters':parameters}
    for index,(domain,headers) in enumerate(DOMAINS):
        order=list(range(5));order=order[index:]+order[:index]
        g,v,u,date,status=[f't0.c{order.index(k)}' for k in range(5)]
        gh,vh,uh,dh,sh=headers
        original=[['A',12,3,'2027-01-31','active'],['B',30,5,'2027-02-01','closed'],['A',18,0,'2027-02-03','active'],['C',6,2,'2027-03-01','closed']]
        primary,primary_spec=dataset(domain,[headers[k] for k in order],[[row[k] for k in order] for row in original])
        def rows(indices):
            out=[]
            for i in indices:
                row=original[i]
                out.append({g:row[0],v:float(row[1]),u:float(row[2]),date:row[3]+'T00:00:00+00:00',status:row[4]})
            return out
        def agg(fn,column=v,group=g,identity='summary',source='t0',name='answer'):
            return step(identity,'aggregate',{'group_by':[] if group is None else [group],'metrics':[{'name':name,'function':fn,**({'column':column} if fn!='rows' else {})}]},[source])
        def add(question,steps=None,expected=None,outputs=None,datasets=None,specs=None,pattern=None):
            result.append({'id':domain+'_'+str(len(result)%20+1),'domain':domain,'pattern':pattern or (steps[-1]['operation'] if steps else 'clarification'),
                           'question':question,'datasets':datasets or [primary],'sources':specs or [primary_spec],
                           'target':{'steps':steps,'outputs':outputs or [steps[-1]['id']]} if steps else None,'expected':expected})
        for fn,values in [('sum',[30,30,6]),('mean',[15,30,6]),('median',[15,30,6])]:
            add(f'Calculate the {fn} of {vh} grouped by {gh}.',[agg(fn)],[[{g:k,'answer':n} for k,n in zip('ABC',values)]])
        add(f'How many rows are in {domain}?',[agg('rows',group=None)],[[{'answer':4}]])
        add(f'Keep {vh} greater than 15, then sum {vh} by {gh}.',[
            step('selected','filter',{'column':v,'operator':'gt','value':15}),agg('sum',source='selected')],[[{g:'A','answer':18},{g:'B','answer':30}]])
        add(f'Show the top 2 rows by {vh}, descending.',[step('ranked','sort',{'column':v,'descending':True,'limit':2})],[rows([1,2])])
        missing=[list(r) for r in original];missing[2][1]=''
        nullable,nullable_spec=dataset(domain+'_missing',[headers[k] for k in order],[[r[k] for k in order] for r in missing])
        expected=rows([2]);expected[0][v]=None
        add(f'Keep rows with missing {vh}.',[step('missing','filter',{'column':v,'operator':'is_null'})],[expected],datasets=[nullable],specs=[nullable_spec])
        expected=rows(range(4));expected[2][v]=0
        add(f'Fill missing {vh} with 0.',[step('filled','fill_missing',{'column':v,'value':0})],[expected],datasets=[nullable],specs=[nullable_spec])
        add(f'Keep {vh} greater than 999.',[step('selected','filter',{'column':v,'operator':'gt','value':999})],[[]])
        expected=rows(range(4))
        for row,ratio in zip(expected,[4,6,None,3]):row['ratio']=ratio
        add(f'Create a column named ratio equal to {vh} divided by {uh}. Preserve all rows.',[step('ratio_step','derive',{'name':'ratio','expression':{'op':'/','left':{'column':v},'right':{'column':u}}})],[expected])
        add(f'Create a month column named month from {dh}, then sum {vh} by month.',[
            step('months','time',{'column':date,'bucket':'month','name':'month'}),agg('sum',group='month',source='months')],[[{'month':'2027-01','answer':12},{'month':'2027-02','answer':48},{'month':'2027-03','answer':6}]])
        add(f'Remove duplicate {gh} rows using {gh} only, keep the first row.',[step('unique','deduplicate',{'columns':[g]})],[rows([0,1,3])])
        unusual,unusual_spec=dataset(domain+'_tail',[vh],[[1],[2],[3],[4],[100]])
        add(f'Flag unusual {vh} using two-sided IQR with multiplier 1.5. Keep every row.',[step('flagged','outliers',{'column':'t0.c0','multiplier':1.5})],[[{'t0.c0':n,'unusual':n==100} for n in [1,2,3,4,100]]],datasets=[unusual],specs=[unusual_spec])
        lookup,lookup_spec=dataset(domain+'_lookup',[gh,'territory'],[['A','East'],['B','West']])
        join=lambda how:step('joined','join',{'left_keys':[g],'right_keys':['t1.c0'],'how':how},['t0','t1'])
        add(f'Inner join {domain} to {domain}_lookup on {gh}, then sum {vh} by territory.',[join('inner'),agg('sum',group='t1.c1',source='joined')],[[{'t1.c1':'East','answer':30},{'t1.c1':'West','answer':30}]],datasets=[primary,lookup],specs=[primary_spec,lookup_spec])
        expected=rows(range(4))
        for row,key,region in zip(expected,['A','B','A',None],['East','West','East',None]):row.update({'t1.c0':key,'t1.c1':region})
        add(f'Left join {domain} to {domain}_lookup on {gh}. Keep all {domain} rows.',[join('left')],[expected],datasets=[primary,lookup],specs=[primary_spec,lookup_spec])
        add(f'Keep rows where {sh} equals active.',[step('selected','filter',{'column':status,'operator':'eq','value':'active'})],[rows([0,2])])
        minmax=step('bounds','aggregate',{'group_by':[g],'metrics':[{'name':'low','function':'min','column':v},{'name':'high','function':'max','column':v}]})
        add(f'Return the min and max of {vh} grouped by {gh}.',[minmax],[[{g:k,'low':lo,'high':hi} for k,lo,hi in [('A',12,18),('B',30,30),('C',6,6)]]])
        add(f'Return the sum of {vh} and mean of {uh} by {gh} as two separate analyses.',[agg('sum'),agg('mean',column=u,identity='staff_summary')],[[{g:k,'answer':n} for k,n in zip('ABC',[30,30,6])],[{g:k,'answer':n} for k,n in zip('ABC',[1.5,5,2])]],outputs=['summary','staff_summary'])
        ambiguous,spec=dataset(domain+'_ambiguous',[gh,'early_score','late_score'],[['A',10,20],['B',30,40]])
        add(f'Calculate the mean score grouped by {gh}.',datasets=[ambiguous],specs=[spec],pattern='ambiguous_column')
        ambiguous_date,spec=dataset(domain+'_dates',[dh,vh],[['01/02/2027',10],['03/04/2027',20]])
        add(f'Group {vh} by month using {dh}. I have not specified whether dates are day/month/year or month/day/year.',datasets=[ambiguous_date],specs=[spec],pattern='ambiguous_date')
    assert len(result)==100
    return result

def main():
    global ROOT
    parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
    if args.output:ROOT=Path(args.output).resolve()
    ROOT.mkdir(parents=True,exist_ok=True);artifacts.ROOT=ROOT/'fixtures'
    os.environ.update(TABLE_INFERENCE_URL='http://127.0.0.1:8003/v1/chat/completions',TABLE_MODEL='flowpilot-tables',TABLE_JSON_MODE='1')
    manifest=cases();serial=[{k:v for k,v in c.items() if k!='datasets'} for c in manifest]
    encoded=json.dumps(serial,ensure_ascii=False,indent=2).encode()
    (ROOT/'manifest.json').write_bytes(encoded)
    app=Path(__file__).resolve().parents[1]/'app'
    config={'model':'flowpilot-tables','runtime':'local Ollama CPU Qwen3-4B-Instruct-2507 Q4_K_M','source_sha256':{name:hashlib.sha256((app/name).read_bytes()).hexdigest() for name in ('table_planning.py','table_generation_schema.py','tabular.py','tables.py')},'manifest_sha256':hashlib.sha256(encoded).hexdigest(),'started_unix':time.time()}
    (ROOT/'configuration.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    digest=hashlib.sha256(encoded).hexdigest();results=[]
    for case in manifest:
        start=time.monotonic();valid=False;semantic=False;intent=None;record={};error=None;actual=None
        try:
            intent,record=propose_table(case['question'],case['datasets'],'huggingface')
            actual=execute_intent(intent,case['datasets']);valid=True
            if case['target']:
                semantic=matches_answer(intent,case['target'],actual,case['expected'])
        except Clarification as exc:
            valid=semantic=case['target'] is None;error=str(exc)
        except Exception as exc:
            error=str(exc);record={'raw_outputs':getattr(exc,'raw_outputs',[]),'validation_findings':getattr(exc,'findings',[])}
        results.append({'id':case['id'],'domain':case['domain'],'pattern':case['pattern'],'question':case['question'],'ambiguous':case['target'] is None,'intent':intent,'actual':actual,'valid':valid,'semantic':semantic,'error':error,'record':record,'seconds':time.monotonic()-start})
        (ROOT/'results.json').write_text(json.dumps({'manifest_sha256':digest,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
        print(case['id'],'valid',valid,'semantic',semantic,flush=True)
    supported=[r for r in results if not r['ambiguous']];ambiguous=[r for r in results if r['ambiguous']]
    report={'count':len(results),'supported':len(supported),'supported_semantic':sum(r['semantic'] for r in supported),'ambiguous':len(ambiguous),'correct_clarifications':sum(r['semantic'] for r in ambiguous),'manifest_sha256':digest,'limitations':'Synthetic acceptance probe across five new schemas with 18 operation patterns; target intents and manually specified answers never sent to the model. Real-user and statistical generalization remain unproven. Join execution is authorized in the evaluation harness only; persisted human confirmation is separately tested.'}
    report['target_met']=report['supported_semantic']>=.9*len(supported) and report['correct_clarifications']==len(ambiguous)
    (ROOT/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)

if __name__=='__main__':main()
