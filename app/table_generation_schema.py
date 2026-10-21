"""Decoder schema for the approved operation language; execution validates again."""

def proposal_schema():
    text={'type':'string'}
    name={'type':'string','pattern':'^[a-z][a-z0-9_]{0,39}$'}
    ref={'type':'string','pattern':'^[a-z][a-z0-9_.]{0,80}$'}
    number={'type':'number'}
    scalar={'anyOf':[text,number,{'type':'boolean'},{'type':'null'}]}
    def array(items,minimum=0,maximum=200):return {'type':'array','items':items,'minItems':minimum,'maxItems':maximum}
    def obj(properties,required=None):return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}
    def enum(values):return {'type':'string','enum':values}
    expr={'anyOf':[obj({'column':ref}),obj({'value':number}),obj({'op':enum(['+','-','*','/']),'left':{'$ref':'#/$defs/expression'},'right':{'$ref':'#/$defs/expression'}})]}
    metric=obj({'name':name,'function':enum(['sum','mean','min','max','count','median','nunique','rows']),'column':ref},['name','function'])
    parameters={
        'profile':obj({}),
        'convert':obj({'column':ref,'type':enum(['number','datetime','text']),'format':text},['column','type']),
        'filter':obj({'column':ref,'operator':enum(['eq','ne','gt','ge','lt','le','is_null','not_null','contains']),'value':scalar},['column','operator']),
        'join':obj({'left_keys':array(ref,1),'right_keys':array(ref,1),'how':enum(['inner','left'])}),
        'derive':obj({'name':name,'expression':{'$ref':'#/$defs/expression'}}),
        'aggregate':obj({'group_by':array(ref),'metrics':array(metric,1,30)}),
        'time':obj({'column':ref,'bucket':enum(['day','week','month','quarter','year']),'name':name}),
        'sort':obj({'column':ref,'descending':{'type':'boolean'},'limit':{'type':'integer','minimum':1,'maximum':10000}},['column']),
        'outliers':obj({'column':ref,'multiplier':{'type':'number','minimum':.1,'maximum':10}},['column']),
        'deduplicate':obj({'columns':array(ref,1)}),
        'fill_missing':obj({'column':ref,'value':scalar}),
    }
    alternatives=[]
    for operation,params in parameters.items():
        count=2 if operation=='join' else 1
        alternatives.append(obj({'id':name,'operation':{'const':operation},'sources':array(name,count,count),'parameters':params}))
    intent=obj({'steps':array({'anyOf':alternatives},1,20),'outputs':array(name,1,10)})
    clarification=obj({'clarification':text,'candidates':array(text,0,20)},['clarification'])
    return {'$defs':{'expression':expr},'anyOf':[intent,clarification]}
