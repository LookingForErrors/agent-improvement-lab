"""Recorded web views preserve the frozen denominator, evidence and playback boundaries."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('story_projection', ROOT / 'web_demo/build_story.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class FamilySiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / 'web_demo/family-experiment.json').read_text())
        cls.examples = json.loads((ROOT / 'web_demo/family-examples.json').read_text())
        cls.story = json.loads((ROOT / 'web_demo/story-data.json').read_text())

    def node_check(self, code):
        result = subprocess.run([shutil.which('node'), '-e', "const fs=require('fs'),assert=require('node:assert/strict'),app=require(process.argv[1]),data=JSON.parse(fs.readFileSync(process.argv[2]));" + code,
                                 str(ROOT / 'web_demo/family.js'), str(ROOT / 'web_demo/family-experiment.json')], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_story_is_a_source_bound_projection_not_another_sample(self):
        self.assertTrue(builder.validate_projection(self.data, self.story, self.examples))
        self.assertEqual((len(self.data['traces']), len(self.data['pairs']), len(self.data['results'])), (504, 252, 3024))
        self.assertEqual(len({p['family_id'] for p in self.story['pairs']}), 84)
        self.assertEqual(len(self.story['traces']), 8)
        self.assertEqual(sum(e['selection_kind'] == 'regressed' for e in self.examples['examples']), 1)
        self.assertTrue(self.story['decision']['conditional_mean_improvement']['supported_with_limitations'])
        self.assertFalse(self.story['decision']['fully_qualified'])
        self.assertFalse(self.story['decision']['default_adopted'])

    def test_projection_rejects_changed_outcomes_evidence_or_intervention_origin(self):
        for mutate in [lambda d: d['results'][0].update(verdict='incorrect' if d['results'][0]['verdict']=='correct' else 'correct'), lambda d: d['statistics'].update(families=252), lambda d: d['action_evidence'][0].update(split='heldout')]:
            d = copy.deepcopy(self.story)
            mutate(d)
            with self.assertRaises(ValueError): builder.validate_projection(self.data, d, self.examples)
        bad = copy.deepcopy(self.data)
        bad['results'][0]['evidence_steps'] = ['invented']
        with self.assertRaises(ValueError): builder.validate(bad, self.examples)
        bad = copy.deepcopy(self.examples)
        bad['examples'][0]['baseline']['trace_sha256'] = '0' * 64
        with self.assertRaises(ValueError): builder.validate(self.data, bad)

    def test_all_criteria_counts_match_saved_engine_without_dropping_unknowns(self):
        self.node_check("assert.equal(app.familyUnits(data),84);const changed=structuredClone(data);changed.statistics.independent_family_units=252;assert.throws(()=>app.familyUnits(changed));")
        self.node_check("""for(const arm of ['baseline','candidate'])for(const c of data.criteria){const a=app.counts(data,arm,c.id),b=data.metrics[arm].heldout[c.id];for(const k of ['expected','correct','incorrect','unassessable','missing','grader_error','not_applicable'])assert.equal(a[k],b[k]);assert.equal(a.rate,a.correct/252);}assert.deepEqual(['baseline','candidate'].map(a=>app.counts(data,a).correct),[80,147]);const p=app.pairs(data);assert.deepEqual(['improved','regressed','unchanged','unknown'].map(k=>p.filter(p=>p.outcome===k).length),[79,14,120,39]);""")

    def test_multiple_evidence_steps_remain_trace_bound_and_scores_unchanged(self):
        self.node_check("""const before=JSON.stringify(data);for(const c of data.results){const t=app.traceFor(data,c.trace_id);assert.deepEqual(app.mapEvidence(t,c).steps,c.evidence_steps);}const t=data.traces[0],c=app.cellFor(data,t.id);assert.equal(app.mapEvidence(t,{...c,trace_sha256:'0'.repeat(64)}).state,'invalid_evidence');assert.equal(app.mapEvidence(t,{...c,evidence_steps:['invented']}).state,'invalid_evidence');assert.deepEqual(app.mapEvidence(t,{...c,status:'grader_error'}).steps,[]);assert.equal(JSON.stringify(data),before);""")

    def test_grid_permutation_preserves_task_identity_and_both_arm_counts(self):
        self.node_check("""const before=JSON.stringify(data),order=app.cohortOrder(data.pairs),ids=rows=>rows.map(p=>p.id);assert.deepEqual(ids(order),ids(app.cohortOrder([...data.pairs].reverse())));assert.deepEqual(ids(order).sort(),ids(data.pairs).sort());assert.notDeepEqual(ids(order),ids(data.pairs));for(const arm of ['baseline','candidate'])assert.deepEqual(app.counts({...data,pairs:order},arm),app.counts(data,arm));const changed=data.pairs.map(p=>({...p,baseline:p.candidate,candidate:p.baseline}));assert.deepEqual(ids(app.cohortOrder(changed)),ids(order));assert.equal(JSON.stringify(data),before);assert.deepEqual(app.cohortOrder([]),[]);""")

    def test_pause_manual_navigation_and_reduced_motion_cancel_progress(self):
        self.node_check("""let id=0,jobs=new Map(),seen=[];const clock={setTimeout:f=>{jobs.set(++id,f);return id;},clearTimeout:i=>jobs.delete(i)};const p=app.player(s=>seen.push(s),false,clock);p.play();assert.equal(jobs.size,1);p.pause();assert.equal(jobs.size,0);assert.equal(p.state().scene,0);p.play();const f=[...jobs.values()][0];jobs.clear();f();assert.equal(p.state().scene,1);p.go(3);assert.equal(jobs.size,0);assert(!p.state().playing);p.go(4);p.play();assert.equal(p.state().scene,0);p.destroy();assert.equal(jobs.size,0);const reduced=app.player(()=>{},true,clock);reduced.play();assert.equal(jobs.size,0);assert(!reduced.state().playing);reduced.go(1);assert.equal(reduced.state().scene,1);""")
    def test_playback_holds_final_result_and_finishes_after_25_seconds(self):
        self.node_check("""let id=0,elapsed=0,jobs=new Map(),seen=[];const clock={setTimeout:(f,ms)=>{jobs.set(++id,{f,ms});return id;},clearTimeout:i=>jobs.delete(i)};const p=app.player(s=>seen.push({...s,elapsed}),false,clock);p.play();let ticks=0;while(jobs.size){const [key,job]=[...jobs][0];jobs.delete(key);elapsed+=job.ms;job.f();assert(++ticks<=5);}assert.equal(elapsed,25000);assert.deepEqual(p.state(),{scene:4,playing:false});assert(seen.some(s=>s.scene===4&&s.playing));assert.equal(seen.at(-1).elapsed,25000);assert.equal(jobs.size,0);""")

    def test_prompt_diff_reconstructs_verbatim_source_and_shared_context(self):
        self.node_check("""const a=data.prompt.baseline.split('\\n').find(s=>s.startsWith('technical:')),b=data.prompt.candidate.split('\\n').find(s=>s.startsWith('- technical:')),d=app.textDiff(a,b);assert.equal(d.before.map(s=>s.text).join(''),a);assert.equal(d.after.map(s=>s.text).join(''),b);assert.equal(d.before.filter(s=>s.kind==='context').map(s=>s.text).join(''),d.after.filter(s=>s.kind==='context').map(s=>s.text).join(''));assert(d.before.some(s=>s.kind==='removed'));assert(d.after.some(s=>s.kind==='added'&&s.text.includes('AND')));""")

    def test_diff_keeps_whitespace_unicode_and_empty_sources(self):
        self.node_check("""for(const [a,b] of [['same line','same line'],['','Added <text>'],['Removed',''],['α  β\\n工具','α\\nновое 工具']]){const d=app.textDiff(a,b);assert.equal(d.before.map(s=>s.text).join(''),a);assert.equal(d.after.map(s=>s.text).join(''),b);if(a===b)assert(d.before.every(s=>s.kind==='context'));}assert.deepEqual(app.textDiff('',''),{before:[],after:[]});""")

    def test_five_examples_are_original_recorded_pairs_with_three_fixes_two_kept_routes(self):
        self.node_check("""const before=JSON.stringify(data),cases=app.storyCases(data);assert.deepEqual(cases.map(e=>e.task),['C3F013-v3','C3F048-v2','C3F038-v1','C3F043-v1','C3F025-v1']);assert.deepEqual(cases.map(e=>[e.baseline.cell.verdict,e.candidate.cell.verdict]),Array.from({length:5},(_,i)=>[i<3?'incorrect':'correct','correct']));for(const e of cases)for(const arm of ['baseline','candidate']){assert.equal(e[arm].trace.id,e.pair[arm]);assert.equal(e[arm].trace.task_id,e.task);assert.notEqual(e[arm].trace.source_kind,'authored_fixture');}assert.equal(JSON.stringify(data),before);""")

    def test_selected_curated_examples_keep_original_trajectories_and_primary_scores(self):
        curated = json.dumps(str(ROOT / 'web_demo/curated-experiment.json'))
        self.node_check("const replay=JSON.parse(fs.readFileSync(" + curated + "));" + """const original=app.storyCases(data),shown=app.storyCases(replay);assert.deepEqual(original.map(e=>e.task),shown.map(e=>e.task));for(const [i,e] of shown.entries())for(const arm of ['baseline','candidate']){assert.deepEqual(e[arm].trace,original[i][arm].trace);assert.deepEqual(e[arm].cell,original[i][arm].cell);}assert.deepEqual(['baseline','candidate'].map(arm=>app.counts(replay,arm).correct),[148,218]);""")

    def test_story_export_retains_source_results_and_all_five_visible_case_bindings(self):
        self.node_check("""const before=JSON.stringify(data),cases=app.storyCases(data),out=app.storyExport(data,cases);assert.equal(out.format,data.format);assert.deepEqual(out.results,data.results);assert.deepEqual(out.presentation.examples.map(e=>e.task_id),cases.map(e=>e.task));for(const e of out.presentation.examples)for(const arm of ['baseline','candidate']){assert.deepEqual(e[arm].trace,app.traceFor(data,e[arm].cell.trace_id));assert.equal(app.mapEvidence(e[arm].trace,e[arm].cell).state,'assessed');}assert.equal(JSON.stringify(data),before);""")

    def test_presentation_focus_rejects_user_blame_and_unbound_source_or_steps(self):
        self.node_check("""const e=app.storyCases(data)[0],{trace,cell,focus}=e.baseline;assert.throws(()=>app.traceFocus(trace,cell,{...focus,anchors:['s001']}));assert.throws(()=>app.traceFocus(trace,cell,{...focus,context:['invented']}));assert.throws(()=>app.traceFocus(trace,{...cell,trace_sha256:'0'.repeat(64)},focus));assert.throws(()=>app.traceFocus(trace,cell,{...focus,verdict:'correct'}));const changed=structuredClone(data);changed.traces.find(t=>t.id===trace.id).source_sha256='0'.repeat(64);assert.throws(()=>app.storyCases(changed));""")

    def test_compact_examples_include_terminal_failure_ticket_context_and_all_questions(self):
        catalog = json.dumps(str(ROOT / 'web_demo/catalog.js'))
        self.node_check("const catalog=require(" + catalog + ");" + """const cases=app.storyCases(data);for(const e of cases)for(const arm of ['baseline','candidate']){const item=e[arm],groups=catalog.trajectoryGroups(item.trace),visible=app.compactGroups(groups,{allClarifications:true,includeFinal:true,focus:item.focus}),steps=visible.flatMap(g=>g.messages.map(m=>m.step_id));for(const s of [...item.focus.anchors,...item.focus.context])assert(steps.includes(s),e.task+'/'+arm+'/'+s);assert(steps.includes(item.trace.messages.at(-1).step_id));assert.equal(new Set(visible).size,visible.length);}const terminal=app.compactGroups(catalog.trajectoryGroups(cases[0].baseline.trace),{allClarifications:true,includeFinal:true,focus:cases[0].baseline.focus});assert(terminal.some(g=>g.messages[0].step_id==='s006'&&g.kind==='conversation'));""")


if __name__ == '__main__':
    unittest.main()
