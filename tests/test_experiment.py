"""Execution contracts with prescribed Inspect outputs; no real provider calls."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import urllib.error
import yaml

from agent_improvement_lab import experiment as ex

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('experiment_demo', ROOT / 'examples/routing-experiment/build_demo.py')
demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(demo)


@unittest.skipUnless(importlib.util.find_spec('inspect_ai'), 'requires the execution extra')
class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.config = demo.build(self.root / 'inputs'); self.workspace = self.root / 'run'

    def edit_config(self, **values):
        config = yaml.safe_load(self.config.read_text()); config.update(values)
        self.config.write_text(yaml.safe_dump(config, sort_keys=False))

    def prepared(self):
        return ex.prepare(self.config, self.workspace)

    def approved(self):
        state = self.prepared(); return ex.approve(self.workspace, state['manifest_sha256'], 'synthetic_test_fixture')

    def test_worker_exit_race_is_reaped(self):
        process = Mock(pid=123, returncode=0)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('worker', 2), 0]
        with patch.object(ex.os, 'killpg', side_effect=ProcessLookupError):
            ex.terminate_worker(process)
        self.assertEqual(process.wait.call_count, 2)

    def test_confirmatory_primary_ids_and_native_heldout_execution(self):
        cases=ex.read(self.config.parent/'cases.json')
        for case in cases:case['split']='heldout'
        (self.config.parent/'cases.json').write_text(json.dumps(cases))
        rule=ex.read(self.config.parent/'rule.json');rule.update(evaluation_split='heldout',expected_pairs=2)
        (self.config.parent/'rule.json').write_text(json.dumps(rule))
        protocol={'format':'prospective-family-comparison-v1','primary_repeat':1,'semantic_contract':'routing-task-outcome-v2'}
        (self.config.parent/'protocol.json').write_text(json.dumps(protocol))
        import random
        scripts={};rng=random.Random(17)
        for case in cases:
            arms=['baseline','candidate'];rng.shuffle(arms)
            for arm in arms:
                ident=f'h01-s{len(scripts)+1:04d}';ticket={'ticket_id':case['ticket_id']}
                script=[demo.response('get_ticket',ticket),demo.response('ask_user',dict(ticket,field='action_permission',question='What do the permissions explicitly say for this action?'))]
                if case==cases[0]:script.append(demo.response('route_ticket',dict(ticket,team='accounts')))
                else:script += [demo.response('ask_user',dict(ticket,field='financial_transaction',question='What does the affected receipt say?')),demo.response('handoff_to_operator',ticket)]
                script.append(demo.response(text='The ticket was assigned.'))
                for i,value in enumerate(script):
                    for call in value['choices'][0]['message'].get('tool_calls',[]):call['id']+='-'+str(i)
                scripts[ident]=script
        (self.config.parent/'outputs.json').write_text(json.dumps(scripts))
        cutoff=time.time()+120
        self.edit_config(study_mode='confirmatory',repeats=1,repeat_start=1,run_id_prefix='h01-',confirmatory_protocol='protocol.json',campaign_deadline_unix=cutoff,action_gate={'baseline':False,'candidate':True})
        self.approved();state=ex.run(self.workspace)
        self.assertEqual(state['completed_slots'],4)
        self.assertTrue(all(s['repeat_id']=='1' and s['run_id'].startswith('h01-') for s in ex.read(self.workspace/'slots.json')))
        self.assertIn('inputs/confirmatory_protocol',ex.read(self.workspace/'manifest.json')['sealed'])
        self.assertEqual(ex.read(self.workspace/'execution.json')['deadline'],cutoff)
        for file in (self.workspace/'slots').glob('*/result.json'):
            self.assertEqual(ex.read(file)['trace']['split'],'heldout')
            self.assertEqual(ex.read(file)['trace']['metadata']['trajectory_completeness'],'complete')

    def test_action_gate_native_fixture_preserves_projections_and_blocks_premature_call(self):
        self.edit_config(action_gate={'baseline':False,'candidate':True})
        import random
        arms=['baseline','candidate'];random.Random(17).shuffle(arms)
        ident='s0001' if arms[0]=='candidate' else 's0002'
        case=ex.read(self.config.parent/'cases.json')[0]
        scripts=ex.read(self.config.parent/'outputs.json')
        early=demo.response('route_ticket',{'ticket_id':case['ticket_id'],'team':'accounts'})
        early['choices'][0]['message']['tool_calls'][0]['id']='fixture-premature-route'
        scripts[ident].insert(0,early)
        (self.config.parent/'outputs.json').write_text(json.dumps(scripts))
        self.approved();state=ex.run(self.workspace)
        self.assertEqual(state['completed_slots'],8)
        candidate=next(s for s in ex.read(self.workspace/'slots.json') if s['arm']=='candidate')
        folder=self.workspace/'slots'/candidate['run_id']
        first=ex.read(folder/'provider-calls/0001-gate-projection.json')
        request=ex.read(folder/'provider-calls/0001-request.json')
        self.assertEqual(first['allowed_tools'],['get_ticket'])
        self.assertEqual([t['function']['name'] for t in request['tools']],['get_ticket'])
        self.assertEqual(request['tool_choice'],'required')
        self.assertEqual(ex.read(folder/'result.json')['trace']['metadata']['action_gate'],True)
        # A fixture that ignores exposure must have an explicit control rejection.
        trace=ex.read(self.workspace/'slots'/ident/'result.json')['trace']
        rejected=[m for m in trace['messages'] if m['role']=='tool' and 'ACTION_GATE_REJECTED' in m['content']]
        self.assertEqual(len(rejected),1)
        self.assertFalse(json.loads(rejected[0]['content'])['tool_executed'])
        self.assertEqual(rejected[0]['tool_call_id'],'fixture-premature-route')

    def test_exited_owner_network_descendant_is_terminated(self):
        marker = self.root / 'descendant-heartbeat'
        child_code = "from pathlib import Path; import sys,time; p=Path(sys.argv[1]);\nwhile True: p.write_text(str(time.time())); time.sleep(.03)"
        owner_code = "import subprocess,sys,time,os; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); time.sleep(.2); os._exit(75)"
        owner = subprocess.Popen([sys.executable, '-c', owner_code, child_code, str(marker)], start_new_session=True)
        self.addCleanup(lambda: ex.terminate_worker(owner))
        owner.wait(timeout=3); self.assertEqual(owner.returncode, 75)
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline: time.sleep(.01)
        self.assertTrue(marker.exists())
        ex.terminate_worker(owner); time.sleep(.1)
        before = marker.read_text(); time.sleep(.1)
        self.assertEqual(marker.read_text(), before)

    def test_supplement_retains_terminal_failures_and_fills_only_technical_gap(self):
        self.edit_config(limits={'wall_s':60,'request_s':2,'sample_s':10})
        self.approved(); original = subprocess.Popen; dispatched = []
        def failed_first(command, **kwargs):
            dispatched.append(command)
            if len(dispatched) == 1:
                command = [sys.executable, '-B', '-c', 'raise SystemExit(75)']
            return original(command, **kwargs)
        # Supplement eligibility is independent of cold worker startup speed.
        with patch.object(ex.subprocess, 'Popen', side_effect=failed_first):
            source_result = ex.run(self.workspace)
        self.assertEqual(source_result['completed_slots'], 7)
        self.assertEqual(ex.read(self.workspace / 'slots/s0001/failure.json')['reason'],
                         'worker exited unsuccessfully')
        frozen = {str(f.relative_to(self.workspace)): ex.sha(f) for f in self.workspace.rglob('*') if f.is_file()}
        supplement = self.root / 'supplement'
        state = ex.prepare_supplement(self.config, supplement, self.workspace)
        lineage = ex.read(supplement / 'retained-source.json')
        self.assertEqual([r['slot']['run_id'] for r in lineage['eligible']], ['s0001'])
        self.assertEqual(len(lineage['retained']), 7)
        for row in lineage['retained']:
            rel = 'slots/' + row['slot']['run_id'] + '/result.json'
            self.assertEqual(ex.sha(supplement / rel), ex.sha(self.workspace / rel))
        ex.approve(supplement, state['manifest_sha256'], 'synthetic_test_fixture')
        with patch.object(ex.subprocess, 'Popen', wraps=original) as dispatch:
            result = ex.run(supplement)
        self.assertEqual(dispatch.call_count, 1); self.assertEqual(result['completed_slots'], 8)
        self.assertEqual(frozen, {str(f.relative_to(self.workspace)):ex.sha(f) for f in self.workspace.rglob('*') if f.is_file()})
        retained = supplement / 'slots/s0002/result.json'; retained.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'sealed input changed'): ex.verify(supplement)

    def test_one_sample_timeout_retains_unknown_and_continues_later_slots(self):
        self.edit_config(limits={'wall_s':30,'request_s':3,'sample_s':3})
        self.approved(); original = subprocess.Popen; dispatched = []
        def one_stalled_worker(command, **kwargs):
            dispatched.append(command)
            if len(dispatched)==1:
                command=[sys.executable,'-B','-c','import time; time.sleep(8)']
            return original(command,**kwargs)
        with patch.object(ex.subprocess,'Popen',side_effect=one_stalled_worker):
            state=ex.run(self.workspace)
        self.assertEqual((state['phase'],state['completed_slots'],state['http_starts']),('complete',7,0))
        self.assertEqual(len(dispatched),8)
        self.assertFalse((self.workspace/'stop.json').exists())
        failure=ex.read(self.workspace/'slots/s0001/failure.json')
        self.assertEqual((failure['stop_scope'],failure['reason']),('sample','sample wall budget exhausted'))
        self.assertEqual(ex.read(self.workspace/'result.json')['decision'],'inconclusive_measurement')
        baseline=[json.loads(s) for s in (self.workspace/'baseline-traces.jsonl').read_text().splitlines()]
        candidate=[json.loads(s) for s in (self.workspace/'candidate-traces.jsonl').read_text().splitlines()]
        self.assertEqual(sum(t['provenance']=='missing_measurement_placeholder' for t in baseline+candidate),1)

    def test_complete_native_tool_execution_and_idempotent_report(self):
        self.approved(); result = ex.run(self.workspace)
        self.assertEqual((result['phase'], result['decision'], result['http_starts']), ('complete', 'advance_to_larger_test', 0))
        self.assertEqual(result['completed_slots'], 8)
        left = ex.read(self.workspace / 'baseline-criteria.json'); right = ex.read(self.workspace / 'candidate-criteria.json')
        self.assertEqual(sum(c['verdict'] == 'correct' for c in left['results'] if c['criterion_id'] == 'routed_terminal'), 3)
        self.assertEqual(sum(c['verdict'] == 'correct' for c in right['results'] if c['criterion_id'] == 'routed_terminal'), 4)
        self.assertTrue(all(t['provenance'] == 'scripted_model_outputs_mock_tools' for t in left['traces'] + right['traces']))
        self.assertFalse(ex.read(self.workspace / 'result.json')['statistical_effect_established'])
        self.assertEqual(ex.read(self.workspace / 'uncertainty.json')['working']['routed_terminal']['status'], 'insufficient_families')
        before = ex.sha(self.workspace / 'registry.json')
        self.assertEqual(ex.run(self.workspace, resume=True)['completed_slots'], 8)
        ex.finalize(self.workspace)
        self.assertEqual(ex.sha(self.workspace / 'registry.json'), before)
        self.assertFalse((self.workspace / 'requests.jsonl').exists())
        raw = next((self.workspace / 'slots').glob('*/provider-calls/*-response.json'))
        raw.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'completed evidence'): ex.status(self.workspace)

    def test_parallel_fixture_scheduler_stays_within_four_and_replays_nothing(self):
        self.edit_config(parallel_workers=4); self.approved(); original = subprocess.Popen; observed = []; peak = []
        def counted(command, **kwargs):
            process = original(command, **kwargs); observed.append(process)
            peak.append(sum(p.poll() is None for p in observed)); return process
        with patch.object(ex.subprocess, 'Popen', side_effect=counted):
            result = ex.run(self.workspace)
        self.assertEqual(result['completed_slots'], 8)
        self.assertLessEqual(max(peak), 4); self.assertGreater(max(peak), 1)
        self.assertTrue(all(p.poll() is not None for p in observed))
        with patch.object(ex.subprocess, 'Popen', side_effect=AssertionError('no replay')):
            ex.run(self.workspace, resume=True)

    def test_remote_budget_is_explicit_and_exact_model_endpoint_required(self):
        config = yaml.safe_load(self.config.read_text())
        config.update(backend='polza', fixture_outputs=None, endpoint='https://polza.ai/api/v1/chat/completions',
            model_id='mistralai/ministral-14b-2512', model_version='mistralai/ministral-14b-2512',
            polza_cap_rub=100.0, parallel_workers=4, limits={'wall_s':30, 'sample_s':20, 'request_s':10})
        self.assertEqual(ex.Config.model_validate(config).backend, 'polza')
        for mutation in [{'polza_cap_rub':None}, {'polza_cap_rub':200.0}, {'endpoint':'https://other.example/api/v1/chat/completions'},
                         {'model_id':'another-model'}, {'repeats':3}, {'measurement_contract':'legacy-v1'}]:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                ex.Config.model_validate({**config, **mutation})

    def test_interrupted_checkpoint_keeps_authenticated_prefix(self):
        self.approved(); slot = ex.read(self.workspace / 'slots.json')[0]
        case = next(c for c in ex.read(self.workspace / 'inputs/cases') if c['id'] == slot['task_id'])
        folder = self.workspace / 'slots' / slot['run_id']; folder.mkdir(parents=True)
        system = ((self.workspace / ('inputs/' + slot['arm'] + '_prompt')).read_text()
                  + '\n' + (self.workspace / 'inputs/common').read_text() + '\nCurrent ticket: ' + case['ticket_id'])
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': case['user_request']},
                    {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'function': 'get_ticket', 'arguments': {'ticket_id': case['ticket_id']}}]},
                    {'role': 'tool', 'content': json.dumps({'status': 'OK', 'ticket_id': case['ticket_id']}), 'tool_call_id': 'c1', 'function': 'get_ticket'}]
        ex.atomic(folder / 'started.json', {'slot': slot})
        checkpoint = {'messages': messages, 'calls': [{}], 'logical_epoch': int(slot['repeat_id']),
                      'execution_identity': {'run_id': slot['run_id'], 'task_id': slot['task_id'], 'arm': slot['arm']},
                      'scaffold_tool_control': slot['tool_control'], 'evidence_first': slot['evidence_first'],
                      'zero_call_recovery_limit': slot['zero_call_recovery']}
        ex.atomic(folder / 'actor-checkpoint.json', checkpoint)
        retained = ex.interrupted_trace(self.workspace, slot)
        self.assertEqual(len(retained['messages']), 4)
        self.assertEqual(retained['metadata']['trajectory_completeness'], 'partial')
        self.assertEqual(retained['metadata']['extraction_binding'], 'explicit_identity_and_inputs')
        self.assertEqual(retained['messages'][-1]['tool_call_id'], 'c1')
        self.assertNotEqual(retained['provenance'], 'missing_measurement_placeholder')
        for identity in (None, {'run_id': 'foreign', 'task_id': slot['task_id'], 'arm': slot['arm']}):
            changed = dict(checkpoint, execution_identity=identity)
            ex.atomic(folder / 'actor-checkpoint.json', changed)
            self.assertEqual(ex.interrupted_trace(self.workspace, slot)['provenance'], 'missing_measurement_placeholder')
        missing = dict(checkpoint); missing.pop('execution_identity')
        ex.atomic(folder / 'actor-checkpoint.json', missing)
        self.assertEqual(ex.interrupted_trace(self.workspace, slot)['provenance'], 'missing_measurement_placeholder')
        checkpoint['messages'][1]['content'] = 'Another user objective'
        ex.atomic(folder / 'actor-checkpoint.json', checkpoint)
        rejected = ex.interrupted_trace(self.workspace, slot)
        self.assertEqual(rejected['provenance'], 'missing_measurement_placeholder')
        self.assertIn('binding mismatch', rejected['metadata']['reason'])

    def test_negative_result_is_terminal_and_required_is_only_a_request(self):
        self.edit_config(tool_control={'baseline': 'auto', 'candidate': 'required'})
        self.prepared(); slots = ex.read(self.workspace / 'slots.json')
        # Mutate the source fixture, then use a NEW workspace/seal.
        scripts = ex.read(self.root / 'inputs/outputs.json')
        pair = [s for s in slots if s['task_id'] == slots[0]['task_id'] and s['repeat_id'] == '1']
        base = next(s for s in pair if s['arm'] == 'baseline'); cand = next(s for s in pair if s['arm'] == 'candidate')
        scripts[base['run_id']] = scripts[cand['run_id']]
        scripts[cand['run_id']] = [scripts[cand['run_id']][0], demo.response(text='Please clarify the permission.')]
        demo.dump(self.root / 'inputs/outputs.json', scripts); self.workspace = self.root / 'negative'
        self.approved(); result = ex.run(self.workspace)
        self.assertEqual((result['phase'], result['decision']), ('complete', 'do_not_adopt'))
        request = ex.read(self.workspace / 'slots' / cand['run_id'] / 'provider-calls/0002-request.json')
        self.assertEqual(request['tool_choice'], 'required')
        self.assertEqual(ex.read(self.workspace / 'slots' / cand['run_id'] / 'result.json')['trace']['metadata']['termination'], 'final_without_assignment')

    def test_stale_approval_rejection_and_provenance_boundaries(self):
        state = self.prepared()
        for identity, reviewer in [('0'*64, 'reviewer'), (state['manifest_sha256'], '')]:
            with self.assertRaises(ValueError): ex.approve(self.workspace, identity, reviewer)
        self.assertFalse((self.workspace / 'approval.json').exists())
        self.assertEqual(ex.approve(self.workspace, state['manifest_sha256'], 'synthetic_test_fixture', True)['phase'], 'rejected')
        with self.assertRaises(ValueError): ex.run(self.workspace)
        self.assertFalse((self.workspace / 'execution.json').exists())
        self.workspace = self.root / 'wrong-kind'
        pack = yaml.safe_load((self.root / 'inputs/criteria.yml').read_text()); pack['data_kind'] = 'recorded_evaluation'
        (self.root / 'inputs/criteria.yml').write_text(yaml.safe_dump(pack))
        with self.assertRaisesRegex(ValueError, 'provenance'): self.prepared()
        self.assertFalse(self.workspace.exists())

    def test_changed_input_runtime_and_completed_evidence_fail_closed(self):
        self.prepared(); prompt = self.workspace / 'inputs/candidate_prompt'; original = prompt.read_text()
        prompt.write_text(original + '\nchanged')
        with self.assertRaisesRegex(ValueError, 'sealed input'): ex.status(self.workspace)
        prompt.write_text(original)
        with patch.object(ex.md, 'version', return_value='changed'):
            with self.assertRaisesRegex(ValueError, 'runtime'): ex.status(self.workspace)

    def test_expired_resume_retains_original_deadline_and_missing_pairs(self):
        self.approved(); deadline = time.time()-1
        ex.once(self.workspace / 'execution.json', {'started': deadline-10, 'deadline': deadline, 'memory_baseline': None})
        state = ex.read(self.workspace / 'state.json'); ex.atomic(self.workspace / 'state.json', dict(state, phase='running'))
        result = ex.run(self.workspace, resume=True)
        self.assertEqual(result['decision'], 'inconclusive_measurement')
        self.assertEqual(ex.read(self.workspace / 'execution.json')['deadline'], deadline)
        self.assertEqual(result['completed_slots'], 0)
        self.assertEqual(ex.read(self.workspace / 'comparison.json')['splits']['working']['routed_terminal']['excluded_pairs'], 4)
        (self.workspace / 'comparison.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'completed evidence'): ex.status(self.workspace)

    def test_parent_crash_never_reexecutes_started_slot(self):
        scripts = ex.read(self.root / 'inputs/outputs.json'); scripts['s0001'][0]['fixture_delay_seconds'] = 5
        demo.dump(self.root / 'inputs/outputs.json', scripts); self.approved()
        process = subprocess.Popen([sys.executable, '-m', 'agent_improvement_lab.workflow_cli', 'experiment', 'run',
            '--workspace', str(self.workspace)], cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        checkpoint = self.workspace / 'slots/s0001/actor-checkpoint.json'; deadline = time.time()+20
        while not checkpoint.exists() and time.time() < deadline: time.sleep(.05)
        self.assertTrue(checkpoint.exists()); execution = ex.read(self.workspace / 'execution.json')
        process.kill(); process.wait(timeout=3)
        stopped = self.workspace / 'slots/s0001/worker-stop.json'
        while not stopped.exists() and time.time() < deadline: time.sleep(.05)
        self.assertTrue(stopped.exists())
        result = ex.run(self.workspace, resume=True)
        self.assertEqual((result['phase'], result['decision'], result['completed_slots']), ('complete', 'inconclusive_measurement', 7))
        self.assertEqual(ex.read(self.workspace / 'execution.json'), execution)
        self.assertEqual(len(list((self.workspace / 'slots/s0001/provider-calls').glob('*request.json'))), 1)
        self.assertFalse((self.workspace / 'slots/s0001/result.json').exists())

    def test_http_starts_reserved_before_failed_transport_and_no_retry(self):
        from agent_improvement_lab import experiment_inspect as actor
        self.edit_config(backend='local_http', fixture_outputs=None, limits={'wall_s': 300, 'sample_s': 60, 'request_s': 30, 'http_starts': 2})
        pack = yaml.safe_load((self.root / 'inputs/criteria.yml').read_text()); pack['data_kind'] = 'recorded_evaluation'
        (self.root / 'inputs/criteria.yml').write_text(yaml.safe_dump(pack))
        evidence = ex.read(self.root / 'inputs/evidence.json'); evidence['data_kind'] = 'recorded_evaluation'
        source = ex.read(self.root / 'inputs/evidence-traces.json'); source[0]['provenance'] = 'live_local_llm_mock_tools'
        evidence['issues'][0]['trace_sha256'] = ex.criteria.digest(source[0])
        demo.dump(self.root / 'inputs/evidence.json', evidence); demo.dump(self.root / 'inputs/evidence-traces.json', source)
        self.approved(); ex.once(self.workspace / 'execution.json', {'started': time.time(), 'deadline': time.time()+300, 'memory_baseline': {'available': 10*2**30, 'swap': 0}})
        budget = ex.Budget(self.workspace); folder = self.workspace / 'transport-unit'
        actor.CONTEXT = {'budget': budget, 'settings': budget.settings, 'slot': {'run_id': 'transport-unit'}, 'folder': folder, 'calls': 0}
        model = actor.ExperimentModel(model_name='test-model')
        cfg = actor.GenerateConfig(max_tokens=256, timeout=30)
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                if len((budget.workspace / 'requests.jsonl').read_text().splitlines()) != self.calls:
                    raise AssertionError('reservation must precede send')
                from io import BytesIO
                raise urllib.error.HTTPError(request.full_url, 500, 'fixture error', {}, BytesIO(b'fixture failure'))
        opener = Opener()
        with patch.object(budget, 'check'), patch('urllib.request.build_opener', return_value=opener):
            for _ in range(2):
                result, _ = asyncio.run(model.generate([], [], 'auto', cfg)); self.assertIsInstance(result, RuntimeError)
            with self.assertRaises(ex.ExecutionStop): asyncio.run(model.generate([], [], 'auto', cfg))
        self.assertEqual(opener.calls, 2)
        self.assertEqual(len((self.workspace / 'requests.jsonl').read_text().splitlines()), 2)
        # Mocked transport only: finalize unknown slots and verify ledger tampering.
        ex.atomic(self.workspace / 'state.json', dict(ex.read(self.workspace / 'state.json'), phase='running'))
        ex.finalize(self.workspace)
        (self.workspace / 'requests.jsonl').write_text('')
        with self.assertRaisesRegex(ValueError, 'completed evidence'): ex.status(self.workspace)

    def test_crash_during_report_resumes_only_finalization(self):
        self.approved(); now = time.time()
        ex.once(self.workspace / 'execution.json', {'started': now, 'deadline': now+300, 'memory_baseline': None})
        ex.atomic(self.workspace / 'state.json', dict(ex.read(self.workspace / 'state.json'), phase='running'))
        real_once = ex.once
        def crash(path, value):
            real_once(path, value)
            if Path(path).name == 'baseline-criteria.json': raise RuntimeError('fixture crash during publication')
        with patch.object(ex, 'once', side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, 'publication'): ex.finalize(self.workspace)
        before = ex.sha(self.workspace / 'baseline-criteria.json')
        with patch.object(ex.subprocess, 'Popen', side_effect=AssertionError('finalization must not execute')):
            result = ex.run(self.workspace, resume=True)
        self.assertEqual((result['phase'], result['decision'], result['completed_slots']), ('complete', 'inconclusive_measurement', 0))
        self.assertEqual(ex.sha(self.workspace / 'baseline-criteria.json'), before)

    def test_evidence_requires_working_source_binding_and_compatible_action(self):
        original = ex.read(self.root / 'inputs/evidence.json')
        for mutation in ('missing-split', 'duplicate', 'wrong-hash', 'scenario-defect'):
            evidence = json.loads(json.dumps(original))
            if mutation == 'missing-split': evidence['issues'][0].pop('split')
            elif mutation == 'duplicate': evidence['issues'].append(dict(evidence['issues'][0]))
            elif mutation == 'wrong-hash': evidence['issues'][0]['trace_sha256'] = '0'*64
            else: evidence['issues'][0]['kind'] = 'scenario_defect'
            demo.dump(self.root / 'inputs/evidence.json', evidence)
            with self.assertRaises(ValueError): self.prepared()
            self.assertFalse(self.workspace.exists())

    def test_no_remote_endpoint_or_hidden_plugin_import(self):
        self.edit_config(endpoint='http://example.com:1234/v1/chat/completions')
        with self.assertRaises(ValueError): self.prepared()
        self.edit_config(endpoint='http://127.0.0.1:1234/v1/chat/completions')
        pack = yaml.safe_load((self.root / 'inputs/criteria.yml').read_text()); pack['criteria'][0]['grader']['id'] = 'not_registered'
        (self.root / 'inputs/criteria.yml').write_text(yaml.safe_dump(pack))
        with self.assertRaisesRegex(ValueError, 'unknown deterministic'): self.prepared()
        self.assertFalse(self.workspace.exists())

    def test_selector_connects_existing_triage_and_plan_without_approval(self):
        spec = importlib.util.spec_from_file_location('replay_demo', ROOT / 'examples/routing-loop/build_demo.py')
        replay_demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(replay_demo)
        source = self.root / 'replay-inputs'; replay_demo.build(source)
        triage = ex.actions.prepare(ex.read(source / 'baseline.json'), ex.read(source / 'fixture-annotations.json'),
            ex.sha(source / 'rubric.txt'), 'routing-loop-fixture-v1', 'test_fixture')
        plan = ex.read(source / 'plan.json'); plan['actions'][0]['verification']['primary_measure'] = 'routed_terminal'
        plan['actions'][0]['verification']['regression_checks'] = ['closed_destination', 'routing_support', 'routing_protocol']
        demo.dump(source / 'triage.json', triage.model_dump(mode='json')); demo.dump(source / 'plan.json', plan)
        output = self.root / 'selection'
        result = ex.select(source / 'triage.json', source / 'plan.json', 'role-routing', output)
        self.assertFalse(result['execution_approved'])
        for old, new in [('action.json', 'action.json'), ('evidence.json', 'evidence.json'), ('evidence-traces.json', 'evidence-traces.json')]:
            (self.root / 'inputs' / new).write_bytes((output / old).read_bytes())
        self.prepared()
        self.assertFalse((self.workspace / 'approval.json').exists())
        self.assertEqual(ex.read(self.workspace / 'manifest.json')['selected_action_id'], 'role-routing')

    def test_explicit_plugin_sealed_and_concurrent_writer_refused(self):
        plugin = self.root / 'custom.py'
        plugin.write_text("from agent_improvement_lab.criteria import Decision\nPLUGIN_API_VERSION=1\ndef register(registry):\n    registry.register('custom_contract','1',lambda trace,parameters: Decision(verdict='correct',evidence_steps=[trace.messages[-1].step_id],explanation='Explicit test plugin'))\n")
        pack = yaml.safe_load((self.root / 'inputs/criteria.yml').read_text())
        pack['criteria'][0]['grader']['id'] = 'custom_contract'
        (self.root / 'inputs/criteria.yml').write_text(yaml.safe_dump(pack))
        state = ex.prepare(self.config, self.workspace, [plugin])
        ex.approve(self.workspace, state['manifest_sha256'], 'synthetic_test_fixture')
        with ex.writer(self.workspace):
            with self.assertRaisesRegex(ValueError, 'writer'): ex.run(self.workspace)
        (self.workspace / 'inputs/plugin_0000.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'sealed input'): ex.run(self.workspace)

    def test_no_improvement_is_terminal(self):
        scripts = ex.read(self.root / 'inputs/outputs.json')
        probe = self.prepared(); slots = ex.read(self.workspace / 'slots.json')
        pair = [s for s in slots if s['task_id'] == slots[0]['task_id'] and s['repeat_id'] == '1']
        base = next(s for s in pair if s['arm'] == 'baseline'); cand = next(s for s in pair if s['arm'] == 'candidate')
        scripts[base['run_id']] = scripts[cand['run_id']]
        demo.dump(self.root / 'inputs/outputs.json', scripts); self.workspace = self.root / 'equal'
        self.approved(); result = ex.run(self.workspace)
        self.assertEqual((result['phase'], result['decision']), ('complete', 'no_observed_improvement'))


if __name__ == '__main__': unittest.main()
