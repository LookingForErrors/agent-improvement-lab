"""A scripted transport demonstration, not an improvement measurement."""
from pathlib import Path
import importlib.util,json,random,yaml
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('scripted_demo',ROOT/'examples/routing-experiment/build_demo.py')
demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)

def build(output):
 config=demo.build(output);output=Path(output);settings=yaml.safe_load(config.read_text())
 settings['zero_call_recovery']={'baseline':0,'candidate':1};settings['agent_version_prefix']='scripted-recovery-v1'
 (output/'baseline.txt').write_bytes((output/'candidate.txt').read_bytes())
 cases=demo.json.loads((output/'cases.json').read_text());rng=random.Random(17);scripts={};ordinal=0
 for case in cases:
  for repeat in [1,2]:
   arms=['baseline','candidate'];rng.shuffle(arms)
   for arm in arms:
    ordinal+=1;ticket={'ticket_id':case['ticket_id']}
    steps=[demo.response('get_ticket',ticket),demo.response(text='What does the permission for this action say?'),demo.response('ask_user',dict(ticket,field='action_permission',question='What does the permission for this action say?'))]
    if case==cases[0]:steps.append(demo.response('route_ticket',dict(ticket,team='accounts')))
    else:steps.extend([demo.response('ask_user',dict(ticket,field='financial_transaction',question='What does the receipt say?')),demo.response('handoff_to_operator',ticket)])
    steps.append(demo.response(text='The mock ticket has been assigned.'))
    for n,item in enumerate(steps):
     for call in item['choices'][0]['message'].get('tool_calls',[]):call['id']=f'scripted-{n}-{call["function"]["name"]}'
    scripts[f's{ordinal:04d}']=steps
 demo.dump(output/'outputs.json',scripts)
 action=demo.json.loads((output/'action.json').read_text());action.update(id='scripted-one-time-recovery',title='One-time continuation control',intervention='scaffold',proposed_change='Enable one recorded zero-call recovery control for candidate; prompts, tools and facts identical. This scripted fixture prescribes successful continuation, not measured model behavior.',cause_hypothesis='A control message might repair undelivered clarification; this authored fixture does not test that hypothesis.')
 demo.dump(output/'action.json',action);config.write_text(yaml.safe_dump(settings,sort_keys=False));return config

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
 print(build(parser.parse_args().output))
