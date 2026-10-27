"""Trusted, bounded map/reduce expansion for decomposable table aggregations."""
import copy,math
from sqlalchemy import select
from . import models as db,artifacts

ROW_LOCAL={'table_filter','table_convert','table_derive','table_time','table_fill_missing'}
MERGEABLE={'sum','count','rows','min','max','mean'}

def partial_parameters(p,schema):
    metrics=[];mapping=[];used=set(schema)
    for i,m in enumerate(p['metrics']):
        names={}
        for fn in (('sum','count') if m['function']=='mean' else (m['function'],)):
            name='fp_part_'+str(i)+'_'+fn
            while name in used:name+='x'
            used.add(name);names[fn]=name
            metrics.append(dict(m,name=name,function=fn))
        mapping.append(names)
    return {'group_by':p['group_by'],'metrics':metrics},mapping

def chunks(session,dataset,size):
    session.get(db.Dataset,dataset.id,with_for_update=True)
    records=list(session.scalars(select(db.DatasetPartition).where(db.DatasetPartition.dataset_id==dataset.id,db.DatasetPartition.chunk_size==size).order_by(db.DatasetPartition.index)))
    if records:return records
    raw=artifacts.read_json(dataset.table_ref)
    for index,start in enumerate(range(0,len(raw['rows']),size)):
        part=dict(raw,rows=raw['rows'][start:start+size])
        row=db.DatasetPartition(dataset_id=dataset.id,chunk_size=size,index=index,artifact=artifacts.put_json(part)['id'],rows=len(part['rows']))
        session.add(row);records.append(row)
    session.flush()
    return records

def prepare_execution(session,plan,datasets,enabled=True,threshold=50000):
    from .tabular import validate_generic_plan,memory_for
    from .contracts import Plan
    from .validator import validate_plan
    schemas=validate_generic_plan(Plan.model_validate(plan))
    by={t['id']:t for t in plan['tasks']};ds={d.id:d for d in datasets};replacement={};expanded=[];notes=[];rows={}
    for t in plan['tasks']:
        if t['handler']=='table_load':rows[t['id']]=ds[t['inputs']['dataset'].split(':',1)[1]].rows
        elif t['handler']!='table_report':rows[t['id']]=max((rows.get(v,0) for v in t['depends_on']),default=0)
    if not enabled:return plan,{'enabled':False,'partitioned_aggregations':[],'notes':['Partitioning disabled'],'row_estimates':rows}
    for ordinal,t in enumerate(plan['tasks']):
        if t['handler']!='table_aggregate':continue
        if any(m['function'] not in MERGEABLE for m in t['parameters']['metrics']):
            notes.append(t['id']+': global aggregation retained for median/distinct counts');continue
        chain=[];node=by[t['inputs']['table']]
        while node['handler'] in ROW_LOCAL:
            chain.insert(0,node);node=by[node['inputs']['table']]
        if node['handler']!='table_load':
            notes.append(t['id']+': global path retained after a join or non-row-local operation');continue
        dataset=ds[node['inputs']['dataset'].split(':',1)[1]]
        if dataset.rows<threshold:continue
        size=min(50000,max(1,125*1048576//(max(1,len(schemas[node['id']]))*320)))
        count=math.ceil(dataset.rows/size)
        # Bound coordinator/DAG overhead. Fall back visibly instead of dropping operations.
        if count>96 or len(plan['tasks'])+len(expanded)+count*(len(chain)+2)>250:
            notes.append(t['id']+': expansion exceeds the bounded DAG budget; global path retained');continue
        parts=chunks(session,dataset,size);new=[];partial_ids=[]
        for part in parts:
            prefix='px'+str(ordinal)+'_'+str(part.index);load=copy.deepcopy(node);load['id']=prefix+'_l';load['handler']='table_load_chunk'
            load['parameters'].update(chunk_size=size,index=part.index)
            load['resources']['memory_mb']=memory_for(part.rows,len(schemas[node['id']]))
            new.append(load);rows[load['id']]=part.rows;source=load['id']
            for i,stage in enumerate(chain):
                local=copy.deepcopy(stage);local['id']=prefix+'_s'+str(i);local['inputs']={'table':source};local['depends_on']=[source]
                local['resources']['memory_mb']=memory_for(part.rows,len(schemas[stage['id']]))
                new.append(local);rows[local['id']]=part.rows;source=local['id']
            partial=copy.deepcopy(t);partial['id']=prefix+'_a';partial['handler']='table_partial';partial['inputs']={'table':source};partial['depends_on']=[source]
            partial['resources']['memory_mb']=memory_for(part.rows,len(schemas[t['inputs']['table']])+2*len(t['parameters']['metrics']))
            new.append(partial);rows[partial['id']]=part.rows;partial_ids.append(partial['id'])
        reduce=copy.deepcopy(t);reduce['handler']='table_reduce';reduce['inputs']={'p'+str(i):v for i,v in enumerate(partial_ids)};reduce['depends_on']=partial_ids
        reduce['parameters']={'aggregation':copy.deepcopy(t['parameters']),'source_schema':schemas[t['inputs']['table']]}
        # Worst case: every input row has a distinct group. Do not underdeclare reducer memory.
        from .tables import ensure_metadata
        profile=ensure_metadata(dataset);group_bound=1
        for group in t['parameters']['group_by']:
            col=next((c for c in profile['columns'] if node['parameters']['alias']+'.'+c['id']==group),None)
            # Derived or converted grouping can change cardinality; use full rows there.
            altered=any(stage['handler'] in ('table_derive','table_convert','table_time','table_fill_missing') for stage in chain)
            group_bound*=min(dataset.rows,col['distinct_count']+1) if col and not altered else dataset.rows
        reducer_rows=min(dataset.rows,count*group_bound)
        reduce['resources']['memory_mb']=memory_for(reducer_rows,len(schemas[t['id']])+2*len(t['parameters']['metrics']))
        rows[t['id']]=reducer_rows
        replacement[t['id']]=new+[reduce];expanded.extend(new)
        notes.append(t['id']+': '+str(len(parts))+' independent chunks; combine partial sums/counts/minima/maxima')
    tasks=[item for t in plan['tasks'] for item in replacement.get(t['id'],[t])]
    required={'report'};lookup={t['id']:t for t in tasks};stack=['report']
    while stack:
        for src in lookup[stack.pop()]['depends_on']:
            if src not in required:required.add(src);stack.append(src)
    result={'tasks':[t for t in tasks if t['id'] in required]}
    validate_plan(result)
    return result,{'enabled':True,'partitioned_aggregations':list(replacement),'notes':notes,'row_estimates':{k:v for k,v in rows.items() if k in required}}

def execute_partition(handler,inputs,p):
    from .tabular import execute_table,propagate,validate_table_output
    if handler=='table_load_chunk':
        return execute_table('table_load',inputs,{k:v for k,v in p.items() if k in ('alias','schema')})
    if handler=='table_partial':
        raw=artifacts.read_json(inputs['table']);params,_=partial_parameters(p,raw['schema'])
        result=execute_table('table_aggregate',inputs,params)
        content=artifacts.read_json(result['id']);content['audit'][-1]['parameters']=p
        return artifacts.put_json(content)
    agg=p['aggregation'];source_schema=p['source_schema'];_,mapping=partial_parameters(agg,source_schema)
    schema=propagate('aggregate',agg,[source_schema]);groups=agg['group_by'];states={};audits=[]
    for ref in inputs.values():
        partial=validate_table_output(artifacts.read_json(ref));audits.append(partial['audit'])
        for row in partial['rows']:
            key=tuple(row[g] for g in groups);state=states.setdefault(key,[[] for _ in agg['metrics']])
            for i,names in enumerate(mapping):state[i].append({fn:row[name] for fn,name in names.items()})
    records=[]
    for key,values in states.items():
        row=dict(zip(groups,key))
        for i,m in enumerate(agg['metrics']):
            fn=m['function'];items=values[i]
            if fn=='mean':
                count=sum(x['count'] or 0 for x in items);total=sum(x['sum'] or 0 for x in items)
                value=total/count if count else None
            else:
                valid=[x[fn] for x in items if x[fn] is not None]
                value=(sum(valid) if fn in ('sum','count','rows') else min(valid) if fn=='min' else max(valid)) if valid else None
            row[m['name']]=value
        records.append(row)
    import pandas as pd,json
    df=pd.DataFrame(records,columns=schema)
    if groups and len(df):df=df.sort_values(groups,na_position='last',kind='stable')
    rows=json.loads(df.to_json(orient='records',double_precision=15))
    audit=[]
    if audits:
        for i in range(len(audits[0])):
            stage=copy.deepcopy(audits[0][i])
            for field in ('input_rows','excluded_rows','affected_rows','null_results','nulls_before'):
                if field in stage:stage[field]=sum(a[i].get(field,0) for a in audits)
            audit.append(stage)
    audit.append({'operation':'partitioned_execution','partitions':len(inputs),'merge':'partial states; weighted means','floating_point':'Reduction order may change the last floating-point digits'})
    return artifacts.put_json(validate_table_output({'schema':schema,'rows':rows,'audit':audit}))

def validate_partition_node(t,schemas,by):
    from .tabular import exact,propagate
    p=t.parameters
    if t.handler=='table_load_chunk':
        exact(p,['alias','schema','chunk_size','index'])
        if type(p['chunk_size'])!=int or not 1<=p['chunk_size']<=50000 or type(p['index'])!=int or not 0<=p['index']<96:raise ValueError('Invalid chunk selection')
        if not t.inputs.get('dataset','').startswith('$dataset:'):raise ValueError('Chunk requires bound dataset')
        return p['schema']
    if t.handler=='table_partial':
        if any(m.get('function') not in MERGEABLE for m in p.get('metrics',[])):raise ValueError('Non-decomposable aggregation')
        source=schemas[t.inputs['table']];propagate('aggregate',p,[source])
        params,_=partial_parameters(p,source);return propagate('aggregate',params,[source])
    exact(p,['aggregation','source_schema'])
    if not t.inputs or len(t.inputs)>96:raise ValueError('Reducer requires bounded partial inputs')
    for key,src in t.inputs.items():
        node=by[src]
        if node.handler!='table_partial' or node.parameters!=p['aggregation'] or schemas[node.inputs['table']]!=p['source_schema']:raise ValueError('Mismatched partial aggregation')
    return propagate('aggregate',p['aggregation'],[p['source_schema']])

