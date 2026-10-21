import {describe,expect,it} from 'vitest';
import {chatTitle,filterChats,followupSubmission,isLatestTurn,orderedTurns,layoutDag,NODE_WIDTH,shouldAcceptDetail,shouldOpenCreatedChat,submission} from './state';
import type {Chat,Draft,PlanNode,Workflow} from './types';
const node=(id:string,depends_on:string[]=[]):PlanNode=>({id,handler:'monthly_sales',depends_on});
describe('saved analysis isolation',()=>{
 it('submits the exact request and selected dataset without changing the draft',()=>{
  const draft:Draft={datasetId:'original-csv',request:'  Calculate monthly sales\nusing this CSV.  ',planner:'huggingface',policy:'bounded'};
  const before={...draft};const body=submission(draft);
  expect(body).toEqual({dataset_id:'original-csv',request:draft.request,planner:'huggingface',policy:'bounded'});
  draft.datasetId='another-csv';expect(body.dataset_id).toBe('original-csv');expect(before.request).toBe(body.request);
 });
 it('rejects delayed detail responses after selecting another chat or newer refresh',()=>{
  const request={epoch:2,id:'chat-a',generation:10};
  expect(shouldAcceptDetail(request,{...request})).toBe(true);
  expect(shouldAcceptDetail(request,{epoch:3,id:'chat-b',generation:11})).toBe(false);
  expect(shouldAcceptDetail(request,{epoch:2,id:'chat-a',generation:11})).toBe(false);
  expect(shouldAcceptDetail(request,{epoch:3,id:'chat-a',generation:10})).toBe(false);
  expect(shouldAcceptDetail(request,{epoch:2,id:null,generation:10})).toBe(false);
 });
 it('does not replace the selected chat or a newer draft when a submitted plan finishes',()=>{
  expect(shouldOpenCreatedChat(4,4,null)).toBe(true);
  expect(shouldOpenCreatedChat(4,4,'saved-chat')).toBe(false);
  expect(shouldOpenCreatedChat(4,5,null)).toBe(false);
 });
 it('searches saved requests, titles and dataset filenames, including legacy chats',()=>{
  const chats:Chat[]=[{id:'a',status:'completed',policy:'greedy',title:'Sales overview',request:'IQR multiplier 2',dataset_filename:'retail.csv'},{id:'b',status:'planned',policy:'heft',request:null}];
  expect(filterChats(chats,'  RETAIL ')).toEqual([chats[0]]);
  expect(filterChats(chats,'multiplier')).toEqual([chats[0]]);
  expect(filterChats(chats,'previous analysis')).toEqual([chats[1]]);
  expect(filterChats(chats,'')).toEqual(chats);expect(chatTitle(chats[1])).toBe('Previous analysis');
 });
});
describe('dependency graph layout',()=>{
 it('draws branches and joins from dependencies rather than a fixed task count',()=>{
  const result=layoutDag([node('root'),node('left',['root']),node('right',['root']),node('report',['left','right'])]);
  const positions=Object.fromEntries(result.nodes.map(n=>[n.id,n]));
  expect(positions.root.level).toBe(0);expect(positions.left.level).toBe(1);expect(positions.right.level).toBe(1);expect(positions.report.level).toBe(2);
  expect(positions.left.x+NODE_WIDTH).toBeLessThan(positions.right.x);
  expect(result.edges).toEqual([{from:'root',to:'left'},{from:'root',to:'right'},{from:'left',to:'report'},{from:'right',to:'report'}]);expect(result.cyclic).toBe(false);
 });
 it('uses longest dependency paths for an uneven graph and keeps graphs independent',()=>{
  const first=layoutDag([node('a'),node('b',['a']),node('c',['a','b'])]);const other=layoutDag([node('single')]);
  expect(first.nodes.find(n=>n.id==='c')?.level).toBe(2);expect(other.nodes.map(n=>n.id)).toEqual(['single']);expect(other.edges).toEqual([]);expect(first.nodes).toHaveLength(3);
 });
 it('reports cycles and missing dependencies without crashing or invalid coordinates',()=>{
  const result=layoutDag([node('a',['b']),node('b',['a']),node('c',['missing'])]);
  expect(result.cyclic).toBe(true);expect(result.missingDependencies).toEqual(['missing']);expect(result.nodes).toHaveLength(3);
  for(const n of result.nodes){expect(Number.isFinite(n.x)).toBe(true);expect(Number.isFinite(n.y)).toBe(true);expect(n.x).toBeGreaterThanOrEqual(0);}
 });
 it('handles an empty graph and does not mutate submitted dependency lists',()=>{
  expect(layoutDag([]).nodes).toEqual([]);expect(layoutDag([]).height).toBe(0);
  const nodes=[node('a'),node('b',['a','a'])];layoutDag(nodes);expect(nodes[1].depends_on).toEqual(['a','a']);
 });
});

const version=(id:string,revision:number):Workflow=>({id,chat_id:'chat-root',revision,request:`Request ${revision}`,status:'completed',policy:'greedy',plan:{tasks:[node(`task-${revision}`)]},tasks:[]});
describe('same-chat workflow versions',()=>{
 it('finds a grouped chat by an earlier full request after a follow-up changes the latest prompt',()=>{
  const chat:Chat={id:'v2',chat_id:'root',revision:2,status:'completed',policy:'greedy',title:'Retail report',request:'Which product has the highest revenue?',search_text:'Retail report with IQR multiplier 2. Which product has the highest revenue?'};
  expect(filterChats([chat],'multiplier 2')).toEqual([chat]);expect(filterChats([chat],'highest revenue')).toEqual([chat]);expect(filterChats([chat],'unrelated')).toEqual([]);
 });
 it('retains each version own request and graph in chronological order',()=>{
  const first=version('v1',1),second=version('v2',2),third=version('v3',3);const detail={...second,turns:[third,first,second],latest_workflow_id:'v3'};
  const turns=orderedTurns(detail);expect(turns.map(t=>t.id)).toEqual(['v1','v2','v3']);expect(turns.map(t=>t.plan.tasks[0].id)).toEqual(['task-1','task-2','task-3']);expect(turns[0].request).toBe('Request 1');expect(isLatestTurn(detail)).toBe(false);expect(isLatestTurn({...third,turns:[first,second,third]})).toBe(true);
 });
 it('uses authoritative latest id even if turn metadata is incomplete',()=>{
  expect(isLatestTurn({...version('v1',1),latest_workflow_id:'v2'})).toBe(false);expect(orderedTurns(version('v1',1))).toHaveLength(1);
 });
 it('follow-ups preserve request verbatim and cannot change dataset through composer payload',()=>{
  expect(followupSubmission({datasetId:'different-dataset',request:'  Use IQR multiplier 3 instead.\n',planner:'template',policy:'heft'})).toEqual({request:'  Use IQR multiplier 3 instead.\n',planner:'template',policy:'heft'});
 });
});
