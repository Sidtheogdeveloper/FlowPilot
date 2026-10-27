import {useId,useMemo} from 'react';
import {handlerLabel,nodeDescription,layoutDag,NODE_HEIGHT,NODE_WIDTH} from './state';
import type {PlanNode,Task} from './types';
export function DagGraph({nodes,tasks}:{nodes:PlanNode[];tasks:Task[]}){
 const layout=useMemo(()=>layoutDag(nodes),[nodes]);const taskMap=new Map(tasks.map(task=>[task.id,task]));const nodeMap=new Map(layout.nodes.map(node=>[node.id,node]));const markerId='arrow'+useId().replaceAll(':','');
 if(!nodes.length)return <p className="muted">No tasks in this plan.</p>;
 return <div className="dag-canvas"><svg viewBox={`0 0 ${layout.width} ${layout.height}`} role="img" aria-label={`Workflow dependency graph with ${nodes.length} tasks`}>
 <defs><marker id={markerId} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7" fill="var(--dag-edge,#a8ada8)"/></marker></defs>
 {layout.edges.map(edge=>{const from=nodeMap.get(edge.from)!,to=nodeMap.get(edge.to)!;const x1=from.x+NODE_WIDTH/2,y1=from.y+NODE_HEIGHT,x2=to.x+NODE_WIDTH/2,y2=to.y;const middle=(y1+y2)/2;return <path key={edge.from+'-'+edge.to} d={`M${x1} ${y1}V${middle}H${x2}V${y2-3}`} fill="none" stroke="var(--dag-edge,#b3b8b3)" strokeWidth="1.5" markerEnd={`url(#${markerId})`}/>;})}
 {layout.nodes.map(node=>{const status=taskMap.get(node.id)?.status||'pending';const done=['completed','accepted','succeeded'].includes(status);const running=status==='running';const failed=status==='failed';return <g key={node.id} transform={`translate(${node.x},${node.y})`}>
 <title>{handlerLabel(node.handler)} ({node.id}): {status}. {nodeDescription(node,nodes)}. Depends on {node.depends_on.join(', ')||'dataset input'}.</title>
 <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="12" fill={done?'var(--dag-complete-bg,#f6faf7)':'var(--surface,#ffffff)'} stroke={failed?'var(--danger,#cd8b87)':running?'var(--accent,#555f58)':done?'var(--dag-complete-line,#ccdcd0)':'var(--line,#dfe3dd)'} strokeWidth={running?1.6:1}/>
 <circle cx="15" cy="18" r="3" fill={done?'var(--success,#45815b)':failed?'var(--danger,#b65b54)':running?'var(--accent,#444f48)':'var(--ink-muted,#c0c7c0)'}/>
 <text x="25" y="22" className="svg-status">{done?'Completed':running?'Running':status==='queued'?'Queued':status==='cancelled'?'Cancelled':failed?'Failed':'Waiting'}</text>
 <text x="14" y="43" className="svg-title">{handlerLabel(node.handler).length>24?handlerLabel(node.handler).slice(0,22)+'…':handlerLabel(node.handler)}</text>
 <text x="14" y="61" className="svg-meta">{nodeDescription(node,nodes).slice(0,24)||(node.resources?`${node.resources.cpu} CPU · ${node.resources.memory_mb} MB`:node.id)}</text>
 </g>;})}</svg>
 {layout.cyclic||layout.missingDependencies.length?<p role="alert" className="inline-error">This saved graph has invalid dependencies. Execution should remain blocked.</p>:null}
 <div className="graph-caption"><span><i className="legend-dot"/>Dependency</span><span>Branches can run in parallel</span></div>
 </div>;
}
