import time
from dataclasses import dataclass
@dataclass
class Decision:
    assignments:dict
    metrics:dict

def schedule(tasks,workers,policy='greedy',max_states=5000,budget_ms=50,max_ready=6,top_k=3):
    start=time.perf_counter();tasks=tasks[:max_ready] if policy=="bounded" else tasks
    capacities={w['id']:[w['cpu'],w['memory_mb']] for w in workers};wm={w['id']:w for w in workers}
    def duration(t,w):return t.get('estimate',1)/wm[w].get('speed',1)
    candidates={t['id']:[w['id'] for w in sorted(workers,key=lambda w:(w.get('queue_seconds',0)+t.get('estimate',1)/w.get('speed',1),w['id'])) if t['handler'] in w['handlers'] and t['resources']['cpu']<=w['cpu'] and t['resources']['memory_mb']<=w['memory_mb']][:(top_k if policy=="bounded" else len(workers))] for t in tasks}
    if policy=='heft':tasks=sorted(tasks,key=lambda t:(-t.get('rank',t.get('estimate',1)),t['id']))
    greedy={};remaining={k:v[:] for k,v in capacities.items()};finish={w['id']:w.get('queue_seconds',0) for w in workers}
    def fits(t,w,rem):return all(d<=c for d,c in zip((t['resources']['cpu'],t['resources']['memory_mb']),rem[w]))
    def place(t,w,rem,sign):
        rem[w][0]+=sign*t['resources']['cpu'];rem[w][1]+=sign*t['resources']['memory_mb']
    for t in tasks:
        eligible=[w for w in candidates[t['id']] if fits(t,w,remaining)]
        if eligible:
            w=min(eligible,key=lambda w:(finish[w]+duration(t,w),w));greedy[t['id']]=w;place(t,w,remaining,-1);finish[w]+=duration(t,w)
    def score(a):
        loads={w['id']:w.get('queue_seconds',0) for w in workers};completion=[]
        for t in tasks:
            if t['id'] in a:
                w=a[t['id']];loads[w]+=duration(t,w);completion.append(loads[w])
        return (-len(a),max(completion,default=0),sum(completion),tuple(sorted(a.items())))
    best=greedy.copy();best_score=score(best);states=0;truncated=False
    def dfs(index,a,rem):
        nonlocal best,best_score,states,truncated
        if states>=max_states or (time.perf_counter()-start)*1000>=budget_ms:truncated=True;return
        states+=1
        if index==len(tasks):
            s=score(a)
            if s<best_score:best=a.copy();best_score=s
            return
        t=tasks[index]
        for w in candidates[t['id']]:
            if fits(t,w,rem):
                a[t['id']]=w;place(t,w,rem,-1);dfs(index+1,a,rem);place(t,w,rem,1);del a[t['id']]
        dfs(index+1,a,rem)
    if policy=='bounded':dfs(0,{}, {k:v[:] for k,v in capacities.items()})
    elif policy not in ('greedy','heft'):raise ValueError('Unknown scheduling policy')
    return Decision(best,{'policy':policy,'elapsed_ms':(time.perf_counter()-start)*1000,'states':states,'truncated':truncated,'deferred':len(tasks)-len(best),'candidate_count':sum(map(len,candidates.values())),'objective':list(best_score[:3]),'estimates':{t['id']:t.get('estimate',1) for t in tasks}})
