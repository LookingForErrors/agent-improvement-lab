"""Build a prescribed execution fixture. No inference or human labels."""
import argparse
import json
from pathlib import Path
import random
import shutil
import yaml
from agent_improvement_lab.schemas import Trace
from agent_improvement_lab.criteria import digest

ROOT = Path(__file__).resolve().parent


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def response(name=None, arguments=None, text='Done.'):
    message = {'role': 'assistant', 'content': '' if name else text}
    if name:
        message['tool_calls'] = [{'id': 'fixture-' + name, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}]
    return {'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if name else 'stop'}]}


def build(output):
    output = Path(output).resolve()
    if output.exists(): raise ValueError('choose a fresh fixture directory')
    output.mkdir(parents=True)
    for name in ['cases.json', 'baseline.txt', 'candidate.txt', 'common.txt']:
        shutil.copyfile(ROOT / name, output / name)
    pack = yaml.safe_load((ROOT / 'criteria.yml').read_text()); pack['data_kind'] = 'test_fixture'
    (output / 'criteria.yml').write_text(yaml.safe_dump(pack, sort_keys=False))
    cases = json.loads((output / 'cases.json').read_text()); scripts = {}; rng = random.Random(17); ordinal = 0
    for case in cases:
        ticket = {'ticket_id': case['ticket_id']}
        for repeat in (1, 2):
            arms = ['baseline', 'candidate']; rng.shuffle(arms)
            for arm in arms:
                ordinal += 1
                script = [response('get_ticket', ticket)]
                if arm == 'baseline' and case == cases[0] and repeat == 1:
                    script += [response(text='What does the download permission say?')]
                else:
                    script += [response('ask_user', dict(ticket, field='action_permission', question='What does the permission for this action say?'))]
                    if case == cases[0]:
                        script += [response('route_ticket', dict(ticket, team='accounts'))]
                    else:
                        script += [response('ask_user', dict(ticket, field='financial_transaction', question='Do you have a receipt for this feature?')),
                                   response('handoff_to_operator', ticket)]
                    script += [response(text='The mock ticket has been assigned.')]
                scripts[f's{ordinal:04d}'] = script
                for call_index, item in enumerate(script):
                    for call in item['choices'][0]['message'].get('tool_calls', []):
                        call['id'] += '-' + str(call_index)
    dump(output / 'outputs.json', scripts)
    source = Trace.model_validate({'id': 'fixture-source', 'task_id': cases[0]['id'], 'family_id': cases[0]['family_id'],
        'split': 'working', 'agent_version': 'fixture-baseline', 'provenance': 'scripted_model_outputs_mock_tools',
        'messages': [{'step_id': 's001', 'role': 'user', 'content': cases[0]['user_request']},
                     {'step_id': 's002', 'role': 'assistant', 'content': 'What does the download permission say?'}]}).model_dump(mode='json')
    dump(output / 'evidence-traces.json', [source])
    dump(output / 'evidence.json', {'data_kind': 'test_fixture', 'issues': [{'id': 'fixture-unrouted', 'split': 'working',
        'kind': 'assistant_failure', 'trace_id': source['id'], 'trace_sha256': digest(source), 'evidence_steps': ['s002'],
        'observation': 'Prescribed fixture ends with a text question and no assignment; not a real model failure.'}]})
    dump(output / 'action.json', {'id': 'typed-clarification-prompt', 'title': 'Prefer an explicit typed clarification',
        'issue_ids': ['fixture-unrouted'], 'observed_summary': 'A scripted fixture deliberately leaves one mock ticket unassigned.',
        'cause_hypothesis': 'The prompt may fail to specify how clarification should be executed; the fixture cannot test this hypothesis.',
        'intervention': 'prompt', 'recipient': 'product', 'proposed_change': 'Use the evidence-first prompt; compare both versions through the same controlled tool interface.',
        'verification': {'primary_measure': 'routed_terminal', 'success_criterion': 'Fewer unresolved executions without destination/support/protocol regressions; confirm separately before adoption.',
            'regression_checks': ['closed_destination', 'routing_support', 'routing_protocol'], 'repeat_runs': 2,
            'keep_fixed': ['model', 'tools and facts', 'grader identities'], 'protocol': 'working_selection_then_family_heldout_check'}})
    dump(output / 'rule.json', {'version': 'diagnostic-1', 'primary': pack['primary_metric'],
        'regressions': pack['regression_metrics'], 'expected_pairs': 4, 'evaluation_split': 'working',
        'scope': 'diagnostic; never statistical evidence or automatic promotion'})
    config = {'format': 'improvement-experiment-v1', 'backend': 'fixture', 'dataset_version': 'authored-routing-fixture-v1',
        'model_id': 'fixture-router', 'model_version': 'prescribed-outputs-v1', 'cases': 'cases.json',
        'baseline_prompt': 'baseline.txt', 'candidate_prompt': 'candidate.txt', 'common': 'common.txt',
        'criteria': 'criteria.yml', 'action': 'action.json', 'evidence': 'evidence.json', 'evidence_traces': 'evidence-traces.json', 'decision_rule': 'rule.json',
        'fixture_outputs': 'outputs.json', 'repeats': 2, 'seed': 17, 'agent_version_prefix': 'fixture-prompt-v1',
        'limits': {'wall_s': 300, 'request_s': 30, 'sample_s': 60}}
    (output / 'experiment.yml').write_text(yaml.safe_dump(config, sort_keys=False))
    return output / 'experiment.yml'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', required=True)
    print(build(parser.parse_args().output))
