"""Portability and evidence boundaries in the authored documentation demo."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from agent_improvement_lab import annotation as ann, criteria, graders, workflow
from agent_improvement_lab.import_traces import import_file
from agent_improvement_lab.schemas import Trace

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'examples/docs-answer'
spec=importlib.util.spec_from_file_location('docs_demo_builder',EXAMPLE/'build_demo.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class DocsAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);builder.build(self.root/'inputs')
    def report(self,arm):
        p=self.root/'inputs';rows=criteria.read(p/f'{arm}-normalized.json')
        pack=criteria.load_pack(p/'criteria.yaml');registry=graders.default_registry()
        graders.load_plugin(registry,EXAMPLE/'citation_plugin.py')
        return criteria.evaluate(rows,pack,registry,graders.SavedAssessments(criteria.read(p/f'{arm}-criteria.json'),rows,pack.resolve()))
    def test_json_and_jsonl_mapping_preserve_same_domain_content(self):
        for arm in ['baseline','candidate']:
            results=[import_file(self.root/f'inputs/{arm}-raw.{ext}',self.root/'inputs/mapping.yaml') for ext in ['json','jsonl']]
            self.assertTrue(all(r.ok for r in results))
            def semantic(result):
                return [{**r.model_dump(mode='json',exclude={'source','task'}),'task':r.task.model_dump(mode='json',exclude={'source'})} for r in result.records]
            self.assertEqual(semantic(results[0]),semantic(results[1]))
            self.assertEqual(len(results[0].records),8)
    def test_criteria_distinguish_linkage_truth_version_and_applicability(self):
        baseline,candidate=self.report('baseline'),self.report('candidate')
        self.assertFalse(any(c.status in ['missing','grader_error'] for c in baseline.results+candidate.results))
        cells={(c.trace_id,c.criterion_id):c for c in baseline.results}
        self.assertEqual(cells['baseline-invented-retention-1','citation_sources'].verdict,'correct')
        self.assertEqual(cells['baseline-invented-retention-1','unsupported_claims'].verdict,'incorrect')
        self.assertEqual(cells['baseline-invented-retention-1','context_use'].verdict,'incorrect')
        self.assertEqual(cells['baseline-wrong-version-1','citation_sources'].verdict,'correct')
        self.assertEqual(cells['baseline-wrong-version-1','context_use'].verdict,'incorrect')
        self.assertEqual(cells['baseline-wrong-version-1','unsupported_claims'].verdict,'incorrect')
        self.assertEqual(cells['baseline-missed-tools-1','tool_arguments'].status,'not_applicable')
        self.assertEqual(cells['baseline-missed-tools-1','tool_selection'].verdict,'incorrect')
        self.assertEqual(cells['baseline-missed-tools-1','unsupported_claims'].verdict,'incorrect')
        self.assertEqual(cells['baseline-missed-tools-1','context_use'].verdict,'incorrect')
        compared=criteria.compare(baseline,candidate)
        self.assertEqual(compared['status'],'descriptive_paired')
        self.assertEqual(compared['splits']['working']['task_completion']['improved'],6)
        self.assertEqual(compared['splits']['working']['task_completion']['regressed'],0)
    def test_generic_blind_projection_and_human_evidence_survive_reload(self):
        p=self.root/'inputs';db=self.root/'labels.sqlite3'
        ann.prepare(db,p/'baseline-normalized.jsonl',p/'rubric.txt')
        case=ann.queue(db)['cases'][0]['id'];public=ann.case_data(db,case)
        self.assertNotIn('current_ticket',public)
        encoded=json.dumps(ann.public_export(db))
        for secret in ['Hidden arm','agent_version','baseline-','fixture_judge','source_sha256','judge_identity']:
            self.assertNotIn(secret,encoded)
        user=next(m['id'] for m in public['messages'] if m['role']=='user')
        assistant=next(m['id'] for m in reversed(public['messages']) if m['role']=='assistant')
        label={'verdict':'unassessable','annotator':'QA_only','evidence_steps':[user,assistant],'error_types':[],
            'comment':'Test evidence persistence, not a product decision.','scenario_problem':False,'scenario_evidence':[],
            'scenario_comment':'','expected_revision':0}
        ann.save(db,case,label);ann.prepare(db,p/'baseline-normalized.jsonl',p/'rubric.txt')
        self.assertEqual(ann.case_data(db,case)['annotation']['evidence_steps'],[user,assistant])
        saved=ann.export_private(db)[0]
        source=next(r for r in criteria.read(p/'baseline-normalized.json') if r['id']==saved['trace_id'])
        self.assertEqual(saved['original_evidence_steps'],['s1',source['messages'][-1]['step_id']])
    def test_multiple_current_ticket_instructions_still_rejected(self):
        row=criteria.read(self.root/'inputs/baseline-normalized.json')[0]
        row['messages'][0]['content']='Current ticket: ONE\nCurrent ticket: TWO'
        with self.assertRaisesRegex(ValueError,'at most one'):ann.project_trace(Trace.model_validate(row))
    def test_scripted_workflow_complete_keeps_real_annotation_database_empty(self):
        target=self.root/'replay'
        process=subprocess.run([sys.executable,str(EXAMPLE/'run_demo.py'),'--output',str(target)],capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr)
        summary=criteria.read(target/'SUMMARY.json');self.assertEqual(summary['phase'],'complete')
        self.assertEqual(ann.export_private(target/'workflow/annotations.sqlite3'),[])
        result=criteria.read(target/'workflow/comparison.json')
        self.assertEqual(result['data_kind'],'test_fixture');self.assertEqual(result['splits']['working']['families'],4)
        self.assertEqual(criteria.read(target/'workflow/decision.json')['reviewer'],'synthetic_test_fixture')
        retry=subprocess.run([sys.executable,str(EXAMPLE/'run_demo.py'),'--output',str(target)],capture_output=True,text=True)
        self.assertNotEqual(retry.returncode,0)
        self.assertEqual(ann.export_private(target/'workflow/annotations.sqlite3'),[])
    def test_human_mode_requires_real_labels_before_analysis(self):
        p=self.root/'inputs';w=self.root/'human'
        state=workflow.operate(w,'start',config_path=p/'human-config.yaml')
        self.assertEqual(state['phase'],'annotation');self.assertEqual(ann.queue(w/'annotations.sqlite3')['reviewed'],0)
        with self.assertRaisesRegex(ValueError,'all selected'):workflow.operate(w,'labels-ready')
        self.assertFalse((w/'human-snapshot.json').exists())


if __name__=='__main__':unittest.main()
