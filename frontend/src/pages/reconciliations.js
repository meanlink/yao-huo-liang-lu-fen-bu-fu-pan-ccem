import {reconciliations} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {ReconPhase,Verdict} from '../constants/enums.js';
import {esc,fmt,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';

const HEADER_ALIASES={batch:'batch',窑次:'batch',line:'line',窑线:'line',fired:'fired',烧成件数:'fired',reported_yield:'reported_yield',上报良率:'reported_yield',良率:'reported_yield'};

function parseReports(text){
 const trimmed=text.trim();
 if(!trimmed)throw new Error('请先粘贴外协上报，或选择 CSV 文件');
 if(trimmed.startsWith('['))return JSON.parse(trimmed);
 const rows=parseCSV(trimmed);
 return rows.map((row,index)=>{
  const mapped={};
  for(const [key,value] of Object.entries(row)){
   const target=HEADER_ALIASES[key.trim().toLowerCase()]||HEADER_ALIASES[key.trim()];
   if(target)mapped[target]=value;
  }
  const missing=['batch','fired','reported_yield'].filter(k=>mapped[k]===undefined||mapped[k]==='');
  if(missing.length)throw new Error('第 '+(index+1)+' 条上报缺少 '+missing.join('、'));
  return {batch:mapped.batch,line:mapped.line||'',fired:Number(mapped.fired),reported_yield:mapped.reported_yield};
 });
}

function phaseBadge(rec){
 const phase=ReconPhase[rec.phase]||rec.phase;
 return '<span class="pill phase-'+esc(rec.phase)+'">'+esc(phase)+'</span>';
}

function verdictBadge(verdict){
 return '<span class="pill verdict-'+esc(verdict)+'">'+esc(Verdict[verdict]||verdict)+'</span>';
}

function listSection(list){
 return '<section class="panel"><div class="section-title"><h2>对账复盘</h2><span>'+list.length+' 份</span></div>'+
  (list.length?'<div class="recon-list">'+list.map(r=>'<a class="recon-item" href="#/reconciliations/'+r.id+'"><strong>'+esc(r.name)+' '+phaseBadge(r)+'</strong><span>总账 '+esc(r.ledger_total??'未到')+' 件 · 第 '+r.revision+' 版 · '+esc(r.approved?'当前版本已批准':(r.approved_revision?('第 '+r.approved_revision+' 版批准已过期'):'未批准'))+(r.summary?' · 待核 '+r.summary.undetermined+' 窑':'')+'</span><small>'+esc(r.note||'')+'</small></a>').join('')+'</div>':'<p class="empty">尚无对账复盘。用下方表单接入外协上报，先做试算。</p>')+'</section>';
}

function createSection(){
 return '<section class="panel"><div class="section-title"><h2>接入外协上报</h2><span>先试算，后对账</span></div><form id="recon-form"><label>复盘名称<input name="name" required minlength="2" placeholder="例如：11 月外协窑对账"></label><div class="parameter-grid"><label>目标良率（%）<input type="number" name="target" step="any" min="0.1" max="100" value="93" required></label><label>上报文件（可选）<input type="file" id="recon-file" accept=".json,.csv"></label></div><label>外协上报 CSV 或 JSON<textarea name="items" rows="7" required placeholder="CSV 表头：batch,line,fired,reported_yield&#10;或 JSON：[{&quot;batch&quot;:&quot;W-1&quot;,&quot;fired&quot;:2000,&quot;reported_yield&quot;:93.0}]"></textarea></label><p class="hint">只收烧成件数与四舍五入到一位小数的良率。合格件数由区间推算，待厂内总账到达后再收窄。</p><button type="submit" class="primary" '+(session.user.role==='viewer'?'disabled':'')+'>保存并试算 ↗</button></form></section>';
}

function numberOrDash(value){return value===null||value===undefined?'—':fmt(value);}

function range(min,max){return min===max?String(min):min+'–'+max;}

function itemsTable(rec){
 const rows=rec.items.map(row=>{
  const supplied=row.supplied_passed??'';
  const canSupply=session.user.role!=='viewer';
  return '<tr class="'+(row.verdict==='待核'?'needs-review':'')+'"><td><strong>'+esc(row.batch)+'</strong><small>'+esc(row.line||'—')+'</small></td>'+
   '<td>'+numberOrDash(row.fired)+'</td><td>'+esc(row.reported_yield)+'%</td>'+
   '<td>'+(row.reported_min===null?'无法解析':range(row.reported_min,row.reported_max))+'</td>'+
   '<td>'+(row.constrained_min===null?'—':range(row.constrained_min,row.constrained_max))+(rec.feasible===true?'':'<small>'+(rec.phase==='needs_input'?'上报待核，暂不推算':rec.ledger_total===null?'总账未到，暂用上报区间':'总账无解，未收窄')+'</small>')+'</td>'+
   '<td>'+numberOrDash(row.required_passed)+'</td>'+
   '<td><div class="supply-cell"><input type="number" min="0" max="'+esc(row.fired)+'" step="1" value="'+esc(supplied)+'" placeholder="'+(row.reported_min===null?'待核对上报':esc(range(row.reported_min,row.reported_max)))+'" aria-label="'+esc(row.batch)+' 补报合格件数" '+(canSupply?'':'disabled')+'><button class="small" data-supply="'+esc(row.batch)+'" '+(canSupply?'':'disabled')+'>补报</button></div>'+(row.supplied_passed!==null&&row.supplied_passed!==undefined?'<small>实数已补</small>':'')+'</td>'+
   '<td>'+verdictBadge(row.verdict)+'</td><td class="reason">'+esc(row.reason||'—')+'</td></tr>';
 }).join('');
 return '<div class="table-wrap"><table><thead><tr><th>窑次 / 窑线</th><th>烧成件数</th><th>上报良率</th><th>上报可解析区间</th><th>整批约束后区间</th><th>达标线</th><th>后补实数</th><th>判定</th><th>待核原因</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}

function ledgerSection(rec){
 const total=rec.total;
 const locked=session.user.role==='viewer';
 return '<section class="panel"><div class="section-title"><h2>整批对账</h2><span>第 '+rec.revision+' 版 · 更新 '+date(rec.updated_at)+'</span></div>'+
  '<div class="metrics compact-metrics">'+
  [['整批烧成件数',total.fired,'件'],['各窑区间合计',range(total.min,total.max),'件'],['厂内总账',total.ledger??'未到','件']].map(([k,v,u])=>'<div><span>'+k+'</span><strong>'+esc(v)+'<small>'+u+'</small></strong></div>').join('')+
  '</div><p class="ledger-note '+(rec.feasible===false?'error':'')+'">'+esc(total.note)+'</p>'+
  '<form id="ledger-form" class="inline-form"><label>厂内总账合格件数<input type="number" name="ledger_total" min="0" step="1" value="'+esc(total.ledger??'')+'" placeholder="整批合格件数" required '+(locked?'disabled':'')+'></label><input type="hidden" name="expected_revision" value="'+rec.revision+'"><button type="submit" class="primary" '+(locked?'disabled':'')+'>提交总账并对账 ↗</button></form>'+
  '<p class="hint">提交总账会收窄每一窑的可能区间，版本号递增。若总账与上报区间对不上，页面会指出差多少件。</p></section>';
}

function approvalSection(rec){
 const isAdmin=session.user.role==='admin';
 const canApprove=rec.can_approve&&!rec.approved;
 const reasons=rec.blocking_reasons||[];
 return '<div class="two-columns"><section class="panel"><div class="section-title"><h2>批准条件</h2><span>'+(rec.approved?'当前版本已批准':(rec.can_approve?'满足条件':'尚不满足'))+'</span></div>'+
  (reasons.length?'<ul class="blocking">'+reasons.map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul>':'<p class="checks">整批总账有解，且全部 '+rec.summary.item_count+' 个窑次都能确定是否达标。</p>')+
  (rec.stale_approval?'<p class="warning">第 '+rec.approved_revision+' 版的批准早于当前数据；补报或改账后需重新核对，旧依据保留在下方留档。</p>':'')+
  '<div class="approval-actions"><button class="primary" id="approve-recon" '+(isAdmin&&canApprove?'':'disabled')+'>批准当前第 '+rec.revision+' 版结果</button><span>'+(rec.approved?'已批准':(isAdmin?(canApprove?'待批准':'补齐依据后才能批准'):'仅业务负责人可批准'))+'</span></div>'+
  '<p class="hint">批准会携带当前版本号；若提交前发生补报或改账，服务端拒绝过期批准并提示刷新。</p></section>'+
  '<section class="panel"><div class="section-title"><h2>批准留档</h2><span>'+(rec.approval_history.length||0)+' 次</span></div>'+
  (rec.approval_history.length?rec.approval_history.map(a=>'<details class="approval-record"'+(a.revision===rec.revision?' open':'')+'><summary>第 '+a.revision+' 版 · '+date(a.approved_at)+'</summary>'+snapshotView(a.snapshot)+'</details>').join(''):'<p class="empty">批准后在此保留当时的整批依据。</p>')+'</section></div>';
}

function snapshotView(snapshot){
 if(!snapshot)return '<p class="empty">无快照</p>';
 const rows=(snapshot.items||[]).map(row=>'<tr><td>'+esc(row.batch)+'</td><td>'+range(row.reported_min,row.reported_max)+'</td><td>'+range(row.constrained_min,row.constrained_max)+'</td><td>'+esc(row.required_passed)+'</td><td>'+verdictBadge(row.verdict)+'</td></tr>').join('');
 return '<div class="snapshot"><p>总账 <strong>'+esc(snapshot.ledger_total??'—')+'</strong> 件 · 目标良率 <strong>'+esc(snapshot.target)+'%</strong> · 计算时间 '+date(snapshot.computed_at)+'</p><div class="table-wrap"><table><thead><tr><th>窑次</th><th>上报区间</th><th>约束后区间</th><th>达标线</th><th>判定</th></tr></thead><tbody>'+rows+'</tbody></table></div></div>';
}

export async function reconPage(id){
 workspace.reconciliations=await reconciliations.list();
 if(!id){
  return heading('05 / RECONCILIATION','外协窑对账复盘','外协只报烧成件数与一位小数良率，先用区间试算；厂内总账到达后收窄每窑范围，再批准整批结论。')+listSection(workspace.reconciliations)+'<div class="recon-grid">'+createSection()+'<section class="method-sheet"><p class="eyebrow">METHOD / RECONCILIATION</p><h2>一位小数良率对应一段可能件数，而不是一个点。</h2><ul><li>上报 93.0% 表示真实良率落在 [92.95%, 93.05%)，换算为整数合格件数是一个闭区间。</li><li>整批总账到达后，各窑区间之和必须等于总账，据此收窄每一窑的上下限。</li><li>区间整体高于达标线的窑次判达标，整体低于的判未达标；跨越达标线的必须补报实数。</li><li>后补任一窑实数会改变整批约束，旧批准保留当时依据但不再代表当前版本。</li></ul><div class="method-trace"><span>输入</span><strong>烧成件数 + 一位小数良率 + 厂内总账</strong><span>输出</span><strong>各窑可能件数上下限 / 待核原因 / 批准快照</strong></div></section></div>';
 }
 const rec=await reconciliations.get(id);
 workspace.selectedRecon=rec;
 return heading('05 / RECONCILIATION',rec.name,'第 '+rec.revision+' 版 · 目标良率 '+rec.target+'% · 补报或改账会让版本递增，旧批准保留依据但不再代表当前结果。',(phaseBadge(rec)+'<button class="button" id="refresh-recon">刷新结果 ↻</button>'))+
  '<div class="recon-selector"><label>对账复盘<select id="select-recon">'+workspace.reconciliations.map(r=>'<option value="'+r.id+'" '+(r.id===rec.id?'selected':'')+'>'+esc(r.name)+' · 第 '+r.revision+' 版</option>').join('')+'</select></label><span>'+(rec.approved?'当前版本已批准':'未批准')+'</span></div>'+
  ledgerSection(rec)+
  '<section class="panel"><div class="section-title"><h2>逐窑范围与待核原因</h2><span>'+rec.summary.item_count+' 窑 · 已确定 '+rec.summary.determined+' · 待核 '+rec.summary.undetermined+'</span></div>'+itemsTable(rec)+'</section>'+
  approvalSection(rec);
}

export function bindRecon(refresh){
 const fileInput=document.querySelector('#recon-file');
 fileInput?.addEventListener('change',async event=>{
  try{
   const file=event.target.files[0];
   if(!file)return;
   document.querySelector('[name=items]').value=await file.text();
  }catch(error){notify(error.message,true);}
 });
 const createForm=document.querySelector('#recon-form');
 if(createForm)submit(createForm,async form=>{
  const items=parseReports(String(form.get('items')));
  const target=Number(form.get('target'));
  const rec=await reconciliations.create({name:form.get('name'),target,items});
  notify('复盘已保存，当前为试算状态');
  location.hash='#/reconciliations/'+rec.id;
 });
 document.querySelector('#refresh-recon')?.addEventListener('click',refresh);
 document.querySelector('#select-recon')?.addEventListener('change',event=>{location.hash='#/reconciliations/'+event.target.value;});
 const ledgerForm=document.querySelector('#ledger-form');
 if(ledgerForm)submit(ledgerForm,async form=>{
  const rec=workspace.selectedRecon;
  try{
   await reconciliations.setLedger(rec.id,{ledger_total:Number(form.get('ledger_total')),expected_revision:Number(form.get('expected_revision'))});
   notify('总账已对账');
  }catch(error){notify(error.message,true);}
  await refresh();
 });
 document.querySelectorAll('[data-supply]').forEach(button=>{
  button.addEventListener('click',async()=>{
   const row=button.closest('tr');
   const input=row.querySelector('input[type=number]');
   const value=input.value.trim();
   if(value==='')return notify('请先填写该窑的合格件数',true);
   const rec=workspace.selectedRecon;
   try{
    await reconciliations.supply(rec.id,{batch:button.dataset.supply,supplied_passed:Number(value),expected_revision:rec.revision});
    notify('已补报 '+button.dataset.supply+' 的实数，整批需重新核对');
   }catch(error){notify(error.message,true);}
   await refresh();
  });
 });
 document.querySelector('#approve-recon')?.addEventListener('click',async event=>{
  const rec=workspace.selectedRecon;
  event.currentTarget.disabled=true;
  try{
   await reconciliations.approve(rec.id,{expected_revision:rec.revision});
   notify('已批准当前版本，依据已留档');
  }catch(error){notify(error.message,true);}
  await refresh();
 });
}
