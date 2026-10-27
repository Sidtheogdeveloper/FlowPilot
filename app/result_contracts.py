import math
from .handlers import COLUMNS

def validate_output(handler,obj):
    if handler.startswith('document_'):
        from .document_analysis import validate_document_output
        return validate_document_output(handler,obj)
    if handler.startswith('table_'):
        from .tabular import validate_table_output
        return validate_table_output(obj)
    if not isinstance(obj,dict) or not isinstance(obj.get('rows'),list):raise ValueError('Result must contain rows array')
    schemas={'validate_dataset':set(COLUMNS),'clean_transactions':set(COLUMNS)|{'revenue'},'monthly_sales':{'month','revenue','transactions'},'product_summary':{'product_id','revenue','quantity'},'flag_unusual_amounts':set(COLUMNS)|{'revenue','unusual','threshold'}}
    schemas['filter_transactions']=schemas['clean_transactions']
    schema=schemas[handler]
    for row in obj['rows']:
        if not isinstance(row,dict) or set(row)!=schema:raise ValueError('Result row fields violate '+handler+' contract')
        if handler=='validate_dataset':
            if any(not isinstance(x,str) for x in row.values()):raise ValueError('Validated raw fields must be strings')
        else:
            numeric={'quantity','unit_price','revenue','threshold','transactions'}&set(row)
            if any(isinstance(row[k],bool) or not isinstance(row[k],(int,float)) or not math.isfinite(row[k]) for k in numeric):raise ValueError('Invalid numeric result')
            if handler=='flag_unusual_amounts' and not isinstance(row['unusual'],bool):raise ValueError('Flag must be boolean')
            for key in ('transaction_id','timestamp','product_id','month'):
                if key in row and not isinstance(row[key],str):raise ValueError('Invalid text result')
    if handler in ('clean_transactions','filter_transactions'):
        if not isinstance(obj.get('rejected'),list) or any(not isinstance(r,dict) or not isinstance(r.get('reasons'),list) or not r['reasons'] or not isinstance(r.get('row'),dict) for r in obj['rejected']):raise ValueError('Invalid rejected row contract')
    return obj
