(function (host) {
  'use strict';
  const caches = new WeakMap();
  function index(data) {
    if (!caches.has(data)) {
      const cells = new Map(data.results.map(c => [c.trace_id + '/' + c.criterion_id, c]));
      if (cells.size !== data.results.length) throw Error('Duplicate metric identity');
      caches.set(data, {cells, traces: new Map([...data.traces, ...data.prior_traces].map(t => [t.id, t]))});
    }
    return caches.get(data);
  }
  const primary = data => data.primary_criterion || data.action.verification.primary_measure;
  function familyUnits(data) { const count = new Set(data.pairs.map(p => p.family_id)).size; if (count !== data.statistics.independent_family_units) throw Error('Family unit mismatch'); return count; }
  const cellFor = (data, id, cid = primary(data)) => index(data).cells.get(id + '/' + cid);
  const traceFor = (data, id) => index(data).traces.get(id);
  function counts(data, arm, cid = primary(data)) {
    const total = {expected: data.pairs.length, correct: 0, incorrect: 0, unassessable: 0, missing: 0, grader_error: 0, not_applicable: 0};
    for (const pair of data.pairs) {
      const c = cellFor(data, pair[arm], cid);
      const key = c?.status === 'assessed' ? c.verdict : c?.status || 'missing';
      if (Object.hasOwn(total, key)) total[key]++; else throw Error('Unknown metric status');
    }
    total.rate = total.correct / total.expected;
    return total;
  }
  function pairs(data, cid = primary(data)) {
    return data.pairs.map(pair => {
      const a = cellFor(data, pair.baseline, cid), b = cellFor(data, pair.candidate, cid);
      const known = [a, b].every(c => c?.status === 'assessed' && ['correct', 'incorrect'].includes(c.verdict));
      const delta = known ? Number(b.verdict === 'correct') - Number(a.verdict === 'correct') : null;
      return {...pair, left: a, right: b, outcome: delta === null ? 'unknown' : delta > 0 ? 'improved' : delta < 0 ? 'regressed' : 'unchanged'};
    });
  }
  // A fixed task-only shuffle preserves tile positions across both versions.
  function cohortOrder(rows) {
    const ordered = [...rows].sort((a, b) => a.task_id < b.task_id ? -1 : a.task_id > b.task_id ? 1 : 0);
    let seed = 0xA11;
    const random = () => { seed = (seed + 0x6D2B79F5) | 0; let t = seed; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
    for (let i = ordered.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [ordered[i], ordered[j]] = [ordered[j], ordered[i]]; }
    for (const [left, right] of [['C3F038-v2', 'C3F001-v1'], ['C3F031-v2', 'C3F077-v2'], ['C3F022-v1', 'C3F058-v1']]) {
      const a = ordered.findIndex(p => p.task_id === left), b = ordered.findIndex(p => p.task_id === right);
      if (a >= 0 && b >= 0) [ordered[a], ordered[b]] = [ordered[b], ordered[a]];
    }
    return ordered;
  }
  function mapEvidence(trace, cell) {
    if (!cell || cell.status !== 'assessed') return {steps: [], state: cell?.status || 'missing'};
    const steps = cell.evidence_steps || [], known = new Set(trace.messages.map(m => m.step_id));
    if (cell.trace_id !== trace.id || cell.trace_sha256 !== trace.source_sha256 || new Set(steps).size !== steps.length || steps.some(s => !known.has(s))) return {steps: [], state: 'invalid_evidence'};
    return {steps: [...steps], state: 'assessed', verdict: cell.verdict};
  }
  const label = value => ({correct: 'Pass', incorrect: 'Miss', unassessable: 'Unknown', unknown: 'Unknown', improved: 'Improved', regressed: 'Regressed', unchanged: 'Unchanged'}[value] || value);
  const percent = value => (value * 100).toFixed(1) + '%';
  // Token-level emphasis preserves both original source lines exactly.
  function textDiff(before, after) {
    const tokens = text => text.match(/\s+|[\p{L}\p{N}_-]+|[^\s\p{L}\p{N}_-]/gu) || [];
    const a = tokens(before), b = tokens(after), table = Array.from({length: a.length + 1}, () => new Uint16Array(b.length + 1));
    for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    const result = {before: [], after: []};
    const push = (arm, text, kind) => { const previous = result[arm].at(-1); if (previous?.kind === kind) previous.text += text; else result[arm].push({text, kind}); };
    let i = 0, j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) { push('before', a[i++], 'context'); push('after', b[j++], 'context'); }
      else if (i < a.length && (j === b.length || table[i + 1][j] >= table[i][j + 1])) push('before', a[i++], 'removed');
      else push('after', b[j++], 'added');
    }
    return result;
  }
  // Playback is timer-injected so pause, tab changes and manual navigation cancel future steps.
  const durations = Object.freeze([4200, 5200, 4400, 6400, 4800]);
  function player(onChange, reduced = false, clock = host) {
    let scene = 0, playing = false, timer = null;
    const cancel = () => { if (timer !== null) clock.clearTimeout(timer); timer = null; };
    const schedule = () => { if (playing) timer = clock.setTimeout(() => { if (scene === 4) { cancel(); playing = false; onChange({scene, playing}); } else go(scene + 1, false); }, durations[scene]); };
    function go(next, manual = true) { cancel(); scene = Math.max(0, Math.min(4, next)); if (manual) playing = false; onChange({scene, playing}); schedule(); }
    return {go, pause: () => { cancel(); playing = false; onChange({scene, playing}); },
      play: () => { cancel(); if (scene === 4) scene = 0; playing = !reduced; onChange({scene, playing}); schedule(); },
      state: () => ({scene, playing}), destroy: cancel};
  }
  // Reviewed presentation anchors are separate from the grader's full evidence set.
  const storyCaseSpecs = [
    {task: 'C3F013-v3', title: 'No action', heading: 'A question that was never sent', why: 'Clarifications reach the user through ask_user. This trace ends with a typed question, without that call or an assignment.', after: 'The missing financial fact is requested through ask_user, then the ticket is assigned to Payments.', expected: 'Clarify → Payments', observed: 'No assignment', target: 'Payments',
      baseline: ['3765d13379e451a3ed329580f253fa6603fbdb170fdbdafa522467bed1d3bbdf', ['s006'], ['s001', 's005']], candidate: ['774c50acaae5c2953cd7ca8c79b9ee3d92daaadccc2a453a6f00962ce85516b8', ['s004', 's006'], ['s001', 's005']]},
    {task: 'C3F048-v2', title: 'Wrong route', heading: 'A malfunction sent to General', why: 'The ticket records a repeatable preview discrepancy. Working access and an active allowance support Technical.', after: 'The assistant checks the discrepancy and assigns the ticket to Technical.', expected: 'Technical', observed: 'General', target: 'Technical',
      baseline: ['de4576af8a783a23d747a1e08b8f5988da28e600981edf25f5f44b8e77c6fe0a', ['s004'], ['s001', 's003']], candidate: ['5ce3985a79f1eed3a889f9ff690e406db2c9a6a839571a4c06f18615eb311e76', ['s006'], ['s001', 's003', 's005']]},
    {task: 'C3F038-v1', title: 'Guessed cause', heading: 'Unknown permission treated as denial', why: 'An unknown answer does not establish an access problem. One clarification remains; unresolved facts should lead to an operator.', after: 'Two unknown replies remain unknown. The assistant hands the ticket to an operator.', expected: 'Clarify → Operator', observed: 'Accounts', target: 'Operator',
      baseline: ['31b394a12009e365fe7eabfccfe08971167de88f5338f791b790d4dc430ff71c', ['s006'], ['s001', 's005']], candidate: ['0a8f1e45ac4303cc98ca5c1c9d0802d4dd27f4af3f15d6672900e227e42f0083', ['s008'], ['s001', 's005', 's007']]},
    {task: 'C3F043-v1', title: 'Technical', heading: 'Correct technical routing', why: '48 orders become 480 in repeated exports, with working access and CSV allowance. The technical assignment is worth preserving.', after: 'The Technical route is preserved. The extra symptom question is still visible below.', expected: 'Technical', observed: 'Technical', target: 'Technical',
      baseline: ['c5f7cf62cd3833bd519b4d37fec7eab86a05a147739c97b534dd95df4c68310c', ['s004'], ['s001']], candidate: ['9a6f308b736acbdd025c1e70c1e5f61cb12d0f681c433beadd9a3be4167c3d3d', ['s006'], ['s001', 's005']]},
    {task: 'C3F025-v1', title: 'Accounts', heading: 'Correct role-based routing', why: 'The screen explicitly denies approval for the Viewer role. Accounts is the right destination.', after: 'The Accounts route is preserved. Additional permission and receipt checks are still visible below.', expected: 'Accounts', observed: 'Accounts', target: 'Accounts',
      baseline: ['5560b7fb58454a58c488ec0d24f5382925d71f435ed2a52e767fd40f8dea10b9', ['s004'], ['s001']], candidate: ['e46d24f56bb1d5c49e1a6307076e313c058f33062cc7bc60e510a9906d76e44c', ['s008'], ['s001', 's005', 's007']]}
  ];
  function storyCases(data) {
    return storyCaseSpecs.map((spec, i) => {
      const pair = data.pairs.find(p => p.task_id === spec.task);
      if (!pair) throw Error('Story example missing');
      const out = {...spec, kind: i < 3 ? 'fix' : 'keep', pair};
      for (const arm of ['baseline', 'candidate']) {
        const trace = traceFor(data, pair[arm]), cell = cellFor(data, pair[arm]), [sha, anchors, context] = spec[arm];
        const verdict = arm === 'baseline' && i < 3 ? 'incorrect' : 'correct';
        if (!trace || trace.task_id !== pair.task_id || trace.arm !== arm || trace.source_kind === 'authored_fixture' || trace.source_sha256 !== sha || cell?.verdict !== verdict || mapEvidence(trace, cell).state !== 'assessed') throw Error('Story example identity or outcome changed');
        out[arm] = {trace, cell, focus: {anchors: [...anchors], context: [...context], verdict}};
        traceFocus(trace, cell, out[arm].focus);
      }
      return out;
    });
  }
  function traceFocus(trace, cell, focus) {
    const evidence = mapEvidence(trace, cell), messages = new Map(trace.messages.map(m => [m.step_id, m]));
    if (evidence.state !== 'assessed' || evidence.verdict !== focus.verdict || !focus.anchors.length || focus.anchors.some(s => messages.get(s)?.role !== 'assistant' || !evidence.steps.includes(s)) || focus.context.some(s => !messages.has(s))) throw Error('Unbound presentation focus');
    return {anchors: new Set(focus.anchors), context: new Set(focus.context), verdict: focus.verdict};
  }
  function compactGroups(groups, options = {}) {
    const first = groups.find(g => g.messages[0].role === 'user'), asks = groups.filter(g => g.messages[0].tool_calls?.some(c => c.name === 'ask_user'));
    const actions = groups.filter(g => g.messages[0].tool_calls?.some(c => ['route_ticket', 'handoff_to_operator'].includes(c.name)));
    const selected = new Set([first, ...(options.allClarifications ? asks : [asks.at(-1)]), actions.at(-1)].filter(Boolean));
    const focused = new Set([...(options.focus?.anchors || []), ...(options.focus?.context || [])]);
    for (const group of groups) if (group.messages.some(m => focused.has(m.step_id))) selected.add(group);
    if (options.includeFinal) { const final = groups.at(-1); if (final?.kind === 'conversation' && final.messages[0].role === 'assistant') selected.add(final); }
    return groups.filter(g => selected.has(g));
  }
  function storyExport(data, cases) {
    return {...data, presentation: {format: 'reviewed-story-examples-v1', origin: 'reviewed_presentation_anchors_not_new_grader_results', scope: 'Five illustrative recorded routing pairs; not a separate evaluation sample', examples: cases.map(e => ({task_id: e.task, title: e.heading, kind: e.kind, why: e.why, after: e.after, baseline: e.baseline, candidate: e.candidate}))}};
  }
  const api = {index, primary, familyUnits, cellFor, traceFor, counts, pairs, cohortOrder, mapEvidence, label, percent, textDiff, player, storyCases, traceFocus, compactGroups, storyExport, sceneDuration: scene => durations[scene]};
  host.AILFamily = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;
  const node = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  api.node = node;
  api.download = (value, name) => { const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], {type: 'application/json'})), a = node('a'); a.href = url; a.download = name; a.click(); host.setTimeout(() => URL.revokeObjectURL(url), 1000); };
  api.theme = () => { const root = document.documentElement, dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme:dark)').matches; root.dataset.theme = dark ? 'light' : 'dark'; try { localStorage.setItem('ail-site-theme', root.dataset.theme); } catch {} };
  try { const theme = localStorage.getItem('ail-site-theme'); if (['light', 'dark'].includes(theme)) document.documentElement.dataset.theme = theme; } catch {}
  function disclosure(title, content) { const d = node('details', undefined, 'catalog-payload'); d.append(node('summary', title), node('pre', content)); return d; }
  api.renderTrace = (trace, cell, options = {}) => {
    const pane = node('section', undefined, 'family-trace-pane'), mapped = options.observation ? {steps: options.observation.evidence_steps, verdict: 'incorrect'} : mapEvidence(trace, cell), marked = new Set(mapped.steps), focus = options.focus ? traceFocus(trace, cell, options.focus) : null;
    const bind = (box, message) => { box.id = 'saved-' + trace.id + '-' + message.step_id; box.dataset.step = message.step_id; box.tabIndex = -1; if (focus) { if (focus.anchors.has(message.step_id)) box.classList.add(focus.verdict === 'incorrect' ? 'is-agent-error' : 'is-correct-action'); } else if (marked.has(message.step_id)) box.classList.add(mapped.verdict === 'incorrect' ? 'is-evidence' : 'is-check-evidence'); };
    const tag = message => focus ? focus.anchors.has(message.step_id) ? (focus.verdict === 'incorrect' ? 'Agent error' : 'Correct action') : '' : marked.has(message.step_id) ? 'Evidence' : '';
    const card = (message, text, role = message.role) => { const e = node('article', undefined, 'catalog-message ' + role); bind(e, message); const heading = node('p', (role === 'user' ? 'User' : 'Assistant') + (message.role === 'tool' ? ' · simulated reply' : ''), 'message-label'); const marker = tag(message); if (marker) heading.append(node('span', marker, 'catalog-issue-tag')); e.append(heading, node('p', text)); return e; };
    if (mapped.state === 'invalid_evidence') pane.append(node('p', 'Evidence identity mismatch; highlights withheld', 'detail-copy'));
    let groups = host.AILCatalog.trajectoryGroups(trace);
    if (options.compact) {
      groups = compactGroups(groups, options);
    }
    const flow = node('div', undefined, 'catalog-messages'); pane.append(flow);
    for (const group of groups) {
      const m = group.messages[0];
      if (m.role === 'system') { const d = disclosure('System instructions', m.content); bind(d, m); flow.append(d); continue; }
      if (group.kind === 'conversation') { flow.append(card(m, m.content)); continue; }
      if (group.kind === 'result') { const d = disclosure('Tool result · ' + (m.function || ''), m.content); bind(d, m); flow.append(d); continue; }
      const replies = group.messages.slice(1), calls = m.tool_calls || [], ask = calls.length === 1 && calls[0].name === 'ask_user';
      if (ask) {
        const call = calls[0], response = replies.find(r => r.tool_call_id === call.id);
        if (m.content?.trim() && !options.compact) flow.append(disclosure('Assistant text before the call', m.content));
        flow.append(card(m, call.arguments.question, 'assistant'));
        if (response) { let payload; try { payload = JSON.parse(response.content); } catch {} flow.append(card(response, typeof payload?.answer === 'string' ? payload.answer : response.content, 'user')); }
        if (!options.compact) { const d = disclosure('Clarification call · ' + call.arguments.field, JSON.stringify({arguments: call.arguments, original_response: response?.content}, null, 2)); flow.append(d); }
        continue;
      }
      const box = node('article', undefined, 'catalog-action-group'); bind(box, m);
      if (tag(m)) box.append(node('span', tag(m), 'action-focus-tag'));
      if (m.content?.trim() && !options.compact) box.append(disclosure('Assistant text before the action', m.content));
      for (const call of calls) {
        const response = replies.find(r => r.tool_call_id === call.id), display = host.AILCatalog.actionDisplay(call, response), d = node('details', undefined, 'catalog-action'), summary = node('summary');
        if (display.error) d.classList.add('has-error');
        summary.append(node('span', '↳', 'catalog-action-symbol'), node('span', display.label, 'catalog-action-label'), node('span', display.status, 'catalog-action-status')); d.append(summary, disclosure('Arguments · ' + call.name, JSON.stringify(call.arguments, null, 2)));
        if (response) { const result = node('div', undefined, 'catalog-response'); bind(result, response); if (!focus && marked.has(response.step_id)) box.classList.add(mapped.verdict === 'incorrect' ? 'is-evidence' : 'is-check-evidence'); result.append(disclosure('Original tool response', response.content)); d.append(result); }
        box.append(d);
      }
      flow.append(box);
      if (focus) for (const reply of replies) if (reply.function === 'get_ticket' && focus.context.has(reply.step_id)) {
        let payload; try { payload = JSON.parse(reply.content); } catch {}
        if (Array.isArray(payload?.history) && payload.history.every(s => typeof s === 'string')) { const context = node('article', undefined, 'catalog-message ticket-context'); context.dataset.step = reply.step_id; context.append(node('p', 'Ticket context', 'message-label'), node('p', payload.history.join('\n'))); flow.append(context); }
      }
    }
    return pane;
  };
  api.revealEvidence = (trace, cell) => { const mapped = mapEvidence(trace, cell); let first; for (const step of mapped.steps) { const e = document.getElementById('saved-' + trace.id + '-' + step); if (!e) continue; first ||= e; for (let p = e.parentElement; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true; } if (first) { first.focus({preventScroll: true}); first.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion:reduce)').matches ? 'auto' : 'smooth'}); } };
  api.evidencePanel = data => {
    const box = node('div'), facts = node('dl', undefined, 'facts'), s = data.statistics, ci = s.worst_completion_gain_interval;
    const fact = (title, text) => { const row = node('div'); row.append(node('dt', title), node('dd', text)); facts.append(row); };
    if (data.provenance.kind === 'illustrative_curated_replay') {
      fact('Dataset', data.pairs.length + ' paired synthetic tasks in ' + familyUnits(data) + ' families. Shared successful fixtures raise both versions; remaining failures and disagreements stay visible.');
      fact('Sources', data.provenance.recorded_traces + ' recorded model trajectories and ' + data.provenance.authored_traces + ' authored successful trajectories. Authored completions replace capture gaps and selected shared failures in this presentation dataset.');
      fact('Assessment', 'Five deterministic routing checks and source-bound semantic assessments. Eleven original disputed records received two fresh blind AI opinions; original dissent is retained. Authored successes use expected fixture outcomes, not observed model success or human gold.');
      fact('Interpretation', 'These rates describe a curated replay. They are not a new measured model improvement or a statistical confirmation. Recorded examples retain their original outputs and evidence.');
      fact('Tools', 'Mock acknowledgments confirm routing only. They do not prove that the customer issue was fixed. No model requests run on this website.');
      box.append(facts); const source = node('a', 'Source binding JSON ↗', 'text-button'); source.href = 'curated-transfer.json'; box.append(source); return box;
    }
    fact('Sample', data.pairs.length + ' paired task presentations, grouped into ' + familyUnits(data) + ' narrative families. Related presentations are correlated. All planned first attempts remain in the denominator.');
    fact('Mean effect', 'Least-favorable point gain ' + (s.gain_identification_bounds[0] * 100).toFixed(1) + ' pp; ' + (s.bootstrap.interval_level * 100).toFixed(3) + '% approximate family interval ' + (ci[0] * 100).toFixed(1) + '–' + (ci[1] * 100).toFixed(1) + ' pp. Conditional on authored-family exchangeability and the measurement contract.');
    fact('Decision', 'Positive conditional mean evidence. Full frozen advancement rule failed: family-direction p=' + s.worst_completion_direction_test.two_sided_p.toFixed(4) + ', threshold ' + data.decision.rule.worst_completion_sign_p_le + '. Candidate not adopted by default.');
    fact('Assessment', 'Six saved criteria: code checks and two independently executed blind AI semantic opinions where required. Disagreement stays unknown. Authored rule fixtures are not human gold or general judge validation.');
    fact('Captures', data.capture.complete + ' complete, ' + data.capture.partial + ' partial, ' + data.capture.missing + ' missing. Unknowns include capture gaps and semantic uncertainty.');
    fact('Scope', 'Real recorded model output, authored user facts and mock tools. Correct routing does not prove the underlying customer problem was fixed or backend state changed. No model calls run on this website.');
    fact('Intervention', 'Prompt replacement and observed-action gate were evaluated together; the gate reads current tool evidence, not reference answers. Candidate frozen before heldout results.');
    fact('Cost', 'Entire campaign, including earlier unsuccessful runs: ' + data.costs.spent_known_rub.toFixed(2) + ' ₽ known + ' + data.costs.retained_unknown_reserve_rub.toFixed(2) + ' ₽ unknown reserve. Reserve is not confirmed spend.');
    box.append(facts);
    const source = node('a', 'Source binding JSON ↗', 'text-button'); source.href = 'family-transfer.json'; box.append(source);
    return box;
  };
})(typeof window !== 'undefined' ? window : globalThis);
