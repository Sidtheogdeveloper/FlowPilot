import {describe,it,expect} from 'vitest';
import {plotRows,scaleValues} from './chartData';
import {tableChats} from './tableChats';
import type {Workflow} from './types';
describe('result chart correctness',()=>{
 it('keeps finite observations in result order without turning null into zero',()=>{
  const table={id:'x',title:'x',schema:{v:{name:'Value',type:'number'}},rows:[{v:null},{v:-4},{v:NaN},{v:0},{v:Infinity},{v:6}],total_rows:6,audit:[]};
  expect(plotRows(table,'','v')).toEqual([{label:'2',value:-4},{label:'4',value:0},{label:'6',value:6}]);
 });
 it('places negative and positive values on opposite sides of a shared zero axis',()=>{
  const s=scaleValues([-5,10]);expect(s.y(-5)).toBeGreaterThan(s.zero);expect(s.y(10)).toBeLessThan(s.zero);
  expect(Number.isFinite(scaleValues([0]).zero)).toBe(true);
 });
 it('limits chart points without changing export or source rows',()=>{
  const rows=Array.from({length:100},(_,v)=>({v}));
  expect(plotRows({id:'x',title:'x',schema:{},rows,total_rows:100,audit:[]},'','v')).toHaveLength(20);
  expect(rows).toHaveLength(100);
 });
 it('keeps pinned conversations first and includes document conversations once',()=>{
  const base={kind:'tabular',status:'completed',policy:'greedy',plan:{tasks:[]},tasks:[]} as const;
  const a={...base,id:'a',chat_id:'a',created:'2026-01-01',pinned:true} as unknown as Workflow;
  const b={...base,id:'b',chat_id:'b',kind:'document',created:'2026-10-01'} as unknown as Workflow;
  expect(tableChats([b,a]).map(x=>x.id)).toEqual(['a','b']);
 });
});
