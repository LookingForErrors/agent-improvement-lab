"""Native Inspect recovery contracts using scripted outputs; no model HTTP calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import yaml
from agent_improvement_lab import experiment as ex

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('recovery_fixture',ROOT/'examples/routing-experiment/build_demo.py')
demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)

@unittest.skipUnless(importlib.util.find_spec('inspect_ai'),'requires execution extra')
class RecoveryGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=demo.build(self.root/'inputs');self.workspace=self.root/'run'
        self.settings=yaml.safe_load(self.config.read_text())
        self.settings['zero_call_recovery']={'baseline':0,'candidate':1}
        self.cases=ex.read(self.root/'inputs/cases.json')
        (self.root/'inputs/baseline.txt').write_bytes((self.root/'inputs/candidate.txt').read_bytes())
        self.scripts=ex.read(self.root/'inputs/outputs.json')
        # Both arms receive the same prescribed sequence: read, text clarification,
        # then native clarification/routing if the scaffold requests continuation.
        slots=[];import random
        rng=random.Random(17)
        for case in self.cases:
            for repeat in (1,2):
                arms=['baseline','candidate'];rng.shuffle(arms)
                for arm in arms:slots.append((case,arm))
        self.slots=slots
        for ordinal,(case,arm) in enumerate(slots,1):
            ticket={'ticket_id':case['ticket_id']}
            steps=[demo.response('get_ticket',ticket),demo.response(text='What does the permission for this action say?'),
                   demo.response('ask_user',dict(ticket,field='action_permission',question='What does the action permission say?'))]
            if case==self.cases[0]:steps.append(demo.response('route_ticket',dict(ticket,team='accounts')))
            else:steps.extend([demo.response('ask_user',dict(ticket,field='financial_transaction',question='What does the receipt say?')),demo.response('handoff_to_operator',ticket)])
            steps.append(demo.response(text='The mock ticket has been assigned.'))
            self.scripts[f's{ordinal:04d}']=steps

    def prepare(self):
        for script in self.scripts.values():
            for n,response in enumerate(script):
                for call in response['choices'][0]['message'].get('tool_calls',[]):call['id']=f'fixture-{n}-{call["function"]["name"]}'
        demo.dump(self.root/'inputs/outputs.json',self.scripts)
        self.config.write_text(yaml.safe_dump(self.settings,sort_keys=False))
        state=ex.prepare(self.config,self.workspace);ex.approve(self.workspace,state['manifest_sha256'],'synthetic_test_fixture')

    def run_fixture(self):
        self.prepare();state=ex.run(self.workspace)
        self.assertEqual((state['phase'],state['completed_slots'],state['http_starts']),('complete',8,0))
        self.assertFalse((self.workspace/'requests.jsonl').exists())
        return [ex.read(self.workspace/f'slots/s{n:04d}/result.json') for n in range(1,9)]

    def test_one_visible_control_preserves_original_and_resumes_normal_tools(self):
        from agent_improvement_lab.experiment_inspect import RECOVERY_CONTROL
        import hashlib
        rows=self.run_fixture()
        for row in rows:
            t=row['trace'];m=t['metadata'];folder=self.workspace/'slots'/t['id']
            if row['slot']['arm']=='baseline':
                self.assertEqual(m['termination'],'final_without_assignment');self.assertEqual(m['control_events'],[]);self.assertEqual(m['generation_attempts'],2)
                continue
            self.assertEqual(m['termination'],'final_after_assignment');self.assertTrue(m['zero_call_recovery_used'])
            self.assertEqual(len(m['control_events']),1);event=m['control_events'][0]
            messages={x['step_id']:x for x in t['messages']}
            self.assertEqual(messages[event['source_step_id']]['role'],'assistant')
            self.assertEqual(messages[event['source_step_id']]['content'],'What does the permission for this action say?')
            self.assertEqual(messages[event['control_step_id']]['role'],'system')
            self.assertEqual(messages[event['control_step_id']]['content'],RECOVERY_CONTROL)
            self.assertEqual(m['message_sources'][event['control_step_id']],'intervention')
            checkpoint=ex.read(folder/'actor-checkpoint.json')
            self.assertEqual(event['source_output_sha256'],hashlib.sha256(ex.encode(checkpoint['calls'][1]).encode()).hexdigest())
            self.assertEqual((event['trigger_generation_attempt'],event['next_generation_attempt'],event['next_output_ordinal']),(2,3,3))
            request=ex.read(folder/'provider-calls/0003-request.json')
            self.assertEqual(request['messages'][-1]['content'],'What does the permission for this action say?')
            self.assertTrue(request['messages'][0]['content'].endswith('\n\n'+RECOVERY_CONTROL))
            self.assertTrue(all(message['role'] in ['user','assistant','tool'] for message in request['messages'][1:]))
            projection=ex.read(folder/'provider-calls/0003-control-projection.json')
            self.assertEqual(projection['original_messages'][-1],{'role':'system','content':RECOVERY_CONTROL})
            self.assertEqual(projection['effective_request_sha256'],hashlib.sha256(ex.encode(request).encode()).hexdigest())
            self.assertEqual(request['tool_choice'],'auto')
            self.assertLessEqual(m['generation_attempts'],8)
            self.assertLessEqual(len(m['requested_fields']),2)
        self.assertEqual((self.workspace/'TRACES.md').read_text().count('**Scaffold control**'),4)
        self.assertIn('Scaffold recovery controls: baseline 0, candidate 4',(self.workspace/'REPORT.md').read_text())
        before=ex.sha(self.workspace/'registry.json');ex.finalize(self.workspace);self.assertEqual(ex.sha(self.workspace/'registry.json'),before)

    def test_second_zero_call_stops_without_a_second_control(self):
        for script in self.scripts.values():script.insert(2,demo.response(text='Please clarify the permission.'))
        for row in self.run_fixture():
            m=row['trace']['metadata'];candidate=row['slot']['arm']=='candidate'
            self.assertEqual(m['termination'],'final_without_assignment')
            self.assertEqual((len(m['control_events']),m['generation_attempts']),(1,3) if candidate else (0,2))
            self.assertIsNone(m['assigned_to_observed'])

    def test_no_generation_budget_means_no_injection_or_extra_call(self):
        self.settings['limits']['max_generations']=2
        for row in self.run_fixture():
            m=row['trace']['metadata'];self.assertEqual(m['generation_attempts'],2);self.assertEqual(m['control_events'],[])
            self.assertFalse(m['zero_call_recovery_used']);self.assertEqual(m['measurement_status'],'observed')
            self.assertEqual(m['termination'],'final_without_assignment')
            self.assertEqual(m['recovery_declined_reason'],'max_generations' if row['slot']['arm']=='candidate' else None)

    def test_native_wrong_route_is_not_recovered_or_hidden(self):
        for n,(case,arm) in enumerate(self.slots,1):
            ticket={'ticket_id':case['ticket_id']}
            self.scripts[f's{n:04d}']=[demo.response('get_ticket',ticket),demo.response('route_ticket',dict(ticket,team='general')),demo.response(text='Routed to general.')]
        for row in self.run_fixture():
            m=row['trace']['metadata'];self.assertEqual(m['assigned_to_observed'],'general');self.assertEqual(m['control_events'],[])
            self.assertFalse(m['zero_call_recovery_used']);self.assertEqual(m['termination'],'final_after_assignment')
        for arm in ['baseline','candidate']:
            report=ex.read(self.workspace/f'{arm}-criteria.json');self.assertTrue(all(x['verdict']=='incorrect' for x in report['results'] if x['criterion_id']=='closed_destination'))

    def test_tool_error_is_preserved_before_one_recovery(self):
        for script in self.scripts.values():
            script.insert(1,demo.response('route_ticket',{'ticket_id':'not-current','team':'accounts'}))
        for row in self.run_fixture():
            t=row['trace'];errors=[json.loads(m['content']) for m in t['messages'] if m['role']=='tool' and json.loads(m['content']).get('status')=='ERROR']
            self.assertEqual([e['code'] for e in errors],['WRONG_TICKET'])
            if row['slot']['arm']=='candidate':
                self.assertEqual(len(t['metadata']['control_events']),1);self.assertEqual(t['metadata']['control_events'][0]['trigger_generation_attempt'],3)
                self.assertEqual(t['metadata']['termination'],'final_after_assignment')

    def test_recovery_config_limits_fail_before_execution(self):
        for value in [2,-1,True,'1',1.0]:
            with self.assertRaises(ValueError):ex.Config.model_validate(dict(self.settings,zero_call_recovery={'baseline':0,'candidate':value}))
        with self.assertRaises(ValueError):ex.Config.model_validate(dict(self.settings,zero_call_recovery={'candidate':1}))

    def test_crash_after_control_consumption_never_replays_started_slot(self):
        self.scripts['s0002'][2]['fixture_delay_seconds']=5
        self.prepare()
        p=subprocess.Popen([sys.executable,'-m','agent_improvement_lab.workflow_cli','experiment','run','--workspace',str(self.workspace)],cwd=self.root,env=dict(os.environ,PYTHONPATH=str(ROOT)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda:p.poll() is None and p.kill())
        checkpoint=self.workspace/'slots/s0002/actor-checkpoint.json';deadline=time.time()+25
        while time.time()<deadline:
            if checkpoint.exists():
                state=ex.read(checkpoint)
                if state['zero_call_recovery_used'] and state['inflight'] and (checkpoint.parent/'provider-calls/0003-request.json').exists():break
            time.sleep(.05)
        else:self.fail('No inflight post-control checkpoint')
        self.assertEqual(len(state['control_events']),1);self.assertEqual(state['control_events'][0]['next_generation_attempt'],3)
        self.assertIsNone(state['control_events'][0]['next_output_ordinal'])
        p.kill();p.wait(timeout=3)
        stop=checkpoint.parent/'worker-stop.json'
        while not stop.exists() and time.time()<deadline:time.sleep(.05)
        self.assertTrue(stop.exists())
        result=ex.run(self.workspace,resume=True)
        self.assertEqual((result['decision'],result['completed_slots']),('inconclusive_measurement',7))
        self.assertEqual(len(list((checkpoint.parent/'provider-calls').glob('*request.json'))),3)
        self.assertFalse((checkpoint.parent/'result.json').exists())

if __name__=='__main__':unittest.main()
