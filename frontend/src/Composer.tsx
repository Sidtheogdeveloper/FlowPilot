import {useRef} from 'react';
import {Icon} from './Icon';
import {countLabel,plannerLabels,policyLabels} from './state';
import type {Dataset,Draft} from './types';
type Props={draft:Draft;datasets:Dataset[];uploading:boolean;generating:boolean;error:string;onDraft:(draft:Draft)=>void;onUpload:(file:File)=>void;onSubmit:()=>void};
export function Composer({draft,datasets,uploading,generating,error,onDraft,onUpload,onSubmit}:Props){
 const fileInput=useRef<HTMLInputElement>(null);const selected=datasets.find(item=>item.id===draft.datasetId);const disabled=uploading||generating;
 return <div className="composer-area"><form className="composer" onSubmit={event=>{event.preventDefault();onSubmit();}} aria-label="New analysis request" aria-busy={disabled}>
 <label htmlFor="analysis-request" className="sr-only">What would you like to analyse?</label>
 <textarea id="analysis-request" placeholder="Describe your retail analysis…" value={draft.request} disabled={generating} onChange={event=>onDraft({...draft,request:event.target.value})} rows={3}/>
 <div className="composer-toolbar"><div className="attach-controls"><button type="button" className="icon-button attach-button" title="Upload transaction CSV" aria-label="Upload transaction CSV" disabled={disabled} onClick={()=>fileInput.current?.click()}><Icon name="plus"/></button>
 <input ref={fileInput} id="dataset-upload" className="sr-only" type="file" accept=".csv,text/csv" aria-label="Transaction CSV file" disabled={disabled} onChange={event=>{const file=event.target.files?.[0];if(file)onUpload(file);event.target.value='';}}/>
 <label htmlFor="dataset-select" className="sr-only">Dataset for this new analysis</label>
 <div className="dataset-select-wrap"><Icon name="file" size={15}/><select id="dataset-select" aria-label="Dataset for this new analysis" value={draft.datasetId} disabled={disabled} onChange={event=>onDraft({...draft,datasetId:event.target.value})}><option value="">Choose a dataset</option>{datasets.map(dataset=><option key={dataset.id} value={dataset.id}>{dataset.filename} · {countLabel(dataset.rows)} rows</option>)}</select></div>
 </div><div className="compose-actions"><details className="settings-menu"><summary aria-label="Plan settings"><Icon name="sliders" size={16}/><span>{plannerLabels[draft.planner]}</span></summary><div className="settings-popover"><label htmlFor="planner-mode">Plan creation</label><select id="planner-mode" value={draft.planner} disabled={disabled} onChange={event=>onDraft({...draft,planner:event.target.value as Draft['planner']})}><option value="template">Deterministic template</option><option value="huggingface">Free local Qwen model</option></select><label htmlFor="scheduling-policy">Scheduling policy</label><select id="scheduling-policy" value={draft.policy} disabled={disabled} onChange={event=>onDraft({...draft,policy:event.target.value as Draft['policy']})}>{Object.entries(policyLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select><p>Model intent is compiled and validated before execution.</p></div></details>
 <button className="send-button" type="submit" disabled={disabled||!draft.datasetId||!draft.request.trim()} aria-label={generating?'Generating and validating plan':'Generate and validate plan'}>{generating?<span className="spinner"/>:<Icon name="arrow" size={19}/>}</button></div></div>
 </form>
 {error?<div role="alert" className="draft-error"><strong>Could not create this analysis.</strong><span>{error}</span><small>Your request is preserved. Adjust it and try again.</small></div>:null}
 <div className="composer-footnote">{uploading?'Uploading dataset…':selected?`${selected.filename} · ${countLabel(selected.rows)} rows`:'Upload a CSV or reuse a dataset from the sidebar.'}<span>Retail analysis · approved operations only</span></div>
 <details className="csv-help"><summary>CSV format</summary><p>Required columns: <code>transaction_id, timestamp, product_id, quantity, unit_price</code>. Unusual-value flags are descriptive; they do not establish fraud.</p></details>
 </div>;
}
