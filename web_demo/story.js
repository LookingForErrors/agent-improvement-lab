(async function () {
  'use strict';
  const ui = window.AILFamily, $ = id => document.getElementById(id), n = ui.node;
  let data, caseData, cases, currentExample, playback, reduced = matchMedia('(prefers-reduced-motion:reduce)');
  try { const responses = await Promise.all(['curated-story.json', 'curated-experiment.json'].map(name => fetch(name))); if (responses.some(r => !r.ok)) throw Error('Saved story unavailable'); [data, caseData] = await Promise.all(responses.map(r => r.json())); if (data.format !== 'agent-improvement-story-v1') throw Error('Story format mismatch'); cases = ui.storyCases(caseData); }
  catch { $('story-loading').textContent = 'The saved story could not be opened. Reload to try again.'; return; }
  const motion = $('story-motion'), reduceNow = () => reduced.matches || motion.checked;
  const base = ui.counts(data, 'baseline'), candidate = ui.counts(data, 'candidate'), rows = ui.pairs(data), families = ui.familyUnits(data);
  const totals = Object.fromEntries(['improved', 'regressed', 'unchanged', 'unknown'].map(k => [k, rows.filter(p => p.outcome === k).length]));
  currentExample = cases[0];
  $('headline-before').textContent = ui.percent(base.rate); $('headline-after').textContent = ui.percent(candidate.rate);
  $('story-evidence-content').append(ui.evidencePanel(data));
  const link = (text, href, cls = 'source-link') => { const a = n('a', text, cls); a.href = href; return a; };
  function transitionLine() { const e = n('div', undefined, 'cohort-transitions'); for (const key of ['improved', 'regressed', 'unchanged', 'unknown']) e.append(n('span', totals[key] + ' ' + key + (key === 'unknown' ? ' pairs' : ''), key)); return e; }
  function outcomeMark(verdict) {
    const mark = n('span', verdict === 'correct' ? '✓' : verdict === 'incorrect' ? undefined : '?', 'outcome-mark' + (verdict === 'incorrect' ? ' is-cross' : ''));
    mark.setAttribute('aria-hidden', 'true');
    if (verdict === 'incorrect') {
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'), path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      svg.setAttribute('viewBox', '0 0 12 12'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
      path.setAttribute('d', 'M2.5 2.5L9.5 9.5M9.5 2.5L2.5 9.5'); path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'currentColor'); path.setAttribute('stroke-width', '1.25'); path.setAttribute('stroke-linecap', 'round');
      svg.append(path); mark.append(svg);
    }
    return mark;
  }
  let gridView, gridArm;
  function grid(arm) {
    gridArm = arm;
    if (gridView) {
      const update = () => { if (gridArm !== arm) return; for (const tile of gridView.querySelectorAll('.task-tile')) { const pair = data.pairs.find(p => p.id === tile.dataset.pair), cell = ui.cellFor(data, pair[arm]), verdict = cell.status === 'assessed' ? cell.verdict : cell.status; tile.className = 'task-tile ' + verdict; tile.replaceChildren(outcomeMark(verdict)); tile.title = pair.title + ' · ' + ui.label(verdict); tile.setAttribute('aria-label', pair.title + ', ' + pair.task_id + ', ' + arm + ' ' + ui.label(verdict) + '; inspect pair'); } };
      if (reduceNow()) update(); else requestAnimationFrame(() => requestAnimationFrame(update));
      const old = gridView.querySelector('.cohort-legend'); old.replaceWith(legend(arm)); return gridView;
    }
    const wrapper = n('div', undefined, 'cohort-visual'), grid = n('div', undefined, 'task-grid'); grid.setAttribute('aria-label', data.pairs.length + ' task outcomes; arrow keys move between tasks');
    const ordered = ui.cohortOrder(data.pairs);
    for (const [i, p] of ordered.entries()) {
      const c = ui.cellFor(data, p[arm]), verdict = c.status === 'assessed' ? c.verdict : c.status;
      const tile = link('', 'experiment.html#pair/' + encodeURIComponent(p.id) + '/' + ui.primary(data), 'task-tile ' + verdict); tile.append(outcomeMark(verdict));
      tile.dataset.pair = p.id; tile.tabIndex = i === 0 ? 0 : -1; tile.title = p.title + ' · ' + ui.label(verdict); tile.setAttribute('aria-label', p.title + ', ' + p.task_id + ', ' + arm + ' ' + ui.label(verdict) + '; inspect pair'); tile.style.setProperty('--delay', Math.min(i % 9 * 20, 160) + 'ms'); grid.append(tile);
    }
    grid.addEventListener('keydown', event => { const step = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: 28, ArrowUp: -28}[event.key]; if (!step) return; event.preventDefault(); const items = [...grid.children], i = items.indexOf(document.activeElement), next = Math.max(0, Math.min(items.length - 1, i + step)); items.forEach((e, index) => e.tabIndex = index === next ? 0 : -1); items[next].focus(); });
    wrapper.append(grid, legend(arm)); gridView = wrapper; return wrapper;
  }
  function legend(arm) {
    const row = n('div', undefined, 'cohort-legend'), count = arm === 'baseline' ? base : candidate;
    for (const key of ['correct', 'incorrect', 'unassessable']) { const e = n('span'), mark = n('span', undefined, 'legend-mark ' + key); mark.setAttribute('aria-hidden', 'true'); mark.append(outcomeMark(key)); e.append(mark, n('span', count[key] + ' ' + (key === 'correct' ? 'pass' : key === 'incorrect' ? 'miss' : 'unknown'))); row.append(e); } return row;
  }
  function cohort(arm) {
    const box = n('div'), layout = n('div', undefined, 'cohort-layout'), summary = n('div', undefined, 'cohort-summary'), count = arm === 'baseline' ? base : candidate;
    summary.append(n('strong', String(count.correct), 'big-count'), n('p', 'successful routes'), n('p', 'out of ' + count.expected + ' paired tasks', 'cohort-division')); layout.append(summary, grid(arm)); box.append(layout);
    if (arm === 'candidate') { box.append(transitionLine()); const decision = n('p', 'Same routing checks across both versions', 'scene-decision'); decision.append(link('Explore the results ↗', 'experiment.html#comparison', '')); box.append(decision); }
    return box;
  }
  function caseBar(scene) {
    const bar = n('div', undefined, 'story-case-bar'), buttons = n('div', undefined, 'story-examples'); buttons.setAttribute('role', 'group'); buttons.setAttribute('aria-label', 'Examples');
    for (const [i, e] of cases.entries()) {
      const button = n('button', undefined, 'example-button'), outcome = scene === 'verify' ? e.kind === 'fix' ? 'Miss → Pass' : 'Pass → Pass' : e.kind === 'fix' ? 'Miss' : 'Pass';
      button.type = 'button'; button.id = 'story-example-' + e.task; button.dataset.example = e.task; button.setAttribute('aria-pressed', String(e.task === currentExample.task)); button.setAttribute('aria-label', 'Example ' + (i + 1) + ': ' + e.title + ', ' + outcome);
      button.append(n('span', String(i + 1).padStart(2, '0'), 'example-number'), n('span', e.title, 'example-title'), n('span', outcome, 'example-outcome'));
      button.onclick = () => { playback.pause(); if (currentExample.task === e.task) return; currentExample = e; render(playback.state(), true); $(button.id).focus({preventScroll: true}); };
      buttons.append(button);
    }
    bar.append(buttons);
    if (scene === 'verify') { const regression = data.examples.find(e => e.selection_kind === 'regressed'); bar.append(link('View a regression ↗', 'experiment.html#pair/' + encodeURIComponent(regression.task_id) + '/' + ui.primary(data), '')); }
    else bar.append(n('span', currentExample.kind === 'fix' ? 'Needs change' : 'Keep this route', 'example-purpose ' + currentExample.kind));
    return bar;
  }
  function focusedTrace(arm) { const item = currentExample[arm]; return ui.renderTrace(item.trace, item.cell, {compact: true, allClarifications: true, includeFinal: true, focus: item.focus}); }
  function find() {
    const box = n('div'), layout = n('div', undefined, 'find-layout'), flow = n('div', undefined, 'story-trace'), note = n('aside', undefined, 'finding-note');
    box.append(caseBar('find')); flow.append(focusedTrace('baseline'));
    note.append(n('h3', currentExample.heading), n('p', currentExample.why));
    const outcome = n('dl', undefined, 'finding-outcome' + (currentExample.kind === 'keep' ? ' is-pass' : ''));
    for (const [label, value] of [['Expected', currentExample.expected], ['Observed', currentExample.observed]]) { const row = n('div'); row.append(n('dt', label), n('dd', value)); outcome.append(row); }
    const compare = n('button', 'Compare this example →', 'text-button compare-example'); compare.onclick = () => manual(3);
    note.append(outcome, compare, link('Full trace ↗', 'experiment.html#trace/' + encodeURIComponent(currentExample.baseline.trace.id))); layout.append(flow, note); box.append(layout); return box;
  }
  function change() {
    const layout = n('div', undefined, 'change-layout'), card = n('article', undefined, 'change-card'); card.append(n('h3', 'Make the routing evidence explicit.'));
    const before = data.prompt.baseline.split('\n').find(line => /^technical:/.test(line)), after = data.prompt.candidate.split('\n').find(line => /^- technical:/.test(line)), diff = ui.textDiff(before, after);
    const comparison = n('section', undefined, 'prompt-diff'); comparison.setAttribute('aria-label', 'Technical routing prompt excerpt: removed baseline line and added candidate line');
    comparison.append(n('div', 'Prompt · technical routing', 'diff-caption'));
    for (const [arm, label, sign] of [['before', 'Removed', '−'], ['after', 'Added', '+']]) {
      const row = n('div', undefined, 'diff-line ' + arm), marker = n('span', sign, 'diff-sign'), code = n('code'); marker.setAttribute('aria-hidden', 'true'); row.setAttribute('aria-label', label);
      for (const part of diff[arm]) code.append(n('span', part.text, 'diff-' + part.kind)); row.append(marker, code); comparison.append(row);
    }
    card.append(comparison);
    const gate = n('div', undefined, 'gate-change'), gateState = n('div', undefined, 'gate-state'); gateState.append(n('span', data.prompt.baseline_gate ? 'On' : 'Off', 'gate-before'), n('span', '→', 'gate-arrow'), n('strong', data.prompt.candidate_gate ? 'On' : 'Off'));
    gate.append(n('span', 'Tool-state guard', 'gate-label'), gateState, n('span', 'Added', 'added-tag')); card.append(gate);
    const details = n('details', undefined, 'disclosure'); details.append(n('summary', 'View the exact change')); const prompts = n('div', undefined, 'prompt-pair'); for (const arm of ['baseline', 'candidate']) { const d = n('details'); d.append(n('summary', arm === 'baseline' ? 'Baseline prompt' : 'Candidate prompt'), n('pre', data.prompt[arm])); prompts.append(d); } details.append(n('p', data.action.proposed_change, 'detail-copy'), prompts, n('p', 'Tool-state guard: ' + data.prompt.gate_uses, 'detail-copy'), n('p', 'Cause hypothesis · unverified: ' + data.action.cause_hypothesis, 'detail-copy')); card.append(details); layout.append(card); return layout;
  }
  function verify() {
    const box = n('div'); box.append(caseBar('verify'), n('p', currentExample.after, 'verify-result-note'));
    const panes = n('div', undefined, 'verify-traces'); for (const arm of ['baseline', 'candidate']) { const {cell} = currentExample[arm], pane = n('section', undefined, 'verify-pane story-trace'), heading = n('div', undefined, 'verify-pane-heading'); heading.append(n('strong', arm === 'baseline' ? 'Before' : 'After'), n('span', ui.label(cell.verdict), cell.verdict === 'incorrect' ? 'miss' : 'pass')); pane.append(heading, focusedTrace(arm)); panes.append(pane); } box.append(panes);
    const foot = n('div', undefined, 'verify-footer'); foot.append(n('span', 'Same recorded task · routing outcome'), link('Full traces & evidence ↗', 'experiment.html#pair/' + encodeURIComponent(currentExample.task) + '/' + ui.primary(data), '')); box.append(foot); return box;
  }
  let previous = -1;
  function render(state, force = false) {
    const titles = ['Before the change', 'Find what needs to change', 'Change the decision process', 'Verify the same examples', 'Measure the whole set'];
    if (state.scene !== previous || force) {
      $('scene-content').replaceChildren(state.scene === 0 ? cohort('baseline') : state.scene === 1 ? find() : state.scene === 2 ? change() : state.scene === 3 ? verify() : cohort('candidate'));
      $('scene-content').classList.remove('entering'); void $('scene-content').offsetWidth; $('scene-content').classList.add('entering');
      $('scene-title').textContent = titles[state.scene]; $('scene-number').textContent = '0' + (state.scene + 1); $('scene-meta').textContent = state.scene === 0 || state.scene === 4 ? data.pairs.length + ' tasks · ' + families + ' families' : state.scene === 1 ? '3 misses · 2 correct routes' : state.scene === 2 ? 'Prompt + scaffold' : 'Same 5 recorded pairs';
      previous = state.scene;
    }
    $('story-status').textContent = (state.playing ? 'Playing' : 'Scene') + ' ' + (state.scene + 1) + ' of 5: ' + titles[state.scene] + ([1, 3].includes(state.scene) ? '. Example ' + (cases.indexOf(currentExample) + 1) + ' of 5: ' + currentExample.heading : '');
    const board = document.querySelector('.story-board'), progress = board.querySelector('.story-progress'); board.dataset.playing = String(state.playing); board.dataset.scene = state.scene; board.style.setProperty('--scene-duration', ui.sceneDuration(state.scene) + 'ms');
    progress.classList.remove('is-running'); if (state.playing) { void progress.offsetWidth; progress.classList.add('is-running'); }
    for (const b of document.querySelectorAll('button[data-scene]')) b.setAttribute('aria-current', Number(b.dataset.scene) === state.scene ? 'step' : 'false');
    $('story-prev').disabled = state.scene === 0; $('story-next').disabled = state.scene === 4;
    $('story-start').setAttribute('aria-label', state.playing ? 'Pause story' : state.scene === 4 ? 'Replay story' : reduceNow() ? 'Next scene' : 'Play story');
    $('story-start').replaceChildren(n('span', state.playing ? 'Playing · Pause' : state.scene === 4 ? 'Replay' : state.scene === 0 ? 'See how' : reduceNow() ? 'Next scene' : 'Continue'), n('span', state.playing ? 'Ⅱ' : state.scene === 4 ? '↻' : '→', 'launch-symbol'));
    $('story-play').setAttribute('aria-label', state.playing ? 'Pause story' : state.scene === 4 ? 'Replay story' : reduceNow() ? 'Next scene' : 'Play story');
    $('story-play').replaceChildren(n('span', state.playing ? 'Ⅱ' : state.scene === 4 ? '↻' : reduceNow() ? '→' : '▶'));
  }
  playback = ui.player(render, reduceNow()); playback.go(0);
  $('story-loading').hidden = true; $('story-ready').hidden = false;
  function manual(scene) { playback.go(scene); $('scene-title').focus({preventScroll: true}); $('scene-title').scrollIntoView({block: 'nearest', behavior: reduceNow() ? 'auto' : 'smooth'}); }
  $('story-start').onclick = () => playback.state().playing ? playback.pause() : reduceNow() ? manual(playback.state().scene === 4 ? 0 : playback.state().scene + 1) : playback.play();
  $('story-play').onclick = () => playback.state().playing ? playback.pause() : reduceNow() ? manual(playback.state().scene === 4 ? 0 : playback.state().scene + 1) : playback.play();
  $('story-prev').onclick = () => manual(playback.state().scene - 1); $('story-next').onclick = () => manual(playback.state().scene + 1); $('story-skip').onclick = () => manual(4);
  for (const b of document.querySelectorAll('button[data-scene]')) b.onclick = () => manual(Number(b.dataset.scene));
  $('story-about').onclick = () => { playback.pause(); $('story-evidence').open = true; $('story-evidence').querySelector('summary').focus(); $('story-evidence').scrollIntoView({block: 'nearest', behavior: reduceNow() ? 'auto' : 'smooth'}); };
  $('story-theme').onclick = ui.theme; $('story-export').onclick = () => { playback.pause(); ui.download(ui.storyExport(data, cases), data.id + '-story.json'); };
  document.addEventListener('visibilitychange', () => { if (document.hidden) playback.pause(); });
  $('story').addEventListener('toggle', event => { if (event.target.open) playback.pause(); }, true);
  $('story').addEventListener('focusin', event => { if (event.target.closest('.scene-content')) playback.pause(); });
  function setMotion() { playback.destroy(); const scene = playback.state().scene; document.documentElement.dataset.reducedMotion = String(reduceNow()); playback = ui.player(render, reduceNow()); playback.go(scene); }
  motion.onchange = setMotion; reduced.addEventListener('change', setMotion);
  if (location.hash === '#result' || location.hash === '#comparison') manual(4);
})();
