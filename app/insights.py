"""Deterministic findings grounded in accepted task outputs."""
import html,statistics
from . import artifacts


def build_insights(inputs, parameters):
    data = {name: artifacts.read_json(value) for name, value in inputs.items()}
    cleaned = data.get('cleaned', {})
    rows = cleaned.get('rows', data.get('flags', {}).get('rows', []))
    monthly = data.get('monthly', {}).get('rows', [])
    products = data.get('products', {}).get('rows', [])
    flags = data.get('flags', {}).get('rows', [])
    revenue = sum(row['revenue'] for row in monthly) if 'monthly' in data else sum(row['revenue'] for row in products) if 'products' in data else sum(row['revenue'] for row in rows)
    count = len(rows) if 'cleaned' in data or 'flags' in data else sum(row['transactions'] for row in monthly) if 'monthly' in data else None
    findings = []
    def add(title, text): findings.append({'title': title, 'text': text})
    summary = f'The analysed records generated {revenue:,.2f} in revenue'
    if count is not None: summary += f' across {count:,} accepted transactions'
    summary += '. Currency is not specified in the dataset.'
    if 'cleaned' in data:
        rejected = len(cleaned.get('rejected', []))
        add('Data quality', f'{rejected:,} input rows were rejected during cleaning. Findings use accepted rows only.')
        scope = cleaned.get('scope', {})
        if scope:
            add('Analysis scope', 'Filtered to ' + ', '.join(f'{key}: {value}' for key, value in scope.items()) + f". {cleaned.get('excluded_rows', 0):,} other accepted rows were excluded.")
        if rows:add('Transaction values', f"Average accepted transaction revenue was {sum(row['revenue'] for row in rows)/len(rows):,.2f}; the median was {statistics.median(row['revenue'] for row in rows):,.2f}.")
    if monthly:
        peak = max(monthly, key=lambda row: row['revenue'])
        add('Strongest observed month', f"{peak['month']} contributed {peak['revenue']:,.2f} in revenue from {peak['transactions']:,} transactions.")
        if len(monthly) > 1:
            ordered = sorted(monthly, key=lambda row: row['month']); before, after = ordered[-2:]
            delta = after['revenue'] - before['revenue']
            change = f" ({delta / before['revenue'] * 100:+.1f}%)" if before['revenue'] else ' (percentage change is undefined because the earlier total was zero)'
            add('Change between the latest observed months', f"Revenue changed by {delta:+,.2f}{change} from {before['month']} to {after['month']}. These are observed month totals; partial months and missing periods can affect comparison.")
        else: add('Trend coverage', 'Only one month is present in this scope, so a month-to-month trend cannot be established.')
    elif 'monthly' in data: add('Monthly sales', 'No accepted transactions matched the requested scope.')
    if products:
        leader = max(products, key=lambda row: row['revenue'])
        share = f", representing {leader['revenue'] / revenue * 100:.1f}% of analysed revenue" if revenue else '; percentage share is undefined because total revenue is zero'
        add('Product with the highest revenue', f"Product {leader['product_id']} generated {leader['revenue']:,.2f}{share}, from {leader['quantity']:,.2f} units. Ties are resolved by the stored product ordering.")
        add('Product coverage', f'{len(products):,} products appear in this scope. Revenue leadership does not establish profitability because costs are unavailable.')
    elif 'products' in data: add('Product performance', 'No accepted transactions matched the requested scope.')
    if flags:
        unusual = [row for row in flags if row['unusual']]
        examples = ', '.join(str(row['transaction_id']) for row in sorted(unusual, key=lambda row: row['revenue'], reverse=True)[:5])
        add('Unusually large transactions', f"{len(unusual):,} of {len(flags):,} transactions exceeded the IQR upper threshold of {flags[0]['threshold']:,.2f}." + (f' Highest flagged transaction IDs: {examples}.' if examples else ' None exceeded the threshold.'))
    elif 'flags' in data: add('Unusual values', 'No accepted transactions matched the requested scope; an outlier threshold cannot be estimated.')
    if not findings: add('No findings', 'No supported analytical outputs were available.')
    return {'headline': 'Retail dataset insights', 'question': parameters.get('question'), 'summary': summary,
            'findings': findings, 'limitations': ['Findings describe the uploaded records, not causal explanations or forecasts.', 'Unusual-value flags are statistical indicators and do not establish fraud.', 'Revenue uses floating-point quantity × unit price; costs, margins and currency are unavailable.']}


def render_report(insights, reference):
    escape = html.escape
    findings = ''.join(f"<section><h2>{escape(item['title'])}</h2><p>{escape(item['text'])}</p></section>" for item in insights['findings'])
    limits = ''.join(f'<li>{escape(item)}</li>' for item in insights['limitations'])
    question = f"<p class='question'>{escape(insights['question'])}</p>" if insights.get('question') else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="workflow-insights" content="{escape(reference)}"><title>Retail dataset insights</title><style>body{{font:17px/1.6 system-ui,sans-serif;color:#26312c;background:#fafbf9;margin:0}}main{{max-width:820px;margin:48px auto;padding:32px}}h1{{font-size:34px;line-height:1.2}}h2{{font-size:21px}}section{{background:white;border:1px solid #e1e5de;border-radius:16px;padding:12px 24px;margin:20px 0}}.question{{color:#536153}}.summary{{font-size:20px}}footer{{font-size:14px;color:#5c675f}}</style></head><body><main><h1>{escape(insights['headline'])}</h1>{question}<p class="summary">{escape(insights['summary'])}</p>{findings}<footer><h2>Interpretation limits</h2><ul>{limits}</ul></footer></main></body></html>'''
