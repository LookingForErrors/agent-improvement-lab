import importlib.util
import tempfile
import unittest
from pathlib import Path

from agent_improvement_lab import annotation as ann
from agent_improvement_lab import workflow as flow

spec = importlib.util.spec_from_file_location('routing_demo', Path(__file__).resolve().parents[1] / 'examples/routing-loop/build_demo.py')
demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(demo)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = demo.build(self.root / 'inputs')
        self.workspace = self.root / 'workflow'

    def start(self):
        return flow.operate(self.workspace, 'start', config_path=self.config)

    def test_builder_rejects_existing_output_without_changing_files(self):
        inputs = self.root / 'inputs'
        before = {p.name: p.read_bytes() for p in inputs.iterdir()}
        with self.assertRaisesRegex(ValueError, 'fresh input directory'):
            demo.build(inputs)
        self.assertEqual({p.name: p.read_bytes() for p in inputs.iterdir()}, before)
        empty = self.root / 'empty'
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh input directory'):
            demo.build(empty)
        self.assertEqual(list(empty.iterdir()), [])

    def propose(self):
        self.start()
        flow.operate(self.workspace, 'labels-ready')
        return flow.operate(self.workspace, 'propose', answer={'command': 'propose', 'plan': flow.read(self.root / 'inputs/plan.json')})

    def approval(self, decision='approve'):
        status = flow.operate(self.workspace, 'status')
        return {'command': decision, 'reviewer': 'test-reviewer', 'action_id': 'role-routing', 'identity': status['checkpoints'][0]['identity']}

    def test_demo_checkpoints_and_complete_replay(self):
        self.assertEqual(self.propose()['phase'], 'approval')
        flow.operate(self.workspace, 'approve', answer=self.approval())
        self.assertEqual(flow.operate(self.workspace, 'status')['phase'], 'complete')
        report = flow.read(self.workspace / 'comparison.json')
        self.assertEqual(report['splits']['working']['equal_family_task_mean_correct_delta'], .5)
        self.assertEqual(report['splits']['heldout']['pairs'], 4)
        self.assertEqual(ann.queue(self.workspace / 'annotations.sqlite3')['reviewed'], 0)
        self.assertEqual(flow.read(self.workspace / 'triage.json')['data_kind'], 'test_fixture')

    def test_reject_produces_no_comparison_and_no_restart(self):
        self.propose()
        with self.assertRaisesRegex(ValueError, 'payload command'):
            flow.operate(self.workspace, 'reject', answer=self.approval('approve'))
        rejection = self.approval('reject'); rejection['action_id'] = None
        flow.operate(self.workspace, 'reject', answer=rejection)
        self.assertFalse((self.workspace / 'comparison.json').exists())
        self.assertEqual(flow.operate(self.workspace, 'status')['next'], [])
        with self.assertRaises(ValueError):
            flow.operate(self.workspace, 'resume')
        with self.assertRaises(ValueError):
            self.start()

    def test_crash_after_write_resumes_with_identical_effect(self):
        self.propose()
        with self.assertRaisesRegex(RuntimeError, 'simulated crash'):
            flow.operate(self.workspace, 'approve', answer=self.approval(), crash_after_comparison=True)
        before = (self.workspace / 'comparison.json').read_bytes()
        self.assertEqual(flow.operate(self.workspace, 'resume')['phase'], 'complete')
        self.assertEqual((self.workspace / 'comparison.json').read_bytes(), before)

    def test_changed_evidence_or_approval_identity_fails_closed(self):
        self.propose()
        answer = self.approval(); answer['identity']['candidate.json'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'stale'):
            flow.operate(self.workspace, 'approve', answer=answer)
        flow.operate(self.workspace, 'approve', answer=self.approval())
        self.assertEqual(flow.operate(self.workspace, 'status')['phase'], 'complete')
        # Use a distinct workspace for evidence mutation after the completed run.
        self.workspace = self.root / 'second'
        self.propose()
        (self.workspace / 'candidate.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            flow.operate(self.workspace, 'approve', answer=self.approval())
        self.assertFalse((self.workspace / 'comparison.json').exists())

    def test_missing_predictions_pairs_and_changed_context_rejected(self):
        for mutation in ['judge', 'pair', 'context']:
            folder = self.root / mutation; config = demo.build(folder)
            path = folder / ('candidate-judge.json' if mutation == 'judge' else 'candidate.json')
            value = flow.read(path)
            if mutation == 'judge': value['assessments'].pop()
            elif mutation == 'pair': value.pop()
            else: value[0]['messages'][1]['content'] = 'A different task'
            demo.dump(path, value)
            with self.assertRaises(ValueError):
                flow.operate(self.root / ('run-' + mutation), 'start', config_path=config)
            self.assertFalse((self.root / ('run-' + mutation)).exists())

    def test_wrong_stage_and_invalid_working_plan(self):
        self.start()
        with self.assertRaisesRegex(ValueError, 'checkpoint'):
            flow.operate(self.workspace, 'approve', answer={})
        flow.operate(self.workspace, 'labels-ready')
        plan = flow.read(self.root / 'inputs/plan.json'); plan['actions'][0]['issue_ids'] = ['heldout-invented']
        with self.assertRaisesRegex(ValueError, 'non-working'):
            flow.operate(self.workspace, 'propose', answer={'command': 'propose', 'plan': plan})
        self.assertFalse((self.workspace / 'plan.json').exists())
        valid = flow.read(self.root / 'inputs/plan.json')
        valid['actions'][0]['verification']['repeat_runs'] = 3
        flow.operate(self.workspace, 'propose', answer={'command': 'propose', 'plan': valid})
        with self.assertRaisesRegex(ValueError, 'repeat requirement'):
            flow.operate(self.workspace, 'approve', answer=self.approval())
        flow.operate(self.workspace, 'reject', answer=self.approval('reject'))

    def test_real_human_checkpoint_requires_complete_queue_and_preserves_revisions(self):
        text = self.config.read_text().replace('mode: scripted_demo', 'mode: human').replace('fixture_annotations: fixture-annotations.json\n', '')
        self.config.write_text(text)
        self.start()
        db = self.workspace / 'annotations.sqlite3'
        self.assertEqual(ann.queue(db)['reviewed'], 0)
        with self.assertRaisesRegex(ValueError, 'all selected'):
            flow.operate(self.workspace, 'labels-ready')
        labels = {row['trace_id']: row['annotation'] for row in flow.read(self.root / 'inputs/fixture-annotations.json')}
        with ann.connect(db) as connection:
            cases = connection.execute('SELECT case_id,source_id FROM cases').fetchall()
        for case in cases:
            label = dict(labels[case['source_id']]); label.pop('revision'); label.pop('saved_at')
            label['annotator'] = 'isolated-test-human'; label['expected_revision'] = 0
            ann.save(db, case['case_id'], label)
        # LangGraph reuses failed interrupt resume; same labels-ready now succeeds.
        flow.operate(self.workspace, 'labels-ready')
        self.assertEqual(ann.queue(db)['reviewed'], 4)
        snapshot = flow.read(self.workspace / 'human-snapshot.json')
        self.assertEqual({r['annotation']['revision'] for r in snapshot}, {1})
        cid = cases[0]['case_id']; label = ann.case_data(db, cid)['annotation']
        payload = {k: v for k, v in label.items() if k not in ['revision', 'saved_at']}
        payload['comment'] = 'Human changed the decision explanation'; payload['expected_revision'] = 1
        ann.save(db, cid, payload)
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            flow.operate(self.workspace, 'status')
        self.assertEqual(ann.case_data(db, cid)['annotation']['revision'], 2)

    def test_seeded_sampling_and_fixture_boundary(self):
        baseline = flow.read(self.root / 'inputs/baseline.json')
        self.assertEqual(flow.sample(baseline, 2, 17), flow.sample(baseline, 2, 17))
        self.assertEqual(len({r['family_id'] for r in flow.sample(baseline, 2, 17)}), 2)
        bad = self.config.read_text().replace('mode: scripted_demo', 'mode: human')
        self.config.write_text(bad)
        with self.assertRaisesRegex(ValueError, 'fixture annotations'):
            self.start()


if __name__ == '__main__':
    unittest.main()
