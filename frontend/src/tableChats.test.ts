import {describe,it,expect} from 'vitest';
import {tableChats,conversationTurns} from './tableChats';
import type {Workflow} from './types';
const turn=(id:string,chat:string,revision:number):Workflow=>({id,chat_id:chat,revision,kind:'tabular',status:'completed',policy:'greedy',title:'Original question',request:`Question ${revision}`,plan:{tasks:[]},tasks:[],created:`2026-10-${20+revision}T12:00:00Z`});
describe('one thread per chat',()=>{
 it('keeps a single sidebar entry after multiple follow-ups',()=>{
  const rows=tableChats([turn('a','root',1),turn('b','root',2),turn('c','root',3),turn('d','another',1)]);
  expect(rows).toHaveLength(2);expect(rows[0].id).toBe('c');expect(rows[0].title).toBe('Original question');
 });
 it('renders every conversational turn once, with the latest polled result',()=>{
  const first=turn('a','root',1),second=turn('b','root',2),latest={...second,status:'running',turns:[second,first]};
  expect(conversationTurns(latest).map(t=>[t.id,t.status])).toEqual([['a','completed'],['b','running']]);
 });
});
