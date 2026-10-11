import copy
import importlib.util
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

from agent_improvement_lab import criteria as c, graders as g
from agent_improvement_lab.schemas import Trace
from agent_improvement_lab.annotation import digest

spec = importlib.util.spec_from_file_location('criteria_demo', Path(__file__).resolve().parents[1] / 'examples/criteria-demo/build_demo.py')
demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(demo)


class CriteriaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = demo.build(Path(self.temp.name) / 'inputs')

    def run_arm(self, arm='baseline', kind='base', mutate=None, plugin=True):
        rows = c.read(self.root / (arm + '.json'))
        pack = c.load_pack(self.root / (kind + '.yaml'))
        saved_doc = c.read(self.root / (kind + '-' + arm + '-assessments.json'))
        registry = g.default_registry()
        if kind == 'custom' and plugin:
            g.load_plugin(registry, self.root / 'ticket_plugin.py')
        if mutate:
            mutate(rows, pack, saved_doc, registry)
        saved = g.SavedAssessments(saved_doc, rows, pack.resolve())
        return c.evaluate(rows, pack, registry, saved)

    def test_catalog_and_pack_selection(self):
        self.assertEqual(set(c.CATALOG), {'task_completion', 'tool_selection', 'tool_arguments', 'context_use', 'clarification', 'unsupported_claims'})
        config = c.load_pack(self.root / 'base.yaml')
        self.assertEqual(config.primary_metric, 'task_completion')
        data = config.model_dump(); data['regression_metrics'].append('task_completion')
        with self.assertRaises(ValueError): c.Pack.model_validate(data)
        data = config.model_dump(); data['criteria'].append(data['criteria'][0])
        with self.assertRaises(ValueError): c.Pack.model_validate(data)

    def test_complete_matrix_keeps_distinct_outcomes(self):
        report = self.run_arm()
        self.assertEqual(len(report.results), 48)
        summary = c.summaries(report)
        self.assertEqual(summary['working']['clarification']['not_applicable'], 4)
        self.assertIsNone(summary['working']['clarification']['correct_rate_among_assessable'])
        self.assertEqual(summary['heldout']['clarification']['correct'], 2)
        self.assertEqual(summary['heldout']['clarification']['not_applicable'], 2)
        self.assertEqual(summary['working']['context_use']['missing'], 1)
        self.assertEqual(summary['heldout']['unsupported_claims']['grader_error'], 1)
        self.assertEqual(summary['heldout']['unsupported_claims']['incorrect'], 0)
        self.assertEqual(summary['working']['task_completion']['unassessable'], 1)
        self.assertAlmostEqual(summary['working']['task_completion']['correct_rate_among_assessable'], 1/3)

    def test_measurement_identity_and_paired_regressions(self):
        result = c.compare(self.run_arm(), self.run_arm('candidate'))
        self.assertEqual(result['status'], 'descriptive_paired')
        self.assertEqual(result['splits']['working']['task_completion']['improved'], 2)
        self.assertEqual(result['splits']['working']['tool_arguments']['improved'], 2)
        self.assertEqual(result['splits']['working']['tool_selection']['improved'], 0)
        self.assertEqual(result['splits']['heldout']['tool_arguments']['regressed'], 2)
        self.assertEqual(result['splits']['heldout']['unsupported_claims']['excluded_pairs'], 1)
        self.assertEqual(result['splits']['working']['context_use']['excluded_pairs'], 1)

    def test_custom_deterministic_and_llm_share_report(self):
        left, right = self.run_arm(kind='custom'), self.run_arm('candidate', 'custom')
        self.assertEqual(len(left.results), 64)
        self.assertEqual(c.summaries(left)['heldout']['ticket_identity']['correct'], 4)
        self.assertEqual(c.summaries(right)['heldout']['ticket_identity']['incorrect'], 2)
        comparison = c.compare(left, right)
        self.assertEqual(comparison['splits']['heldout']['reply_grounding']['regressed'], 4)
        self.assertEqual(comparison['splits']['heldout']['ticket_identity']['regressed'], 2)

    def test_plugin_opt_in_and_duplicate_registration(self):
        with self.assertRaisesRegex(ValueError, 'explicitly load'): self.run_arm(kind='custom', plugin=False)
        registry = g.default_registry(); g.load_plugin(registry, self.root / 'ticket_plugin.py')
        with self.assertRaisesRegex(ValueError, 'duplicate'): g.load_plugin(registry, self.root / 'ticket_plugin.py')
        path = self.root / 'bad-plugin.py'; path.write_text('PLUGIN_API_VERSION=2\ndef register(registry): pass\n')
        with self.assertRaisesRegex(ValueError, 'API1'): g.load_plugin(g.default_registry(), path)

    def test_grader_crash_is_not_assistant_failure(self):
        def change(rows, pack, saved, registry):
            entry = registry._entries['ticket_identity']
            def crash(trace, options): raise RuntimeError('hidden arbitrary diagnostic')
            registry._entries['ticket_identity'] = (entry[0], crash, entry[2])
        report = self.run_arm(kind='custom', mutate=change)
        cells = [cell for cell in report.results if cell.criterion_id == 'ticket_identity']
        self.assertEqual({cell.status for cell in cells}, {'grader_error'})
        self.assertTrue(all(cell.verdict is None for cell in cells))
        self.assertNotIn('hidden arbitrary', c.markdown(report))
        class OddError(RuntimeError):
            code = 42
        def odd(rows, pack, saved, registry):
            entry = registry._entries['ticket_identity']
            def fail(trace, options): raise OddError('private message')
            registry._entries['ticket_identity'] = (entry[0], fail, entry[2])
        report = self.run_arm(kind='custom', mutate=odd)
        self.assertEqual({cell.error_code for cell in report.results if cell.criterion_id == 'ticket_identity'}, {'OddError'})

    def test_malformed_custom_output_remains_measurement_error(self):
        for decision in [dict(status='assessed', verdict='correct', evidence_steps=['invented'], explanation='Wrong evidence'),
                         dict(status='assessed', verdict='correct', evidence_steps=['s2', 's2'], explanation='Duplicate'),
                         dict(status='assessed', verdict='correct', evidence_steps=[], explanation='No evidence'),
                         dict(status='not_applicable', verdict='correct', evidence_steps=[], explanation='Contradiction')]:
            def change(rows, pack, saved, registry):
                entry = registry._entries['ticket_identity']
                registry._entries['ticket_identity'] = (entry[0], lambda trace, opts: decision, entry[2])
            report = self.run_arm(kind='custom', mutate=change)
            self.assertTrue(all(cell.status == 'grader_error' for cell in report.results if cell.criterion_id == 'ticket_identity'))

    def test_saved_binding_and_assessor_errors_not_quality(self):
        for field, value in [('trace_sha256', '0' * 64), ('criterion_sha256', '0' * 64), ('judge_identity_sha256', '0' * 64), ('assessor_kind', 'human')]:
            def change(rows, pack, saved, registry): saved['assessments'][0][field] = value
            report = self.run_arm(mutate=change)
            self.assertEqual(report.results[0].status, 'grader_error')
            self.assertIsNone(report.results[0].verdict)

    def test_duplicate_or_foreign_saved_assessments_fail_fast(self):
        for mutation in ['duplicate', 'foreign', 'grader']:
            def change(rows, pack, saved, registry):
                if mutation == 'duplicate': saved['assessments'].append(saved['assessments'][0])
                elif mutation == 'foreign': saved['assessments'][0]['trace_id'] = 'unknown'
                else: saved['assessments'][0]['grader_id'] = 'foreign'
            with self.assertRaises(ValueError): self.run_arm(mutate=change)

    def test_missing_all_assessments_gives_undefined_rates(self):
        def change(rows, pack, saved, registry): saved['assessments'] = []
        report = self.run_arm(mutate=change)
        for split in c.summaries(report).values():
            for metric in split.values():
                self.assertEqual(metric['decision_coverage'], 0)
                self.assertIsNone(metric['correct_rate_among_assessable'])

    def test_provider_model_options_and_plugin_changes_block_delta(self):
        left = self.run_arm(kind='custom')
        for key in ['provider', 'model', 'prompt_sha256', 'config_sha256']:
            def change(rows, pack, saved, registry):
                saved['judges']['fixture_judge'][key] = '0' * 64 if key.endswith('sha256') else 'different'
                for row in saved['assessments']: row['judge_identity_sha256'] = digest(saved['judges']['fixture_judge'])
            right = self.run_arm('candidate', 'custom', mutate=change)
            self.assertIsNone(c.compare(left, right)['delta'])
        right = self.run_arm('candidate', 'custom').model_dump()
        right['grader_signatures']['ticket_identity']['implementation_sha256'] = '0' * 64
        self.assertEqual(c.compare(left, right)['status'], 'incompatible_measurement')
        def options(rows, pack, saved, registry):
            pack.criteria[-2].grader.parameters = {'changed_option': True}
        self.assertEqual(c.compare(left, self.run_arm('candidate', 'custom', mutate=options))['status'], 'incompatible_measurement')

    def test_changed_rubric_is_incompatible_even_with_better_verdicts(self):
        def change(rows, pack, saved, registry):
            pack.criteria[0].builtin = None; pack.criteria[0].definition = c.CATALOG['task_completion'].model_copy(update={'version': '2'})
            resolved = pack.resolve()
            for row in saved['assessments']:
                if row['criterion_id'] == 'task_completion': row['criterion_sha256'] = digest(resolved.criteria[0].model_dump(mode='json'))
        right = self.run_arm('candidate', mutate=change)
        self.assertEqual(c.compare(self.run_arm(), right)['status'], 'incompatible_measurement')

    def test_pair_inventory_and_task_context_changes_rejected(self):
        left = self.run_arm()
        for key in ['task', 'language', 'evaluation_context', 'repeat', 'system_fact']:
            right = self.run_arm('candidate').model_dump()
            for trace in right['traces']:
                if key == 'task': trace['task']['objective'] = 'Different objective'
                elif key == 'language': trace['language'] = 'ru'
                elif key == 'evaluation_context': trace['metadata']['evaluation_context']['tools_version'] = 'changed'
                elif key == 'system_fact': trace['messages'][0]['content'] = trace['messages'][0]['content'].replace('DEMO-1', 'OTHER-2')
                else: trace['metadata']['repeat_id'] = '1'
            for cell in right['results']:
                trace = next(t for t in right['traces'] if t['id'] == cell['trace_id']); cell['trace_sha256'] = digest(trace)
            with self.assertRaises(ValueError): c.compare(left, right)

    def test_paired_inventory_cannot_silently_intersect(self):
        left = self.run_arm(); right = self.run_arm('candidate').model_dump()
        tid = right['traces'].pop()['id']; right['results'] = [r for r in right['results'] if r['trace_id'] != tid]
        with self.assertRaises(ValueError): c.compare(left, right)

    def test_family_task_weighting_is_not_pooled_trace_rate(self):
        traces = self.run_arm().traces[:3]
        cells = []
        # One task has two correct repeats; another has one incorrect observation.
        for index, trace in enumerate(traces):
            cells.append(c.Cell(trace_id=trace.id, criterion_id='x', trace_sha256=digest(trace.model_dump(mode='json')), criterion_sha256='a' * 64,
                status='assessed', verdict='correct' if index < 2 else 'incorrect', evidence_steps=['s2'], explanation='Authored'))
        metric = c.summarize(cells, traces)
        self.assertAlmostEqual(metric['correct_rate_among_assessable'], 2/3)
        self.assertEqual(metric['equal_family_task_mean_correct_rate'], .5)

    def test_report_tampering_and_yaml_code_rejected(self):
        report = self.run_arm().model_dump(); report['results'][0]['trace_sha256'] = '0' * 64
        with self.assertRaises(ValueError): c.Report.model_validate(report)
        path = self.root / 'unsafe.yaml'; path.write_text('!!python/object/apply:os.system ["touch bad"]')
        with self.assertRaises(Exception): c.load_pack(path)
        config = c.load_pack(self.root / 'base.yaml').model_dump(); config['criteria'][0]['grader']['id'] = 'os.system'
        with self.assertRaises(ValueError): c.Pack.model_validate(config)

    def test_no_overwrite_and_fixture_provenance_boundary(self):
        output = self.root / 'report'; c.write_report(output, {'a.txt': 'original'})
        with self.assertRaises(ValueError): c.write_report(output, {'a.txt': 'replacement'})
        self.assertEqual((output / 'a.txt').read_text(), 'original')
        def change(rows, pack, saved, registry): saved['origin'] = 'recorded_model'
        with self.assertRaisesRegex(ValueError, 'provenance'): self.run_arm(mutate=change)

    def test_provider_neutral_requests_keep_rule_source_binding(self):
        rows = c.read(self.root / 'baseline.json'); pack = c.load_pack(self.root / 'custom.yaml')
        requests = c.requests(rows, pack)
        self.assertEqual(len(requests), 8 * 7)
        self.assertNotIn('ticket_identity', {r['criterion_id'] for r in requests})
        self.assertEqual(requests[0]['trace_sha256'], digest(requests[0]['trace']))
        self.assertEqual(requests[0]['criterion_sha256'], digest(requests[0]['criterion']))
        self.assertIn('response_schema', requests[0])

    def test_invalid_measurement_signature_rejected(self):
        report = self.run_arm(kind='custom').model_dump()
        report['grader_signatures']['ticket_identity']['kind'] = 'saved_llm'
        with self.assertRaises(ValueError): c.Report.model_validate(report)

    def test_structural_argument_check_is_not_semantic_success(self):
        trace = self.run_arm().traces[0]
        registry = g.default_registry()
        decision = registry.evaluate('argument_fields', trace, c.Criterion(id='argument_shape',
            definition=c.Definition(version='1', title='Fields only', question='Present?', applicability='Has calls', rubric='Check presence only', target='assistant_behavior'),
            grader=c.Grader(kind='deterministic', id='argument_fields', parameters={'required_fields': ['ticket_id', 'team']})))
        self.assertEqual(decision.verdict, 'correct')
        self.assertIn('not semantically verified', decision.explanation)

    def test_separate_intervention_prompt_can_change_without_task_change(self):
        reports = [self.run_arm().model_dump(), self.run_arm('candidate').model_dump()]
        for index, report in enumerate(reports):
            for trace in report['traces']:
                trace['messages'].append({'step_id': 'agent-prompt', 'role': 'system', 'content': 'Agent style ' + str(index),
                    'reasoning': None, 'tool_calls': [], 'tool_call_id': None, 'function': None})
                trace['metadata']['message_sources']['agent-prompt'] = 'intervention'
            for cell in report['results']:
                trace = next(t for t in report['traces'] if t['id'] == cell['trace_id']); cell['trace_sha256'] = digest(trace)
        self.assertEqual(c.compare(*reports)['status'], 'descriptive_paired')

    def test_documented_module_cli_accepts_saved_and_plugin_decisions(self):
        output = self.root / 'cli-custom'
        result = subprocess.run([sys.executable, '-m', 'agent_improvement_lab.criteria', 'evaluate',
            '--traces', str(self.root / 'candidate.json'), '--config', str(self.root / 'custom.yaml'),
            '--assessments', str(self.root / 'custom-candidate-assessments.json'), '--plugin', str(self.root / 'ticket_plugin.py'),
            '--output', str(output)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = c.Report.model_validate(c.read(output / 'report.json'))
        self.assertEqual(sum(cell.status == 'grader_error' for cell in report.results), 0)
        self.assertEqual(c.summaries(report)['heldout']['ticket_identity']['incorrect'], 2)
        self.assertEqual(c.summaries(report)['heldout']['reply_grounding']['incorrect'], 4)


if __name__ == '__main__':
    unittest.main()
