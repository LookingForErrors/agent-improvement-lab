"""Saved scores/evidence drive a visitor UI without making the UI a scoring dependency."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('saved_public_boundary',ROOT/'web_demo/build_experiment.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class SavedExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.data=json.loads((ROOT/'web_demo/experiment-fixture.json').read_text())

    def node_check(self,code):
        command=[shutil.which('node'),'-e',"const fs=require('fs'),assert=require('node:assert/strict'),app=require(process.argv[1]),data=JSON.parse(fs.readFileSync(process.argv[2]));"+code,str(ROOT/'web_demo/experiment.js'),str(ROOT/'web_demo/experiment-fixture.json')]
        self.assertIsNotNone(command[0]);r=subprocess.run(command,capture_output=True,text=True,timeout=30);self.assertEqual(r.returncode,0,r.stderr)

    def test_full_saved_inventory_and_prior_source_namespaces(self):
        d=builder.validate(copy.deepcopy(self.data));self.assertEqual((len(d['traces']),len(d['results']),len(d['pairs'])),(16,64,8))
        self.assertEqual(len({t['family_id'] for t in d['traces']}),4)
        self.assertEqual({t['repeat_id'] for t in d['traces']},{'1','2'})
        self.assertEqual({t['id'] for t in d['prior_traces']},{'prior-s0002','prior-s0008'})
        self.assertEqual(d['decision']['decision'],'do_not_adopt');self.assertFalse(d['provenance']['llm_judge_used'])
        self.assertFalse(d['decision']['statistical_effect_established']);self.assertEqual(d['provenance']['new_model_calls'],0)

    def test_public_boundary_rejects_foreign_fields_bad_links_or_changed_observations(self):
        mutations=[lambda d:d.update(human_snapshot={}),lambda d:d['action'].update(assessor='private'),lambda d:d['results'][0].update(evidence_steps=['missing-step']),lambda d:d['pairs'].pop(),lambda d:d['traces'][0].update(split='heldout'),lambda d:d['comparison']['splits']['working']['closed_destination'].update(regressed=0),lambda d:d['metrics']['baseline']['working']['closed_destination'].update(correct=8),lambda d:d['metrics']['baseline']['working']['closed_destination'].update(annotation={}),lambda d:d['decision'].update(default_promoted=True),lambda d:d['prompt'].update(delta='made up')]
        for mutate in mutations:
            d=copy.deepcopy(self.data);mutate(d)
            with self.assertRaises(ValueError):builder.validate(d)
        private={'private_labels':{'assessor':'private'}}
        nested_mutations=[lambda d:d['uncertainty']['working']['closed_destination'].update(interval_95=private),lambda d:d['metrics']['baseline']['working']['closed_destination'].update(correct_rate_among_assessable=private),lambda d:d['comparison']['splits']['working']['closed_destination'].update(equal_family_task_mean_correct_delta=private),lambda d:d['action']['verification']['keep_fixed'].append(private),lambda d:d['criteria'][0]['definition'].update(rubric=private)]
        for mutate in nested_mutations:
            d=copy.deepcopy(self.data);mutate(d)
            with self.assertRaises(ValueError):builder.validate(d)
        d=copy.deepcopy(self.data);t=d['traces'][0];m=next(m for m in t['messages'] if m['role']=='tool');payload=json.loads(m['content']);payload['annotation']={'assessor':'private'};m['content']=json.dumps(payload);t['projection_sha256']=builder.digest({k:v for k,v in t.items() if k!='projection_sha256'})
        with self.assertRaises(ValueError):builder.validate(d)

    def test_browser_aggregates_and_all_pair_outcomes_match_source_engine(self):
        self.node_check("""for(const arm of ['baseline','candidate'])for(const c of data.criteria){const a=app.aggregate(data,arm,c.id),source=data.metrics[arm].working[c.id];for(const k of ['expected','assessed','correct','incorrect','unassessable','missing','grader_error','not_applicable','assessable'])assert.equal(a[k],source[k]);}for(const c of data.criteria){const p=app.pairs(data,c.id),source=data.comparison.splits.working[c.id];assert.equal(p.length,8);assert.equal(p.filter(p=>p.outcome==='improved').length,source.improved);assert.equal(p.filter(p=>p.outcome==='regressed').length,source.regressed);}assert.equal(app.decision(data),data.decision.decision);const missing=structuredClone(data),cell=missing.results.find(c=>c.criterion_id===missing.decision.rule.primary);Object.assign(cell,{status:'grader_error',verdict:null,evidence_steps:[],error_code:'bad_output'});assert.equal(app.decision(missing),'inconclusive_measurement');assert.equal(app.aggregate(missing,missing.traces.find(t=>t.id===cell.trace_id).arm,cell.criterion_id).grader_error,1);""")

    def test_evidence_mapping_is_trace_bound_multiple_and_does_not_rewrite_scores(self):
        self.node_check("""const before=JSON.stringify(data);for(const cell of data.results){const t=data.traces.find(t=>t.id===cell.trace_id),m=app.mapEvidence(t,cell);assert.deepEqual(m.steps,cell.evidence_steps);assert.equal(m.verdict,cell.verdict);}assert(data.results.some(c=>c.evidence_steps.length>1));const t=data.traces[0],c=data.results.find(c=>c.trace_id===t.id);assert.equal(app.mapEvidence(t,{...c,evidence_steps:['foreign']}).state,'invalid_evidence');assert.equal(app.mapEvidence(t,{...c,trace_sha256:'0'.repeat(64)}).state,'invalid_evidence');const prior=data.prior_traces.find(t=>t.source_id==='s0002'),current=data.results.find(c=>c.trace_id==='s0002');assert.equal(app.mapEvidence(prior,current).state,'invalid_evidence');assert.deepEqual(app.mapEvidence(t,{...c,status:'missing',verdict:null,evidence_steps:[]}).steps,[]);assert.equal(JSON.stringify(data),before);""")

    def test_prompt_delta_and_uncertainty_limits_remain_exact(self):
        d=self.data;self.assertEqual(d['prompt']['candidate'],d['prompt']['baseline']+d['prompt']['delta'])
        self.assertIn('call ask_user',d['prompt']['delta']);self.assertEqual(d['action']['status'],'draft_pending_human_selection')
        for c in d['uncertainty']['working'].values():self.assertIsNone(c['interval_95']);self.assertEqual(c['paired_families'],4);self.assertEqual(c['status'],'insufficient_families')

if __name__=='__main__':unittest.main()
