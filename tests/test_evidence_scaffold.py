"""Evidence provenance, provider projection and bounded two-phase fixture execution."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from agent_improvement_lab import experiment as ex
from agent_improvement_lab import evidence_scaffold as ef

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('evidence_fixture', ROOT / 'examples/routing-experiment/build_demo.py')
demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(demo)


def plan(quote, next_step='ask_user', missing=('action_permission',)):
    return {'observations': [{'step_id': 's001', 'quote': quote}],
        'missing_fields': list(missing), 'next_step': next_step,
        'reason': 'The recorded symptom alone does not establish a cause.'}


class EvidenceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{'role': 'system', 'content': 'rules'},
            {'role': 'user', 'content': 'Download is disabled. I can view the board.'},
            {'role': 'assistant', 'content': 'My unsupported diagnosis.'},
            {'role': 'tool', 'content': '{"status":"OK","history":[]}'}]
        self.snapshot = ef.visible_evidence(self.messages)
        self.plan = plan('Download is disabled.')

    def test_only_observed_user_tool_text_can_be_quoted(self):
        self.assertEqual([m['step_id'] for m in self.snapshot], ['s001', 's003'])
        self.assertEqual(ef.validate_plan(json.dumps(self.plan), self.snapshot), self.plan)
        for ref in ['s000', 's002', 'missing']:
            p = copy.deepcopy(self.plan); p['observations'][0]['step_id'] = ref
            with self.assertRaises(ValueError): ef.validate_plan(json.dumps(p), self.snapshot)
        self.assertNotIn('unsupported diagnosis', json.dumps(self.snapshot))

    def test_foreign_nested_fields_forged_quotes_and_duplicate_json_fail(self):
        mutations = [lambda p:p.update(expected_team='accounts'),
            lambda p:p['observations'][0].update(quote='The permission is denied.'),
            lambda p:p['observations'][0].update(assessor='private'),
            lambda p:p.update(reason={'secret':'metadata'}),
            lambda p:p.update(missing_fields=['expected_team']),
            lambda p:p.update(missing_fields=['own_role','own_role']),
            lambda p:p.update(next_step='refund'),
            lambda p:p['observations'].append(copy.deepcopy(p['observations'][0]))]
        for mutate in mutations:
            p = copy.deepcopy(self.plan); mutate(p)
            with self.assertRaises(ValueError): ef.validate_plan(json.dumps(p), self.snapshot)
        with self.assertRaises(ValueError): ef.validate_plan('{"reason":"a","reason":"b"}', self.snapshot)
        self.assertEqual(ef.validate_plan('```json\n'+json.dumps(self.plan)+'\n```', self.snapshot),self.plan)
        with self.assertRaises(ValueError):ef.validate_plan('```json\n'+json.dumps(self.plan)+'\n```\nExtra prose.',self.snapshot)

    def test_projection_preserves_dialogue_and_native_links_binds_observations(self):
        body = {'model':'fixture', 'messages':copy.deepcopy(self.messages), 'tools':[{'name':'route_ticket'}]}
        record = {'ordinal':1,'snapshot':self.snapshot,'raw_text':json.dumps(self.plan),
            'status':'validated_structure','plan':self.plan}
        before = copy.deepcopy(body); projected, proof = ef.project_plan(body, record)
        self.assertEqual(body, before); self.assertEqual(projected['messages'][1:], body['messages'][1:])
        self.assertEqual(projected['tools'], body['tools']); self.assertFalse(proof['additional_user_facts'])
        self.assertIn('untrusted data', projected['messages'][0]['content'])
        self.assertEqual(proof['effective_request_sha256'], ef.digest(projected))
        changed = copy.deepcopy(body); changed['messages'][1]['content'] += ' Updated observation.'
        with self.assertRaises(ValueError): ef.project_plan(changed, record)
        self.assertIs(ef.project_plan(body, None)[0], body)

    def test_rejected_analysis_never_projects_raw_or_invented_facts(self):
        body = {'messages':copy.deepcopy(self.messages)}
        record = {'ordinal':1,'snapshot':self.snapshot,'raw_text':'secret invalid invented denial',
            'status':'rejected_structure','plan':None}
        projected, proof = ef.project_plan(body, record)
        self.assertNotIn(record['raw_text'], json.dumps(projected))
        self.assertEqual(projected['messages'][1:], body['messages'][1:])
        self.assertEqual(proof['planner_status'], 'rejected_structure')
        record['status'] = 'human_accepted'
        with self.assertRaises(ValueError): ef.project_plan(body, record)


@unittest.skipUnless(importlib.util.find_spec('inspect_ai'), 'requires approved execution extra')
class EvidenceExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.config = demo.build(self.root / 'inputs')
        self.workspace = self.root / 'run'; self.settings = yaml.safe_load(self.config.read_text())
        self.settings['evidence_first'] = {'baseline':False, 'candidate':True}
        (self.root / 'inputs/baseline.txt').write_bytes((self.root / 'inputs/candidate.txt').read_bytes())
        self.cases = ex.read(self.root / 'inputs/cases.json'); self.scripts = {}; self.slots = []
        import random
        rng = random.Random(17)
        for case in self.cases:
            for repeat in (1,2):
                arms = ['baseline','candidate']; rng.shuffle(arms)
                for arm in arms: self.slots.append((case,arm))
        for n,(case,arm) in enumerate(self.slots,1):
            ticket = {'ticket_id':case['ticket_id']}; quote = case['user_request']
            actor = [demo.response('get_ticket',ticket),
                demo.response('ask_user',dict(ticket,field='action_permission',question='What does this action permission say?'))]
            if case == self.cases[0]:
                actor += [demo.response('route_ticket',dict(ticket,team='accounts'))]
            else:
                actor += [demo.response('ask_user',dict(ticket,field='financial_transaction',question='What does the receipt say?')),
                    demo.response('handoff_to_operator',ticket)]
            actor += [demo.response(text='The mock ticket has been assigned.')]
            script = []
            for i,response in enumerate(actor):
                if arm == 'candidate' and 1 <= i < len(actor)-1:
                    name = response['choices'][0]['message']['tool_calls'][0]['function']['name']
                    script.append(demo.response(text=json.dumps(plan(quote,name))))
                script.append(response)
            for i,response in enumerate(script):
                for call in response['choices'][0]['message'].get('tool_calls',[]): call['id'] = f'call-{n}-{i}'
            self.scripts[f's{n:04d}'] = script

    def execute(self):
        demo.dump(self.root / 'inputs/outputs.json', self.scripts)
        self.config.write_text(yaml.safe_dump(self.settings, sort_keys=False))
        prepared = ex.prepare(self.config,self.workspace)
        ex.approve(self.workspace,prepared['manifest_sha256'],'synthetic_fixture_review')
        state = ex.run(self.workspace)
        self.assertEqual((state['phase'],state['completed_slots'],state['http_starts']),('complete',8,0))
        return [ex.read(self.workspace / f'slots/s{n:04d}/result.json') for n in range(1,9)]

    def test_shared_call_budget_sidecars_and_actor_dialogue_provenance(self):
        for row in self.execute():
            t = row['trace']; m = t['metadata']; folder = self.workspace / 'slots' / t['id']
            candidate = row['slot']['arm'] == 'candidate'
            self.assertEqual(m['termination'],'final_after_assignment')
            self.assertEqual(m['generation_attempts'],m['planner_attempts']+m['actor_attempts'])
            self.assertLessEqual(m['generation_attempts'],8)
            self.assertEqual(m['planner_attempts'],(2 if row['slot']['task_id']==self.cases[0]['id'] else 3) if candidate else 0)
            self.assertFalse(any('model_generated_analysis' in str(message['content']) for message in t['messages']))
            for event in m['planner_events']:
                record = ex.read(folder / event['record'])
                self.assertEqual(ex.sha(folder / event['record']),event['record_sha256'])
                self.assertEqual(record['origin'],'model_generated_analysis')
                provider = ex.read(folder / f'provider-calls/{event["output_ordinal"]:04d}-request.json')
                self.assertNotIn('tools',provider); self.assertEqual(provider['messages'][-1]['role'],'user')
                self.assertNotIn('expected_team', json.dumps(provider))
                self.assertNotIn('The archive settings do not tell', json.dumps(provider) if event['ordinal']==1 else '')
            projections = list((folder / 'provider-calls').glob('*evidence-projection.json'))
            self.assertEqual(len(projections),m['planner_attempts'])
            for p in projections:
                proof = ex.read(p); request = ex.read(p.with_name(p.name.replace('evidence-projection','request')))
                self.assertFalse(proof['additional_user_facts']);self.assertEqual(request['messages'][-1]['role'],'tool')
            self.assertFalse((self.workspace/'requests.jsonl').exists())
        self.assertIn('preliminary model analyses',(self.workspace/'REPORT.md').read_text())
        before = ex.sha(self.workspace/'registry.json'); ex.finalize(self.workspace)
        self.assertEqual(ex.sha(self.workspace/'registry.json'),before)
        planner_files = sorted((self.workspace/'slots').glob('*/planner/*.json'))
        self.assertTrue(planner_files)
        boundary = ex.read(self.workspace/'finalization.json')['raw']
        for path in planner_files:
            self.assertEqual(boundary[str(path.relative_to(self.workspace))], ex.sha(path))
        planner_files[0].write_text(planner_files[0].read_text()+' ')
        with self.assertRaisesRegex(ValueError, '(completed|frozen execution) evidence changed'):
            ex.finalize(self.workspace)

    def test_invalid_plan_is_preserved_and_actor_uses_only_original_evidence(self):
        for n,(case,arm) in enumerate(self.slots,1):
            if arm=='candidate':self.scripts[f's{n:04d}'][1]=demo.response(text='made-up private extra facts')
        for row in self.execute():
            if row['slot']['arm']=='candidate':
                m=row['trace']['metadata'];event=m['planner_events'][0]
                self.assertEqual(event['status'],'rejected_structure');self.assertEqual(m['termination'],'final_after_assignment')
                request=ex.read(self.workspace/f'slots/{row["trace"]["id"]}/provider-calls/0003-request.json')
                self.assertNotIn('made-up private',json.dumps(request));self.assertIn('rejected',request['messages'][0]['content'])

    def test_planner_tool_call_is_never_executed(self):
        for n,(case,arm) in enumerate(self.slots,1):
            if arm=='candidate':self.scripts[f's{n:04d}'][1]=demo.response('route_ticket',{'ticket_id':'forged','team':'general'})
        for row in self.execute():
            t=row['trace']
            if row['slot']['arm']=='candidate':
                self.assertEqual(t['metadata']['planner_events'][0]['error_code'],'PLANNER_TOOL_CALL_NOT_ALLOWED')
                self.assertFalse(any(c['arguments'].get('ticket_id')=='forged' for m in t['messages'] for c in m.get('tool_calls',[])))

    def test_generation_cap_includes_planning_without_silent_expansion(self):
        self.settings['limits']['max_generations']=3
        for row in self.execute():
            m=row['trace']['metadata'];self.assertEqual(m['generation_attempts'],3)
            self.assertEqual(m['termination'],'turn_budget')
            self.assertEqual(m['planner_attempts'],1 if row['slot']['arm']=='candidate' else 0)

    def test_configuration_is_strict_and_distinct_from_recovery(self):
        for value in [1,'true',None]:
            with self.assertRaises(ValueError):ex.Config.model_validate(dict(self.settings,evidence_first={'baseline':False,'candidate':value}))
        with self.assertRaises(ValueError):ex.Config.model_validate(dict(self.settings,evidence_first={'candidate':True}))
        with self.assertRaises(ValueError):ex.Config.model_validate(dict(self.settings,zero_call_recovery={'baseline':0,'candidate':1}))

    def test_guarded_budget_rejection_retains_raw_and_actor_can_complete(self):
        self.settings['planner_state_guard'] = {'baseline': False, 'candidate': True}
        for n, (case, arm) in enumerate(self.slots, 1):
            if arm == 'candidate' and case != self.cases[0]:
                self.scripts[f's{n:04d}'][5] = demo.response(text=json.dumps(plan(case['user_request'], 'ask_user')))
        rejected = 0
        for row in self.execute():
            if row['slot']['arm'] != 'candidate': continue
            trace = row['trace']; folder = self.workspace / 'slots' / trace['id']
            self.assertTrue(trace['metadata']['planner_state_guard'])
            self.assertEqual(trace['metadata']['termination'], 'final_after_assignment')
            for event in trace['metadata']['planner_events']:
                record = ex.read(folder / event['record'])
                self.assertEqual(record['version'], ef.GUARDED_VERSION)
                if event['status'] == 'rejected_state':
                    rejected += 1
                    self.assertEqual(record['observed_state']['remaining_clarifications'], 0)
                    request = ex.read(folder / f'provider-calls/{event["output_ordinal"]+1:04d}-request.json')
                    self.assertNotIn(record['raw_text'], request['messages'][0]['content'])
                    self.assertEqual(record['error_code'], 'CLARIFICATION_BUDGET_EXHAUSTED')
        self.assertEqual(rejected, 2)

    def test_state_guard_config_cannot_enable_without_planner_or_nonboolean_values(self):
        for values in ({'candidate': True}, {'baseline': True, 'candidate': False},
                       {'baseline': False, 'candidate': 1}, {'baseline': False, 'candidate': 'true'}):
            with self.assertRaises(ValueError): ex.Config.model_validate(dict(self.settings, planner_state_guard=values))

    def test_explicit_stop_marker_blocks_new_request_reservations(self):
        import time
        ex.prepare(self.config,self.workspace)
        ex.once(self.workspace/'execution.json',{'started':time.time(),'deadline':time.time()+60,'memory_baseline':None})
        budget=ex.Budget(self.workspace)
        ex.once(self.workspace/'stop.json',{'reason':'fixture outer stop'})
        with self.assertRaisesRegex(ex.ExecutionStop,'stop marker'):
            budget.reserve({'model':'fixture'},'s0001')
        self.assertFalse((self.workspace/'requests.jsonl').exists())


if __name__=='__main__': unittest.main()
