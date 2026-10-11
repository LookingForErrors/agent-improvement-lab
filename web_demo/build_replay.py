"""Build an explicitly illustrative replay without changing recorded experiment data."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

from agent_improvement_lab import routing_measurement as rm
from agent_improvement_lab.schemas import Trace


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


QUESTIONS = {
    'action_permission': 'What do the permission settings for this action explicitly say about whether you can perform it?',
    'financial_transaction': 'If you have a payment receipt or confirmation for the affected feature, what does it say?',
    'own_access': 'Using your own account, can you open the affected workspace?',
    'own_role': 'What role is listed for your own account in the affected workspace?',
    'current_plan': 'What does the current plan show about the allowance for this feature?',
    'observed_symptom': 'What result do you expect for this action, and what actually happens?',
}


def question_fields(reference):
    facts = reference['initial_facts']
    target = reference['expected_team']
    if target == 'operator':
        fields = [field for field in ['action_permission', 'financial_transaction',
                  'observed_symptom', 'current_plan', 'own_access']
                  if facts.get(field, 'unknown') == 'unknown']
        if len(fields) < 2:
            raise ValueError('Fallback fixture requires two still-missing factual checks')
        return fields[:2]
    paths = []
    for path in reference['routing_paths']:
        if path['destination'] != target:
            continue
        missing = [field for field, state in path['requires'].items() if facts.get(field) != state]
        if len(missing) <= 2 and all(reference['answers'][f]['state'] == path['requires'][f] for f in missing):
            paths.append(missing)
    if not paths:
        raise ValueError('No truthful bounded successful authored trajectory for this case')
    return min(paths, key=lambda fields: (len(fields), fields))


def authored_trace(case, reference, prompt, arm):
    messages = []
    call_number = 0

    def message(role, content='', **extra):
        messages.append(dict(step_id='s%03d' % (len(messages) + 1), role=role, content=content, **extra))

    def exchange(name, arguments, response):
        nonlocal call_number
        call_number += 1
        cid = 'authored-call-%02d' % call_number
        message('assistant', tool_calls=[dict(id=cid, name=name, arguments=arguments)])
        message('tool', json.dumps(response, ensure_ascii=False), tool_call_id=cid, function=name)

    ticket = case['ticket_id']
    message('system', prompt + '\n\nCurrent ticket: ' + ticket)
    message('user', case['user_request'])
    exchange('get_ticket', {'ticket_id': ticket}, {**case['mock_ticket'], 'ticket_status': case['mock_ticket']['status'], 'status': 'OK'})
    for field in question_fields(reference):
        question = QUESTIONS[field]
        if field == 'observed_symptom' and any(m.get('function') == 'ask_user' for m in messages):
            question = 'What specific expected result and observed result can you report for the current request?'
        exchange('ask_user', {'ticket_id': ticket, 'field': field, 'question': question},
                 {'status': 'OK', 'field': field, 'answer': reference['answers'][field]['answer']})
    team = reference['expected_team']
    action = 'handoff_to_operator' if team == 'operator' else 'route_ticket'
    arguments = {'ticket_id': ticket} if team == 'operator' else {'ticket_id': ticket, 'team': team}
    exchange(action, arguments, {'status': 'OK', 'ticket_id': ticket, 'assigned_to': team})
    message('assistant', 'I have handed this ticket to an operator for review.' if team == 'operator'
            else 'I have routed this ticket to the %s team.' % team)
    trace = Trace.model_validate({'id': 'authored-replay-' + case['id'] + '-' + arm,
        'task_id': case['id'], 'family_id': case['family_id'], 'split': 'working', 'language': 'en',
        'provenance': 'authored_successful_demo_trajectory_not_model_output',
        'agent_version': 'authored-replay-' + arm, 'messages': messages,
        'metadata': {'trajectory_completeness': 'complete', 'termination': 'final_after_assignment',
                     'model_calls': 0, 'human_gold': False}})
    public = {key: value for key, value in trace.model_dump(mode='json').items()
              if key in {'id', 'task_id', 'family_id', 'split', 'agent_version', 'messages'}}
    public.update(arm=arm, source_id=trace.id, repeat_id='1', source_sha256=digest(trace.model_dump(mode='json')),
                  source_kind='authored_fixture', projection_sha256=digest(public['messages']))
    return trace, public


def build(source, plan_path, output):
    source, output = Path(source), Path(output)
    plan = read(plan_path)
    if plan['format'] != 'illustrative-replay-plan-v1':
        raise ValueError('Wrong replay plan')
    for name, sha in plan['source_files'].items():
        if file_sha(source / name) != sha:
            raise ValueError('Recorded source changed: ' + name)
    original, original_story = read(source / 'family-experiment.json'), read(source / 'story-data.json')
    data = {key: copy.deepcopy(original[key]) for key in
        ['format', 'id', 'title', 'criteria', 'action', 'action_evidence', 'prompt',
         'prior_traces', 'pairs', 'traces', 'results']}
    data['id'] = 'support-routing-curated-replay-v1'
    data['title'] = 'Curated support-routing replay'
    primary = data['action']['verification']['primary_measure']
    cells = {(cell['trace_id'], cell['criterion_id']): cell for cell in data['results']}
    trace_index = {trace['id']: trace for trace in data['traces']}
    pair_index = {pair['id']: pair for pair in data['pairs']}
    replacements, new_cells, raw_authored = {}, [], []
    measures = {'observed_action_v2': rm.observed_action, 'closed_destination_v2': rm.destination,
        'routing_support_v2': rm.support, 'routing_protocol_v2': rm.protocol,
        'response_after_action_v2': rm.confirmation}
    if len({item['case']['id'] for item in plan['authored_pairs']}) != len(plan['authored_pairs']):
        raise ValueError('Duplicate authored case')
    for item in plan['authored_pairs']:
        case, reference = item['case'], item['reference']
        pair = pair_index[case['id']]
        if reference['ticket_id'] != case['ticket_id'] or case['family_id'] != pair['family_id']:
            raise ValueError('Authored task binding differs')
        for arm in ['baseline', 'candidate']:
            old_id = pair[arm]
            trace, public = authored_trace(case, reference, data['prompt'][arm], arm)
            raw_authored.append(trace.model_dump(mode='json'))
            parameters = {'cases': {case['id']: reference}}
            checks = {cid: grader(trace, parameters) for cid, grader in measures.items()}
            if any(check.verdict != 'correct' for check in checks.values()):
                raise ValueError('Authored success fails a mechanical check: ' + trace.id + ' ' + str(checks))
            replacements[old_id] = public
            pair[arm] = public['id']
            pair['source_kind'] = 'authored_success_pair'
            for definition in data['criteria']:
                cid = definition['id']
                old_cell = cells[(old_id, cid)]
                if cid == primary:
                    evidence = list(dict.fromkeys(step for check in checks.values() for step in check.evidence_steps))
                    verdict, explanation = 'correct', 'Authored successful fixture; five mechanical checks pass. This is an expected demo outcome, not a newly observed model success.'
                else:
                    check = checks[cid]
                    verdict, explanation, evidence = check.verdict, check.explanation, check.evidence_steps
                new_cells.append(dict(trace_id=public['id'], trace_sha256=public['source_sha256'],
                    criterion_id=cid, criterion_sha256=old_cell['criterion_sha256'], status='assessed',
                    verdict=verdict, evidence_steps=evidence, explanation=explanation, error_code=None,
                    assessment_kind='authored_fixture' if cid == primary else 'deterministic',
                    human_gold=False))
    data['traces'] = [replacements.get(trace['id'], trace) for trace in data['traces']]
    data['results'] = [cell for cell in data['results'] if cell['trace_id'] not in replacements] + new_cells
    cells = {(cell['trace_id'], cell['criterion_id']): cell for cell in data['results']}
    for overlay in plan['semantic_overlays']:
        cell = cells[(overlay['trace_id'], primary)]
        if cell['trace_sha256'] != overlay['trace_sha256'] or cell['verdict'] != 'unassessable':
            raise ValueError('Overlay differs from original unknown trace')
        if overlay['verdict'] not in ['correct', 'incorrect'] or not overlay['review_pass']:
            raise ValueError('Unreviewed overlay')
        steps = {m['step_id'] for m in trace_index[overlay['trace_id']]['messages']}
        if not overlay['evidence_steps'] or not set(overlay['evidence_steps']) <= steps:
            raise ValueError('Unbound overlay evidence')
        cell.update(verdict=overlay['verdict'], evidence_steps=overlay['evidence_steps'],
                    explanation=overlay['explanation'], assessment_kind='blind_ai_adjudication',
                    adjudication=overlay, human_gold=False)
    counts, transitions = {}, Counter()
    for arm in ['baseline', 'candidate']:
        counts[arm] = dict(Counter(cells[(pair[arm], primary)]['verdict'] for pair in data['pairs']))
    for pair in data['pairs']:
        a, b = [cells[(pair[arm], primary)]['verdict'] for arm in ['baseline', 'candidate']]
        transitions['unknown' if 'unassessable' in [a, b] else 'improved' if a == 'incorrect' and b == 'correct'
                    else 'regressed' if a == 'correct' and b == 'incorrect' else 'unchanged'] += 1
    data['provenance'] = {'kind': 'illustrative_curated_replay', 'new_model_calls': 0, 'human_gold': False,
        'recorded_traces': len(data['traces']) - len(replacements), 'authored_traces': len(replacements),
        'semantic_adjudicated_traces': len(plan['semantic_overlays']), 'source_plan_sha256': file_sha(plan_path),
        'original_fixture_sha256': file_sha(source / 'family-experiment.json'), 'builder_sha256': file_sha(__file__),
        'model_gain_inference': False, 'tools': 'mock', 'user_facts': 'authored synthetic'}
    data['statistics'] = {'version': 'illustrative-replay-counts-v1', 'counts': counts,
        'planned_pairs': len(data['pairs']), 'independent_family_units': len({p['family_id'] for p in data['pairs']}),
        'transitions': dict(transitions), 'human_gold': False, 'inferential_result': False}
    data['decision'] = {'decision': 'illustrative_only', 'default_adopted': False, 'human_gold': False,
        'model_improvement_claim': False}
    data['costs'] = {'new_actor_calls': 0, 'new_provider_spend_rub': 0}
    data['metrics'] = {}
    for arm in ['baseline', 'candidate']:
        data['metrics'][arm] = {'demo': {}}
        for definition in data['criteria']:
            cid = definition['id']
            outcomes = Counter(cells[(pair[arm], cid)]['verdict'] for pair in data['pairs'])
            data['metrics'][arm]['demo'][cid] = {'expected': len(data['pairs']), **{
                key: outcomes.get(key, 0) for key in ['correct', 'incorrect', 'unassessable']}}
    data['capture'] = {'complete': len(data['traces']), 'partial': 0, 'missing': 0,
        'meaning': 'Assembled demo records, including authored completions; not a model capture audit'}
    selected = {e[a]['trace_id'] for e in original_story['examples'] for a in ['baseline', 'candidate']}
    if selected & set(replacements):
        raise ValueError('Recorded illustrative pair replaced')
    story = {key: copy.deepcopy(data[key]) for key in original_story if key in data}
    story.update(format='agent-improvement-story-v1', primary_criterion=primary,
        examples=copy.deepcopy(original_story['examples']),
        traces=[t for t in data['traces'] if t['id'] in selected],
        results=[c for c in data['results'] if c['criterion_id'] == primary or c['trace_id'] in selected])
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('curated-experiment.json', data), ('curated-story.json', story)]:
        text = json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n'
        if any(secret in text for secret in ['/Users/', '/private/', '.env.local', 'Bearer ', 'api_key']):
            raise ValueError('Private data in public replay')
        (output / name).write_text(text)
    binding = {'format': 'illustrative-replay-binding-v1', 'source_plan_sha256': file_sha(plan_path),
        'builder_sha256': file_sha(__file__),
        'source_files': plan['source_files'], 'files': {name: file_sha(output / name)
        for name in ['curated-experiment.json', 'curated-story.json']},
        'counts': counts, 'transitions': dict(transitions), 'authored_traces': len(replacements),
        'recorded_traces': data['provenance']['recorded_traces'], 'actor_calls': 0, 'human_gold': False,
        'inferential_result': False}
    (output / 'curated-transfer.json').write_text(json.dumps(binding, indent=2) + '\n')
    return binding, raw_authored


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).parent)
    parser.add_argument('--plan', type=Path, default=Path(__file__).with_name('replay-plan.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result, _ = build(args.source, args.plan, args.output)
    print(json.dumps(result, indent=2))
