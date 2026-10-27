export function formatEstimate(seconds:number|null){
 if(seconds===null||!Number.isFinite(seconds))return 'Unavailable';
 return seconds<.1?'<0.1 s':seconds.toFixed(1)+' s';
}
