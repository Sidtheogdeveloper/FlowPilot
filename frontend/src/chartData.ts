export type ChartTable={id:string;title:string;schema:Record<string,{name:string;type:string}>;rows:Record<string,unknown>[];total_rows:number;audit:unknown[]};
export function plotRows(table:ChartTable,label:string,value:string){
 return table.rows.map((row,index)=>({label:label?String(row[label]??'Missing'):String(index+1),value:row[value]})).filter((p):p is {label:string;value:number}=>typeof p.value==='number'&&Number.isFinite(p.value)).slice(0,20);
}
export function scaleValues(values:number[]){
 const low=Math.min(0,...values),high=Math.max(0,...values),range=high-low||1;
 return {low,high,y:(value:number)=>220-((value-low)/range)*180,zero:220-((0-low)/range)*180};
}
