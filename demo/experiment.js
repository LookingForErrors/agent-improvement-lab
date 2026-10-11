(function(host){
 'use strict';
 const cellFor=(data,id,cid)=>data.results.find(c=>c.trace_id===id&&c.criterion_id===cid);
 function aggregate(data,arm,cid){
  const ids=new Set(data.traces.filter(t=>t.arm===arm).map(t=>t.id)),cells=data.results.filter(c=>ids.has(c.trace_id)&&c.criterion_id===cid);
  const counts={expected:ids.size,assessed:0,correct:0,incorrect:0,unassessable:0,not_applicable:0,missing:0,grader_error:0};
  for(const c of cells){if(Object.hasOwn(counts,c.status))counts[c.status]++;if(c.status==='assessed'&&Object.hasOwn(counts,c.verdict))counts[c.verdict]++;}
  counts.missing+=ids.size-cells.length;counts.assessable=counts.correct+counts.incorrect;return counts;
 }
 function pairs(data,cid){return data.pairs.map(pair=>{const a=cellFor(data,pair.baseline,cid),b=cellFor(data,pair.candidate,cid),valid=a?.status==='assessed'&&b?.status==='assessed'&&['correct','incorrect'].includes(a.verdict)&&['correct','incorrect'].includes(b.verdict);const delta=valid?Number(b.verdict==='correct')-Number(a.verdict==='correct'):null;return {...pair,delta,outcome:delta===null?'unassessable':delta>0?'improved':delta<0?'regressed':'unchanged'};});}
 function mapEvidence(trace,cell){
  if(!cell || ['missing','grader_error'].includes(cell.status))return {state:cell?.status || 'missing',steps:[],verdict:null};
  const available=new Set(trace.messages.map(m=>m.step_id)),steps=cell.evidence_steps || [];
  if(cell.trace_id!==trace.id || cell.trace_sha256!==trace.source_sha256 || steps.some(s=>!available.has(s)) || new Set(steps).size!==steps.length)return {state:'invalid_evidence',steps:[],verdict:null};
  return {state:cell.status,steps:[...steps],verdict:cell.verdict};
 }
 function decision(data){const r=data.decision.rule,selected=[r.primary,...r.regressions],all=selected.map(c=>pairs(data,c));if(data.decision.recorded_slots!==data.decision.planned_slots || all.some(p=>p.length!==r.expected_pairs || p.some(row=>row.delta===null)))return 'inconclusive_measurement';if(all.some(p=>p.some(row=>row.delta<0)))return 'do_not_adopt';return all[0].some(p=>p.delta>0)?'advance_to_larger_test':'no_observed_improvement';}
 const api={aggregate,pairs,mapEvidence,decision,cellFor};host.AILExperiment=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
 if(typeof document==='undefined')return;
 try{const theme=localStorage.getItem('ail-site-theme');if(['light','dark'].includes(theme))document.documentElement.dataset.theme=theme;}catch{}
 const data=host.AIL_EXPERIMENT_DATA,get=id=>document.getElementById(id),node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 if(!data){get('experiment-status').textContent='Saved experiment data missing';get('experiment-status').classList.remove('sr-only');return;}
 let stage='evaluation',criterion=data.decision.rule.primary,pairFilter='all',shown=[];
 const traceBy=id=>[...data.traces,...data.prior_traces].find(t=>t.id===id),definition=cid=>data.criteria.find(c=>c.id===cid),status=text=>{get('experiment-status').textContent=text;};
 const verdictLabel=c=>c?.status==='assessed'?({correct:'Pass',incorrect:'Miss',unassessable:'Unassessable'}[c.verdict]):({not_applicable:'Not applicable',missing:'Missing',grader_error:'Grader error'}[c?.status] || 'Missing');
 const button=(text,action,cls='text-button')=>{const e=node('button',text,cls);e.type='button';e.addEventListener('click',action);return e;};
 const goStage=name=>{location.hash=name;},openTrace=(id,cid=criterion)=>{location.hash='trace/'+id+'/'+cid;};
 function populate(select){for(const c of data.criteria){const o=node('option',c.definition.title);o.value=c.id;select.append(o);}select.value=criterion;}
 for(const id of ['evaluation-criterion','comparison-criterion','viewer-criterion'])populate(get(id));
 function metricBox(title,left,right){const e=node('div',undefined,'experiment-metric');e.append(node('span',title,'small-label'),node('strong',right===undefined?left:left+' → '+right));return e;}
 const ratio=(arm,cid)=>{const a=aggregate(data,arm,cid);return a.correct+'/'+a.assessable;};
 function renderEvaluation(){
  get('evaluation-metrics').replaceChildren(metricBox('Authored destination',ratio('baseline','closed_destination')),metricBox('Confirmed assignment',ratio('baseline','routed_terminal')));
  get('finding-links').replaceChildren();for(const t of data.traces.filter(t=>t.arm==='baseline'&&cellFor(data,t.id,'routed_terminal').verdict==='incorrect')){const pair=data.pairs.find(p=>p.baseline===t.id);get('finding-links').append(button(pair.title+' · repeat '+pair.repeat_id,()=>openTrace(t.id,'routed_terminal')));}
  get('evaluation-question').textContent=definition(criterion).definition.question;get('evaluation-criterion').value=criterion;
  get('evaluation-results').replaceChildren();
  for(const pair of data.pairs){const c=cellFor(data,pair.baseline,criterion),row=node('div',undefined,'experiment-result-row');row.append(node('span',pair.title),node('span','Repeat '+pair.repeat_id,'small-label'),node('span',verdictLabel(c),'experiment-verdict '+(c.verdict==='incorrect'?'miss':'')),button('Inspect ↗',()=>openTrace(pair.baseline)));get('evaluation-results').append(row);}
 }
 get('experiment-delta').textContent=data.prompt.delta.trim();get('experiment-baseline-prompt').textContent=data.prompt.baseline;
 get('experiment-observation').textContent=data.action.observed_summary;
 get('experiment-hypothesis').textContent='Unverified hypothesis: '+data.action.cause_hypothesis;
 get('experiment-verification').textContent=data.action.verification.success_criterion;
 for(const e of data.action_evidence)get('prior-evidence-links').append(button('Source case · '+traceBy(e.trace_id).family_id,()=>openTrace(e.trace_id)));
 get('experiment-model').textContent=data.provenance.model_attestation+' · attested, not independently verified by runner';
 function renderComparison(){
  get('comparison-metrics').replaceChildren(metricBox('Authored destination',ratio('baseline','closed_destination'),ratio('candidate','closed_destination')),metricBox('Confirmed assignment',ratio('baseline','routed_terminal'),ratio('candidate','routed_terminal')));
  const selected=pairs(data,criterion),count=name=>selected.filter(p=>p.outcome===name).length;
  get('comparison-outcomes').textContent=count('improved')+' improved · '+count('regressed')+' regressed · '+count('unchanged')+' unchanged — '+definition(criterion).definition.title;
  get('comparison-criterion').value=criterion;get('comparison-filter').value=pairFilter;
  get('comparison-pairs').replaceChildren();
  for(const p of selected.filter(p=>pairFilter==='all'||p.outcome===pairFilter)){
   const row=node('div',undefined,'experiment-pair-row'),identity=node('div');identity.append(node('strong',p.title),node('span','Repeat '+p.repeat_id,'small-label'));
   const left=cellFor(data,p.baseline,criterion),right=cellFor(data,p.candidate,criterion);
   row.append(identity,node('span',verdictLabel(left)+' → '+verdictLabel(right),'experiment-pair-verdict'),node('span',p.outcome[0].toUpperCase()+p.outcome.slice(1),'experiment-outcome '+p.outcome),button('Compare ↗',()=>{location.hash='pair/'+p.id+'/'+criterion;}));get('comparison-pairs').append(row);
  }
  if(!get('comparison-pairs').children.length)get('comparison-pairs').append(node('p','No pairs in this filter','detail-copy'));
  get('experiment-decision-rule').textContent='Complete paired measurement first. Reject if any pair regresses on the primary or selected regression checks. Observed decision: '+data.decision.decision+'. Defaults were unchanged';
 }
 const anchor=(trace,step)=>'experiment-'+trace.id+'-step-'+step;
 function rawDisclosure(title,text){const d=node('details',undefined,'catalog-payload');d.append(node('summary',title),node('pre',text));return d;}
 function renderTrace(trace,cell,priorIssue){
  const pane=node('section',undefined,'experiment-trace-pane'),heading=node('div',undefined,'experiment-trace-heading'),label=trace.arm==='prior'?'Prior source':trace.arm==='baseline'?'Baseline':'Candidate';
  heading.append(node('h2',label),node('span',priorIssue?'AI/code observation':verdictLabel(cell),'experiment-verdict '+(cell?.verdict==='incorrect'?'miss':'')));pane.append(heading);
  const mapped=priorIssue?{state:'source_observation',steps:priorIssue.evidence_steps,verdict:null}:mapEvidence(trace,cell),highlight=new Set(mapped.steps),failed=cell?.verdict==='incorrect'||Boolean(priorIssue),summary=node('p',priorIssue?.observation || cell?.explanation || 'No result for this criterion','experiment-check-explanation');pane.append(summary);
  if(mapped.state==='invalid_evidence')pane.append(node('p','Evidence link invalid; no steps highlighted','experiment-check-explanation'));
  pane.append(button('View evidence ↗',()=>reveal(trace,mapped.steps)));const flow=node('div',undefined,'catalog-messages');pane.append(flow);
  const marked=(box,message)=>{box.id=anchor(trace,message.step_id);box.tabIndex=-1;if(highlight.has(message.step_id)){box.classList.add(failed?'is-evidence':'is-check-evidence');}};
  const card=(message,text,role=message.role,assign=true)=>{const e=node('article',undefined,'catalog-message '+role);if(assign)marked(e,message);e.append(node('p',role==='user'?'User'+(message.role==='tool'?' · controlled reply':''):'Assistant','message-label'),node('p',text));return e;};
  for(const group of host.AILCatalog.trajectoryGroups(trace)){
   const m=group.messages[0];
   if(m.role==='system'){const d=node('details',undefined,'catalog-payload experiment-system');marked(d,m);d.append(node('summary','System instructions'),node('pre',m.content));flow.append(d);continue;}
   if(group.kind==='conversation'){flow.append(card(m,m.content));continue;}
   if(group.kind==='result'){const d=rawDisclosure('Tool result · '+m.function,m.content);marked(d,m);flow.append(d);continue;}
   const calls=m.tool_calls,ask=calls.length===1&&calls[0].name==='ask_user',responses=group.messages.slice(1);
   if(ask){const call=calls[0],reply=responses[0],payload=reply?JSON.parse(reply.content):null;flow.append(card(m,[m.content,call.arguments.question].filter(Boolean).join('\n\n'),'assistant'));if(reply)flow.append(card(reply,payload.answer,'user'));const d=node('details',undefined,'catalog-action experiment-ask-details');d.append(node('summary','Clarification details'),rawDisclosure('Arguments',JSON.stringify(call.arguments,null,2)));if(reply)d.append(rawDisclosure('Original tool response',reply.content));flow.append(d);continue;}
   const box=node('article',undefined,'catalog-action-group');marked(box,m);
   if(m.content)box.append(card(m,m.content,'assistant',false));
   for(const call of calls){const response=responses.find(r=>r.tool_call_id===call.id),display=host.AILCatalog.actionDisplay(call,response),d=node('details',undefined,'catalog-action'),s=node('summary');s.append(node('span','↳','catalog-action-symbol'),node('span',display.label,'catalog-action-label'),node('span',display.status,'catalog-action-status'));d.append(s,rawDisclosure('Arguments · '+call.name,JSON.stringify(call.arguments,null,2)));
    if(response){const result=node('div',undefined,'catalog-response');marked(result,response);if(highlight.has(response.step_id))box.classList.add(failed?'is-evidence':'is-check-evidence');result.append(rawDisclosure('Original response',response.content));d.append(result);}box.append(d);
   }flow.append(box);
  }
  const details=node('details',undefined,'disclosure experiment-score-details');details.append(node('summary','Check & source details'));
  if(cell){const c=definition(cell.criterion_id);details.append(node('p',c.definition.question,'detail-copy'),node('p',c.definition.rubric,'detail-copy'),node('p',c.grader.kind==='deterministic'?'Saved deterministic grader · '+c.grader.id:'Saved LLM judge','small-label'),rawDisclosure('Recorded result',JSON.stringify(cell,null,2)));}
  details.append(node('p','Source '+trace.source_id+' · '+trace.agent_version,'detail-copy'),rawDisclosure('Source trace identity',trace.source_sha256));pane.append(details);return pane;
 }
 function reveal(trace,steps){let first=null;for(const step of steps){const box=get(anchor(trace,step));if(!box)continue;first ||= box;for(let p=box.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;for(const d of box.querySelectorAll('details'))d.open=true;}if(first){first.focus({preventScroll:true});first.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}}
 function viewer(kind,id,cid){
  const pair=kind==='pair'?data.pairs.find(p=>p.id===id):null,trace=kind==='trace'?traceBy(id):null;if(!pair&&!trace)return false;if(pair)stage='comparison';else if(trace.arm==='prior')stage='change';
  if(cid&&definition(cid))criterion=cid;shown=pair?[traceBy(pair.baseline),traceBy(pair.candidate)]:[trace];
  get('experiment-viewer-title').textContent=pair?pair.title+' · repeat '+pair.repeat_id:trace.arm==='prior'?'Source evidence before the change':data.pairs.find(p=>[p.baseline,p.candidate].includes(trace.id)).title;
  get('viewer-criterion').parentElement.hidden=Boolean(trace?.arm==='prior');get('viewer-criterion').value=criterion;get('experiment-viewer-traces').classList.toggle('single',!pair);get('experiment-viewer-traces').replaceChildren();
  for(const t of shown)get('experiment-viewer-traces').append(renderTrace(t,cellFor(data,t.id,criterion),data.action_evidence.find(e=>e.trace_id===t.id)));
  const index=pair?data.pairs.indexOf(pair):-1;get('experiment-pair-prev').hidden=!pair;get('experiment-pair-next').hidden=!pair;get('experiment-pair-prev').disabled=index===0;get('experiment-pair-next').disabled=index===data.pairs.length-1;
  get('experiment-pair-prev').onclick=()=>{location.hash='pair/'+data.pairs[index-1].id+'/'+criterion;};get('experiment-pair-next').onclick=()=>{location.hash='pair/'+data.pairs[index+1].id+'/'+criterion;};return true;
 }
 function route(focus=true){const [name,id,cid]=location.hash.slice(1).split('/');const viewing=viewer(name,id,cid);get('experiment-overview').hidden=viewing;get('experiment-viewer').hidden=!viewing;if(viewing){if(focus)get('experiment-viewer-title').focus();return;}
  if(['evaluation','change','comparison'].includes(name))stage=name;get('experiment-evaluation').hidden=stage!=='evaluation';get('experiment-change').hidden=stage!=='change';get('experiment-comparison').hidden=stage!=='comparison';
  for(const b of document.querySelectorAll('.experiment-stage-nav button'))b.setAttribute('aria-current',b.dataset.stage===stage?'step':'false');renderEvaluation();renderComparison();status(stage+' · saved experiment');if(focus)get(stage==='evaluation'?'evaluation-title':stage==='change'?'change-title':'comparison-title').focus();
 }
 for(const b of document.querySelectorAll('[data-stage]'))b.addEventListener('click',()=>goStage(b.dataset.stage));
 for(const id of ['evaluation-criterion','comparison-criterion'])get(id).addEventListener('change',()=>{criterion=get(id).value;renderEvaluation();renderComparison();get(id).focus();});
 get('comparison-filter').addEventListener('change',()=>{pairFilter=get('comparison-filter').value;renderComparison();get('comparison-filter').focus();});
 get('viewer-criterion').addEventListener('change',()=>{criterion=get('viewer-criterion').value;const [kind,id]=location.hash.slice(1).split('/');history.replaceState(null,'','#'+kind+'/'+id+'/'+criterion);route(false);get('viewer-criterion').focus();});
 get('experiment-viewer-back').addEventListener('click',()=>goStage(stage));
 function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)+'\n'],{type:'application/json'})),link=node('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 get('experiment-export').addEventListener('click',()=>download(data,data.id+'.json'));
 get('experiment-viewer-export').addEventListener('click',()=>download({format:'saved-experiment-trace-view-v1',experiment_id:data.id,provenance:data.provenance,traces:shown,results:data.results.filter(c=>shown.some(t=>t.id===c.trace_id)),source_observations:data.action_evidence.filter(e=>shown.some(t=>t.id===e.trace_id)),new_model_calls:0,human_gold:false},data.id+'-traces.json'));
 get('experiment-about').addEventListener('click',()=>get('experiment-about-dialog').showModal());get('experiment-about-close').addEventListener('click',()=>get('experiment-about-dialog').close());
 get('experiment-theme').addEventListener('click',()=>{const r=document.documentElement,dark=r.dataset.theme?r.dataset.theme==='dark':matchMedia('(prefers-color-scheme:dark)').matches;r.dataset.theme=dark?'light':'dark';try{localStorage.setItem('ail-site-theme',r.dataset.theme);}catch{}});
 window.addEventListener('hashchange',()=>route());route(false);
})(typeof window!=='undefined'?window:globalThis);
