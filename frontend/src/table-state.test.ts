import {describe,it,expect} from 'vitest';
import {nodeDescription} from './state';
import type {PlanNode} from './types';
const load:PlanNode={id:'t0',handler:'table_load',depends_on:[],parameters:{alias:'t0',schema:{'t0.c0':{name:'Classroom',type:'text'},'t0.c1':{name:'Exam score',type:'number'}}}};
describe('schema-aware DAG labels',()=>{
 it('uses original headers rather than opaque column IDs',()=>{
  const aggregate:PlanNode={id:'summary',handler:'table_aggregate',depends_on:['t0'],parameters:{group_by:['t0.c0'],metrics:[{name:'average',function:'mean',column:'t0.c1'}]}};
  expect(nodeDescription(aggregate,[load,aggregate])).toBe('mean Exam score');
 });
 it('shows join keys and join semantics without executing metadata',()=>{
  const right:PlanNode={id:'t1',handler:'table_load',depends_on:[],parameters:{alias:'t1',schema:{'t1.c0':{name:'Class code',type:'text'}}}};
  const join:PlanNode={id:'joined',handler:'table_join',depends_on:['t0','t1'],parameters:{left_keys:['t0.c0'],right_keys:['t1.c0'],how:'left'}};
  expect(nodeDescription(join,[load,right,join])).toContain('Classroom');
  expect(nodeDescription(join,[load,right,join])).toContain('Class code (left)');
 });
});
