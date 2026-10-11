(async function () {
  'use strict';
  const ui = window.AILFamily, $ = id => document.getElementById(id), n = ui.node;
  let data; try { const r = await fetch('curated-experiment.json'); if (!r.ok) throw Error('Missing experiment'); data = await r.json(); if (data.format !== 'saved-family-experiment-web-v2') throw Error('Wrong format'); } catch { $('results-loading').textContent = 'Saved results could not be opened. Reload to try again.'; return; }
  let criterion = ui.primary(data), filter = 'all', query = '', page = 0, shown = [], activePair = null;
  const definitions = new Map(data.criteria.map(c => [c.id, c])), size = 18;
  for (const id of ['results-criterion', 'pair-criterion']) { for (const c of data.criteria) { const o = n('option', c.definition.title); o.value = c.id; $(id).append(o); } $(id).value = criterion; }
  $('results-evidence-content').append(ui.evidencePanel(data)); $('results-action').textContent = data.action.proposed_change; $('results-baseline-prompt').textContent = data.prompt.baseline; $('results-candidate-prompt').textContent = data.prompt.candidate; $('results-gate').textContent = 'Action gate reads: ' + data.prompt.gate_uses;
  const a = ui.counts(data, 'baseline'), b = ui.counts(data, 'candidate'), headline = n('div'), rate = n('div', undefined, 'result-rate'); rate.append(n('span', ui.percent(a.rate)), n('i', '↗'), n('strong', ui.percent(b.rate))); headline.append(rate, n('p', 'Routing success · ' + a.correct + ' → ' + b.correct + ' / ' + a.expected, 'result-label'));
  const transitions = n('div', undefined, 'cohort-transitions'), primaryRows = ui.pairs(data); for (const key of ['improved', 'regressed', 'unchanged', 'unknown']) transitions.append(n('span', primaryRows.filter(p => p.outcome === key).length + ' ' + key + (key === 'unknown' ? ' pairs' : ''), key)); $('results-metrics').append(headline, transitions);
  function list() {
    const all = ui.pairs(data, criterion), searched = all.filter(p => (filter === 'all' || p.outcome === filter) && (!query || [p.title, p.task_id, p.family_id].join(' ').toLowerCase().includes(query)));
    const pages = Math.max(1, Math.ceil(searched.length / size)); page = Math.min(page, pages - 1);
    $('results-criterion').value = criterion; $('results-pairs').replaceChildren();
    for (const p of searched.slice(page * size, (page + 1) * size)) { const row = n('div', undefined, 'result-row'), identity = n('div'), link = n('a', 'Inspect ↗'); identity.append(n('strong', p.title), n('span', p.task_id + ' · ' + p.family_id, 'small-label')); link.href = '#pair/' + encodeURIComponent(p.id) + '/' + criterion; row.append(identity, n('span', ui.label(p.left?.verdict || p.left?.status || 'missing') + ' → ' + ui.label(p.right?.verdict || p.right?.status || 'missing'), 'result-transition'), n('span', ui.label(p.outcome), 'result-outcome ' + p.outcome), link); $('results-pairs').append(row); }
    if (!searched.length) $('results-pairs').append(n('p', 'No pairs match these filters', 'catalog-empty'));
    $('results-totals').textContent = searched.length + ' of ' + all.length + ' pairs · ' + definitions.get(criterion).definition.title; $('results-page').textContent = 'Page ' + (page + 1) + ' / ' + pages; $('results-prev').disabled = page === 0; $('results-next').disabled = page === pages - 1;
  }
  function pane(trace, cell, prior) {
    const box = n('section', undefined, 'family-trace-pane'), grade = n('div', undefined, 'pair-grade'), label = prior ? 'Working-set source' : trace.arm === 'baseline' ? 'Baseline' : 'Candidate';
    grade.append(n('strong', label), n('span', prior ? 'Observation' : ui.label(cell?.verdict || cell?.status || 'missing'), cell?.verdict === 'incorrect' ? 'miss' : cell?.verdict === 'correct' ? 'pass' : 'unknown')); box.append(grade);
    if (cell) { if (cell.assessment_kind !== 'authored_fixture') box.append(n('p', cell.explanation, 'pair-explanation')); const evidence = n('button', 'View evidence ↗', 'text-button pair-evidence-link'); evidence.onclick = () => ui.revealEvidence(trace, cell); box.append(evidence); }
    if (prior) box.append(n('p', 'Observed failure evidence used before the candidate was frozen; excluded from heldout totals.', 'pair-explanation'));
    box.append(ui.renderTrace(trace, cell, {observation: prior}));
    const details = n('details', undefined, 'disclosure score-record'); details.append(n('summary', 'Criterion & source identity'));
    if (cell?.assessment_kind === 'authored_fixture') details.append(n('p', cell.explanation, 'detail-copy'));
    if (cell) details.append(n('p', definitions.get(cell.criterion_id).definition.rubric, 'detail-copy'), n('p', cell.assessment_kind === 'authored_fixture' ? 'Authored expected outcome + verified mechanical checks' : cell.assessment_kind === 'blind_ai_adjudication' ? 'Two fresh blind AI opinions; original dissent retained' : cell.criterion_id === ui.primary(data) ? 'Code checks + two recorded blind AI semantic opinions where required' : 'Saved deterministic check', 'small-label'), n('pre', JSON.stringify(cell, null, 2)));
    details.append(n('p', 'Source trace SHA-256', 'small-label'), n('pre', trace.source_sha256)); box.append(details); return box;
  }
  let legacyData;
  async function route(focus = false) {
    const [kind, encoded, cid] = location.hash.slice(1).split('/'); let id; try { id = decodeURIComponent(encoded || ''); } catch { id = ''; }
    if (definitions.has(cid)) criterion = cid;
    activePair = kind === 'pair' ? data.pairs.find(p => p.id === id) : null;
    const single = kind === 'trace' ? ui.traceFor(data, id) : null;
    if ((kind === 'pair' || kind === 'trace') && !activePair && !single) {
      const frozenHash = location.hash;
      try { if (!legacyData) { const response = await fetch('experiment-data.json'); if (response.ok) legacyData = await response.json(); } } catch {}
      if (location.hash !== frozenHash) return;
      const exists = legacyData?.format === 'saved-experiment-web-v1' && (kind === 'pair' ? legacyData.pairs.some(p => p.id === id) : [...legacyData.traces, ...legacyData.prior_traces].some(t => t.id === id));
      if (exists && location.hash === frozenHash) { location.replace('pilot.html' + frozenHash); return; }
      $('results-status').textContent = 'That trace or pair is unavailable; showing all saved results';
    }
    const viewing = Boolean(activePair || single); $('results-overview').hidden = viewing; $('results-viewer').hidden = !viewing;
    if (!viewing) { shown = []; list(); if (focus) $('results-title').focus({preventScroll: true}); return; }
    shown = activePair ? [ui.traceFor(data, activePair.baseline), ui.traceFor(data, activePair.candidate)] : [single];
    $('pair-title').textContent = activePair?.title || (single.arm === 'prior' ? 'Evidence before the change' : data.pairs.find(p => [p.baseline, p.candidate].includes(single.id))?.title || single.task_id);
    $('pair-criterion').value = criterion; $('pair-criterion').parentElement.hidden = single?.arm === 'prior'; $('pair-traces').classList.toggle('single', !activePair); $('pair-traces').replaceChildren();
    for (const trace of shown) $('pair-traces').append(pane(trace, ui.cellFor(data, trace.id, criterion), data.action_evidence.find(e => e.trace_id === trace.id)));
    $('pair-note').textContent = activePair ? activePair.task_id + ' · ' + activePair.family_id + ' · ' + definitions.get(criterion).definition.title : single.arm === 'prior' ? 'Working-set observation · not a heldout result' : single.task_id + ' · ' + single.family_id + ' · ' + single.arm + ' · ' + (single.source_kind === 'authored_fixture' ? 'authored demo trajectory' : 'recorded first attempt') + ' · ' + definitions.get(criterion).definition.title;
    const position = activePair ? data.pairs.indexOf(activePair) : -1; $('pair-prev').hidden = $('pair-next').hidden = !activePair; $('pair-prev').disabled = position <= 0; $('pair-next').disabled = position === data.pairs.length - 1;
    $('pair-prev').onclick = () => { location.hash = 'pair/' + encodeURIComponent(data.pairs[position - 1].id) + '/' + criterion; }; $('pair-next').onclick = () => { location.hash = 'pair/' + encodeURIComponent(data.pairs[position + 1].id) + '/' + criterion; };
    if (focus) $('pair-title').focus({preventScroll: true});
  }
  $('results-criterion').onchange = () => { criterion = $('results-criterion').value; page = 0; list(); };
  $('results-filter').onchange = () => { filter = $('results-filter').value; page = 0; list(); };
  $('results-query').oninput = () => { query = $('results-query').value.trim().toLowerCase(); page = 0; list(); };
  $('results-prev').onclick = () => { page--; list(); }; $('results-next').onclick = () => { page++; list(); };
  $('pair-criterion').onchange = () => { criterion = $('pair-criterion').value; history.replaceState(null, '', '#' + (activePair ? 'pair/' + encodeURIComponent(activePair.id) : 'trace/' + encodeURIComponent(shown[0].id)) + '/' + criterion); route(); $('pair-criterion').focus(); };
  $('pair-back').onclick = () => { location.hash = 'comparison'; };
  $('results-theme').onclick = ui.theme;
  $('results-about').onclick = () => { if (!$('results-viewer').hidden) { history.replaceState(null, '', '#comparison'); route(); } $('results-evidence').open = true; $('results-evidence').querySelector('summary').focus(); $('results-evidence').scrollIntoView({block: 'nearest', behavior: matchMedia('(prefers-reduced-motion:reduce)').matches ? 'auto' : 'smooth'}); };
  $('results-export').onclick = () => ui.download(data, data.id + '.json');
  $('pair-export').onclick = () => ui.download({format: 'recorded-family-trace-view-v1', experiment_id: data.id, provenance: data.provenance, traces: shown, results: data.results.filter(c => shown.some(t => t.id === c.trace_id)), source_observations: data.action_evidence.filter(e => shown.some(t => t.id === e.trace_id)), new_model_calls: 0, human_gold: false}, data.id + '-traces.json');
  window.addEventListener('hashchange', () => route(true)); $('results-loading').hidden = true; route();
})();
