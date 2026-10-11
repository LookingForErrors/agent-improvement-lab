"""Source-checkout, local replay workflow. No inference or remote effects."""
import argparse
import hashlib
import json
import os
import random
import sqlite3
import platform
from importlib.metadata import version
from collections import defaultdict
from pathlib import Path
from typing import Literal, TypedDict

os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'

import yaml
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, interrupt
from pydantic import Field, model_validator

from . import annotation as ann, failure_actions as actions, judge_metrics as metrics
from .import_traces import UniqueSafeLoader, import_file, strict_json
from .schemas import Model, Trace


class Config(Model):
    format: Literal['improvement-replay-v1'] = 'improvement-replay-v1'
    mode: Literal['human', 'scripted_demo']
    dataset_version: str = Field(min_length=1)
    baseline: str
    candidate: str
    baseline_judge: str
    candidate_judge: str
    rubric: str
    mapping: str | None = None
    fixture_annotations: str | None = None
    max_cases: int = Field(default=20, ge=1)
    seed: int = 17

    @model_validator(mode='after')
    def fixture_boundary(self):
        if (self.mode == 'scripted_demo') != (self.fixture_annotations is not None):
            raise ValueError('fixture annotations belong only to scripted_demo')
        return self


class State(TypedDict, total=False):
    phase: str
    artifacts: dict[str, str]
    decision: str


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return strict_json(Path(path).read_text())


def runtime_identity():
    return {'python': platform.python_version(), 'packages': {name: version(name) for name in
        ['langgraph', 'langgraph-checkpoint-sqlite', 'pydantic', 'PyYAML']}}


def write_once(path, content):
    """Local deterministic effects: identical retry is safe, changed bytes fail closed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode() if isinstance(content, str) else content
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f'artifact changed: {path.name}')
        return
    # Exclusive creation plus fsync. A partial crash is detected on retry, never replaced.
    with path.open('xb') as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())


def records(path, mapping=None):
    if mapping:
        result = import_file(path, mapping)
        if not result.ok or any(not isinstance(row, Trace) for row in result.records):
            raise ValueError('trace import failed: ' + str(result.diagnostics))
        return [row.model_dump(mode='json') for row in result.records]
    return [Trace.model_validate(row).model_dump(mode='json') for row in metrics.load_records(path)]


def paired_inventory(baseline, candidate):
    def index(rows):
        result = {}; family_splits = {}; task_families = {}; repeats = defaultdict(set)
        for row in rows:
            trace = Trace.model_validate(row)
            repeat = trace.metadata.get('repeat_id')
            if not all([trace.task_id, trace.family_id, trace.split, trace.agent_version]) or not isinstance(repeat, str) or not repeat:
                raise ValueError('explicit task/family/split/version/string repeat_id required')
            key = (trace.task_id, trace.family_id, trace.split, repeat)
            if key in result:
                raise ValueError('duplicate task/family/repeat pair')
            result[key] = trace
            repeats[trace.task_id].add(repeat)
            if family_splits.setdefault(trace.family_id, trace.split) != trace.split or task_families.setdefault(trace.task_id, trace.family_id) != trace.family_id:
                raise ValueError('task/family split leakage')
        if not result or any(len(values) < 2 for values in repeats.values()):
            raise ValueError('at least two repeats per task required')
        if len({t.id for t in result.values()}) != len(result):
            raise ValueError('duplicate trace id')
        versions = {t.agent_version for t in result.values()}
        if len(versions) != 1:
            raise ValueError('one explicit agent version per arm required')
        return result
    left, right = index(baseline), index(candidate)
    if set(left) != set(right):
        raise ValueError('candidate must match complete baseline task/family/split/repeat inventory')
    if next(iter(left.values())).agent_version == next(iter(right.values())).agent_version:
        raise ValueError('baseline and candidate versions must differ')
    # Pairing also binds authored context, not merely reusable identifiers.
    for key in left:
        def inputs(t):
            origins = t.metadata.get('message_sources', {})
            return [(m.role, m.content, m.reasoning, [(c.name, c.arguments) for c in m.tool_calls])
                    for m in t.messages if m.role not in ['system', 'developer'] and origins.get(m.step_id) == 'input']
        if not inputs(left[key]) or inputs(left[key]) != inputs(right[key]):
            raise ValueError('paired authored context differs or is absent')
    return left, right


def sample(rows, count, seed):
    groups = defaultdict(list)
    for row in rows:
        if row['split'] == 'working':
            groups[row['family_id']].append(row)
    rng = random.Random(seed)
    keys = sorted(groups); rng.shuffle(keys)
    for key in keys:
        groups[key].sort(key=lambda row: row['id']); rng.shuffle(groups[key])
    selected = []
    while len(selected) < count and any(groups.values()):
        for key in keys:
            if groups[key] and len(selected) < count:
                selected.append(groups[key].pop())
    if not selected:
        raise ValueError('at least one working trace is needed for annotation')
    return selected


def seal(config_path, workspace):
    config_path = Path(config_path).resolve()
    config = Config.model_validate(yaml.load(config_path.read_text(), Loader=UniqueSafeLoader))
    if workspace.exists():
        raise ValueError('workspace exists; use a new directory or resume')
    sources = {}
    for key in ['baseline', 'candidate', 'baseline_judge', 'candidate_judge', 'rubric', 'mapping', 'fixture_annotations']:
        value = getattr(config, key)
        if value is not None:
            path = (config_path.parent / value).resolve()
            sources[key] = (path, path.read_bytes())
    baseline = records(sources['baseline'][0], sources.get('mapping', (None,))[0])
    candidate = records(sources['candidate'][0], sources.get('mapping', (None,))[0])
    paired_inventory(baseline, candidate)
    rubric_hash = hashlib.sha256(sources['rubric'][1]).hexdigest()
    judges = [read(sources[key][0]) for key in ['baseline_judge', 'candidate_judge']]
    for rows, judge in zip([baseline, candidate], judges):
        data = metrics.prepare(rows, [], judge, rubric_hash, config.dataset_version, 'test_fixture' if config.mode == 'scripted_demo' else 'human_validation')
        if any(case.judge is None for case in data.cases):
            raise ValueError('complete saved judge coverage required')
    left_identity, right_identity = [dict(judge['judge_identity']) for judge in judges]
    left_identity.pop('run_id'); right_identity.pop('run_id')
    if left_identity != right_identity:
        raise ValueError('comparison requires the same judge model/version/prompt/config')
    selected = sample(baseline, config.max_cases, config.seed)
    # Validate viewer support before creating any workspace.
    for row in selected:
        ann.project_trace(Trace.model_validate(row))
    workspace.mkdir(parents=True)
    sealed = {}
    def put(name, payload):
        write_once(workspace / name, payload); sealed[name] = sha(workspace / name)
    put('config.json', encode(config.model_dump()))
    for key, (path, payload) in sources.items():
        put('inputs/' + key + path.suffix, payload)
    put('baseline.json', encode(baseline)); put('candidate.json', encode(candidate))
    put('baseline-judge.json', encode(judges[0])); put('candidate-judge.json', encode(judges[1]))
    put('rubric.txt', sources['rubric'][1])
    put('selected.jsonl', ''.join(encode(row).replace('\n', '') + '\n' for row in selected))
    if config.fixture_annotations:
        put('fixture-annotations.json', sources['fixture_annotations'][1])
    engine = {path.name: sha(path) for path in Path(__file__).parent.glob('*.py')}
    write_once(workspace / 'manifest.json', encode({'format': 'replay-manifest-v1', 'sealed': sealed, 'engine': engine,
        'runtime': runtime_identity(),
        'origins': {key: str(path) for key, (path, _) in sources.items()},
        'mode': config.mode, 'limitations': 'Saved replay only; no inference, calibrated judge, causal result or remote effects.'}))
    ann.prepare(workspace / 'annotations.sqlite3', workspace / 'selected.jsonl', workspace / 'rubric.txt')
    return sha(workspace / 'manifest.json')


def verify_annotation_inventory(workspace):
    expected = {row['id']: row for row in records(workspace / 'selected.jsonl')}
    with sqlite3.connect(workspace / 'annotations.sqlite3') as db:
        rows = db.execute('SELECT source_id,source_hash,source_json,public_json,step_map FROM cases').fetchall()
        rubric = db.execute("SELECT value FROM settings WHERE key='rubric_hash'").fetchone()
    if len(rows) != len(expected) or {row[0] for row in rows} != set(expected) or rubric != (sha(workspace / 'rubric.txt'),):
        raise ValueError('annotation database inventory/rubric changed')
    for tid, fingerprint, source, public_json, steps_json in rows:
        public, steps = ann.project_trace(Trace.model_validate(expected[tid]))
        read_json = strict_json(public_json)
        stored = {k: v for k, v in read_json.items() if k not in ['id', 'number']}
        if fingerprint != ann.digest(expected[tid]) or strict_json(source) != expected[tid] or stored != public or strict_json(steps_json) != steps:
            raise ValueError('annotation source/projection changed')


def check(workspace, state):
    if sha(workspace / 'manifest.json') != state['artifacts']['manifest.json']:
        raise ValueError('manifest changed')
    manifest = read(workspace / 'manifest.json')
    if manifest['runtime'] != runtime_identity():
        raise ValueError('runtime changed; start a new workflow')
    for name, expected in {**manifest['sealed'], **state['artifacts']}.items():
        if sha(workspace / name) != expected:
            raise ValueError('sealed evidence changed: ' + name)
    for name, expected in manifest['engine'].items():
        if sha(Path(__file__).parent / name) != expected:
            raise ValueError('engine changed; start a new workflow: ' + name)
    if 'human-snapshot.json' in state['artifacts'] and manifest['mode'] == 'human':
        verify_annotation_inventory(workspace)
        current = sorted(ann.export_private(workspace / 'annotations.sqlite3'), key=lambda row: row['trace_id'])
        if current != read(workspace / 'human-snapshot.json'):
            raise ValueError('annotation revision changed; start a new workflow snapshot')


def validate_plan(workspace, answer):
    if not isinstance(answer, dict) or set(answer) != {'command', 'plan'} or answer['command'] != 'propose':
        raise ValueError('expected propose with a plan')
    proposal = actions.Plan.model_validate(answer['plan'])
    triage = actions.Triage.model_validate(read(workspace / 'triage.json'))
    report = actions.report(triage, proposal)
    if not proposal.actions:
        raise ValueError('at least one curated action required')
    return proposal, report


def approval_identity(workspace, state):
    return {key: state['artifacts'].get(key) or sha(workspace / key) for key in ['plan.json', 'candidate.json']}


def validate_decision(workspace, state, answer):
    if not isinstance(answer, dict) or set(answer) != {'command', 'reviewer', 'action_id', 'identity'} or answer['command'] not in ['approve', 'reject'] or not isinstance(answer['reviewer'], str) or not answer['reviewer'].strip() or answer['identity'] != approval_identity(workspace, state):
        raise ValueError('invalid/stale approval identity or reviewer')
    if answer['command'] == 'reject':
        return  # Rejection never needs an eligible experiment action.
    selected = [a for a in read(workspace / 'plan.json')['actions'] if a['id'] == answer['action_id']]
    if len(selected) != 1 or selected[0]['verification']['protocol'] != 'working_selection_then_family_heldout_check':
        raise ValueError('select one assistant comparison action')
    population = defaultdict(set)
    for row in read(workspace / 'baseline.json'):
        population[row['task_id']].add(row['metadata']['repeat_id'])
    if any(len(repeats) < selected[0]['verification']['repeat_runs'] for repeats in population.values()):
        raise ValueError('replay does not meet the selected action repeat requirement')


def build(checkpointer, workspace, crash_after_comparison=False):
    def publish(state, phase, files):
        hashes = dict(state['artifacts'])
        for name, content in files.items():
            write_once(workspace / name, content); hashes[name] = sha(workspace / name)
        return {'phase': phase, 'artifacts': hashes}

    def human(state):
        check(workspace, state)
        config = Config.model_validate(read(workspace / 'config.json'))
        answer = interrupt({'kind': 'annotation', 'mode': config.mode, 'database': str(workspace / 'annotations.sqlite3'),
                            'instruction': 'Complete the selected blind queue, then labels-ready. Demo uses test fixtures only.'})
        check(workspace, state)
        if answer != {'command': 'labels-ready'}:
            raise ValueError('expected labels-ready')
        selected = records(workspace / 'selected.jsonl')
        if config.mode == 'scripted_demo':
            rows = read(workspace / 'fixture-annotations.json')
            if any(row['annotation']['annotator'] != 'synthetic_test_fixture' for row in rows):
                raise ValueError('demo labels must name synthetic_test_fixture')
        else:
            verify_annotation_inventory(workspace)
            rows = ann.export_private(workspace / 'annotations.sqlite3')
        if {row['trace_id'] for row in rows} != {row['id'] for row in selected} or len(rows) != len(selected):
            raise ValueError('all selected traces must have one annotation')
        rows = sorted(rows, key=lambda row: row['trace_id'])
        triage = actions.prepare(read(workspace / 'baseline.json'), rows, sha(workspace / 'rubric.txt'), config.dataset_version,
                                 'test_fixture' if config.mode == 'scripted_demo' else 'human_reference')
        data = metrics.prepare(read(workspace / 'baseline.json'), rows, read(workspace / 'baseline-judge.json'),
                               sha(workspace / 'rubric.txt'), config.dataset_version,
                               'test_fixture' if config.mode == 'scripted_demo' else 'human_validation')
        report = metrics.compare(data)
        return publish(state, 'plan', {'human-snapshot.json': encode(rows), 'triage.json': triage.model_dump_json(indent=2) + '\n',
            'judge-validation.json': encode(report), 'judge-validation.md': metrics.markdown(report),
            'plan-template.json': encode(actions.template(triage))})

    def plan(state):
        check(workspace, state)
        answer = interrupt({'kind': 'plan', 'template': str(workspace / 'plan-template.json'),
                            'instruction': 'Curate working evidence; provide a failure-action-plan-v1 JSON file.'})
        check(workspace, state)
        proposal, report = validate_plan(workspace, answer)
        files = {'plan.json': proposal.model_dump_json(indent=2) + '\n', 'actions.json': encode(report), 'actions.md': actions.markdown(report)}
        for action in report['actions']:
            files['drafts/' + action['id'] + '.md'] = actions.ticket(action, report['data_kind'])
        return publish(state, 'approval', files)

    def approval(state):
        check(workspace, state)
        identity = approval_identity(workspace, state)
        answer = interrupt({'kind': 'approval', 'identity': identity, 'instruction': 'Approve one draft and this exact saved candidate replay, or reject.'})
        check(workspace, state)
        validate_decision(workspace, state, answer)
        audit = dict(answer, manifest_sha256=state['artifacts']['manifest.json'],
                     human_snapshot_sha256=state['artifacts']['human-snapshot.json'])
        change = publish(state, answer['command'], {'decision.json': encode(audit)})
        change['decision'] = answer['command']
        return change

    def comparison(state):
        check(workspace, state)
        left, right = paired_inventory(read(workspace / 'baseline.json'), read(workspace / 'candidate.json'))
        judges = [{a['trace_id']: a['verdict'] for a in read(workspace / name)['assessments']} for name in ['baseline-judge.json', 'candidate-judge.json']]
        pairs = [{'task_id': key[0], 'family_id': key[1], 'split': key[2], 'repeat_id': key[3],
                  'baseline': judges[0][left[key].id], 'candidate': judges[1][right[key].id]} for key in sorted(left)]
        splits = {}
        for split in ['working', 'heldout']:
            rows = [row for row in pairs if row['split'] == split]
            families = defaultdict(list)
            tasks = defaultdict(list)
            for row in rows:
                tasks[row['task_id']].append(row)
            for task_rows in tasks.values():
                families[task_rows[0]['family_id']].append(sum((r['candidate'] == 'correct') - (r['baseline'] == 'correct') for r in task_rows) / len(task_rows))
            values = [sum(v) / len(v) for v in families.values()]
            splits[split] = {'pairs': len(rows), 'tasks': len(tasks), 'families': len(families),
                'baseline_correct': sum(r['baseline'] == 'correct' for r in rows), 'candidate_correct': sum(r['candidate'] == 'correct' for r in rows),
                'baseline_unassessable': sum(r['baseline'] == 'unassessable' for r in rows), 'candidate_unassessable': sum(r['candidate'] == 'unassessable' for r in rows),
                'equal_family_task_mean_correct_delta': sum(values) / len(values) if values else None}
        mode = read(workspace / 'manifest.json')['mode']
        report = {'format': 'paired-replay-comparison-v1', 'mode': 'saved_replay',
                  'data_kind': 'test_fixture' if mode == 'scripted_demo' else 'human_reference',
                  'approval_sha256': state['artifacts']['decision.json'], 'splits': splits,
                  'working_pairs': [p for p in pairs if p['split'] == 'working'],
                  'limitations': ['Judge labels are saved, uncalibrated measurements; no new model execution or causal improvement.',
                    'Unassessable is retained as a separate verdict and counted as not-correct in the declared delta.',
                    'Repeats are averaged by task then family; no independence or confidence-interval claim.',
                    'Heldout aggregate only; no automatic promotion or acceptance gate.',
                    'Candidate was supplied in advance; approval authorizes only replay, not applying the proposed intervention.']}
        result = publish(state, 'complete', {'comparison.json': encode(report),
            'comparison.md': '# Saved paired replay\n\n' + '\n'.join('- ' + x for x in report['limitations']) + '\n\n```json\n' + encode(splits) + '```\n'})
        if crash_after_comparison:
            raise RuntimeError('simulated crash after deterministic comparison write')
        return result

    graph = StateGraph(State)
    for name, node in [('human', human), ('plan', plan), ('approval', approval), ('comparison', comparison)]:
        graph.add_node(name, node)
    graph.add_edge(START, 'human'); graph.add_edge('human', 'plan'); graph.add_edge('plan', 'approval')
    graph.add_conditional_edges('approval', lambda s: s['decision'], {'approve': 'comparison', 'reject': END})
    graph.add_edge('comparison', END)
    return graph.compile(checkpointer=checkpointer)


def operate(workspace, command, *, config_path=None, answer=None, crash_after_comparison=False):
    workspace = Path(workspace).resolve()
    manifest_hash = seal(config_path, workspace) if command == 'start' else None
    if not (workspace / 'checkpoints.sqlite3').exists() and command != 'start':
        raise ValueError('workflow not started')
    config = {'configurable': {'thread_id': 'improvement-replay'}, 'recursion_limit': 12}
    with SqliteSaver.from_conn_string(str(workspace / 'checkpoints.sqlite3')) as cp:
        graph = build(cp, workspace, crash_after_comparison)
        snapshot = graph.get_state(config)
        if command == 'start':
            graph.invoke({'phase': 'annotation', 'artifacts': {'manifest.json': manifest_hash}}, config)
        else:
            check(workspace, snapshot.values)
            if command != 'status':
                pending = [i for task in snapshot.tasks for i in task.interrupts]
                if command == 'resume':
                    if pending or not snapshot.next:
                        raise ValueError('resume is for interrupted execution errors; use the requested checkpoint command')
                    graph.invoke(None, config)
                else:
                    expected = {'labels-ready': 'annotation', 'propose': 'plan', 'approve': 'approval', 'reject': 'approval'}
                    if command not in expected or len(pending) != 1 or pending[0].value['kind'] != expected[command]:
                        raise ValueError('command does not match pending checkpoint')
                    if answer is not None and (not isinstance(answer, dict) or answer.get('command') != command):
                        raise ValueError('payload command does not match requested command')
                    # Invalid payloads must not enter LangGraph's cached interrupt
                    # resume history: users can correct them in this same workspace.
                    if command == 'propose':
                        validate_plan(workspace, answer)
                    elif command in ['approve', 'reject']:
                        validate_decision(workspace, snapshot.values, answer)
                    elif answer not in [None, {'command': 'labels-ready'}]:
                        raise ValueError('expected labels-ready')
                    graph.invoke(Command(resume=answer or {'command': command}), config)
        snapshot = graph.get_state(config)
        return {'phase': snapshot.values['phase'], 'next': list(snapshot.next),
                'checkpoints': [i.value for task in snapshot.tasks for i in task.interrupts],
                'artifacts': snapshot.values['artifacts'], 'workspace': str(workspace)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'labels-ready', 'propose', 'approve', 'reject', 'resume'])
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--config'); parser.add_argument('--plan')
    parser.add_argument('--reviewer'); parser.add_argument('--action-id')
    parser.add_argument('--plan-sha256'); parser.add_argument('--candidate-sha256')
    args = parser.parse_args()
    answer = None
    try:
        if args.command == 'propose':
            answer = {'command': 'propose', 'plan': read(args.plan)}
        elif args.command in ['approve', 'reject']:
            answer = {'command': args.command, 'reviewer': args.reviewer, 'action_id': args.action_id,
                      'identity': {'plan.json': args.plan_sha256, 'candidate.json': args.candidate_sha256}}
        print(encode(operate(args.workspace, args.command, config_path=args.config, answer=answer)))
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
