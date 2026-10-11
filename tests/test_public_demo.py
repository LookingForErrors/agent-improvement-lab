"""Public fixture export and browser arithmetic must preserve evidence/provenance."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_demo_builder',ROOT/'web_demo/build_demo.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
spec=importlib.util.spec_from_file_location('public_release_builder',ROOT/'scripts/stage_release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)

class PublicDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        result=subprocess.run([sys.executable,'-B',str(ROOT/'examples/docs-answer/run_demo.py'),'--output',str(cls.root/'fixture')],capture_output=True,text=True,timeout=60)
        if result.returncode:raise RuntimeError(result.stderr)
        builder.export(cls.root/'fixture',cls.root/'site')
        cls.data=json.loads((cls.root/'site/demo-data.json').read_text())
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_export_has_only_public_assets_and_no_private_snapshot_fields(self):
        self.assertEqual(set(p.name for p in (self.root/'site').iterdir()),set(release.DEMO))
        text=json.dumps(self.data)
        for forbidden in ['human_snapshot','annotation_data','saved_at','/Users/','annotations.sqlite3','checkpoints.sqlite3']:self.assertNotIn(forbidden,text)
        self.assertEqual(self.data['provenance']['real_human_labels'],0)
        self.assertEqual(self.data['provenance']['model_calls'],0)
        self.assertEqual(self.data['population']['families'],4)
        self.assertIn('identical authored copies',self.data['provenance']['repeats'])
        self.assertEqual(len(self.data['traces']),16)
    def test_evidence_links_resolve_to_actual_steps_and_observed_contradictions(self):
        traces={t['id']:t for t in self.data['traces']}
        for item in self.data['action']['evidence']:
            ids={m['step_id'] for m in traces[item['trace_id']]['messages']}
            self.assertTrue(set(item['evidence_steps'])<=ids)
            self.assertIn('s1',item['evidence_steps'])
            if item['family_id'] in ['wrong-version','invented-retention']:self.assertIn('s5',item['evidence_steps'])
    def test_real_annotation_mode_cannot_be_exported(self):
        target=self.root/'nonfixture';shutil.copytree(self.root/'fixture',target)
        path=target/'SUMMARY.json';summary=json.loads(path.read_text());summary['mode']='real_human_checkpoint';path.write_text(json.dumps(summary))
        with self.assertRaises(ValueError):builder.export(target,self.root/'forbidden-output')
        self.assertFalse((self.root/'forbidden-output').exists())
    def test_machine_path_in_authored_content_is_rejected(self):
        target=self.root/'machine';shutil.copytree(self.root/'fixture',target)
        path=target/'criteria-baseline/report.json';report=json.loads(path.read_text());report['traces'][0]['messages'][1]['content']='/Users/private/person/file';path.write_text(json.dumps(report))
        with self.assertRaises(ValueError):builder.export(target,self.root/'machine-output')
        self.assertFalse((self.root/'machine-output').exists())
    def test_browser_calculations_match_python_and_keep_unknowns_separate(self):
        node=shutil.which('node')
        self.assertIsNotNone(node,'Node is required for the browser calculation contract')
        code='''const fs=require('fs'),assert=require('node:assert/strict');const app=require(process.argv[1]),data=JSON.parse(fs.readFileSync(process.argv[2]));const rows={baseline:app.evaluate(data,'baseline'),candidate:app.evaluate(data,'candidate')};for(const arm of ['baseline','candidate'])for(const c of data.criteria){const actual=app.counts(rows[arm],c.id),expected=data.expected.metrics[arm].working[c.id];for(const key of ['expected','correct','incorrect','unassessable','not_applicable','missing','grader_error'])assert.equal(actual[key],expected[key],arm+' '+c.id+' '+key);}for(const c of data.criteria){const actual=app.compare(data,rows.baseline,rows.candidate,c.id),expected=data.expected.criteria_comparison.splits.working[c.id];assert.equal(actual.improved,expected.improved,c.id);assert.equal(actual.regressed,expected.regressed,c.id);assert.equal(actual.excluded,expected.excluded_pairs,c.id);assert.equal(actual.delta,expected.equal_family_task_mean_correct_delta,c.id);}assert.deepEqual(app.referenceComparison(data.references).matrix,data.expected.reference_confusion);assert.deepEqual(app.generate(17),app.generate(17));assert.notDeepEqual(app.generate(17),app.generate(18));assert.throws(()=>app.generate(-1));assert.throws(()=>app.taskExport(-1,app.generate(17)));assert.equal(app.taskExport(17,app.generate(17)).seed,17);assert(app.taskExport(17,app.generate(17)).tasks.every(t=>t.seed===17));assert.equal(new Set(app.generate(17).map(t=>t.family_id)).size,4);assert(app.generate(17).every(t=>t.assessment_status==='not_run'&&!t.independent_new_family));const trace=data.traces.find(t=>t.id==='baseline-invented-retention-1');assert.equal(app.citation(trace).verdict,'correct');const broken=structuredClone(trace);broken.messages.find(m=>m.function==='read_doc').content='{';assert.equal(app.citation(broken).verdict,'unassessable');const absent=structuredClone(trace);absent.messages=absent.messages.filter(m=>m.role==='user');assert.equal(app.citation(absent).status,'not_applicable');const unknown=app.counts([{criterion_id:'x',status:'assessed',verdict:'unassessable'}],'x');assert.equal(unknown.unassessable,1);assert.equal(unknown.rate,null);'''
        result=subprocess.run([node,'-e',code,str(ROOT/'web_demo/app.js'),str(self.root/'site/demo-data.json')],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
    def proposal_check(self,code):
        node=shutil.which('node');self.assertIsNotNone(node)
        prefix="const fs=require('fs'),assert=require('node:assert/strict');const app=require(process.argv[1]),data=JSON.parse(fs.readFileSync(process.argv[2]));const fields={family_ids:['invented-retention'],intervention:'prompt',proposed_change:'Check each factual claim against the retrieved source before answering.',cause_hypothesis:'',success_criterion:'Use the documented30dayretention and preserve export correctness.',primary_criterion:'task_completion',regression_check:'Keep the successful audit export answer correct.',repeat_runs:2};const identity={id:'proposal-unit-test-0001',revision:1,created_at:'2026-10-09T10:00:00Z',updated_at:'2026-10-09T10:00:00Z'};"
        result=subprocess.run([node,'-e',prefix+code,str(ROOT/'web_demo/app.js'),str(self.root/'site/demo-data.json')],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_custom_proposal_keeps_evidence_and_never_inherits_fixture_results(self):
        self.proposal_check("""const before=JSON.stringify(data);const draft=app.proposalDraft(data,fields,identity);assert.equal(draft.execution_status,'not_run');assert.equal(draft.measured_result,null);assert.equal(draft.candidate_ref,null);assert.equal(draft.human_validation,null);assert.equal(draft.data_kind,'test_fixture');assert.equal(draft.author_kind,'browser_user_input');assert.equal(draft.cause_hypothesis,null);assert.equal(draft.cause_status,'unknown');assert.deepEqual([draft.support_counts.traces,draft.support_counts.tasks,draft.support_counts.families],[2,1,1]);assert(draft.evidence.every(e=>e.split==='working'&&e.trace_id.startsWith('baseline-')));const exported=app.proposalExport(data,draft);assert.deepEqual(exported,draft);assert(!('metrics' in exported)&&!('expected' in exported));assert.equal(JSON.stringify(data),before);for(const intervention of ['prompt','context','tools','scaffold','retrieval','model','training_data','post_training','investigation']){const d=app.proposalDraft(data,{...fields,intervention},identity);assert.equal(d.execution_status,'not_run');assert.equal(d.verification.repeat_runs,intervention==='investigation'?null:2);}const hypothesis=app.proposalDraft(data,{...fields,cause_hypothesis:'The prompt may not require claim verification.'},identity);assert.equal(hypothesis.cause_status,'unverified_hypothesis');""")
    def test_custom_proposal_rejects_foreign_or_unresolvable_evidence_and_stale_source(self):
        self.proposal_check("""for(const family_ids of [[],['foreign'],['invented-retention','invented-retention']])assert.throws(()=>app.proposalDraft(data,{...fields,family_ids},identity));for(const patch of [{success_criterion:' '},{proposed_change:'TODO'},{primary_criterion:'foreign'},{repeat_runs:1},{intervention:'magic'}])assert.throws(()=>app.proposalDraft(data,{...fields,...patch},identity));const broken=structuredClone(data);broken.action.evidence.find(e=>e.family_id==='invented-retention').evidence_steps=['unknown-step'];assert.throws(()=>app.proposalDraft(broken,fields,identity));const heldout=structuredClone(data);heldout.traces.find(t=>t.id===heldout.action.evidence.find(e=>e.family_id==='invented-retention').trace_id).split='heldout';assert.throws(()=>app.proposalDraft(heldout,fields,identity));const draft=app.proposalDraft(data,fields,identity);const newer={...data,dataset_version:'new-version'};assert.throws(()=>app.proposalExport(newer,draft));for(const patch of [{measured_result:{score:1}},{candidate_ref:'supplied-candidate'},{human_validation:'accepted'},{execution_status:'completed'}])assert.throws(()=>app.proposalExport(data,{...draft,...patch}));""")
    def test_custom_proposal_partial_input_and_revision_are_explicit(self):
        self.proposal_check("""const partial={...fields,family_ids:[],proposed_change:'',success_criterion:'',repeat_runs:null};assert.equal(app.proposalFields(data,partial,false).repeat_runs,null);assert.equal(app.proposalFields(data,{...partial,repeat_runs:2.5},false).repeat_runs,2.5);assert.throws(()=>app.proposalFields(data,partial,true));for(const patch of [{revision:0},{id:'foreign'},{created_at:'not-a-date'},{updated_at:123}])assert.throws(()=>app.proposalDraft(data,fields,{...identity,...patch}));const draft=app.proposalDraft(data,fields,identity),r2=app.proposalDraft(data,{...fields,proposed_change:'Add a tool-side claim validation gate.'},{...draft,revision:2,updated_at:'2026-10-09T11:00:00Z'});assert.equal(r2.id,draft.id);assert.equal(r2.created_at,draft.created_at);assert.equal(r2.revision,2);assert.notEqual(r2.proposed_change,draft.proposed_change);assert.equal(app.proposalExport(data,draft).revision,1);""")
    def test_existing_output_is_preserved(self):
        before=(self.root/'site/demo-data.json').read_bytes()
        with self.assertRaises(ValueError):builder.export(self.root/'fixture',self.root/'site')
        self.assertEqual((self.root/'site/demo-data.json').read_bytes(),before)
    def test_release_snapshot_binds_exact_source_and_demo_without_private_trees(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'release';manifest=release.stage(output,self.root/'site')
            for name,digest in manifest['files'].items():self.assertEqual(hashlib.sha256((output/name).read_bytes()).hexdigest(),digest)
            self.assertFalse(manifest['publication']);self.assertEqual(manifest['license'],'MIT')
            self.assertTrue(manifest['includes_catalog'])
            self.assertEqual((output/'demo/demo-data.json').read_bytes(),(self.root/'site/demo-data.json').read_bytes())
            self.assertTrue((output/'tests/test_public_demo.py').exists())
            self.assertFalse(any(part in ['artifacts','annotation_data','tasks','logs','decisions','.venv'] for name in manifest['files'] for part in Path(name).parts))
    def test_source_and_legacy_demo_snapshots_do_not_claim_an_absent_catalog(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);legacy=temp/'legacy';legacy.mkdir()
            for name in release.BASE_DEMO:shutil.copyfile(self.root/'site'/name,legacy/name)
            shutil.copyfile(self.root/'site/guided.html',legacy/'index.html')
            manifest=json.loads((legacy/'DEMO-MANIFEST.json').read_text())
            manifest['kind']='unpublished_static_fixture_demo'
            for key in ['includes_saved_experiment','saved_model_requests','includes_family_experiment','saved_family_records','family_units','includes_curated_replay','curated_authored_traces','curated_recorded_traces','curated_inferential_result']:manifest.pop(key,None)
            manifest['files']={name:hashlib.sha256((legacy/name).read_bytes()).hexdigest() for name in release.BASE_DEMO if name!='DEMO-MANIFEST.json'}
            (legacy/'DEMO-MANIFEST.json').write_text(json.dumps(manifest))
            for label,demo in [('source',None),('legacy',legacy)]:
                out=temp/(label+'-output');result=release.stage(out,demo)
                self.assertFalse(result['includes_catalog']);self.assertNotIn('Open `demo/catalog.html`',(out/'README.md').read_text())
                self.assertFalse(result['includes_saved_experiment']);self.assertNotIn('Open `demo/experiment.html`',(out/'README.md').read_text())
    def test_standalone_markdown_links_resolve_within_public_snapshot(self):
        import re
        from urllib.parse import unquote,urlsplit
        with tempfile.TemporaryDirectory() as temp:
            output=(Path(temp)/'release').resolve();release.stage(output,self.root/'site')
            for path in output.rglob('*.md'):
                for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',path.read_text()):
                    parsed=urlsplit(target)
                    if parsed.scheme or not parsed.path:continue
                    linked=(path.parent/unquote(parsed.path)).resolve()
                    self.assertTrue(linked.is_relative_to(output),str(path.relative_to(output))+': '+target)
                    self.assertTrue(linked.exists(),str(path.relative_to(output))+': '+target)
    def test_release_rejects_changed_demo_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);demo=temp/'demo';shutil.copytree(self.root/'site',demo);(demo/'app.js').write_text('changed')
            with self.assertRaises(ValueError):release.stage(temp/'output',demo)
            self.assertFalse((temp/'output').exists())
    def test_release_rejects_incomplete_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);demo=temp/'demo';shutil.copytree(self.root/'site',demo);p=demo/'DEMO-MANIFEST.json';manifest=json.loads(p.read_text());manifest['files'].pop('app.js');p.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):release.stage(temp/'output',demo)
            self.assertFalse((temp/'output').exists())

if __name__=='__main__':unittest.main()
