import io,html
import pandas as pd
from . import artifacts
COLUMNS=['transaction_id','timestamp','product_id','quantity','unit_price']
def inspect_csv(data):
    df=pd.read_csv(io.BytesIO(data),dtype=str)
    if list(df.columns)!=COLUMNS:raise ValueError('CSV columns must be: '+', '.join(COLUMNS))
    if len(df)>1000000:raise ValueError('Dataset exceeds 1 million rows')
    return df

def execute(handler,inputs,parameters):
    if handler.startswith('table_'):
        from .tabular import execute_table
        return execute_table(handler,inputs,parameters)
    if handler=='validate_dataset':
        df=inspect_csv(artifacts.read(inputs['dataset']))
        return artifacts.put_json({'rows':df.fillna('').to_dict('records')})
    if handler=='clean_transactions':
        rows=artifacts.read_json(inputs['validated'])['rows'];accepted=[];rejected=[];seen=set()
        for row in rows:
            reasons=[];tid=row['transaction_id'].strip();product=row['product_id'].strip()
            if not tid or not product:reasons.append('missing identifier')
            if tid in seen:reasons.append('duplicate transaction_id')
            try:
                timestamp=pd.Timestamp(row['timestamp'])
                if pd.isna(timestamp):raise ValueError()
                quantity=float(row['quantity']);price=float(row['unit_price'])
                import math
                if not math.isfinite(quantity) or not math.isfinite(price) or not math.isfinite(quantity*price) or quantity<=0 or price<0:raise ValueError()
            except (ValueError,TypeError):reasons.append('invalid timestamp or numeric values')
            if reasons:rejected.append({'row':row,'reasons':reasons});continue
            seen.add(tid);accepted.append({'transaction_id':tid,'timestamp':timestamp.isoformat(),'product_id':product,'quantity':quantity,'unit_price':price,'revenue':quantity*price})
        return artifacts.put_json({'rows':accepted,'rejected':rejected})
    if handler=='filter_transactions':
        original=artifacts.read_json(inputs['cleaned']);rows=original['rows']
        selected=[row for row in rows if (not parameters.get('month') or pd.Timestamp(row['timestamp']).strftime('%Y-%m')==parameters['month']) and (not parameters.get('product_id') or row['product_id']==parameters['product_id'])]
        return artifacts.put_json({'rows':selected,'rejected':original.get('rejected',[]),'scope':parameters,'excluded_rows':len(rows)-len(selected)})
    if handler in ('monthly_sales','product_summary','flag_unusual_amounts'):
        rows=artifacts.read_json(inputs['cleaned'])['rows'];df=pd.DataFrame(rows)
        if not rows:return artifacts.put_json({'rows':[]})
        if handler=='monthly_sales':
            df['month']=df.timestamp.map(lambda x:pd.Timestamp(x).strftime('%Y-%m'))
            result=df.groupby('month',sort=True).agg(revenue=('revenue','sum'),transactions=('transaction_id','count')).reset_index().to_dict('records')
        elif handler=='product_summary':result=df.groupby('product_id',sort=True).agg(revenue=('revenue','sum'),quantity=('quantity','sum')).reset_index().to_dict('records')
        else:
            q1,q3=df.revenue.quantile([.25,.75]);limit=float(q3+parameters.get('iqr_multiplier',1.5)*(q3-q1));result=[dict(r,unusual=bool(r['revenue']>limit),threshold=limit) for r in rows]
        return artifacts.put_json({'rows':result})
    if handler=='build_report':
        from .insights import build_insights,render_report
        insights=build_insights(inputs,parameters);reference=artifacts.put_json(insights)['id']
        content=render_report(insights,reference)
        return artifacts.put(content.encode(),'.html')
    raise ValueError('Unsupported handler')

