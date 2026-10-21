import type {Chat,Draft,PlanNode,Workflow} from './types';
export function orderedTurns(workflow:Workflow){const turns=new Map((workflow.turns||[]).map(turn=>[turn.id,turn]));turns.set(workflow.id,workflow);return [...turns.values()].sort((a,b)=>(a.revision||1)-(b.revision||1));}
export function isLatestTurn(workflow:Workflow){const turns=orderedTurns(workflow);return (workflow.latest_workflow_id||turns[turns.length-1]?.id)===workflow.id;}
export function followupSubmission(draft:Draft){return {request:draft.request,planner:draft.planner,policy:draft.policy};}
export const plannerLabels = {template:'Template',huggingface:'Local Qwen'};
export const policyLabels = {greedy:'Greedy earliest finish',heft:'HEFT adaptation',bounded:'Bounded joint search'};
export const handlerLabels:Record<string,string> = {filter_transactions:'Filter transactions',validate_dataset:'Validate CSV',clean_transactions:'Clean transactions',monthly_sales:'Monthly sales',product_summary:'Product performance',flag_unusual_amounts:'Flag unusual values',build_report:'Build report'};
export function handlerLabel(name:string){return handlerLabels[name] || name.replaceAll('_',' ');}
export function nodeDescription(node:PlanNode,nodes:PlanNode[]):string{
 const names:Record<string,string>={};
 for(const source of nodes){const schema=source.parameters?.schema;if(schema&&typeof schema==='object')for(const [key,value] of Object.entries(schema)){if(value&&typeof value==='object'&&'name' in value)names[key]=String(value.name);}}
 const name=(key:unknown)=>names[String(key)]||String(key);
 const p=node.parameters||{};
 if(node.handler==='table_join')return `${(p.left_keys as string[]||[]).map(name).join(', ')} → ${(p.right_keys as string[]||[]).map(name).join(', ')} (${p.how})`;
 if(node.handler==='table_aggregate'){const metrics=p.metrics as {function:string;column?:string}[]||[];return metrics.map(m=>`${m.function} ${m.column?name(m.column):'rows'}`).join(', ');}
 if(p.column)return name(p.column);
 if(node.handler==='table_load')return `${p.alias}: ${Object.values(p.schema as Record<string,{name:string}>||{}).map(c=>c.name).join(', ')}`;
 return '';
}
export function chatTitle(chat:Chat){return chat.title?.trim() || chat.request?.trim().slice(0,78) || 'Previous analysis';}
export function filterChats(chats:Chat[],query:string){const needle=query.trim().toLowerCase();return needle?chats.filter(c=>[chatTitle(c),c.request,c.search_text,c.dataset_filename,c.id].join(' ').toLowerCase().includes(needle)):chats;}
export function submission(draft:Draft){return {dataset_id:draft.datasetId,request:draft.request,planner:draft.planner,policy:draft.policy};}
export function shouldAcceptDetail(request:{epoch:number;id:string;generation:number},current:{epoch:number;id:string|null;generation:number}){return request.epoch===current.epoch && request.id===current.id && request.generation===current.generation;}
export function shouldOpenCreatedChat(submittedEpoch:number,currentEpoch:number,selectedId:string|null){return submittedEpoch===currentEpoch && selectedId===null;}
export function countLabel(count:number){return new Intl.NumberFormat().format(count);}
export function statusLabel(status:string){return ({planned:'Ready to start',running:'Running',paused:'Paused',completed:'Completed',failed:'Failed',cancelled:'Cancelled'} as Record<string,string>)[status] || status;}
export function timeLabel(value?:string){if(!value)return '';const date=new Date(value);return Number.isNaN(date.getTime())?'':date.toLocaleDateString(undefined,{month:'short',day:'numeric'});}
export type GraphNode=PlanNode & {x:number;y:number;level:number};
export type GraphLayout={nodes:GraphNode[];edges:{from:string;to:string}[];width:number;height:number;cyclic:boolean;missingDependencies:string[]};
export const NODE_WIDTH=168,NODE_HEIGHT=76;
export function layoutDag(input:PlanNode[]):GraphLayout{
  const map=new Map(input.map(node=>[node.id,node]));const children=new Map<string,string[]>();const indegrees=new Map<string,number>();const levels=new Map<string,number>();const edges:{from:string;to:string}[]=[];const missingDependencies:string[]=[];
  for(const node of map.values()){
    const dependencies=[...new Set(node.depends_on)].filter(id=>{if(!map.has(id)){missingDependencies.push(id);return false;}return true;});indegrees.set(node.id,dependencies.length);levels.set(node.id,0);
    for(const parent of dependencies){edges.push({from:parent,to:node.id});children.set(parent,[...(children.get(parent)||[]),node.id]);}
  }
  const ready=[...map.keys()].filter(id=>indegrees.get(id)===0);let visited=0;
  while(ready.length){const id=ready.shift()!;visited++;for(const child of children.get(id)||[]){levels.set(child,Math.max(levels.get(child)||0,(levels.get(id)||0)+1));indegrees.set(child,indegrees.get(child)!-1);if(indegrees.get(child)===0)ready.push(child);}}
  const cyclic=visited!==map.size;const rows=new Map<number,PlanNode[]>();
  for(const node of map.values()){const level=cyclic && indegrees.get(node.id)!>0?0:levels.get(node.id)||0;rows.set(level,[...(rows.get(level)||[]),node]);}
  const maxRow=Math.max(1,...[...rows.values()].map(row=>row.length));const width=Math.max(420,maxRow*NODE_WIDTH+(maxRow-1)*22+40);const maxLevel=Math.max(0,...rows.keys());const height=input.length?(maxLevel+1)*(NODE_HEIGHT+54)-54+40:0;const nodes:GraphNode[]=[];
  for(const [level,row] of rows){const rowWidth=row.length*NODE_WIDTH+(row.length-1)*22;row.forEach((node,index)=>nodes.push({...node,level,x:(width-rowWidth)/2+index*(NODE_WIDTH+22),y:20+level*(NODE_HEIGHT+54)}));}
  return {nodes,edges,width,height,cyclic,missingDependencies};
}

