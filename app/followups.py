"""Resolve supported follow-up intent against the previous validated DAG."""
import copy, json, re, calendar
from .planner import propose, template_plan
from .request_constraints import extract_constraints
from .validator import validate_plan

BRANCHES={'monthly':'monthly_sales','products':'product_summary','flags':'flag_unusual_amounts'}


def propose_followup(request, previous_plan, mode='template'):
    previous=previous_plan['tasks']
    sections=[name for name,handler in BRANCHES.items() if any(task['handler']==handler for task in previous)]
    scope=copy.deepcopy(next((task['parameters'] for task in previous if task['handler']=='filter_transactions'),{}))
    multiplier=next((task['parameters'].get('iqr_multiplier',1.5) for task in previous if task['handler']=='flag_unusual_amounts'),1.5)
    text=request.lower()
    if re.search(r'\bprofit(?:able|ability|s)?\b|\bmargins?\b|\bcosts?\b',text):raise ValueError('Profit, margin and cost analysis require fields that are not available in this retail dataset')
    if re.search(r'\b(?:last|next|previous) (?:week|month|year|quarter)\b|\btoday\b|\byesterday\b',text):raise ValueError('Use an explicit calendar month such as February 2026 or 2026-02')
    if any(phrase in text for phrase in ('remove filter','clear filter','entire dataset','whole dataset')):scope={}
    if re.search(r'\b(?:remove|clear) (?:the )?month filter\b',text):scope.pop('month',None)
    if re.search(r'\b(?:remove|clear) (?:the )?product filter\b',text):scope.pop('product_id',None)
    if 'all months' in text:scope.pop('month',None)
    if 'all products' in text:scope.pop('product_id',None)
    dates=re.findall(r'\b(\d{4}-(?:0[1-9]|1[0-2]))\b',request)
    for number in range(1,13):
        for month in (calendar.month_name[number],calendar.month_abbr[number]):
            for year in re.findall(r'\b'+month+r'\s+(\d{4})\b',request,re.I):dates.append(f'{year}-{number:02d}')
    named_months=[number for number in range(1,13) if re.search(r'\b'+calendar.month_name[number]+r'\b',request,re.I)]
    if len(named_months)>1:raise ValueError('Use one filtered month per follow-up, or request all months for comparison')
    if len(set(dates))>1:raise ValueError('Use one calendar month per filtered follow-up, or request all months for comparison')
    if dates:scope['month']=dates[0]
    elif re.search(r'\b\d{4}-\d{2}\b',request):raise ValueError('Month filters must use a valid YYYY-MM month')
    elif any(re.search(r'\b'+calendar.month_name[number]+r'\b',request,re.I) for number in range(1,13)):raise ValueError('Include the year with the month, for example February 2026')
    product=re.search(r'(?:\b(?:for|only|about|show|filter(?:ed)?(?:\s+to)?)\s+product\b|\bproduct_id\b|\bsku\b|\bproduct(?=\s*[=:"\x27]))\s*(?:=|:)?\s*(?:"([^"]+)"|\x27([^\x27]+)\x27|([A-Za-z0-9][A-Za-z0-9_.-]*))',request,re.I)
    if product:
        value=next(group for group in product.groups() if group is not None)
        if value.lower() not in {'has','had','have','with','is','was','revenue','performance','sales','summary','summaries','quantities','quantity','generated','generates','contributes','contributed','analysis','report','id'}:scope['product_id']=value.rstrip('.,')
    explicit=extract_constraints(request)
    if explicit:multiplier=explicit['iqr_multiplier']
    selected=[]
    if re.search(r'\bmonthly\b|\bmonth.by.month\b|\bmonth.over.month\b|\btrend\b',text):selected.append('monthly')
    if re.search(r'\bproducts\b|\bproduct (?:performance|summary|summaries|revenue)\b|\b(?:which|top|best|highest|leading) product\b',text):selected.append('products')
    if re.search(r'\bunusual\b|\boutliers?\b|\bflags?\b',text):selected.append('flags')
    if 'full report' in text or 'all analyses' in text:selected=list(BRANCHES)
    if selected:
        if re.search(r'\b(?:remove|drop|exclude|without)\b',text):sections=[name for name in sections if name not in selected]
        elif re.search(r'\b(?:add|include|also|too)\b',text) and 'only' not in text:sections=list(dict.fromkeys(sections+selected))
        else:sections=selected
    if explicit and 'flags' not in sections:sections.append('flags')
    if not (selected or explicit or dates or product or re.search(r'\breport\b|\bsales\b|\btransactions?\b|\brepeat\b|\bagain\b|\b(?:remove|clear) (?:the )?(?:(?:month|product) )?filter\b|\ball months\b|\ball products\b|\b(?:entire|whole) dataset\b',text)):raise ValueError('Specify monthly sales, product performance, unusual transactions, an IQR multiplier or a product/month filter')
    if not sections:raise ValueError('Specify monthly sales, product performance or unusual transactions')
    context={'sections':sections,'scope':scope,'iqr_multiplier':multiplier}
    # The provider remains an intent assistant; executable operations are compiled below.
    _,record=propose('Retail report. '+request,mode,context=context)
    plan=template_plan();by={task['id']:task for task in plan['tasks']}
    tasks=[by['validate'],by['clean']];source='clean'
    if scope:
        source='filter'
        tasks.append({'id':'filter','handler':'filter_transactions','version':'1','inputs':{'cleaned':'clean'},'depends_on':['clean'],'parameters':scope,'resources':{'cpu':1,'memory_mb':128}})
    for section in sections:
        task=by[section];task['inputs']={'cleaned':source};task['depends_on']=[source]
        if section=='flags':task['parameters']={'iqr_multiplier':multiplier}
        tasks.append(task)
    report=by['report'];report['inputs']={name:name for name in sections};report['inputs']['cleaned']=source;report['depends_on']=sections+[source];report['parameters']={'question':request};tasks.append(report)
    raw={'tasks':tasks};validate_plan(raw)
    record=dict(record,strategy='contextual follow-up + validated catalogue compiler',resolved_intent=context,effective_request=json.dumps(context,sort_keys=True),request=request)
    return raw,record
