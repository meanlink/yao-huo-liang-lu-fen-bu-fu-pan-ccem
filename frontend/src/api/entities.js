import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const reconciliations={
 list:()=>api('/reconciliations'),
 get:id=>api('/reconciliations/'+id),
 create:data=>api('/reconciliations',{method:'POST',body:JSON.stringify(data)}),
 setLedger:(id,data)=>api('/reconciliations/'+id+'/ledger',{method:'PUT',body:JSON.stringify(data)}),
 supply:(id,data)=>api('/reconciliations/'+id+'/supply',{method:'PUT',body:JSON.stringify(data)}),
 approve:(id,data)=>api('/reconciliations/'+id+'/approve',{method:'POST',body:JSON.stringify(data)})};
