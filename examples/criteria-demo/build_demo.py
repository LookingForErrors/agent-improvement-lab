"""Public authored criteria fixtures, deliberately including measurement faults."""
import argparse
import importlib.util
import json
from pathlib import Path

import yaml
from agent_improvement_lab.schemas import Trace, Task
from agent_improvement_lab.criteria import CATALOG, Pack
from agent_improvement_lab.annotation import digest

ROOT = Path(__file__).resolve().parents[2]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def build(target):
    target = Path(target)
    if target.exists():
        raise ValueError('demo inputs exist; choose a fresh directory')
    target.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location('routing_fixture', ROOT / 'examples/routing-loop/build_demo.py')
    routing = importlib.util.module_from_spec(spec); spec.loader.exec_module(routing)
    routing.build(target / 'routing-source')
    arms = {}
    for arm in ['baseline', 'candidate']:
        rows = json.loads((target / 'routing-source' / (arm + '.json')).read_text())
        for row in rows:
            row['task'] = Task(id=row['task_id'], objective='Route the current ticket according to the supplied facts and routing rules.',
                family_id=row['family_id'], split=row['split'], language='en').model_dump(mode='json')
            row['metadata']['evaluation_context'] = {'scenario_version': 'criteria-fixture-v2', 'tools_version': 'routing-mocks-v1', 'product_rules_version': 'routing-v1'}
            row['messages'][0]['content'] += '\nTool contracts: route_ticket(ticket_id:string, team:accounts|payments|technical|general); handoff_to_operator(ticket_id:string, reason:string). Both return an observed ok status for the specified ticket. No operator response-time SLA is provided.'
            if row['family_id'] == 'unknown-restriction':
                row['messages'][1]['content'] = 'Archive export only says Restricted. Can you route my request to the right support team? I do not know whether this is a plan limit or permission problem.'
                row['messages'] = [row['messages'][0], row['messages'][1],
                    {'step_id': 's2', 'role': 'assistant', 'content': 'Does the billing page say export is restricted by your plan?'},
                    {'step_id': 's3', 'role': 'user', 'content': 'It does not explain export limits. I cannot identify a plan restriction.'},
                    {'step_id': 's4', 'role': 'assistant', 'content': 'Can you find an export permission or a role explanation in the project settings?'},
                    {'step_id': 's5', 'role': 'user', 'content': 'No. I checked but cannot find either explanation.'},
                    {'step_id': 's6', 'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call-1', 'name': 'handoff_to_operator', 'arguments': {'ticket_id': 'DEMO-1', 'reason': 'Still ambiguous after two questions'}}]},
                    {'step_id': 's7', 'role': 'tool', 'content': '{"ok":true,"handoff":"operator"}', 'tool_call_id': 'call-1', 'function': 'handoff_to_operator'},
                    {'step_id': 's8', 'role': 'assistant', 'content': 'I have handed your request to an operator.'}]
                row['metadata']['message_sources'] = {m['step_id']: ('input' if m['role'] in ['system', 'user'] else 'generate' if m['role'] == 'assistant' else None) for m in row['messages']}
            if arm == 'candidate' and row['family_id'] == 'invoice-dispute':
                row['messages'][2]['tool_calls'][0]['arguments']['ticket_id'] = 'OTHER-2'
            if arm == 'candidate' and row['family_id'] == 'unknown-restriction':
                row['messages'][-1]['content'] += ' An operator will reply within five minutes.'
            if arm == 'baseline' and row['family_id'] == 'pdf-failure' and row['metadata']['repeat_id'] == '2':
                row['messages'] = row['messages'][:3]
                row['metadata']['message_sources'] = {m['step_id']: row['metadata']['message_sources'][m['step_id']] for m in row['messages']}
            normalized = Trace.model_validate(row).model_dump(mode='json')
            row.clear(); row.update(normalized)
        arms[arm] = rows
        dump(target / (arm + '.json'), rows)
    base = {'format': 'criteria-pack-v1', 'id': 'routing-quality', 'version': '1', 'data_kind': 'test_fixture',
            'primary_metric': 'task_completion', 'regression_metrics': ['tool_arguments', 'unsupported_claims'],
            'criteria': [{'id': cid, 'builtin': cid, 'grader': {'kind': 'saved_llm', 'id': 'fixture_judge'}} for cid in CATALOG]}
    custom = json.loads(json.dumps(base)); custom['id'] = 'routing-custom-quality'
    custom['regression_metrics'] += ['ticket_identity', 'reply_grounding']
    custom['criteria'] += [
        {'id': 'ticket_identity', 'definition': {'version': '1', 'title': 'Current ticket identity', 'question': 'Do all calls target the trusted current ticket?',
            'applicability': 'At least one call with a ticket_id field exists.', 'rubric': 'Read the trusted Current ticket system line; compare every argument-bearing call. Missing trusted context is unassessable.', 'target': 'assistant_behavior'},
         'grader': {'kind': 'deterministic', 'id': 'ticket_identity', 'parameters': {}}},
        {'id': 'reply_grounding', 'definition': {'version': '1', 'title': 'Grounded routing reply', 'question': 'Does the final reply describe the observed action without an unsupported guarantee?',
            'applicability': 'An assistant reports routing or a support commitment.', 'rubric': 'Compare the final reply with actual calls/results. Product policy gives no response-time SLA. Do not judge whether the chosen team was correct here.', 'target': 'assistant_behavior'},
         'grader': {'kind': 'saved_llm', 'id': 'fixture_judge'}},
    ]
    for label, config in [('base', base), ('custom', custom)]:
        (target / (label + '.yaml')).write_text(yaml.safe_dump(config, sort_keys=False))
        pack = Pack.model_validate(config).resolve()
        for arm, rows in arms.items():
            identity = {'provider': 'authored-fixture', 'version': 'fixture-judge-v2', 'model': 'no-model', 'run_id': arm,
                        'prompt_sha256': 'a' * 64, 'config_sha256': 'b' * 64}
            assessments = []
            for row in rows:
                for criterion in pack.criteria:
                    if criterion.grader.kind != 'saved_llm':
                        continue
                    cid = criterion.id; family = row['family_id']
                    outcome = 'correct'; status = 'assessed'
                    action, result_step, final = ('s6', 's7', 's8') if family == 'unknown-restriction' else ('s2', 's3', 's4')
                    evidence, explanation = {
                        'task_completion': (['s0', 's1', action, result_step], 'Observed successful tool result supports the authorized task; backend state is unverified.'),
                        'tool_selection': (['s0', 's1', action], 'The required relevant tool was called; destination argument correctness is checked separately.'),
                        'tool_arguments': (['s0', 's1', action], 'Argument structure, identity and values match supplied contracts and task facts.'),
                        'context_use': (['s0', 's1', action], 'Decision respects the supplied task facts.'),
                        'unsupported_claims': ([result_step, final], 'Reply is supported by the observed tool result; no timing promise.'),
                        'reply_grounding': ([result_step, final], 'Reply describes the observed action without an unsupported guarantee.'),
                        'clarification': (['s0', 's1', 's2', 's3', 's4', 's5', action], 'Two focused questions acquired previously unknown facts but did not resolve uncertainty; authorized operator handoff followed.')
                    }[cid]
                    if cid == 'clarification':
                        if family != 'unknown-restriction':
                            status = 'not_applicable'; outcome = None; evidence = []; explanation = 'Clear authored task with no clarification question.'
                    if arm == 'baseline' and family == 'role-change' and cid in ['task_completion', 'tool_arguments', 'context_use']:
                        outcome = 'incorrect'; explanation = 'Explicit Viewer-to-Editor role change was routed to technical instead of accounts.'
                    if arm == 'candidate' and family == 'invoice-dispute' and cid in ['task_completion', 'tool_arguments', 'context_use']:
                        outcome = 'incorrect'; explanation = 'Call targets OTHER-2 rather than the trusted current ticket DEMO-1.'
                    if arm == 'candidate' and family == 'invoice-dispute' and cid in ['unsupported_claims', 'reply_grounding']:
                        outcome = 'incorrect'; evidence = ['s0', action, result_step, final]
                        explanation = 'Reply claims the user request was routed, but the observed call targets OTHER-2 instead of trusted DEMO-1.'
                    if arm == 'candidate' and family == 'unknown-restriction' and cid in ['unsupported_claims', 'reply_grounding']:
                        outcome = 'incorrect'; evidence = ['s0', 's8']; explanation = 'Five-minute commitment has no authority in this fixture policy.'
                    if arm == 'baseline' and family == 'pdf-failure' and row['metadata']['repeat_id'] == '2':
                        if cid == 'task_completion':
                            outcome = 'unassessable'; evidence = [action]; explanation = 'Trace ends at the call; no response establishes completion or failure.'
                        if cid in ['unsupported_claims', 'reply_grounding']:
                            status = 'not_applicable'; outcome = None; evidence = []; explanation = 'Truncated trace has no externally checkable assistant claim.'
                    # Distinct missing vs grader-error paths, not agent-quality failures.
                    if arm == 'baseline' and family == 'pdf-failure' and row['metadata']['repeat_id'] == '2' and cid == 'context_use':
                        continue
                    entry = {'trace_id': row['id'], 'criterion_id': cid, 'trace_sha256': digest(row),
                        'criterion_sha256': digest(criterion.model_dump(mode='json')), 'grader_id': 'fixture_judge',
                        'judge_identity_sha256': digest(identity), 'assessor_kind': 'llm',
                        'decision': {'status': status, 'verdict': outcome, 'evidence_steps': evidence, 'explanation': explanation}}
                    if arm == 'baseline' and family == 'invoice-dispute' and row['metadata']['repeat_id'] == '2' and cid == 'unsupported_claims':
                        entry['trace_sha256'] = '0' * 64
                    assessments.append(entry)
            dump(target / (label + '-' + arm + '-assessments.json'), {'format': 'criteria-assessments-v1', 'data_kind': 'test_fixture',
                'origin': 'authored_test_fixture', 'judges': {'fixture_judge': identity}, 'assessments': assessments})
    (target / 'ticket_plugin.py').write_text(PLUGIN)
    dump(target / 'PROVENANCE.json', {'origin': 'authored_test_fixture', 'human_labels': False, 'model_calls': 0,
        'base_criteria': list(CATALOG), 'custom_criteria': ['ticket_identity', 'reply_grounding'],
        'deliberate_faults': ['truncated baseline tool call', 'missing context assessment', 'stale source binding', 'candidate wrong ticket', 'candidate unsupported guarantee'],
        'warning': 'Scripted metric arithmetic, not measured LLM quality or human gold.'})
    return target


PLUGIN = '''"""Trusted self-contained local check; no network or tool effects."""
import re
from agent_improvement_lab.criteria import Decision
PLUGIN_API_VERSION = 1

def check(trace, parameters):
    if parameters:
        raise ValueError('This check takes no options')
    calls = [(m.step_id, c) for m in trace.messages for c in m.tool_calls if 'ticket_id' in c.arguments]
    if not calls:
        return Decision(status='not_applicable', explanation='No ticket-bearing call.')
    tickets = [tid for m in trace.messages if m.role == 'system' and isinstance(m.content, str)
               for tid in re.findall(r'^Current ticket: ([A-Za-z0-9_-]+)$', m.content, re.M)]
    if len(tickets) != 1:
        return Decision(verdict='unassessable', explanation='Exactly one trusted current ticket is required.')
    bad = [step for step, call in calls if call.arguments['ticket_id'] != tickets[0]]
    return Decision(verdict='incorrect' if bad else 'correct', evidence_steps=bad or [step for step, _ in calls],
                    explanation='Wrong ticket identity.' if bad else 'Every ticket argument matches trusted current ticket.')

def register(registry):
    registry.register('ticket_identity', '1', check)
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    build(parser.parse_args().output)
