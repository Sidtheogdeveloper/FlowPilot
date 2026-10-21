import json,os,httpx
from .catalogue import CATALOGUE,INTENT_ALIASES
from .intent_schema import Intent,BASE_OPERATIONS,normalize_intent,intent_prompt,intent_example,check_context
from .validator import validate_plan
from .request_constraints import extract_constraints,enforce_plan_constraints

def template_plan():
    tasks=[]
    for id,handler,inputs in [('validate','validate_dataset',{'dataset':'$dataset'}),('clean','clean_transactions',{'validated':'validate'}),('monthly','monthly_sales',{'cleaned':'clean'}),('products','product_summary',{'cleaned':'clean'}),('flags','flag_unusual_amounts',{'cleaned':'clean'}),('report','build_report',{'monthly':'monthly','products':'products','flags':'flags','cleaned':'clean'})]:
        tasks.append({'id':id,'handler':handler,'version':'1','inputs':inputs,'depends_on':list(dict.fromkeys(v for v in inputs.values() if v!='$dataset')),'parameters':{},'resources':{'cpu':1,'memory_mb':128}})
    return {'tasks':tasks}

def compile_intent(raw,constraints=None,context=None):
    constraints=constraints or {}
    if isinstance(raw,dict):
        raw=dict(normalize_intent(raw),**constraints)
        if isinstance(raw.get('operations'),list):raw['operations']=[INTENT_ALIASES.get(op,op) if isinstance(op,str) else op for op in raw['operations']]
    intent=Intent.model_validate(raw)
    check_context(intent,context)
    if len(set(intent.operations))!=len(intent.operations) or not set(intent.operations)<=set(CATALOGUE):raise ValueError('Intent contains duplicate or unsupported operations')
    if 'build_report' not in intent.operations:raise ValueError('Selected use case requires build_report')
    # Report contract requires all three branches; dependency closure adds prerequisites.
    plan=template_plan();next(t for t in plan['tasks'] if t['handler']=='flag_unusual_amounts')['parameters']={'iqr_multiplier':intent.iqr_multiplier}
    return plan

def propose(request,mode='template',context=None):
    constraints=extract_constraints(request)
    provenance={'explicit_parameter_source':'request' if constraints else None,'enforced_parameters':constraints}
    lowered=request.lower()
    if any(s in lowered for s in ('forecast','predict','image','fraud model','train model','delete','email','execute','python','translate','weather','medical','scrape','recommendation')):raise ValueError('Requested operation is outside the retail catalogue')
    if not any(s in lowered for s in ('retail','transaction','sales','monthly','product','unusual','report','clean')):raise ValueError('Ambiguous request: specify the supported retail analysis')
    if mode=='template':
        raw=enforce_plan_constraints(template_plan(),constraints);return raw,{'provider':'template','raw':raw,'repaired':False,'strategy':'fixed retail template',**provenance}
    endpoint=os.getenv('HF_INFERENCE_URL','http://127.0.0.1:8001/v1/chat/completions')
    token=os.getenv('HF_TOKEN');headers={'Authorization':f'Bearer {token}'} if token else {}
    strategy=os.getenv('HF_PLANNING_MODE','intent')
    if strategy=='dag':
        prompt='Return ONLY JSON for a retail workflow. Use exactly approved handlers, IDs, inputs and dependencies shown. Catalogue: '+json.dumps(CATALOGUE)+' Example: '+json.dumps(template_plan())
    else:
        prompt=intent_prompt(constraints,context)
    if constraints and strategy=='dag':prompt+=' The authoritative IQR multiplier is '+str(constraints['iqr_multiplier'])+'.'
    history=[{'role':'system','content':prompt},{'role':'user','content':request}];raw_outputs=[]
    for attempt in range(2):
        response=httpx.post(endpoint,trust_env=not ('127.0.0.1' in endpoint or 'localhost' in endpoint),headers=headers,json={'model':os.getenv('HF_MODEL','Qwen/Qwen2.5-0.5B-Instruct'),'messages':history,'max_tokens':2048 if strategy=='dag' else 384,'temperature':0},timeout=180)
        response.raise_for_status();payload=response.json();content=payload['choices'][0]['message']['content'];raw_outputs.append(content)
        try:
            clean=content.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip();generated=json.loads(clean)
            raw=generated if isinstance(generated,dict) and 'tasks' in generated else compile_intent(generated,constraints,context)
            raw=enforce_plan_constraints(raw,constraints)
            validate_plan(raw)
            return raw,{'provider':'huggingface','raw_outputs':raw_outputs,'repaired':bool(attempt),'model':payload.get('model',os.getenv('HF_MODEL','Qwen/Qwen2.5-0.5B-Instruct')),'training':payload.get('training'),'strategy':'direct DAG' if 'tasks' in generated else 'LLM intent + deterministic catalogue compiler',**provenance}
        except (ValueError,TypeError) as exc:
            repair_example=template_plan() if strategy=='dag' else intent_example(constraints,context)
            history += [{'role':'assistant','content':content},{'role':'user','content':'Your JSON did not match the schema: '+str(exc)[:400]+'. Original request: '+request+' Explicit user constraints (must preserve): '+json.dumps(constraints)+'. Return ONLY a JSON object in this exact format: '+json.dumps(repair_example)+'. Keep operation names unchanged. No error object, report content or additional keys.'}]
    raise ValueError('LLM proposal failed validation after one correction: '+str(raw_outputs[-1])[:300])

