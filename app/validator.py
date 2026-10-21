import networkx as nx
from .contracts import Plan
from .catalogue import CATALOGUE

def validate_plan(raw):
    plan=Plan.model_validate(raw)
    errors=[]; nodes={t.id:t for t in plan.tasks}
    if len(nodes)!=len(plan.tasks):errors.append('Duplicate task IDs')
    g=nx.DiGraph();g.add_nodes_from(nodes)
    for t in plan.tasks:
        c=CATALOGUE.get(t.handler)
        if c is None:errors.append(f'{t.id}: unsupported handler');continue
        if t.resources.memory_mb<c['memory_mb']:errors.append(f'{t.id}: memory below catalogue minimum')
        if len(set(t.depends_on))!=len(t.depends_on):errors.append(f'{t.id}: duplicate dependencies')
        required=set(c['inputs'])-set(c.get('optional_inputs',[]))
        if t.handler!='table_report' and (not required<=set(t.inputs) or set(t.inputs)-set(c['inputs'])):errors.append(f'{t.id}: wrong input bindings')
        if t.handler=='build_report' and not set(t.inputs)&{'monthly','products','flags'}:errors.append(f'{t.id}: report requires an analytical input')
        sources=set()
        for name,source in t.inputs.items():
            expected='table' if t.handler=='table_report' else c['inputs'].get(name)
            if source=='$dataset' or source.startswith('$dataset:'):
                if expected!='dataset':errors.append(f'{t.id}: incompatible dataset binding')
            elif source not in nodes:errors.append(f'{t.id}: dangling input {source}')
            else:
                sources.add(source)
                actual=CATALOGUE.get(nodes[source].handler,{}).get('output')
                if expected!=actual:errors.append(f'{t.id}: incompatible artifact type')
        if sources!=set(t.depends_on):errors.append(f'{t.id}: dependencies must match input sources')
        for dep in t.depends_on:
            if dep not in nodes:errors.append(f'{t.id}: dangling dependency')
            else:g.add_edge(dep,t.id)
        if t.handler.startswith('table_'):pass
        elif t.handler=='flag_unusual_amounts':
            if set(t.parameters)-{'iqr_multiplier'}:errors.append(f'{t.id}: unknown parameter')
            k=t.parameters.get('iqr_multiplier',1.5)
            if isinstance(k,bool) or not isinstance(k,(int,float)) or not 0.1<=k<=10:errors.append(f'{t.id}: invalid IQR multiplier')
        elif t.handler=='filter_transactions':
            import re
            if not t.parameters or set(t.parameters)-{'month','product_id'}:errors.append(f'{t.id}: invalid filter parameters')
            month=t.parameters.get('month')
            if month is not None and (not isinstance(month,str) or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',month)):errors.append(f'{t.id}: invalid month filter')
            product=t.parameters.get('product_id')
            if product is not None and (not isinstance(product,str) or not product.strip() or len(product)>200):errors.append(f'{t.id}: invalid product filter')
        elif t.handler=='build_report':
            if set(t.parameters)-{'question'} or ('question' in t.parameters and (not isinstance(t.parameters['question'],str) or len(t.parameters['question'])>2000)):errors.append(f'{t.id}: invalid report parameters')
        elif t.parameters:errors.append(f'{t.id}: handler accepts no parameters')
    if not nx.is_directed_acyclic_graph(g):errors.append('Graph contains a cycle')
    reports=[t for t in plan.tasks if t.handler in ('build_report','table_report')]
    if len(reports)!=1:errors.append('Exactly one terminal report required')
    elif g.out_degree(reports[0].id):errors.append('Report must be terminal')
    if reports and nx.is_directed_acyclic_graph(g):
        ancestors=nx.ancestors(g,reports[0].id)|{reports[0].id}
        if ancestors!=set(nodes):errors.append('All tasks must contribute to report')
    if errors:raise ValueError('; '.join(errors))
    if any(t.handler.startswith('table_') for t in plan.tasks):
        if not all(t.handler.startswith('table_') for t in plan.tasks):raise ValueError('Mixed legacy and generic plans are unsupported')
        from .tabular import validate_generic_plan
        validate_generic_plan(plan)
    return plan
