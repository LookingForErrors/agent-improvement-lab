"""Authored public test fixtures. No model output or human annotation claims."""
import argparse
import hashlib
import json
from pathlib import Path

from agent_improvement_lab import annotation as ann, failure_actions as actions
from agent_improvement_lab.schemas import Trace, Assessment


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def build(target):
    target = Path(target)
    if target.exists():
        raise ValueError('Choose a fresh input directory')
    target.mkdir(parents=True)
    rubric = ('Route role and access changes to accounts; invoice disputes to payments; '
              'reproducible failures to technical. Trust mock tool results. '
              'Ask at most two focused clarifications; hand unresolved ambiguity to an operator.\n')
    (target / 'rubric.txt').write_text(rubric)
    rubric_hash = hashlib.sha256(rubric.encode()).hexdigest()
    scenarios = [
        ('role-change', 'working', 'I can edit the other workspace. Here my membership says Viewer. The owner wants me to edit this launch project. Can you help change my role?', 'accounts'),
        ('pdf-failure', 'working', 'The PDF export fails with error E500. I own the project and can export CSV. I tried twice after restarting the app; the PDF still fails.', 'technical'),
        ('invoice-dispute', 'heldout', 'The invoice is higher than the price in our signed quote. The old access problem is resolved. Please review the amount we were charged.', 'payments'),
        ('unknown-restriction', 'heldout', 'Archive export only says Restricted. I cannot find a permission or plan explanation. After checking both settings, I still do not know what causes it.', 'general'),
    ]
    arms = {}; judges = {}; labels = []
    for arm in ['baseline', 'candidate']:
        identity = {'version': 'authored-fixture-v1', 'model': 'no-model-scripted-fixture', 'run_id': arm,
                    'prompt_sha256': 'a' * 64, 'config_sha256': 'b' * 64}
        rows = []; predictions = []
        for family, split, request, expected in scenarios:
            for repeat in ['1', '2']:
                tid = f'{arm}-{family}-{repeat}'
                route = 'technical' if arm == 'baseline' and family == 'role-change' else expected
                correct = route == expected
                messages = [
                    {'step_id': 's0', 'role': 'system', 'content': 'Current ticket: DEMO-1\n' + rubric},
                    {'step_id': 's1', 'role': 'user', 'content': request},
                    {'step_id': 's2', 'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call-1', 'name': 'route_ticket', 'arguments': {'ticket_id': 'DEMO-1', 'team': route}}]},
                    {'step_id': 's3', 'role': 'tool', 'content': json.dumps({'ok': True, 'team': route}), 'tool_call_id': 'call-1', 'function': 'route_ticket'},
                    {'step_id': 's4', 'role': 'assistant', 'content': 'I have sent your request to ' + route + ' support.'},
                ]
                trace = Trace(id=tid, task_id=family, family_id=family, split=split, language='en',
                    provenance='authored_test_fixture', agent_version=arm + '-fixture-v1', messages=messages,
                    metadata={'repeat_id': repeat, 'noise': 'clean', 'message_sources': {'s0': 'input', 's1': 'input', 's2': 'generate', 's3': None, 's4': 'generate'}})
                row = trace.model_dump(mode='json'); rows.append(row)
                verdict = 'correct' if correct else 'incorrect'
                predictions.append(Assessment(id='judge-' + tid, trace_id=tid, verdict=verdict, assessor_kind='llm',
                    assessor_id='scripted_judge_fixture', rubric_version=rubric_hash, evidence_steps=['s2'],
                    error_types=[] if correct else ['wrong_route'], explanation='Authored test verdict; no inference.',
                    metadata={'source_sha256': ann.digest(row), 'judge_identity': identity}).model_dump(mode='json'))
                if arm == 'baseline' and split == 'working':
                    public, steps = ann.project_trace(trace)
                    labels.append({'case_id': 'fixture-' + tid, 'trace_id': tid, 'task_id': family, 'family_id': family, 'split': split,
                        'source_sha256': ann.digest(row), 'rubric_sha256': rubric_hash, 'assessor_kind': 'human',
                        'annotation': {'verdict': verdict, 'annotator': 'synthetic_test_fixture', 'evidence_steps': ['m02'],
                            'error_types': [] if correct else ['wrong_route'], 'comment': 'Authored reference: role change belongs to accounts.' if not correct else 'Authored correct routing fixture.',
                            'scenario_problem': False, 'scenario_evidence': [], 'scenario_comment': '', 'revision': 1, 'saved_at': 'fixture-not-a-human-timestamp'},
                        'original_evidence_steps': [steps['m02']], 'original_scenario_evidence': []})
        arms[arm] = rows
        judges[arm] = {'format': 'judge-assessments-v1', 'judge_identity': identity, 'assessments': predictions}
        dump(target / (arm + '.json'), rows)
        dump(target / (arm + '-judge.json'), judges[arm])
    labels.sort(key=lambda r: r['trace_id']); dump(target / 'fixture-annotations.json', labels)
    triage = actions.prepare(arms['baseline'], labels, rubric_hash, 'routing-loop-fixture-v1', 'test_fixture')
    plan = actions.template(triage)
    plan['analyst'] = 'synthetic_test_fixture'
    plan['actions'] = [{'id': 'role-routing', 'title': 'Route explicit role changes to accounts',
        'issue_ids': [issue.id for issue in actions.issues(triage)],
        'observed_summary': 'The baseline routes two Viewer-to-Editor requests to technical support.',
        'cause_hypothesis': 'The baseline may treat inability to edit as a generic technical failure.',
        'intervention': 'prompt', 'recipient': 'product', 'proposed_change': 'Prioritize explicit role evidence when routing a role-change request.',
        'verification': {'primary_measure': 'Whole-trace correct routing', 'success_criterion': 'Improve role routing without regressions in fixed other families.',
            'regression_checks': ['PDF failure and invoice routing'], 'repeat_runs': 2,
            'keep_fixed': ['task facts', 'tools', 'rubric', 'judge identity'], 'protocol': 'working_selection_then_family_heldout_check'},
        'status': 'draft_pending_human_selection'}]
    actions.report(triage, plan)
    dump(target / 'plan.json', plan)
    (target / 'config.yaml').write_text('format: improvement-replay-v1\nmode: scripted_demo\ndataset_version: routing-loop-fixture-v1\nbaseline: baseline.json\ncandidate: candidate.json\nbaseline_judge: baseline-judge.json\ncandidate_judge: candidate-judge.json\nrubric: rubric.txt\nfixture_annotations: fixture-annotations.json\nmax_cases: 4\nseed: 17\n')
    return target / 'config.yaml'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    build(parser.parse_args().output)
