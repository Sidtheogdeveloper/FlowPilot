"""Generate reviewed synthetic intent targets using the shared inference schema.

These labels teach schema/constraint adherence, not financial reasoning. Held-out
wording and months/product IDs are excluded from training.
"""
import json
from pathlib import Path
from app.intent_schema import intent_prompt,intent_example

ROOT=Path(__file__).resolve().parent
def record(identifier,request,constraints=None,context=None):
    return {'id':identifier,'messages':[{'role':'system','content':intent_prompt(constraints,context)},{'role':'user','content':request}], 'target':intent_example(constraints,context),'constraints':constraints or {},'context':context}

def build():
    training=[]
    initial=['Clean these retail transactions and make a sales report.', 'Analyse monthly sales, product revenue and unusual transactions.', 'Summarise my transaction CSV with monthly totals and product performance.', 'Build a retail report after validating and cleaning the data.', 'Provide monthly revenue, product quantities and unusually large amounts.', 'Generate the supported retail analysis from these transactions.']
    for index,text in enumerate(initial):
        for multiplier in (None,1,1.5,2,2.5,3,4,5):
            constraints={} if multiplier is None else {'iqr_multiplier':multiplier}
            request=text if multiplier is None else text+f' Use IQR multiplier {multiplier}.'
            training.append(record(f'initial-{index}-{multiplier}',request,constraints))
    questions=[('monthly','Show only monthly sales.'),('products','Which product has the highest revenue?'),('flags','Find unusual transactions.'),('all','Repeat the full report.')]
    for index,(branch,text) in enumerate(questions):
        sections=['monthly','products','flags'] if branch=='all' else [branch]
        for month in (None,'2025-01','2025-06','2026-03'):
            for product in (None,'A101','SKU-17'):
                scope={}
                suffix=''
                if month:scope['month']=month;suffix+=f' Filter to {month}.'
                if product:scope['product_id']=product;suffix+=f' For product {product}.'
                multiplier=2 if branch=='flags' else 1.5
                context={'sections':sections,'scope':scope,'iqr_multiplier':multiplier}
                training.append(record(f'followup-{index}-{month}-{product}','Retail report. '+text+suffix,{},context))
    for multiplier in (.5,1,2,2.5,3,4,5,6):
        context={'sections':['monthly','products','flags'],'scope':{},'iqr_multiplier':multiplier}
        training.append(record(f'change-{multiplier}',f'Retail report. Use IQR multiplier {multiplier} instead.',{'iqr_multiplier':multiplier},context))
    heldout=[record('hold-initial-default','Please turn the uploaded retail ledger into the complete approved analysis.'),record('hold-initial-explicit','Review transactions and provide a retail report with IQR multiplier 3.5.',{'iqr_multiplier':3.5}),record('hold-initial-small','I need the retail sales report; set the IQR multiplier to 0.75.',{'iqr_multiplier':.75})]
    cases=[('Which product has the highest revenue in February 2026?',{'sections':['products'],'scope':{'month':'2026-02'},'iqr_multiplier':1.5},{}),('What were month-by-month sales for product P003?',{'sections':['monthly'],'scope':{'product_id':'P003'},'iqr_multiplier':1.5},{}),('Focus on unusual values in 2026-09.',{'sections':['flags'],'scope':{'month':'2026-09'},'iqr_multiplier':2},{}),('Show product performance for product ITEM-42 in December 2024.',{'sections':['products'],'scope':{'month':'2024-12','product_id':'ITEM-42'},'iqr_multiplier':1.5},{}),('Use IQR multiplier 3.5 instead.',{'sections':['monthly','products','flags'],'scope':{'month':'2026-02'},'iqr_multiplier':3.5},{'iqr_multiplier':3.5}),('Remove the month filter and compare monthly sales.',{'sections':['monthly'],'scope':{},'iqr_multiplier':1.5},{}),('Also include unusual transactions for SKU ZX9.',{'sections':['products','flags'],'scope':{'product_id':'ZX9'},'iqr_multiplier':1.5},{})]
    heldout.extend(record(f'hold-followup-{i}','Retail report. '+text,constraints,context) for i,(text,context,constraints) in enumerate(cases))
    assert not {r['messages'][1]['content'] for r in training}&{r['messages'][1]['content'] for r in heldout}
    for name,rows in [('train',training),('heldout',heldout)]:
        (ROOT/f'{name}.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    print(json.dumps({'train_examples':len(training),'heldout_examples':len(heldout)}))
if __name__=='__main__':build()
