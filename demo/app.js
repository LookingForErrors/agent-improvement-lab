/* Deterministic browser demo. Semantic decisions and candidate outputs are authored fixtures. */
(function (host) {
  'use strict';
  function citation(trace) {
    const final = [...trace.messages].reverse().find(m => m.role === 'assistant' && !m.tool_calls.length);
    if (!final || typeof final.content !== 'string' || !final.content.trim()) return {status:'not_applicable',verdict:null,evidence_steps:[]};
    const calls = new Set(trace.messages.flatMap(m => m.tool_calls).filter(c => c.name === 'read_doc').map(c => c.id));
    const seen = new Map(), malformed = [];
    for (const m of trace.messages.slice(0,trace.messages.indexOf(final))) {
      if (m.role !== 'tool' || !calls.has(m.tool_call_id)) continue;
      try {
        const p = typeof m.content === 'string' ? JSON.parse(m.content) : m.content;
        if (!p || Array.isArray(p) || typeof p !== 'object' || typeof p.ok !== 'boolean') throw Error('Malformed read');
        if (p.ok) {
          if (typeof p.doc_id !== 'string' || typeof p.text !== 'string') throw Error('Missing document');
          seen.set(p.doc_id,m.step_id);
        }
      } catch (_) { malformed.push(m.step_id); }
    }
    if (malformed.length) return {status:'assessed',verdict:'unassessable',evidence_steps:malformed};
    const cites = [...final.content.matchAll(/\[([A-Za-z0-9_-]+)\]/g)].map(x=>x[1]);
    if (!cites.length || cites.some(id=>!seen.has(id))) return {status:'assessed',verdict:'incorrect',evidence_steps:[final.step_id]};
    return {status:'assessed',verdict:'correct',evidence_steps:[...new Set([final.step_id,...cites.map(id=>seen.get(id))])]};
  }
  function evaluate(data,arm) {
    const rows=data.decisions.filter(d=>data.traces.some(t=>t.id===d.trace_id && t.arm===arm)).map(d=>({...d}));
    for (const trace of data.traces.filter(t=>t.arm===arm)) {
      const index=rows.findIndex(d=>d.trace_id===trace.id && d.criterion_id==='citation_sources');
      if (index<0) throw Error('Missing citation criterion');
      rows[index]={...rows[index],...citation(trace),origin:'computed_in_browser'};
    }
    return rows;
  }
  function counts(rows,criterion) {
    const items=rows.filter(d=>d.criterion_id===criterion),result={expected:items.length,correct:0,incorrect:0,unassessable:0,not_applicable:0,missing:0,grader_error:0};
    for (const d of items) {
      if (d.status==='assessed') result[d.verdict]++;
      else if (Object.hasOwn(result,d.status)) result[d.status]++;
      else throw Error('Unknown criterion status');
    }
    result.assessable=result.correct+result.incorrect;
    result.rate=result.assessable?result.correct/result.assessable:null;
    return result;
  }
  function compare(data,baseline,candidate,criterion='task_completion') {
    const pairs=[],groups=new Map();let improved=0,regressed=0,unchanged=0,excluded=0;
    const traceRows=data.traces.filter(t=>t.arm==='baseline');
    for (const trace of traceRows) {
      const other=data.traces.find(t=>t.arm==='candidate' && t.task_id===trace.task_id && t.family_id===trace.family_id && t.repeat_id===trace.repeat_id && t.split===trace.split);
      const a=baseline.find(d=>d.trace_id===trace.id && d.criterion_id===criterion),b=candidate.find(d=>d.trace_id===other?.id && d.criterion_id===criterion);
      if (!a || !b || a.status!=='assessed' || b.status!=='assessed' || !['correct','incorrect'].includes(a.verdict) || !['correct','incorrect'].includes(b.verdict)) { excluded++;continue; }
      const delta=Number(b.verdict==='correct')-Number(a.verdict==='correct');
      if (delta>0) improved++;else if (delta<0) regressed++;else unchanged++;
      const key=JSON.stringify([trace.family_id,trace.task_id]);
      if (!groups.has(key)) groups.set(key,{family_id:trace.family_id,task_id:trace.task_id,deltas:[]});
      groups.get(key).deltas.push(delta);
      pairs.push({family_id:trace.family_id,task_id:trace.task_id,repeat_id:trace.repeat_id,baseline:a.verdict,candidate:b.verdict,split:trace.split});
    }
    const families=new Map();
    for (const task of groups.values()) {
      if (!families.has(task.family_id)) families.set(task.family_id,[]);
      families.get(task.family_id).push(task.deltas.reduce((a,b)=>a+b,0)/task.deltas.length);
    }
    const familyMeans=[...families.values()].map(values=>values.reduce((a,b)=>a+b,0)/values.length);
    return {pairs,expected:traceRows.length,improved,regressed,unchanged,excluded,families:families.size,tasks:groups.size,delta:familyMeans.length?familyMeans.reduce((a,b)=>a+b,0)/familyMeans.length:null};
  }
  function referenceComparison(references) {
    const labels=['correct','incorrect','unassessable'],matrix=labels.map(()=>[0,0,0]);
    for (const r of references) {
      const a=labels.indexOf(r.reference),b=labels.indexOf(r.judge);
      if (a<0 || b<0) throw Error('Unknown reference verdict');
      matrix[a][b]++;
    }
    return {labels,matrix,matched:matrix.reduce((n,row,i)=>n+row[i],0),total:references.length};
  }
  function generate(seed) {
    if (!Number.isInteger(seed) || seed<0 || seed>4294967295) throw Error('Use a whole-number seed from 0 to 4294967295');
    let state=seed>>>0;const random=()=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return state/4294967296;};
    const families=[
      {id:'supported-export',requests:['Can I download NovaDocs v2 audit events as JSON, or is CSV the only option?','I need to send our NovaDocs v2 audit log to another system. Which export formats can I use?'],fact:'NovaDocs v2 audit export supports CSV and JSON.',expected:'Read the v2 export document and cite both supported formats.'},
      {id:'invented-retention',requests:['How long are audit events kept in NovaDocs v2? Please check the docs before I tell compliance.','We moved to NovaDocs v2. I need the documented retention period, not an estimate.'],fact:'NovaDocs v2 retains audit events for 30 days; extensions are not described.',expected:'Read the v2 audit document, answer 30 days and avoid inventing an extension.'},
      {id:'missed-tools',requests:['Please look up the NovaDocs v2 audit export formats and give me the source.','Before I plan an integration, could you verify the export formats in the current NovaDocs v2 documentation?'],fact:'NovaDocs v2 audit export supports CSV and JSON.',expected:'Execute search_docs and read_doc before the answer; include its citation.'},
      {id:'wrong-version',requests:['An old NovaDocs v1 page says seven days. What is the audit retention in v2?','I found a legacy seven-day limit. We use NovaDocs v2 now — can you check the current retention?'],fact:'NovaDocs v1: 7 days. NovaDocs v2: 30 days.',expected:'Search and read version v2; do not use the legacy page as evidence for v2.'}
    ];
    return Array.from({length:8},(_,i)=>{const family=families[i%4],wording=family.requests[Math.floor(random()*family.requests.length)];return {id:`generated-${seed}-${i+1}`,family_id:family.id,split:'working',language:'en',user_request:wording,context:i<4?'Short standalone request.':'Follow-up context: the team is preparing an internal audit; no request to change settings.',known_facts:family.fact,expected_behavior:family.expected,provenance:'authored_template_variant',generator_version:'novadocs-variants-v1',seed,source_family:family.id,assessment_status:'not_run',independent_new_family:false};});
  }
  function taskExport(seed,tasks) {
    if (!tasks.length || tasks.some(t=>t.seed!==seed || t.generator_version!=='novadocs-variants-v1')) throw Error('Generated task identity does not match the export seed');
    return {format:'synthetic-task-proposals-v1',seed,generator_version:'novadocs-variants-v1',model_calls:0,tasks};
  }
  const interventionTypes = ['prompt','context','tools','scaffold','retrieval','model','training_data','post_training','investigation'];
  function proposalSource(data) {
    return {data_format:data.format, dataset_version:data.dataset_version, evidence_kind:'authored_test_fixture', input_hashes:Object.fromEntries(Object.entries(data.input_hashes).sort(([a],[b]) => a.localeCompare(b)))};
  }
  function proposalFields(data, input, complete = true) {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw Error('Proposal fields are missing');
    const text = (key, limit, required) => {
      if (typeof input[key] !== 'string' || input[key].length > limit) throw Error('Invalid ' + key.replaceAll('_', ' '));
      const value = input[key].trim();
      if (complete && required && (!value || ['todo','tbd'].includes(value.toLowerCase()))) throw Error('Fill ' + key.replaceAll('_', ' '));
      return value;
    };
    if (!interventionTypes.includes(input.intervention)) throw Error('Choose a supported change type');
    const allowed = new Set(data.action.evidence.map(evidence => evidence.family_id));
    if (!Array.isArray(input.family_ids) || input.family_ids.some(id => typeof id !== 'string' || !allowed.has(id)) || new Set(input.family_ids).size !== input.family_ids.length) throw Error('Choose evidence from this example’s failure families');
    if (complete && !input.family_ids.length) throw Error('Choose at least one failure family');
    if (!data.criteria.some(criterion => criterion.id === input.primary_criterion)) throw Error('Choose a check from this criterion pack');
    if (complete && (!Number.isInteger(input.repeat_runs) || input.repeat_runs < 2 || input.repeat_runs > 20)) throw Error('Plan between 2 and 20 runs per task');
    if (!complete && input.repeat_runs !== null && (typeof input.repeat_runs !== 'number' || !Number.isFinite(input.repeat_runs))) throw Error('Invalid planned run count');
    return {family_ids:[...input.family_ids].sort(), intervention:input.intervention, proposed_change:text('proposed_change',1500,true), cause_hypothesis:text('cause_hypothesis',1000,false), success_criterion:text('success_criterion',1000,true), primary_criterion:input.primary_criterion, regression_check:text('regression_check',500,true), repeat_runs:input.repeat_runs};
  }
  function proposalDraft(data, input, identity) {
    const fields = proposalFields(data, input);
    if (!identity || typeof identity.id !== 'string' || !/^proposal-[a-z0-9-]{8,80}$/.test(identity.id) || !Number.isInteger(identity.revision) || identity.revision < 1 || typeof identity.created_at !== 'string' || typeof identity.updated_at !== 'string' || !Number.isFinite(Date.parse(identity.created_at)) || !Number.isFinite(Date.parse(identity.updated_at))) throw Error('Invalid proposal revision');
    const evidence = data.action.evidence.filter(item => fields.family_ids.includes(item.family_id)).map(item => {
      const trace = data.traces.find(trace => trace.id === item.trace_id);
      if (!trace || trace.arm !== 'baseline' || trace.split !== 'working' || trace.family_id !== item.family_id || !item.evidence_steps.length || item.evidence_steps.some(step => !trace.messages.some(message => message.step_id === step))) throw Error('Proposal evidence does not resolve to a working baseline trace');
      return {issue_id:item.id, trace_id:trace.id, task_id:trace.task_id, family_id:trace.family_id, split:trace.split, evidence_steps:[...item.evidence_steps], observation:item.observation, origin:'authored_test_fixture'};
    });
    const investigation = fields.intervention === 'investigation';
    const recipients = {prompt:'product',context:'product',tools:'backend',scaffold:'product',retrieval:'ml',model:'ml',training_data:'ml',post_training:'ml',investigation:'evaluation'};
    return {format:'custom-proposal-draft-v1', id:identity.id, revision:identity.revision, created_at:identity.created_at, updated_at:identity.updated_at, source:proposalSource(data), data_kind:'test_fixture', author_kind:'browser_user_input', status:'draft_pending_human_selection', execution_status:'not_run', measured_result:null, candidate_ref:null, human_validation:null, fields, intervention:fields.intervention, recipient_hint:recipients[fields.intervention], proposed_change:fields.proposed_change, cause_hypothesis:fields.cause_hypothesis || null, cause_status:fields.cause_hypothesis ? 'unverified_hypothesis' : 'unknown', evidence, support_counts:{traces:evidence.length,tasks:new Set(evidence.map(item => item.task_id)).size,families:new Set(evidence.map(item => item.family_id)).size, repeats:'identical authored copies, not independent observations'}, verification:{primary_criterion:fields.primary_criterion, success_criterion:fields.success_criterion, regression_checks:[fields.regression_check], protocol:investigation ? 'evidence_investigation' : 'working_selection_then_family_heldout_check', repeat_runs:investigation ? null : fields.repeat_runs, keep_fixed:['tasks','documentation','rubric','judge identity'], heldout_confirmation:'required separately before adopting an assistant change'}};
  }
  function proposalExport(data, draft) {
    if (!draft || draft.format !== 'custom-proposal-draft-v1' || JSON.stringify(draft.source) !== JSON.stringify(proposalSource(data))) throw Error('This draft belongs to another source version');
    if (draft.execution_status !== 'not_run' || draft.measured_result !== null || draft.candidate_ref !== null || draft.human_validation !== null || draft.status !== 'draft_pending_human_selection') throw Error('An unrun proposal cannot inherit results or validation');
    return proposalDraft(data, draft.fields, draft);
  }

  const api = {citation, evaluate, counts, compare, referenceComparison, generate, taskExport, proposalSource, proposalFields, proposalDraft, proposalExport};
  host.AILDemo = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  try { const theme = localStorage.getItem('ail-site-theme'); if (['light', 'dark'].includes(theme)) document.documentElement.dataset.theme = theme; } catch {}

  const get = id => document.getElementById(id);
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const data = host.AIL_DEMO_DATA;
  if (!data) {
    get('status').classList.remove('sr-only');
    get('status').textContent = 'Example data is missing. Build the static demo using its documented command';
    return;
  }
  const cases = [
    {id:'invented-retention', title:'Invented fact', subtitle:'Audit retention', description:'Current-version audit retention'},
    {id:'missed-tools', title:'Skipped lookup', subtitle:'Export formats', description:'The user asked for a lookup. Check whether one actually happened'},
    {id:'wrong-version', title:'Wrong version', subtitle:'v1 vs v2', description:'Check which product version the request and source refer to'},
    {id:'supported-export', title:'Supported answer', subtitle:'Audit exports', description:'A successful example belongs in the comparison too'}
  ];
  const storageKey = 'ail-workspace-v2-' + data.format + '-' + data.dataset_version;
  let phase = 0, stage = 0, seed = 17, family = cases[0].id, arm = 'baseline', repeat = '1';
  let tasks = [], baseline = [], candidate = [], highlightedSteps = [];
  const canVisit = index => index === 0 || (phase > 0 && index < 3) || (phase === 2 && index === 3);
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (saved && [0,1,2,3].includes(saved.phase)) {
      phase = saved.phase;
      if (Number.isInteger(saved.stage) && saved.stage >= 0 && saved.stage <= 3 && canVisit(saved.stage)) stage = saved.stage;
      else stage = phase === 2 ? 3 : phase === 3 ? 2 : phase;
      if (Number.isInteger(saved.seed) && saved.seed >= 0 && saved.seed <= 4294967295) seed = saved.seed;
      if (cases.some(item => item.id === saved.family)) family = saved.family;
      if (saved.arm === 'candidate' && phase === 2) arm = saved.arm;
      if (['1','2'].includes(String(saved.repeat))) repeat = String(saved.repeat);
    }
  } catch (_) {}
  const save = () => {
    try { localStorage.setItem(storageKey, JSON.stringify({phase, stage, seed, family, arm, repeat})); } catch (_) {}
  };
  const status = text => { get('status').textContent = text; };
  const motion = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  const traceFor = (selectedFamily = family, selectedArm = arm, selectedRepeat = repeat) => data.traces.find(trace => trace.family_id === selectedFamily && trace.arm === selectedArm && String(trace.repeat_id) === String(selectedRepeat));
  const finalAnswer = trace => [...trace.messages].reverse().find(message => message.role === 'assistant' && !message.tool_calls.length);
  const label = result => result?.status === 'assessed' ? ({correct:'Passed', incorrect:'Failed', unassessable:'Unassessable'}[result.verdict] || 'Unknown') : ({not_applicable:'Not applicable', missing:'Missing', grader_error:'Grader error'}[result?.status] || 'Not checked');
  const verdictClass = result => result?.status === 'assessed' ? (result.verdict === 'correct' ? 'good' : result.verdict === 'incorrect' ? 'bad' : '') : '';
  function verdict(result) { return node('span', label(result), 'verdict ' + verdictClass(result)); }
  function checkFor(trace, criterion) { return (trace.arm === 'candidate' ? candidate : baseline).find(row => row.trace_id === trace.id && row.criterion_id === criterion); }
  function table(headings, rows) {
    const element = node('table'), head = node('thead'), body = node('tbody'), heading = node('tr');
    headings.forEach(text => { const cell = node('th', text); cell.scope = 'col'; heading.append(cell); });
    head.append(heading); element.append(head);
    rows.forEach(values => { const row = node('tr'); values.forEach(value => row.append(node('td', value))); body.append(row); });
    element.append(body); return element;
  }
  function highlightedText(element, text) {
    element.replaceChildren();
    for (const part of String(text).split(/(\b\d+\s+days\b)/g)) element.append(/^\d+\s+days$/.test(part) ? node('mark', part) : document.createTextNode(part));
  }
  function renderCases() {
    get('case-list').replaceChildren();
    for (const item of cases) {
      const button = node('button', undefined, 'case-button');
      button.id = 'case-' + item.id;
      button.setAttribute('aria-current', String(item.id === family));
      const result = phase ? checkFor(traceFor(item.id, 'baseline', 1), 'task_completion') : null;
      const marker = node('span', undefined, 'case-marker ' + verdictClass(result));
      marker.setAttribute('aria-hidden', 'true');
      button.append(node('span', item.title, 'case-title'), marker, node('span', item.subtitle, 'case-subtitle'));
      if (result) button.setAttribute('aria-label', item.title + ', baseline ' + label(result).toLowerCase());
      button.addEventListener('click', () => {
        family = item.id; arm = 'baseline'; repeat = '1'; highlightedSteps = [];
        get('full-trace').open = false; save(); renderCases(); renderTrace();
        get('case-' + item.id).focus({preventScroll:true});
      });
      get('case-list').append(button);
    }
  }
  function renderTrace() {
    const trace = traceFor();
    if (!trace) return;
    get('trace-select').replaceChildren();
    for (const item of data.traces.filter(item => item.family_id === family && (item.arm === 'baseline' || phase === 2))) {
      const option = node('option', (item.arm === 'baseline' ? 'Baseline' : 'Supplied candidate') + ' · copy ' + item.repeat_id);
      option.value = item.id; get('trace-select').append(option);
    }
    get('trace-select').value = trace.id;
    get('trace-summary').textContent = trace.id + ' · authored trace';
    get('trace-length').textContent = trace.messages.length + ' steps';
    get('case-description').textContent = cases.find(item => item.id === family).description;
    const request = trace.messages.find(message => message.role === 'user');
    get('user-request').textContent = request?.content || 'No request recorded';
    highlightedText(get('assistant-answer'), finalAnswer(trace)?.content || 'No final answer recorded');
    get('answer-version').textContent = arm === 'baseline' ? 'Baseline' : 'Supplied candidate';
    const read = [...trace.messages].reverse().find(message => message.role === 'tool' && message.function === 'read_doc');
    let source = null;
    if (read) { try { source = typeof read.content === 'string' ? JSON.parse(read.content) : read.content; } catch (_) {} }
    if (source?.ok && typeof source.text === 'string') {
      highlightedText(get('source-text'), source.text);
      get('source-text').classList.remove('empty-evidence');
      get('source-version').textContent = source.doc_id || 'Document';
    } else {
      get('source-text').textContent = read ? 'No readable successful source recorded' : 'No documentation was read';
      get('source-text').classList.add('empty-evidence'); get('source-version').textContent = 'No source';
    }
    const result = phase ? checkFor(trace, 'task_completion') : null;
    get('case-outcome').hidden = !result;
    get('case-outcome').textContent = label(result);
    get('case-outcome').className = 'verdict ' + verdictClass(result);
    get('messages').replaceChildren();
    for (const message of trace.messages) {
      const expandable = message.role === 'system' || message.role === 'tool' || message.tool_calls.length > 0;
      const box = node(expandable ? 'details' : 'article', undefined, 'message ' + message.role + (highlightedSteps.includes(message.step_id) ? ' is-evidence' : ''));
      box.id = 'e-' + trace.id + '-' + message.step_id;
      const role = message.role === 'tool' ? 'Tool result' : message.tool_calls.length ? 'Assistant · tool call' : message.role === 'system' ? 'System rules' : message.role === 'user' ? 'User' : 'Assistant';
      box.append(node(expandable ? 'summary' : 'p', message.step_id + ' / ' + role + (message.function ? ' / ' + message.function : ''), 'message-label'));
      if (message.content) {
        let content = message.content;
        if (message.role === 'tool' && typeof content === 'string') { try { content = JSON.stringify(JSON.parse(content), null, 2); } catch (_) {} }
        box.append(node(message.role === 'tool' ? 'pre' : 'p', typeof content === 'string' ? content : JSON.stringify(content, null, 2)));
      }
      for (const call of message.tool_calls) box.append(node('pre', call.name + '(' + JSON.stringify(call.arguments, null, 2) + ')'));
      if (message.reasoning) { const reasoning = node('details'); reasoning.append(node('summary', 'Recorded reasoning'), node('p', message.reasoning)); box.append(reasoning); }
      get('messages').append(box);
    }
    if (stage === 1) renderDiagnosis(trace);
  }
  function inspect(id, steps) {
    const trace = data.traces.find(item => item.id === id);
    if (!trace || (trace.arm === 'candidate' && phase !== 2)) return;
    family = trace.family_id; arm = trace.arm; repeat = trace.repeat_id; highlightedSteps = steps || [];
    stage = phase ? 1 : 0; save(); render(); get('full-trace').open = true;
    const anchor = get('e-' + id + '-' + highlightedSteps[0]);
    if (anchor) {
      if (anchor.tagName === 'DETAILS') anchor.open = true;
      anchor.tabIndex = -1; anchor.focus({preventScroll:true}); anchor.scrollIntoView({behavior:motion(), block:'center'});
    } else {
      const summary = get('full-trace').querySelector('summary');
      summary.focus({preventScroll:true}); summary.scrollIntoView({behavior:motion(), block:'center'});
    }
  }
  function renderDiagnosis(trace) {
    const problem = checkFor(trace, 'unsupported_claims');
    get('diagnosis-summary').textContent = (problem?.explanation || 'Review the labels beside their evidence').replace(/^Authored (?:fixture only|criterion decision):\s*/i, '');
    get('key-checks').replaceChildren();
    for (const id of ['task_completion', 'unsupported_claims', 'citation_sources']) {
      const criterion = data.criteria.find(item => item.id === id), result = checkFor(trace, id);
      const item = node('details', undefined, 'check-item'), summary = node('summary', undefined, 'check-row');
      const description = node('span', undefined, 'check-description');
      description.append(node('span', id === 'task_completion' ? 'Task completion' : id === 'unsupported_claims' ? 'Claim grounding' : 'Citation linkage'), node('span', id === 'citation_sources' ? 'Checked here' : 'Authored label', 'check-origin'));
      summary.append(description, verdict(result)); item.append(summary);
      item.append(node('p', criterion.definition.question, 'detail-copy'), node('p', result?.explanation || 'No assessment recorded', 'detail-copy'));
      if (result?.evidence_steps?.length) {
        const evidence = node('button', 'View evidence · ' + result.evidence_steps.join(', '), 'text-button');
        evidence.addEventListener('click', () => inspect(trace.id, result.evidence_steps)); item.append(evidence);
      }
      get('key-checks').append(item);
    }
  }
  function renderMetrics() {
    const text = result => result ? (result.assessable ? result.correct + '/' + result.assessable + ' passed' : 'No assessable results') + ' · N/A ' + result.not_applicable + ' · unknown ' + result.unassessable : 'Not compared';
    get('metrics').replaceChildren(table(['Check', 'Baseline', 'Supplied candidate', 'Origin'], data.criteria.map(criterion => [criterion.definition.title, text(counts(baseline, criterion.id)), phase === 2 ? text(counts(candidate, criterion.id)) : 'Not compared', criterion.id === 'citation_sources' ? 'Checked here' : 'Authored label'])));
    const reference = referenceComparison(data.references);
    get('confusion').replaceChildren(table(['Reference / Judge', ...reference.labels], reference.matrix.map((row, index) => [reference.labels[index], ...row])));
    get('agreement').textContent = reference.matched + '/' + reference.total + ' agreement · shared authored origin';
  }
  function renderProposal() {
    get('action-change').textContent = data.action.proposed_change;
    get('action-observed').textContent = 'The baseline invents a fact, skips a lookup and uses a legacy page';
    get('action-cause').textContent = data.action.cause_hypothesis;
    get('action-verification').textContent = data.action.verification.success_criterion;
    get('action-support').textContent = data.action.support_counts.families + ' task families · ' + data.action.support_counts.traces + ' authored traces, including identical copies';
    get('action-evidence').replaceChildren();
    for (const item of cases.filter(item => data.action.evidence.some(evidence => evidence.family_id === item.id))) {
      const evidence = data.action.evidence.find(evidence => evidence.family_id === item.id && evidence.trace_id.endsWith('-1')) || data.action.evidence.find(evidence => evidence.family_id === item.id);
      const button = node('button', undefined, 'evidence-link');
      button.append(node('span', item.title), node('span', 'View evidence ↗', 'small-label'));
      button.addEventListener('click', () => inspect(evidence.trace_id, evidence.evidence_steps)); get('action-evidence').append(button);
    }
    get('decision-feedback').hidden = phase !== 3;
    get('decision-feedback').textContent = 'Proposal rejected. Candidate comparison and next-input generation are stopped';
    get('approve').hidden = phase === 3;
    get('approve').textContent = phase === 2 ? 'View comparison →' : 'Compare supplied candidate →';
    get('reject').hidden = phase === 3;
    get('reconsider').hidden = phase !== 3;
  }
  function renderComparison() {
    const comparison = compare(data, baseline, candidate);
    const groups = new Map();
    for (const pair of comparison.pairs) {
      const key = JSON.stringify([pair.family_id, pair.task_id]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(pair);
    }
    let before = 0, after = 0, repaired = 0, regressed = 0;
    for (const pairs of groups.values()) {
      const a = pairs.filter(pair => pair.baseline === 'correct').length / pairs.length;
      const b = pairs.filter(pair => pair.candidate === 'correct').length / pairs.length;
      before += a; after += b;
      if (b > a) repaired++; else if (b < a) regressed++;
    }
    const number = value => Number.isInteger(value) ? String(value) : value.toFixed(2);
    const total = node('div', undefined, 'comparison-total');
    total.append(node('strong', number(before) + '/' + groups.size + ' → ' + number(after) + '/' + groups.size), node('span', 'Task-completion score · authored labels'));
    const change = node('div', undefined, 'comparison-change');
    change.append(node('strong', repaired + ' repaired / ' + regressed + ' regressed'), node('span', 'By task · identical copies averaged'));
    get('comparison-summary').replaceChildren(total, change);
    get('family-comparison').replaceChildren();
    const columns = node('div', undefined, 'comparison-columns');
    ['Task', 'Baseline', 'Supplied candidate'].forEach(text => columns.append(node('span', text))); get('family-comparison').append(columns);
    for (const item of cases) {
      const row = node('div', undefined, 'family-row'), name = node('div', undefined, 'family-name');
      name.append(node('h3', item.title));
      const inspectButton = node('button', 'Inspect trace ↗');
      inspectButton.addEventListener('click', () => inspect(traceFor(item.id, 'baseline', 1).id, [])); name.append(inspectButton); row.append(name);
      for (const selectedArm of ['baseline', 'candidate']) {
        const trace = traceFor(item.id, selectedArm, 1), cell = node('div', undefined, 'family-answer');
        cell.dataset.label = selectedArm === 'baseline' ? 'Baseline' : 'Supplied candidate';
        cell.append(verdict(checkFor(trace, 'task_completion')), node('p', finalAnswer(trace)?.content || 'No final answer recorded')); row.append(cell);
      }
      get('family-comparison').append(row);
    }
    get('pairs').replaceChildren(table(['Family', 'Copy', 'Baseline label', 'Candidate label'], comparison.pairs.map(pair => [pair.family_id, pair.repeat_id, pair.baseline, pair.candidate])));
    get('pairs').append(node('p', comparison.expected + ' expected pairs · ' + comparison.excluded + ' excluded · ' + comparison.tasks + ' tasks · ' + comparison.families + ' families · ' + (comparison.delta === null ? 'N/A' : Math.round(comparison.delta * 100) + ' pp') + ' equal-family/task difference', 'detail-copy'));
  }
  function renderGenerated() {
    get('generated').replaceChildren();
    for (const task of tasks) {
      const box = node('details', undefined, 'task');
      box.append(node('summary', task.user_request), node('p', task.id + ' / ' + task.family_id + ' / unscored', 'task-label'), node('p', 'Known facts: ' + task.known_facts), node('p', 'Expected behavior: ' + task.expected_behavior));
      get('generated').append(box);
    }
    get('download-tasks').disabled = !tasks.length;
    get('generation-note').hidden = !tasks.length;
  }
  function render() {
    baseline = phase ? evaluate(data, 'baseline') : [];
    candidate = phase === 2 ? evaluate(data, 'candidate') : [];
    if (!canVisit(stage)) stage = phase === 2 ? 3 : phase === 3 ? 2 : phase;
    if (phase !== 2) arm = 'baseline';
    get('investigation').hidden = stage > 1;
    get('proposal').hidden = stage !== 2;
    get('comparison').hidden = stage !== 3;
    get('stage-label').textContent = stage === 0 ? '01 / Inspect' : '02 / Diagnose';
    get('investigation-title').textContent = stage === 0 ? 'Read the answer beside its source' : 'What failed, and what did not?';
    get('diagnosis').hidden = stage !== 1;
    get('all-checks').hidden = stage !== 1;
    get('run').hidden = stage !== 0 || phase > 0;
    get('to-diagnose').hidden = stage !== 0 || phase === 0;
    get('to-propose').hidden = stage !== 1;
    get('check-origin').hidden = stage !== 0 || phase > 0;
    document.querySelectorAll('[data-stage]').forEach(button => {
      const index = Number(button.dataset.stage); button.disabled = !canVisit(index);
      if (index === stage) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
    });
    renderCases(); renderTrace();
    if (phase) renderMetrics();
    if (stage === 2) { renderProposal(); renderCustomSummary(); }
    if (phase === 2) renderComparison();
    status(phase === 0 ? 'Inspect the request, answer and source, then check the example' : phase === 1 ? 'Baseline checked. Review the evidence and labels' : phase === 2 ? 'Supplied candidate compared. This is an authored fixture comparison' : 'Proposal rejected. Comparison and generation are stopped');
    get('seed').value = seed; get('seed').removeAttribute('aria-invalid');
    get('generation-status').textContent = tasks.length ? tasks.length + ' task proposals · unscored · seed ' + seed : '';
    renderGenerated();
  }
  function showStage(index) {
    if (!canVisit(index)) return;
    stage = index; save(); render();
    const heading = get(index < 2 ? 'investigation-title' : index === 2 ? 'proposal-heading' : 'comparison-heading');
    heading.focus({preventScroll:true}); window.scrollTo({top:0, behavior:motion()});
  }
  function download(value, name) {
    const blob = new Blob([JSON.stringify(value, null, 2) + '\n'], {type:'application/json'});
    const url = URL.createObjectURL(blob), link = node('a');
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    const utility = document.querySelector('.utilities');
    if (utility.open) { utility.open = false; utility.querySelector('summary').focus(); }
  }
  document.querySelectorAll('[data-stage]').forEach(button => button.addEventListener('click', () => showStage(Number(button.dataset.stage))));
  get('run').addEventListener('click', () => { phase = 1; tasks = []; status('Baseline checked. Review the evidence and labels'); showStage(1); });
  get('to-diagnose').addEventListener('click', () => showStage(1));
  get('to-propose').addEventListener('click', () => showStage(2));
  get('approve').addEventListener('click', () => {
    if (phase !== 1 && phase !== 2) return;
    phase = 2; status('Supplied candidate compared. This is an authored fixture comparison'); showStage(3);
  });
  get('reject').addEventListener('click', () => {
    if (phase !== 1 && phase !== 2) return;
    phase = 3; tasks = []; get('generation-status').textContent = ''; status('Proposal rejected. Comparison and generation are stopped'); showStage(2);
  });
  get('reconsider').addEventListener('click', () => { if (phase !== 3) return; phase = 1; status('Proposal reopened for a demo decision'); showStage(2); });
  get('reset').addEventListener('click', () => {
    phase = 0; stage = 0; seed = 17; family = cases[0].id; arm = 'baseline'; repeat = '1'; tasks = []; highlightedSteps = [];
    get('generation-status').textContent = ''; get('full-trace').open = false; get('all-checks').open = false;
    document.querySelector('.utilities').open = false; save(); render(); get('investigation-title').focus({preventScroll:true}); status('Example reset. Your personal proposal is preserved; no product changes have been applied');
  });
  get('trace-select').addEventListener('change', () => {
    const trace = data.traces.find(item => item.id === get('trace-select').value);
    if (!trace || (trace.arm === 'candidate' && phase !== 2)) return;
    family = trace.family_id; arm = trace.arm; repeat = trace.repeat_id; highlightedSteps = []; save(); renderCases(); renderTrace();
  });
  get('generate').addEventListener('click', () => {
    if (phase !== 2 || stage !== 3) return;
    try {
      if (!get('seed').value.trim()) throw Error('Enter a whole-number seed from 0 to 4294967295');
      const nextSeed = Number(get('seed').value), nextTasks = generate(nextSeed);
      seed = nextSeed; tasks = nextTasks; save(); renderGenerated(); get('seed').removeAttribute('aria-invalid');
      get('generation-status').textContent = '8 task proposals · unscored · seed ' + seed;
      status('Eight task inputs created. No assistant run or evaluation has occurred on them');
    } catch (error) { get('seed').setAttribute('aria-invalid', 'true'); get('generation-status').textContent = error.message; status(error.message); }
  });
  get('download').addEventListener('click', () => download(data, 'agent-improvement-example.json'));
  get('download-tasks').addEventListener('click', () => { if (phase === 2 && tasks.length) download(taskExport(seed, tasks), 'synthetic-tasks-' + seed + '.json'); });
  get('theme').addEventListener('click', () => {
    const root = document.documentElement, dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('ail-site-theme', root.dataset.theme); } catch {}
    const utility = document.querySelector('.utilities'); utility.open = false; utility.querySelector('summary').focus();
  });
  for (const id of ['about', 'provenance']) get(id).addEventListener('click', () => get('about-dialog').showModal());
  const customKey = 'ail-custom-proposal-v1-' + data.format + '-' + data.dataset_version;
  const emptyCustomFields = () => ({family_ids:[...new Set(data.action.evidence.map(item => item.family_id))].sort(),intervention:'prompt',proposed_change:'',cause_hypothesis:'',success_criterion:'',primary_criterion:'task_completion',regression_check:'Preserve the successful current-version audit export answer',repeat_runs:2});
  let customInput = emptyCustomFields(), customSaved = null, customStorageAvailable = true, customRestoreWarning = '';
  try {
    const saved = JSON.parse(localStorage.getItem(customKey) || 'null');
    if (saved) {
      if (saved.format !== 'custom-proposal-local-state-v1' || JSON.stringify(saved.source) !== JSON.stringify(proposalSource(data))) throw Error('Saved draft belongs to a different source. Exported copies remain separate; start a new draft here');
      let restoredInput = false;
      try { customInput = proposalFields(data, saved.input, false); restoredInput = true; }
      catch (_) { customRestoreWarning = 'Unfinished fields could not be restored'; }
      if (saved.saved) {
        try { customSaved = proposalExport(data, saved.saved); if (!restoredInput) customInput = customSaved.fields; }
        catch (_) { customRestoreWarning += (customRestoreWarning ? '. ' : '') + 'Saved proposal could not be restored for this source'; }
      }
    }
  } catch (error) { customRestoreWarning = 'Draft could not be restored: ' + error.message; }
  function persistCustom() {
    try {
      localStorage.setItem(customKey, JSON.stringify({format:'custom-proposal-local-state-v1',source:proposalSource(data),input:customInput,saved:customSaved}));
      customStorageAvailable = true; customRestoreWarning = ''; return true;
    } catch (_) {
      customStorageAvailable = false; return false;
    }
  }
  function readCustomForm() {
    return {family_ids:[...get('custom-families').querySelectorAll('input:checked')].map(input => input.value),intervention:get('custom-type').value,proposed_change:get('custom-change').value,cause_hypothesis:get('custom-hypothesis').value,success_criterion:get('custom-check').value,primary_criterion:get('custom-criterion').value,regression_check:get('custom-regression').value,repeat_runs:get('custom-repeats').value.trim() ? Number(get('custom-repeats').value) : null};
  }
  function renderCustomSummary() {
    get('custom-saved').hidden = !customSaved;
    get('custom-propose').hidden = Boolean(customSaved);
    const hasInput = customInput.proposed_change || customInput.cause_hypothesis || customInput.success_criterion;
    get('custom-propose').textContent = customSaved ? 'Edit your own proposal ↗' : hasInput ? 'Continue your draft ↗' : 'Propose your own change ↗';
    const changed = customSaved && JSON.stringify(customInput) !== JSON.stringify(customSaved.fields);
    get('custom-summary-status').textContent = customRestoreWarning || (!customStorageAvailable ? 'Session only: browser storage is unavailable. Export the saved proposal to keep it' : changed ? 'Unfinished edits are kept separately. Export uses the saved revision' : hasInput && !customSaved ? 'Unfinished draft kept in this browser' : '');
    if (!customSaved) return;
    get('custom-saved-change').textContent = customSaved.proposed_change;
    get('custom-saved-meta').textContent = (customStorageAvailable ? 'Saved in this browser' : 'Session only') + ' · revision ' + customSaved.revision + ' · ' + customSaved.support_counts.families + (customSaved.support_counts.families === 1 ? ' failure family · ' : ' failure families · ') + customSaved.intervention.replaceAll('_',' ');
    get('custom-saved-check').textContent = customSaved.verification.success_criterion;
    get('custom-saved-hypothesis').textContent = customSaved.cause_hypothesis || 'No cause proposed';
    get('custom-saved-evidence').replaceChildren();
    for (const selectedFamily of customSaved.fields.family_ids) {
      const item = cases.find(item => item.id === selectedFamily), evidence = customSaved.evidence.find(item => item.family_id === selectedFamily);
      const box = node('div', undefined, 'custom-saved-evidence'), button = node('button', item.title + ' · View evidence ↗', 'text-button');
      button.addEventListener('click', () => inspect(evidence.trace_id, evidence.evidence_steps));
      box.append(button,node('p',evidence.observation.replace(/^Authored fixture only:\s*/i,'')));get('custom-saved-evidence').append(box);
    }
  }
  function openCustomEditor() {
    get('custom-families').replaceChildren();
    for (const item of cases.filter(item => data.action.evidence.some(evidence => evidence.family_id === item.id))) {
      const label = node('label'), input = node('input'); input.type = 'checkbox'; input.value = item.id; input.name = 'custom-family'; input.checked = customInput.family_ids.includes(item.id);
      label.append(input,document.createTextNode(item.title));get('custom-families').append(label);
    }
    get('custom-criterion').replaceChildren();
    for (const criterion of data.criteria) { const option = node('option', criterion.definition.title); option.value = criterion.id; get('custom-criterion').append(option); }
    get('custom-type').value = customInput.intervention;
    get('custom-change').value = customInput.proposed_change; get('custom-hypothesis').value = customInput.cause_hypothesis;
    get('custom-check').value = customInput.success_criterion; get('custom-criterion').value = customInput.primary_criterion;
    get('custom-regression').value = customInput.regression_check; get('custom-repeats').value = customInput.repeat_runs ?? '';
    get('custom-editor-status').textContent = customRestoreWarning || (customStorageAvailable ? 'Incomplete fields are kept locally as you type' : 'Browser storage unavailable. This draft stays in the current session');
    get('custom-dialog').showModal();get('custom-change').focus();
  }
  function keepCustomInput() {
    customInput = readCustomForm();
    const stored = persistCustom();
    get('custom-editor-status').textContent = stored ? 'Draft fields kept in this browser · not yet checked for completeness' : 'Browser storage unavailable. Save and export to keep this session’s draft';
    renderCustomSummary();
  }
  get('custom-propose').addEventListener('click', openCustomEditor);
  get('custom-edit').addEventListener('click', openCustomEditor);
  get('custom-form').addEventListener('input', keepCustomInput);
  get('custom-form').addEventListener('submit', event => {
    event.preventDefault();
    try {
      const fields = proposalFields(data, readCustomForm()), now = new Date().toISOString();
      const identity = customSaved ? {...customSaved,revision:customSaved.revision+1,updated_at:now} : {id:'proposal-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10),revision:1,created_at:now,updated_at:now};
      customSaved = proposalDraft(data, fields, identity);customInput = fields;
      const stored = persistCustom();renderCustomSummary();get('custom-dialog').close();
      get('custom-export').focus();status(stored ? 'Your proposal saved locally. No experiment has run' : 'Proposal ready in this session. Export it; browser storage is unavailable');
    } catch (error) {
      if (/run count|runs per task|criterion pack|regression check/.test(error.message)) get('custom-form').querySelector('details').open = true;
      get('custom-editor-status').textContent = error.message;get('custom-editor-status').focus();
    }
  });
  get('custom-export').addEventListener('click', () => {
    try { if (customSaved) download(proposalExport(data, customSaved), customSaved.id+'-r'+customSaved.revision+'.json'); }
    catch (error) { get('custom-summary-status').textContent = error.message; }
  });
  get('custom-clear').addEventListener('click', () => {
    customInput = emptyCustomFields();customSaved = null;customRestoreWarning = '';
    try { localStorage.removeItem(customKey);customStorageAvailable = true; } catch (_) { customStorageAvailable = false;customRestoreWarning = 'Session draft cleared, but browser storage could not be cleared'; }
    get('custom-dialog').close();renderCustomSummary();get('custom-propose').focus();status('Personal draft cleared. Supplied example and its decision are unchanged');
  });
  get('custom-dialog').addEventListener('close', renderCustomSummary);

  get('bring-own').addEventListener('click', () => get('own-data-dialog').showModal());
  document.querySelectorAll('.dialog-close').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  render();
})(typeof window === 'undefined' ? globalThis : window);
