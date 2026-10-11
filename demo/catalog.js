(function(host) {
  'use strict';
  const variants = {success:'Scripted success',wrong_team:'Wrong team',wrong_ticket:'Wrong ticket',missed_tool:'Missing action',fabricated_result:'Unsupported claim',tool_error_ignored:'Tool error ignored'};
  const styles = {terse:'Short request',angry:'Frustrated user',correction:'User correction',topic_shift:'Topic change',overloaded:'Long context'};
  const destination = value => ({payments:'Payments',accounts:'Accounts',technical:'Technical',general:'General',operator:'Operator'}[value] || 'Not recorded');
  function filterCases(data, filters) {
    const query = String(filters.query || '').trim().toLocaleLowerCase();
    if (filters.variant && !Object.hasOwn(variants,filters.variant)) throw Error('Unknown trace variant');
    if (filters.style && !Object.hasOwn(styles,filters.style)) throw Error('Unknown conversation style');
    if (filters.clarifications && !['0','1','2'].includes(String(filters.clarifications))) throw Error('Unknown clarification filter');
    return data.cases.filter(item => {
      if (filters.style && item.style !== filters.style) return false;
      if (filters.clarifications !== '' && filters.clarifications !== undefined && String(item.clarifications) !== String(filters.clarifications)) return false;
      const traces=item.traces.filter(trace=>!filters.variant || trace.variant===filters.variant);
      const text=[item.title,item.active_request,item.opening_request,item.intended_destination,...traces.flatMap(trace=>trace.messages.map(message=>(message.content || '')+' '+JSON.stringify(message.tool_calls || [])))].join('\n').toLocaleLowerCase();
      return !query || text.includes(query);
    });
  }
  function summarize(cases, variant = '') {
    if (variant && !Object.hasOwn(variants,variant)) throw Error('Unknown trace variant');
    const traces = cases.flatMap(item=>item.traces.filter(trace=>!variant || trace.variant === variant));
    return {scenarios:cases.length,families:new Set(cases.map(item=>item.family_id)).size,traces:traces.length,variants:Object.fromEntries(Object.keys(variants).map(name=>[name,traces.filter(trace=>trace.variant === name).length]))};
  }
  function evidence(item, trace) {
    const calls = trace.messages.flatMap(message => (message.tool_calls || []).filter(call=>['route_ticket','handoff_to_operator'].includes(call.function)).map(call=>({message,call})));
    const action = calls.at(-1), response = action ? trace.messages.find(message=>message.tool_call_id === action.call.id) : null;
    let payload = {};
    if (response) { try { payload = JSON.parse(response.content); } catch (_) {} }
    const last = trace.messages.at(-1);
    const actual = action ? action.call.arguments.team || 'operator' : null;
    const ticket = action?.call.arguments.ticket_id || null;
    const confirmed=Boolean(action && payload.status==='OK' && payload.ticket_id===ticket && payload.assigned_to===actual);
    let statement = 'No routing or handoff call is recorded. The acknowledgement alone does not confirm assignment';
    let step = last?.step_id;
    if (action) {
      if (payload.status === 'ERROR') { statement = 'The tool reports an error, but the assistant still says the ticket was sent'; step = response.step_id; }
      else if (ticket !== item.ticket_id) { statement = 'The action targets '+ticket+'; this request belongs to '+item.ticket_id; step = action.message.step_id; }
      else if (actual !== item.intended_destination) { statement = 'The action routes to '+destination(actual)+'. The authored scenario expects '+destination(item.intended_destination); step = action.message.step_id; }
      else if (payload.status==='OK' && !confirmed) { statement='The tool result conflicts with the requested assignment. Inspect the recorded call and response';step=response.step_id; }
      else if (trace.variant === 'fabricated_result' && confirmed) { statement = 'Routing is confirmed. The final reply also claims an action that the recorded tools do not support'; step = last.step_id; }
      else if (confirmed) { statement = 'The tool confirms assignment of the requested ticket to the authored destination'; step = response.step_id; }
      else { statement = 'A routing call is recorded, but no readable OK result confirms it'; step = action.message.step_id; }
    }
    return {intended_destination:item.intended_destination,action_destination:actual,action_ticket:ticket,tool_status:payload.status || 'Not recorded',tool_confirmed_assignment:confirmed,statement,evidence_step:step,read_ticket:trace.messages.some(message=>message.role === 'tool' && message.function === 'get_ticket')};
  }
  function traceExport(data, item, trace) {
    if (!data.cases.some(row=>row === item) || !item.traces.some(row=>row === trace)) throw Error('Trace does not belong to this catalog');
    return {format:'scripted-catalog-trace-v1',dataset_version:data.dataset_version,provenance:data.provenance,scenario:{id:item.id,family_id:item.family_id,split:item.split,title:item.title,intended_destination:item.intended_destination,intent_origin:item.intent_origin},trace,observed_recorded_action:evidence(item,trace),model_calls:0,human_labels:0};
  }
  function responsePayload(response) {
    try {const value=JSON.parse(response?.content || '');return value && typeof value==='object' && !Array.isArray(value)?value:{};} catch (_) {return {};}
  }
  function trajectoryGroups(trace) {
    const groups=[];
    for(let index=0;index<trace.messages.length;index++) {
      const message=trace.messages[index],calls=message.tool_calls || [],messages=[message];
      if(message.role==='assistant' && calls.length) {
        // Pair by call identity only within the uninterrupted tool-response block.
        // A delayed or unrelated result keeps its own chronological entry.
        while(index+1<trace.messages.length) {
          const next=trace.messages[index+1];
          if(next.role!=='tool' || !calls.some(call=>call.id===next.tool_call_id))break;
          messages.push(next);index++;
        }
      }
      groups.push({kind:calls.length?'action':message.role==='tool'?'result':'conversation',messages});
    }
    return groups;
  }
  function actionDisplay(call,response) {
    call={...call,function:call.function || call.name};
    const payload=responsePayload(response),team=call.arguments.team || 'operator';
    const label=call.function==='get_ticket'?'Read ticket':call.function==='route_ticket'?'Route → '+destination(team):call.function==='ask_user'?'Ask user':'Hand off → Operator';
    const matched=response?.tool_call_id===call.id && response?.function===call.function;
    let status='Unconfirmed';
    if(matched && payload.status==='ERROR')status='Error'+(payload.code?' · '+payload.code:'');
    else if(matched && call.function==='get_ticket' && payload.ticket_id===call.arguments.ticket_id && Array.isArray(payload.history))status='Loaded';
    else if(matched && call.function==='ask_user' && payload.status==='OK' && payload.field===call.arguments.field && typeof payload.answer==='string')status='Answered';
    else if(matched && payload.status==='OK' && payload.ticket_id===call.arguments.ticket_id && payload.assigned_to===team)status='OK';
    return {label,status,error:status.startsWith('Error'),confirmed:['OK','Loaded','Answered'].includes(status)};
  }
  function traceSignal(item,trace) {
    const recorded=evidence(item,trace),last=trace.messages.at(-1),steps=[recorded.evidence_step];
    let label='Routed → '+destination(recorded.action_destination),issue=true,tag='Unconfirmed action';
    if(!recorded.action_destination){label='No routing action';tag='Missing action';}
    else if(recorded.tool_status==='ERROR'){label='Routing failed · assistant claims success';tag='Tool error';steps.push(last.step_id);}
    else if(recorded.action_ticket!==item.ticket_id){label='Wrong ticket · '+recorded.action_ticket;tag='Wrong ticket';}
    else if(recorded.action_destination!==item.intended_destination){label='Routed → '+destination(recorded.action_destination)+' · expected '+destination(item.intended_destination);tag='Wrong team';}
    else if(!recorded.tool_confirmed_assignment){label='Routing not confirmed';}
    else if(trace.variant==='fabricated_result'){label='Routing confirmed · extra claim unsupported';tag='Unsupported claim';}
    else {issue=false;tag='';if(recorded.action_destination==='operator')label='Handed off → Operator';}
    return {label,issue,tag,steps:steps.filter(Boolean),recorded};
  }
  const api = {variants,styles,filterCases,summarize,evidence,traceExport,trajectoryGroups,actionDisplay,traceSignal};
  host.AILCatalog = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined' || !document.getElementById('catalog-list-view')) return;
  try { const theme = localStorage.getItem('ail-site-theme'); if (['light', 'dark'].includes(theme)) document.documentElement.dataset.theme = theme; } catch {}
  const data = host.AIL_CATALOG_DATA, get = id => document.getElementById(id);
  const node = (tag,text,className) => { const element=document.createElement(tag);if(text !== undefined)element.textContent=text;if(className)element.className=className;return element; };
  if (!data) {get('catalog-status').classList.remove('sr-only');get('catalog-status').textContent='Catalog data is missing. Rebuild the browser assets';return;}
  const key = 'ail-catalog-v1-'+data.dataset_version+'-'+data.provenance.source_hashes.selection;
  let filters={query:'',variant:'success',style:'',clarifications:''}, page=0, current=null, currentTrace=null, lastCase=null, variantChangeFocus=false;
  const pageSize=10, status=text=>{get('catalog-status').textContent=text;};
  try {
    const saved=JSON.parse(sessionStorage.getItem(key) || 'null');
    if (saved && typeof saved.filters.query === 'string' && saved.filters.query.length<=200) {
      filterCases(data,saved.filters);filters={...filters,...saved.filters};if(Number.isInteger(saved.page)&&saved.page>=0)page=saved.page;
    }
  } catch (_) {}
  const persist=()=>{try{sessionStorage.setItem(key,JSON.stringify({filters,page}));}catch(_) {}};
  const populate=(select,labels)=>{for(const [value,label] of Object.entries(labels)){const option=node('option',label);option.value=value;select.append(option);}};
  populate(get('catalog-detail-variant'),variants);
  get('catalog-population').textContent=data.population.scenarios+' scenarios · '+data.population.traces+' scripted traces';
  get('catalog-version').textContent=data.dataset_version+' · authored synthetic data · no external sources';
  get('catalog-rules').textContent=data.rules;
  function renderList() {
    const matches=filterCases(data,filters), summary=summarize(matches,filters.variant), pages=Math.max(1,Math.ceil(matches.length/pageSize));
    page=Math.min(page,pages-1);persist();
    get('catalog-query').value=filters.query;get('catalog-variant').value=filters.variant;get('catalog-style').value=filters.style;get('catalog-clarifications').value=filters.clarifications;
    const count=summary.scenarios+' scenarios'+(filters.variant?'':' · '+summary.traces+' traces');
    get('catalog-count').textContent=count;status(count);
    get('catalog-rows').replaceChildren();
    for (const item of matches.slice(page*pageSize,(page+1)*pageSize)) {
      const row=node('tr'), description=node('td'), title=node('p',item.title,'catalog-case-title');description.append(title,node('p',item.active_request,'catalog-case-preview'));
      const conversation=node('td');conversation.append(node('span',styles[item.style]),node('span',item.clarifications ? item.clarifications+' clarification'+(item.clarifications===1?'':'s') : 'No clarification','catalog-row-note'));
      const context=node('td');context.append(node('span',item.history_chars>=1500 ? (item.history_chars/1000).toFixed(1)+'k characters' : item.history_chars+' characters'),node('span',item.history_records+' records','catalog-row-note'));
      const action=node('td'), open=node('a','Open trace ↗','catalog-open');open.href='#'+item.id+'/'+(filters.variant || 'success');open.id='catalog-open-'+item.id;open.setAttribute('aria-label','Open '+item.title);action.append(open);row.append(description,conversation,context,action);get('catalog-rows').append(row);
    }
    get('catalog-empty').hidden=matches.length>0;get('catalog-page').textContent='Page '+(page+1)+' of '+pages;get('catalog-prev').disabled=page===0;get('catalog-next').disabled=page===pages-1;
    const composition=summarize(matches);
    get('catalog-composition').replaceChildren();
    for(const [variant,label] of Object.entries(variants)) {const button=node('button',undefined,'variant-count');button.type='button';button.append(node('span',label),node('strong',String(composition.variants[variant])));button.addEventListener('click',()=>{filters.variant=variant;page=0;renderList();get('catalog-variant').focus();});get('catalog-composition').append(button);}
  }
  function disclosure(title,text) { const box=node('details',undefined,'catalog-payload');box.append(node('summary',title),node('pre',text));return box; }
  function messageAnchor(box,message) {box.id='catalog-step-'+message.step_id;box.tabIndex=-1;}
  function conversation(message,signal,anchor=true) {
    const flagged=signal.issue && signal.steps.includes(message.step_id),box=node('article',undefined,'catalog-message '+message.role+(flagged?' is-evidence':''));
    if(anchor)messageAnchor(box,message);
    const label=node('p',message.role==='user'?'User':'Assistant','message-label');
    if(flagged)label.append(node('span',signal.tag==='Tool error'?'Delivery unconfirmed':signal.tag,'catalog-issue-tag'));
    box.append(label,node('p',message.content));return box;
  }
  function responseDetails(message) {
    const box=node('div',undefined,'catalog-response'),payload=responsePayload(message);messageAnchor(box,message);
    if(message.function==='get_ticket' && Array.isArray(payload.history)) {
      const context=node('details',undefined,'catalog-payload catalog-context');context.append(node('summary','Ticket context'));
      for(const record of payload.history){const item=node('article',undefined,'catalog-history-record');item.append(node('p',record.record_id+' / '+record.kind+(record.closed?' / closed':''),'small-label'),node('p',record.text));context.append(item);}box.append(context);
    }
    box.append(disclosure('Original response',message.content));return box;
  }
  function renderMessages(trace,signal) {
    get('catalog-messages').replaceChildren();
    for(const group of trajectoryGroups(trace)) {
      const message=group.messages[0];
      if(group.kind==='conversation'){get('catalog-messages').append(conversation(message,signal));continue;}
      const flagged=signal.issue && group.messages.some(message=>signal.steps.includes(message.step_id)),box=node('article',undefined,'catalog-action-group'+(flagged?' is-evidence':''));
      if(group.kind==='result') {
        const payload=responsePayload(message),details=node('details',undefined,'catalog-action');
        details.append(node('summary',payload.status==='ERROR'?'Tool error · '+(payload.code || message.function):'Tool response · '+message.function));
        details.append(responseDetails(message));box.append(details);get('catalog-messages').append(box);continue;
      }
      messageAnchor(box,message);
      if(message.content)box.append(conversation(message,signal,false));
      for(const call of message.tool_calls) {
        const response=group.messages.find(message=>message.tool_call_id===call.id),display=actionDisplay(call,response),details=node('details',undefined,'catalog-action'+(display.error?' has-error':''));
        const summary=node('summary'),symbol=node('span','↳','catalog-action-symbol');symbol.setAttribute('aria-hidden','true');
        summary.append(symbol,node('span',display.label,'catalog-action-label'),node('span',display.status,'catalog-action-status'));
        details.append(summary);
        if(flagged)details.append(node('p',signal.label,'catalog-action-reason'));
        const args=node('div',undefined,'catalog-action-arguments');args.append(node('p',call.function+' · arguments','small-label'),node('pre',JSON.stringify(call.arguments,null,2)));details.append(args);
        if(response)details.append(responseDetails(response));
        else details.append(node('p','No linked result recorded','detail-copy'));
        box.append(details);
      }
      if(flagged)box.append(node('span',signal.tag,'catalog-action-issue'));
      get('catalog-messages').append(box);
    }
  }
  function renderTrace(item,trace) {
    current=item;currentTrace=trace;lastCase=item.id;
    get('catalog-trace-title').textContent=item.title;get('catalog-detail-variant').value=trace.variant;
    const signal=traceSignal(item,trace),recorded=signal.recorded;
    get('catalog-recorded-action').classList.toggle('has-issue',signal.issue);get('catalog-recorded-check').textContent=signal.label;get('catalog-evidence').dataset.step=recorded.evidence_step || '';
    get('catalog-source-facts').replaceChildren();
    for(const [label,value] of [['Origin','Authored scripted trace'],['Trace',trace.id],['Conversation',styles[item.style]],['Clarifications',String(item.clarifications)],['Expected route',destination(item.intended_destination)],['Recorded evidence',recorded.statement]]) {const fact=node('div');fact.append(node('dt',label),node('dd',value));get('catalog-source-facts').append(fact);}
    renderMessages(trace,signal);status(variants[trace.variant]+' · '+item.title);
  }
  function route(focus=true) {
    const [id,variant]=location.hash.slice(1).split('/'), item=data.cases.find(item=>item.id===id), trace=item?.traces.find(trace=>trace.variant===(variant || 'success'));
    get('catalog-list-view').hidden=Boolean(item&&trace);get('catalog-trace-view').hidden=!item||!trace;
    get('catalog-heading').hidden=Boolean(item&&trace);get('catalog-footer').hidden=Boolean(item&&trace);
    if(item&&trace){renderTrace(item,trace);if(focus)get(variantChangeFocus?'catalog-detail-variant':'catalog-trace-title').focus({preventScroll:variantChangeFocus});}
    else {current=null;currentTrace=null;renderList();if(id&&id!=='catalog')status('Unknown case or variant. Showing the catalog');if(focus&&lastCase&&id!=='catalog'){const link=get('catalog-open-'+lastCase);if(link){link.focus({preventScroll:true});link.scrollIntoView({block:'center'});}}}
    variantChangeFocus=false;
  }
  get('catalog-filters').addEventListener('submit',event=>event.preventDefault());
  for(const [id,field] of [['catalog-query','query'],['catalog-variant','variant'],['catalog-style','style'],['catalog-clarifications','clarifications']])get(id).addEventListener(id==='catalog-query'?'input':'change',()=>{filters[field]=get(id).value;page=0;renderList();});
  get('catalog-clear').addEventListener('click',()=>{filters={query:'',variant:'success',style:'',clarifications:''};page=0;renderList();get('catalog-query').focus();});
  get('catalog-prev').addEventListener('click',()=>{page--;renderList();get('catalog-page').focus();});get('catalog-next').addEventListener('click',()=>{page++;renderList();get('catalog-page').focus();});
  get('catalog-back').addEventListener('click',()=>{location.hash='';});
  get('catalog-detail-variant').addEventListener('change',()=>{if(current){variantChangeFocus=true;location.hash=current.id+'/'+get('catalog-detail-variant').value;}});
  get('catalog-evidence').addEventListener('click',()=>{const box=get('catalog-step-'+get('catalog-evidence').dataset.step);if(box){for(let parent=box.parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;for(const detail of box.querySelectorAll('details'))detail.open=true;box.focus({preventScroll:true});box.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}});
  get('catalog-export').addEventListener('click',()=>{if(!current||!currentTrace)return;const blob=new Blob([JSON.stringify(traceExport(data,current,currentTrace),null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download=currentTrace.id+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  get('catalog-theme').addEventListener('click',()=>{const root=document.documentElement,dark=root.dataset.theme?root.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;root.dataset.theme=dark?'light':'dark';try{localStorage.setItem('ail-site-theme',root.dataset.theme);}catch{}});
  get('catalog-about').addEventListener('click',()=>get('catalog-about-dialog').showModal());get('catalog-about-close').addEventListener('click',()=>get('catalog-about-dialog').close());
  window.addEventListener('hashchange',()=>route());route(false);
})(typeof window !== 'undefined' ? window : globalThis);
