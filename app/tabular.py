"""Typed analytical intent, schema propagation and deterministic table execution.

The model chooses operations, never executable code or artifact paths.
"""
import copy
import html
import json
import math
import re
from typing import Literal
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from . import artifacts
from .tables import MAX_ROWS

OPS = ('profile', 'convert', 'filter', 'join', 'derive', 'aggregate', 'time', 'sort', 'outliers', 'deduplicate', 'fill_missing')

class Step(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    operation: Literal['profile','convert','filter','join','derive','aggregate','time','sort','outliers','deduplicate','fill_missing']
    sources: list[str] = Field(min_length=1, max_length=2)
    parameters: dict = Field(default_factory=dict)

class TableIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    steps: list[Step] = Field(min_length=1, max_length=20)
    outputs: list[str] = Field(min_length=1, max_length=10)

class Clarification(ValueError):
    def __init__(self, question, candidates=None):
        super().__init__(question)
        self.question = question
        self.candidates = candidates or []

def exact(params, required, optional=()):
    if not isinstance(params, dict) or not set(required) <= set(params) or set(params) - set(required) - set(optional):
        raise ValueError('Missing or unknown operation parameters')

def column(schema, name, kind=None):
    if name not in schema:
        raise ValueError('Unknown column: ' + str(name))
    if kind and schema[name]['type'] != kind:
        raise Clarification(f'Column {name} needs an explicit {kind} conversion before this operation', [name])

def expression_type(expr, schema, depth=0):
    if depth > 8 or not isinstance(expr, dict):
        raise ValueError('Invalid arithmetic expression')
    if set(expr) == {'column'}:
        column(schema, expr['column'], 'number')
    elif set(expr) == {'value'}:
        v = expr['value']
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('Expression constant must be finite numeric')
    elif set(expr) == {'op', 'left', 'right'} and expr['op'] in ('+', '-', '*', '/'):
        expression_type(expr['left'], schema, depth+1)
        expression_type(expr['right'], schema, depth+1)
    else:
        raise ValueError('Only column references, numbers and basic arithmetic are allowed')

def propagate(op, p, schemas, confirmations=()):
    s = copy.deepcopy(schemas[0])
    if op == 'profile':
        exact(p, [])
        return {'column': {'name':'Column','type':'text'}, 'non_null': {'name':'Non-null rows','type':'number'}, 'missing': {'name':'Missing values','type':'number'}, 'distinct': {'name':'Distinct values','type':'number'}, 'minimum':{'name':'Minimum','type':'number'},'maximum':{'name':'Maximum','type':'number'},'mean':{'name':'Mean','type':'number'}}
    if op == 'convert':
        exact(p, ['column','type'], ['format'])
        column(s, p['column'])
        if p['type'] not in ('number','datetime','text'):
            raise ValueError('Unsupported conversion')
        if p['type'] == 'datetime' and not p.get('format'):
            raise Clarification('Specify the date format, for example %d/%m/%Y', [p['column']])
        s[p['column']]['type'] = p['type']
    elif op == 'filter':
        exact(p, ['column','operator'], ['value'])
        column(s, p['column'])
        if p['operator'] not in ('eq','ne','gt','ge','lt','le','is_null','not_null','contains'):
            raise ValueError('Unsupported filter operator')
        if p['operator'] not in ('is_null','not_null') and 'value' not in p:
            raise ValueError('Filter needs a value')
        if p['operator'] in ('gt','ge','lt','le') and s[p['column']]['type'] not in ('number','datetime'):
            raise Clarification('Choose an explicit numeric or date conversion for ordered comparison', [p['column']])
        if p['operator'] not in ('is_null','not_null'):
            kind=s[p['column']]['type'];value=p.get('value')
            if kind=='number' and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                raise ValueError('Numeric filters require a finite JSON number')
            if kind in ('text','unresolved') and not isinstance(value,str):raise ValueError('Text filters require a text value')
            if kind=='datetime' and (not isinstance(value,str) or not re.match(r'^\d{4}-\d{2}-\d{2}',value) or pd.isna(pd.to_datetime(value,errors='coerce',utc=True))):
                raise Clarification('Use a complete ISO date such as 2026-02-01 for this filter',[p['column']])
            if p['operator']=='contains' and kind not in ('text','unresolved'):raise ValueError('Contains requires a text column')
    elif op == 'join':
        exact(p, ['left_keys','right_keys','how'], ['confirmed','many_to_many_confirmed'])
        if len(schemas) != 2 or p['how'] not in ('inner','left'):
            raise ValueError('Join requires two tables and inner/left semantics')
        left, right = p['left_keys'], p['right_keys']
        if not isinstance(left,list) or not isinstance(right,list) or not left or len(left) != len(right) or len(set(left))!=len(left) or len(set(right))!=len(right):
            raise ValueError('Join requires nonempty matching unique key lists')
        for a,b in zip(left,right):
            column(s,a);column(schemas[1],b)
            if s[a]['type'] != schemas[1][b]['type'] or s[a]['type']=='unresolved':
                raise Clarification('Join keys need compatible explicit types', [a,b])
        signature = json.dumps({'left_keys':left,'right_keys':right,'how':p['how']},sort_keys=True)
        if signature not in confirmations:
            raise Clarification('Confirm join keys and join type: '+signature, [signature])
        if set(s) & set(schemas[1]):
            raise ValueError('Join inputs contain overlapping column identities')
        s.update(copy.deepcopy(schemas[1]))
    elif op == 'derive':
        exact(p,['name','expression'])
        if not isinstance(p['name'],str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',p['name']) or p['name'] in s:
            raise ValueError('Derived column needs a new simple name')
        expression_type(p['expression'],s)
        s[p['name']]={'name':p['name'],'type':'number'}
    elif op == 'aggregate':
        exact(p,['group_by','metrics'])
        if not isinstance(p['group_by'],list) or len(set(p['group_by']))!=len(p['group_by']) or not isinstance(p['metrics'],list) or not p['metrics']:
            raise ValueError('Invalid aggregation')
        for key in p['group_by']:column(s,key)
        result={k:s[k] for k in p['group_by']}
        for metric in p['metrics']:
            exact(metric,['name','function'],['column'])
            f=metric['function'];name=metric['name']
            if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',name) or name in result or f not in ('sum','mean','min','max','count','median','nunique','rows'):
                raise ValueError('Invalid aggregate metric')
            if f!='rows':column(s,metric.get('column'),'number' if f in ('sum','mean','median') else None)
            result[name]={'name':name,'type':s[metric['column']]['type'] if f in ('min','max') else 'number'}
        return result
    elif op == 'time':
        exact(p,['column','bucket','name'])
        column(s,p['column'],'datetime')
        if p['bucket'] not in ('day','week','month','quarter','year') or not isinstance(p['name'],str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',p['name']) or p['name'] in s:
            raise ValueError('Invalid time bucket')
        s[p['name']]={'name':p['name'],'type':'text'}
    elif op == 'sort':
        exact(p,['column'],['descending','limit']);column(s,p['column'])
        if s[p['column']]['type']=='unresolved':raise Clarification('Choose an explicit type conversion before sorting this mixed column',[p['column']])
        if not isinstance(p.get('descending',False),bool) or isinstance(p.get('limit',10),bool) or not isinstance(p.get('limit',10),int) or not 1<=p.get('limit',10)<=10000:
            raise ValueError('Invalid sort parameters')
    elif op == 'outliers':
        exact(p,['column'],['multiplier']);column(s,p['column'],'number')
        k=p.get('multiplier',1.5)
        if isinstance(k,bool) or not isinstance(k,(int,float)) or not .1<=k<=10:raise ValueError('Invalid IQR multiplier')
        if 'unusual' in s:raise ValueError('Outlier output name collision')
        s['unusual']={'name':'Outside IQR bounds','type':'boolean'}
    elif op == 'deduplicate':
        exact(p,['columns']);
        if not p['columns']:raise ValueError('Select deduplication columns')
        for key in p['columns']:column(s,key)
    elif op == 'fill_missing':
        exact(p,['column','value']);column(s,p['column'])
        if s[p['column']]['type']=='number' and (isinstance(p['value'],bool) or not isinstance(p['value'],(int,float)) or not math.isfinite(p['value'])):raise ValueError('Fill value type mismatch')
        if s[p['column']]['type'] in ('text','unresolved') and not isinstance(p['value'],str):raise ValueError('Fill value type mismatch')
        if s[p['column']]['type']=='datetime':raise ValueError('Date filling is not supported')
    else:raise ValueError('Unknown table operation')
    return s

def compile_table_intent(raw, datasets, confirmations=()):
    from .tables import ensure_metadata
    intent=TableIntent.model_validate(raw)
    schemas={}; tasks=[]; profiles={}
    for i,dataset in enumerate(datasets):
        alias=f't{i}';description=ensure_metadata(dataset);profiles[dataset.id]=dataset.profile_ref
        schema={alias+'.'+c['id']:{'name':c['name'],'type':c['type']} for c in description['columns']}
        schemas[alias]=schema
        tasks.append({'id':alias,'handler':'table_load','version':'1','inputs':{'dataset':'$dataset:'+dataset.id},'depends_on':[],
                      'parameters':{'alias':alias,'schema':schema},'resources':{'cpu':1,'memory_mb':memory_for(dataset.rows,len(schema))}})
    for step in intent.steps:
        if step.id in schemas or any(src not in schemas for src in step.sources) or len(step.sources)!=(2 if step.operation=='join' else 1):
            raise ValueError('Duplicate step or missing/invalid source')
        schema=propagate(step.operation,step.parameters,[schemas[src] for src in step.sources],confirmations)
        schemas[step.id]=schema
        p=copy.deepcopy(step.parameters)
        if step.operation=='join':
            p['confirmed']=True
            signature=json.dumps({'left_keys':p['left_keys'],'right_keys':p['right_keys'],'how':p['how']},sort_keys=True)
            p['many_to_many_confirmed']='many:'+signature in confirmations
        tasks.append({'id':step.id,'handler':'table_'+step.operation,'version':'1',
                      'inputs':{('left' if step.operation=='join' else 'table'):step.sources[0],**({'right':step.sources[1]} if step.operation=='join' else {})},
                      'depends_on':step.sources,'parameters':p,'resources':{'cpu':1,'memory_mb':memory_for(sum(d.rows for d in datasets),len(schema),step.operation=='join')}})
    if any(o not in schemas or o.startswith('t') and o[1:].isdigit() for o in intent.outputs) or len(set(intent.outputs))!=len(intent.outputs):
        raise ValueError('Report outputs must reference unique analysis steps')
    step_names={step.id:step.operation.replace('_',' ').title() for step in intent.steps}
    tasks.append({'id':'report','handler':'table_report','version':'1','inputs':{'result'+str(i):v for i,v in enumerate(intent.outputs)},
                  'depends_on':intent.outputs,'parameters':{'question':'','titles':{'result'+str(i):step_names[v] for i,v in enumerate(intent.outputs)}},'resources':{'cpu':1,'memory_mb':128}})
    # Keep only requested branches and their ancestors.
    required=set(intent.outputs);changed=True
    while changed:
        old=len(required)
        for task in tasks:
            if task['id'] in required:required.update(task['depends_on'])
        changed=len(required)!=old
    unused={step.id for step in intent.steps}-required
    if unused:
        raise ValueError('Unused analysis steps: '+', '.join(sorted(unused))+'. Report outputs must include the final requested calculations, not only intermediate rows')
    tasks=[t for t in tasks if t['id'] in required or t['id']=='report']
    return {'tasks':tasks},profiles

def memory_for(rows,columns,join=False):
    # Conservative JSON/Python-object working-set declaration, not a measured limit.
    return max(128, min(262144, int(rows*max(columns,1)*160*(3 if join else 2)/1048576)+128))

def validate_generic_plan(plan):
    schemas={};by={t.id:t for t in plan.tasks}
    import networkx as nx
    g=nx.DiGraph();g.add_nodes_from(by)
    for t in plan.tasks:
        for dep in t.depends_on:g.add_edge(dep,t.id)
    if not nx.is_directed_acyclic_graph(g):raise ValueError('Graph contains a cycle')
    for tid in nx.topological_sort(g):
        t=by[tid];op=t.handler.removeprefix('table_')
        if op in ('load_chunk','partial','reduce'):
            from .partitioning import validate_partition_node
            schemas[tid]=validate_partition_node(t,schemas,by)
        elif op=='load':
            exact(t.parameters,['alias','schema'])
            if not t.inputs.get('dataset','').startswith('$dataset:'):raise ValueError('Table loader requires dataset identity')
            schemas[tid]=t.parameters['schema']
        elif op=='report':
            exact(t.parameters,['question'],['titles'])
            if 'titles' in t.parameters and (not isinstance(t.parameters['titles'],dict) or any(not isinstance(v,str) for v in t.parameters['titles'].values())):raise ValueError('Report titles must be text')
            if not t.inputs or any(src not in schemas for src in t.inputs.values()):raise ValueError('Report requires analytical inputs')
        else:
            p=t.parameters
            confirmations=[]
            if op=='join' and p.get('confirmed'):
                confirmations=[json.dumps({k:p[k] for k in ('left_keys','right_keys','how')},sort_keys=True)]
            sources=[schemas[t.inputs[k]] for k in (('left','right') if op=='join' else ('table',))]
            schemas[tid]=propagate(op,p,sources,confirmations)
    return schemas

def validate_table_output(obj):
    if not isinstance(obj,dict) or not isinstance(obj.get('schema'),dict) or not isinstance(obj.get('rows'),list) or len(obj['rows'])>MAX_ROWS or not isinstance(obj.get('audit'),list):
        raise ValueError('Invalid table result envelope')
    for meta in obj['schema'].values():
        if not isinstance(meta,dict) or meta.get('type') not in ('number','text','datetime','unresolved','boolean'):raise ValueError('Invalid column type')
    for row in obj['rows']:
        if set(row)!=set(obj['schema']):raise ValueError('Output fields do not match schema')
        for k,v in row.items():
            kind=obj['schema'][k]['type']
            if v is None:continue
            if kind=='number' and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)):raise ValueError('Non-finite or invalid number')
            if kind in ('text','datetime','unresolved') and not isinstance(v,str):raise ValueError('Invalid text/date')
            if kind=='datetime' and pd.isna(pd.to_datetime(v,errors='coerce',utc=True)):raise ValueError('Invalid datetime')
            if kind=='boolean' and not isinstance(v,bool):raise ValueError('Invalid flag')
    return obj

def evaluate_expr(expr,df):
    if 'column' in expr:return df[expr['column']]
    if 'value' in expr:return expr['value']
    a,b=evaluate_expr(expr['left'],df),evaluate_expr(expr['right'],df)
    if expr['op']=='+':return a+b
    if expr['op']=='-':return a-b
    if expr['op']=='*':return a*b
    if isinstance(b,pd.Series):b=b.mask(b==0)
    elif b==0:return pd.Series([float('nan')]*len(df),index=df.index)
    return a/b

def check_join(left,right,p):
    lk,rk=p['left_keys'],p['right_keys']
    valid_left=left.dropna(subset=lk);valid_right=right.dropna(subset=rk)
    lc=valid_left.groupby(lk,dropna=False).size().to_dict()
    rc=valid_right.groupby(rk,dropna=False).size().to_dict()
    many=any(n>1 and rc.get(k,0)>1 for k,n in lc.items())
    if many and not p.get('many_to_many_confirmed'):
        signature=json.dumps({k:p[k] for k in ('left_keys','right_keys','how')},sort_keys=True)
        raise Clarification('Confirm many-to-many join before execution',['many:'+signature])
    predicted=sum(n*(max(rc.get(k,0),1) if p['how']=='left' else rc.get(k,0)) for k,n in lc.items())
    if p['how']=='left':predicted+=len(left)-len(valid_left)
    if predicted>MAX_ROWS:raise ValueError('Join exceeds one million intermediate rows')
    return {'output_rows':int(predicted),'many_to_many':many,'left_null_keys':len(left)-len(valid_left),'right_null_keys':len(right)-len(valid_right),'unmatched_left_rows':int(sum(n for k,n in lc.items() if not rc.get(k)))+len(left)-len(valid_left)}

def execute_table(handler,inputs,p):
    op=handler.removeprefix('table_')
    if op in ('load_chunk','partial','reduce'):
        from .partitioning import execute_partition
        return execute_partition(handler,inputs,p)
    if op=='report':return table_report(inputs,p)
    if op=='load':
        raw=artifacts.read_json(inputs['dataset']);schema=p['schema']
        expected={p['alias']+'.'+key:meta for key,meta in raw['schema'].items()}
        if schema!=expected:raise ValueError('Dataset profile/schema mismatch')
        rows=[]
        for row in raw['rows']:
            result={}
            for key,v in row.items():
                key=p['alias']+'.'+key;kind=schema[key]['type']
                result[key]=None if isinstance(v,str) and not v.strip() else float(v) if kind=='number' else pd.to_datetime(v,utc=True).isoformat() if kind=='datetime' else v
            rows.append(result)
        return artifacts.put_json(validate_table_output({'rows':rows,'schema':schema,'audit':[]}))
    original=artifacts.read_json(inputs.get('table',inputs.get('left')))
    df=pd.DataFrame(original['rows'],columns=original['schema']);audit=list(original['audit'])
    other=artifacts.read_json(inputs['right']) if op=='join' else None
    confirmations=[]
    if op=='join' and p.get('confirmed'):confirmations=[json.dumps({k:p[k] for k in ('left_keys','right_keys','how')},sort_keys=True)]
    schema=propagate(op,p,[original['schema']]+([other['schema']] if other else []),confirmations)
    if op=='profile':
        records=[]
        for key,meta in original['schema'].items():
            numeric=meta['type']=='number' and df[key].notna().any()
            records.append({'column':meta['name'],'non_null':int(df[key].notna().sum()),'missing':int(df[key].isna().sum()),'distinct':int(df[key].nunique()),'minimum':float(df[key].min()) if numeric else None,'maximum':float(df[key].max()) if numeric else None,'mean':float(df[key].mean()) if numeric else None})
        df=pd.DataFrame(records,columns=schema)
        audit.append({'operation':'profile','input_rows':len(original['rows'])})
    elif op=='convert':
        col=p['column'];before=int(df[col].isna().sum())
        if p['type']=='number':df[col]=pd.to_numeric(df[col],errors='raise')
        elif p['type']=='datetime':df[col]=pd.to_datetime(df[col],format=p['format'],errors='raise',utc=True).map(lambda v:None if pd.isna(v) else v.isoformat())
        else:df[col]=df[col].map(lambda v:None if pd.isna(v) else str(v))
        audit.append({'operation':'convert','column':col,'type':p['type'],'format':p.get('format'),'nulls_before':before})
    elif op=='filter':
        series=df[p['column']];value=p.get('value');operator=p['operator']
        kind=original['schema'][p['column']]['type']
        if operator not in ('is_null','not_null'):
            if kind=='number':value=float(value)
            elif kind=='datetime':series=pd.to_datetime(series,utc=True);value=pd.to_datetime(value,utc=True)
        mask=series.isna() if operator=='is_null' else series.notna() if operator=='not_null' else series.astype('string').str.contains(str(value),regex=False,na=False) if operator=='contains' else {'eq':lambda:series==value,'ne':lambda:series!=value,'gt':lambda:series>value,'ge':lambda:series>=value,'lt':lambda:series<value,'le':lambda:series<=value}[operator]() & series.notna()
        df=df[mask.fillna(False)];audit.append({'operation':'filter','parameters':p,'excluded_rows':len(original['rows'])-len(df)})
    elif op=='join':
        right=pd.DataFrame(other['rows'],columns=other['schema']);lk,rk=p['left_keys'],p['right_keys']
        # SQL-style null semantics: null keys never match.
        diagnostics=check_join(df,right,p);valid_right=right.dropna(subset=rk)
        merged=df.merge(valid_right,left_on=lk,right_on=rk,how=p['how'],indicator=True,sort=False)
        df=merged.drop(columns='_merge')
        audit.extend(other['audit']);audit.append({'operation':'join','how':p['how'],'left_keys':lk,'right_keys':rk,**diagnostics})
    elif op=='derive':
        df[p['name']]=evaluate_expr(p['expression'],df)
        audit.append({'operation':'derive','name':p['name'],'expression':p['expression'],'null_results':int(df[p['name']].isna().sum())})
    elif op=='aggregate':
        groups=p['group_by'];records=[]
        partitions=df.groupby(groups,dropna=False,sort=True) if groups else [((),df)]
        for key,part in partitions:
            keys=key if isinstance(key,tuple) else (key,);record=dict(zip(groups,keys))
            for m in p['metrics']:
                fn=m['function'];series=part[m['column']] if fn!='rows' else None
                value=len(part) if fn=='rows' else int(series.count()) if fn=='count' else int(series.nunique()) if fn=='nunique' else series.sum(min_count=1) if fn=='sum' else getattr(series,fn)()
                record[m['name']]=value
            records.append(record)
        df=pd.DataFrame(records,columns=schema);audit.append({'operation':'aggregate','parameters':p,'input_rows':len(original['rows'])})
    elif op=='time':
        dates=pd.to_datetime(df[p['column']],utc=True)
        frequencies={'day':'D','week':'W','month':'M','quarter':'Q','year':'Y'}
        df[p['name']]=dates.dt.tz_localize(None).dt.to_period(frequencies[p['bucket']]).astype('string').where(dates.notna(),None)
        audit.append({'operation':'time','column':p['column'],'bucket':p['bucket'],'timezone':'UTC'})
    elif op=='sort':
        df=df.sort_values(p['column'],ascending=not p.get('descending',False),kind='stable',na_position='last')
        if 'limit' in p:df=df.head(p['limit'])
        audit.append({'operation':'sort','parameters':p,'excluded_rows':len(original['rows'])-len(df)})
    elif op=='outliers':
        values=pd.to_numeric(df[p['column']]);q1,q3=values.quantile([.25,.75]);k=p.get('multiplier',1.5)
        df['unusual']=(values<q1-k*(q3-q1))|(values>q3+k*(q3-q1))
        audit.append({'operation':'outliers','column':p['column'],'multiplier':k,'lower':None if pd.isna(q1) else float(q1-k*(q3-q1)),'upper':None if pd.isna(q3) else float(q3+k*(q3-q1))})
    elif op=='deduplicate':
        df=df.drop_duplicates(subset=p['columns']);audit.append({'operation':'deduplicate','columns':p['columns'],'removed_rows':len(original['rows'])-len(df)})
    elif op=='fill_missing':
        count=int(df[p['column']].isna().sum());df[p['column']]=df[p['column']].fillna(p['value']);audit.append({'operation':'fill_missing','column':p['column'],'value':p['value'],'affected_rows':count})
    if len(df)>MAX_ROWS:raise ValueError('Intermediate row limit exceeded')
    for key,meta in schema.items():
        if meta['type']=='number' and df[key].map(lambda v:not pd.isna(v) and not math.isfinite(float(v))).any():
            raise ValueError('Operation produced a non-finite number')
    # JSON conversion preserves nulls and eliminates numpy scalar types.
    rows=json.loads(df.to_json(orient='records',date_format='iso',double_precision=15))
    return artifacts.put_json(validate_table_output({'rows':rows,'schema':schema,'audit':audit}))

def table_report(inputs,p):
    sections=[]
    for label,reference in inputs.items():
        data=validate_table_output(artifacts.read_json(reference));rows=data['rows'];schema=data['schema']
        findings=[f'The result contains {len(rows)} rows.']
        if 'unusual' in schema:
            findings.append(f"{sum(bool(r.get('unusual')) for r in rows)} rows fall outside the specified IQR bounds. These are descriptive flags, not verified errors or fraud.")
        for change in data['audit']:
            if change['operation']=='filter':findings.append(f"Filter applied: {json.dumps(change['parameters'],ensure_ascii=False)}. {change['excluded_rows']} rows were excluded.")
            elif change['operation']=='join':findings.append(f"A {change['how']} join produced {change['output_rows']} rows; {change['unmatched_left_rows']} left input rows had no matching key.")
            elif change['operation']=='deduplicate':findings.append(f"Explicit deduplication removed {change['removed_rows']} rows.")
            elif change['operation']=='fill_missing':findings.append(f"Explicit missing-value replacement affected {change['affected_rows']} rows.")
            elif change['operation']=='aggregate' and change['input_rows']==0:
                findings.append('No input rows remained for this aggregation. Metrics without observations are missing; row and non-null counts are zero.')
            elif change['operation']=='profile' and change['input_rows']==0:
                findings.append('The dataset contains column headers but no data rows.')
            elif change['operation']=='sort':
                direction='descending' if change['parameters'].get('descending',False) else 'ascending'
                findings.append(f"Results are sorted in {direction} order. The requested limit excluded {change['excluded_rows']} rows." if 'limit' in change['parameters'] else f"Results are sorted in {direction} order; sorting retained every input row.")
        if not rows:findings.append('No rows match this analysis; no further findings can be inferred.')
        elif len(rows)<=20:
            for row in rows:
                findings.append('; '.join(f"{schema[k]['name']}: {v if v is not None else 'missing'}" for k,v in row.items()))
        else:
            for key,meta in schema.items():
                if meta['type']=='number':
                    vals=[r[key] for r in rows if r[key] is not None]
                    if vals:findings.append(f"{meta['name']} ranges from {min(vals):g} to {max(vals):g}, with mean {sum(vals)/len(vals):g}.")
            findings.append('Download the complete accepted result for all records.')
        sections.append({'title':p.get('titles',{}).get(label,label),'findings':findings,'artifact':reference,'audit':data['audit']})
    insights={'kind':'tabular','title':'Dataset insights','question':p.get('question',''),'sections':sections}
    reference=artifacts.put_json(insights)['id']
    body=''.join('<section><h2>'+html.escape(s['title'])+'</h2>'+''.join('<p>'+html.escape(f)+'</p>' for f in s['findings'])+'<details><summary>Filters, joins and transformations</summary><pre>'+html.escape(json.dumps(s['audit'],indent=2))+'</pre></details></section>' for s in sections)
    return artifacts.put(('<!doctype html><html><head><meta charset="utf-8"><meta name="workflow-insights" content="'+reference+'"><title>Dataset insights</title></head><body><h1>Dataset insights</h1><p>'+html.escape(p.get('question',''))+'</p>'+body+'</body></html>').encode(),'.html')
