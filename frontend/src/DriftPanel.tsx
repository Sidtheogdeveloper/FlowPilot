import {useEffect,useState} from 'react';
import {api} from './api';
export type Drift={baseline_id:string|null;flagged:boolean;message?:string;rows_before?:number;rows_after?:number;schema_and_quality_changes?:{kind:string;column:string;before?:unknown;after?:unknown;percentage_points?:number}[];distributions?:{column:string;flagged:boolean;ks_distance?:number;note?:string}[];method?:string};
export function DriftResult({data}:{data:Drift}){
 return <section className={'drift-panel '+(data.flagged?'flagged':'')}><h3>{data.baseline_id?(data.flagged?'Dataset changes detected':'No drift flags at configured thresholds'):'Dataset drift'}</h3>{data.message&&<p>{data.message}</p>}{data.baseline_id&&<><p>{data.rows_before?.toLocaleString()} → {data.rows_after?.toLocaleString()} rows. Changed data may be expected; review before refreshing an analysis.</p>{data.schema_and_quality_changes?.map((c,i)=><p key={i}><strong>{c.column}</strong>: {c.kind.replaceAll('_',' ')}{c.before!==undefined?' · '+String(c.before)+' → '+String(c.after):''}{c.percentage_points!==undefined?' ('+c.percentage_points+' percentage points)':''}</p>)}{data.distributions?.map(c=><p key={c.column}><strong>{c.column}</strong>: {c.ks_distance===undefined?c.note:'distribution distance '+c.ks_distance.toFixed(3)+(c.flagged?' · flagged':'')}</p>)}<p className="tool-note">{data.method}</p></>}</section>;
}
export function DriftPanel({id}:{id:string}){
 const [data,setData]=useState<Drift|null>(null),[error,setError]=useState('');
 useEffect(()=>{const c=new AbortController();void api<Drift>('/product/datasets/'+id+'/drift',{signal:c.signal}).then(setData).catch(e=>{if(!c.signal.aborted)setError(String(e));});return()=>c.abort();},[id]);
 return <>{data?<DriftResult data={data}/>:<p role="status">Checking dataset versions…</p>}{error&&<p role="alert">{error}</p>}</>;
}
