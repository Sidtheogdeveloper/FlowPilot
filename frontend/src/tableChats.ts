import type {Workflow} from './types';
export function tableChats(workflows:Workflow[]):Workflow[]{
 const chats=new Map<string,Workflow>();
 for(const flow of workflows){
  if(!['tabular','document'].includes(flow.kind||''))continue;
  const key=flow.chat_id||flow.id,prior=chats.get(key);
  if(!prior||(flow.revision||1)>(prior.revision||1)||((flow.revision||1)===(prior.revision||1)&&(flow.created||'')>(prior.created||'')))chats.set(key,flow);
 }
 return [...chats.values()].sort((a,b)=>Number(Boolean(b.pinned))-Number(Boolean(a.pinned))||(b.created||'').localeCompare(a.created||''));
}
export function conversationTurns(flow:Workflow):Workflow[]{
 const turns=new Map<string,Workflow>();
 for(const turn of flow.turns||[])turns.set(turn.id,turn);
 turns.set(flow.id,flow);
 return [...turns.values()].sort((a,b)=>(a.revision||1)-(b.revision||1));
}
