import {DagGraph} from './DagGraph';
import {artifactUrl} from './api';
import type {Workflow} from './types';
type TableInsights={sections?:{title:string;findings:string[];artifact:string;audit:unknown[]}[]};
export function TableChatTurn({turn,latest,busy,onAction}:{turn:Workflow;latest:boolean;busy:boolean;onAction:(action:string)=>void}){
 const insights=turn.insights as unknown as TableInsights|null;
 const warnings=(turn as Workflow&{capacity_warnings?:string[]}).capacity_warnings;
 return <section className="conversation-turn" aria-label={`Conversation turn ${turn.revision||1}`}>
  <div className="user-message"><span>You</span><p>{turn.request}</p></div>
  <div className="table-assistant-message"><div className="assistant-heading"><img src="/favicon.svg" alt="" width="24" height="24"/><strong>FlowPilot</strong><span className={`run-badge ${turn.status}`}>{turn.status}</span></div>
   <p className="turn-caption">DAG version {turn.revision||1} · {turn.plan.tasks.length} approved operations</p>
   {turn.status==='planned'&&<p>Your workflow is validated and ready. Review the steps below, then run it.</p>}
   {turn.status==='running'&&<p role="status">Analysing your data. Results will appear here when the workflow completes.</p>}
   {turn.status==='failed'&&<p role="alert">This run did not complete. Review the task details or ask a revised question.</p>}
   {turn.status==='cancelled'&&<p>This run was cancelled. Previously accepted outputs remain available.</p>}
   {warnings?.map(w=><p role="status" key={w}>{w}</p>)}
   {insights?.sections?.map((section,i)=><div className="findings-section" key={i}><h3>{section.title}</h3>{section.findings.map((finding,j)=><p key={j}>{finding}</p>)}<a href={artifactUrl(section.artifact)}>Download calculated result</a><details><summary>Applied scope and transformations</summary><pre>{JSON.stringify(section.audit,null,2)}</pre></details></div>)}
   {turn.report_url&&<a className="report-download" href={turn.report_url}>Download insights report ↗</a>}
   <details className="turn-dag" open={latest}><summary>Workflow DAG · version {turn.revision||1}</summary><DagGraph nodes={turn.plan.tasks} tasks={turn.tasks}/><details><summary>Operation details and column mappings</summary>{turn.plan.tasks.map(t=><div key={t.id}><strong>{t.id}: {t.handler}</strong><pre>{JSON.stringify(t.parameters,null,2)}</pre></div>)}</details></details>
   {latest&&<div className="run-actions">{turn.status==='planned'&&<button disabled={busy} onClick={()=>onAction('start')}>Run validated workflow</button>}{turn.status==='running'&&<button disabled={busy} onClick={()=>onAction('pause')}>Pause</button>}{turn.status==='paused'&&<button disabled={busy} onClick={()=>onAction('resume')}>Resume</button>}{['running','paused','planned'].includes(turn.status)&&<button disabled={busy} onClick={()=>onAction('cancel')}>Cancel run</button>}</div>}
  </div>
 </section>;
}
