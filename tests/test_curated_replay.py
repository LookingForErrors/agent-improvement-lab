"""Authored replay remains reproducible and separate from recorded model evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('curated_replay', ROOT / 'web_demo/build_replay.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class CuratedReplayTests(unittest.TestCase):
    def test_rebuild_checks_each_authored_trace_and_preserves_source_evidence(self):
        original = replay.read(ROOT / 'web_demo/family-experiment.json')
        source_sha = replay.file_sha(ROOT / 'web_demo/family-experiment.json')
        with tempfile.TemporaryDirectory() as directory:
            binding, raw = replay.build(ROOT / 'web_demo', ROOT / 'web_demo/replay-plan.json', directory)
            self.assertEqual(len(raw), 134)
            self.assertEqual(binding['recorded_traces'], 370)
            self.assertEqual(binding['counts']['baseline'], {'correct':148,'incorrect':103,'unassessable':1})
            self.assertEqual(binding['counts']['candidate'], {'correct':218,'incorrect':29,'unassessable':5})
            self.assertEqual(binding['transitions']['unknown'], 6)
            for name in binding['files']:
                self.assertEqual(replay.file_sha(Path(directory) / name), replay.file_sha(ROOT / 'web_demo' / name))
            data = replay.read(Path(directory) / 'curated-experiment.json')
            self.assertFalse(data['statistics']['inferential_result'])
            self.assertFalse(data['decision']['model_improvement_claim'])
            self.assertNotIn('bootstrap', data['statistics'])
            self.assertFalse(data['provenance']['human_gold'])
            source = {t['id']:t for t in original['traces']}
            for trace in data['traces']:
                if trace.get('source_kind') != 'authored_fixture':
                    self.assertEqual(trace, source[trace['id']])
            index = {t['id']:t for t in data['traces']}
            for cell in data['results']:
                trace = index[cell['trace_id']]
                self.assertEqual(cell['trace_sha256'],trace['source_sha256'])
                self.assertLessEqual(set(cell['evidence_steps']),{m['step_id'] for m in trace['messages']})
        self.assertEqual(replay.file_sha(ROOT / 'web_demo/family-experiment.json'), source_sha)

    def test_changed_source_and_unbound_adjudication_are_rejected(self):
        plan = replay.read(ROOT / 'web_demo/replay-plan.json')
        for mutation in [lambda p:p['source_files'].update({'story-data.json':'0'*64}),
                         lambda p:p['semantic_overlays'][0].update(trace_sha256='0'*64),
                         lambda p:p['semantic_overlays'][0].update(evidence_steps=['invented'])]:
            bad = copy.deepcopy(plan); mutation(bad)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'bad-plan.json'; path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):replay.build(ROOT / 'web_demo',path,Path(directory)/'out')

    def test_authored_success_does_not_bypass_real_mechanical_checks(self):
        plan = replay.read(ROOT / 'web_demo/replay-plan.json')
        bad = copy.deepcopy(plan)
        case = bad['authored_pairs'][0]
        case['reference']['expected_team'] = 'technical'
        case['reference']['routing_paths'] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad-plan.json'; path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):replay.build(ROOT / 'web_demo',path,Path(directory)/'out')

    def test_authored_questions_request_still_missing_facts(self):
        plan = replay.read(ROOT / 'web_demo/replay-plan.json')
        for item in plan['authored_pairs']:
            reference = item['reference']
            for arm in ['baseline', 'candidate']:
                trace, _ = replay.authored_trace(item['case'], reference, 'Mock rules', arm)
                known = dict(reference['initial_facts'])
                for message in trace.messages:
                    for call in message.tool_calls:
                        if call.name == 'ask_user':
                            field = call.arguments['field']
                            self.assertEqual(known.get(field, 'unknown'), 'unknown',trace.id+' '+field)
                            known[field] = reference['answers'][field]['state']


if __name__ == '__main__':
    unittest.main()
