import {describe,it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {parseAppearance,resolveTheme} from './appearance';

describe('saved appearance preferences',()=>{
 it('recovers from unavailable or malformed storage',()=>{
  expect(parseAppearance(null)).toEqual({theme:'system',layout:'comfortable'});
  expect(parseAppearance('broken')).toEqual({theme:'system',layout:'comfortable'});
  expect(parseAppearance('null')).toEqual({theme:'system',layout:'comfortable'});
 });
 it('retains only supported theme and layout choices',()=>{
  expect(parseAppearance('{"theme":"dark","layout":"focus"}')).toEqual({theme:'dark',layout:'focus'});
  expect(parseAppearance('{"theme":"invalid","layout":"invalid"}')).toEqual({theme:'system',layout:'comfortable'});
 });
 it('follows the device setting only in system mode',()=>{
  expect(resolveTheme('system',true)).toBe('dark');
  expect(resolveTheme('system',false)).toBe('light');
  expect(resolveTheme('light',true)).toBe('light');
  expect(resolveTheme('dark',false)).toBe('dark');
 });
 it('applies the saved choice before React starts, without inline scripts',()=>{
  const document={documentElement:{dataset:{},style:{colorScheme:''}}};
  const boot=readFileSync(new URL('../public/appearance-init.js',import.meta.url),'utf8');
  runInNewContext(boot,{document,localStorage:{getItem:()=>'{"theme":"dark","layout":"focus"}'},window:{matchMedia:()=>({matches:false})}});
  expect(document.documentElement.dataset).toEqual({theme:'dark',layout:'focus'});
  expect(document.documentElement.style.colorScheme).toBe('dark');
 });
 it('still initializes when browser storage is disabled',()=>{
  const document={documentElement:{dataset:{},style:{colorScheme:''}}};
  const boot=readFileSync(new URL('../public/appearance-init.js',import.meta.url),'utf8');
  runInNewContext(boot,{document,localStorage:{getItem:()=>{throw new Error('Storage unavailable');}},window:{matchMedia:()=>({matches:true})}});
  expect(document.documentElement.dataset.theme).toBe('dark');
 });
});
