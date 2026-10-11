"""Analyst-only failure triage and curated action drafts; no inference or effects."""
import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from .schemas import Model, Trace
from .annotation import digest, project_trace, validate_label
from .import_traces import strict_json

Kind = Literal['assistant_failure', 'scenario_defect', 'evaluation_gap']


class SavedLabel(Model):
    verdict: Literal['correct', 'incorrect', 'unassessable']
    annotator: str = Field(min_length=1)
    evidence_steps: list[str]
    error_types: list[str]
    comment: str
    scenario_problem: bool
    scenario_evidence: list[str]
    scenario_comment: str
    revision: int = Field(ge=1)
    saved_at: str = Field(min_length=1)


class Snapshot(Model):
    case_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    family_id: str = Field(min_length=1)
    split: Literal['working', 'heldout']
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    rubric_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    assessor_kind: Literal['human']
    annotation: SavedLabel
    original_evidence_steps: list[str]
    original_scenario_evidence: list[str]


def validate_snapshot(trace, snapshot, rubric):
    if snapshot.trace_id != trace.id or snapshot.source_sha256 != digest(trace.model_dump(mode='json')):
        raise ValueError('annotation trace/source mismatch')
    if snapshot.rubric_sha256 != rubric:
        raise ValueError('annotation rubric mismatch')
    for key in ['task_id', 'family_id', 'split']:
        if getattr(snapshot, key) != getattr(trace, key):
            raise ValueError('annotation task/family/split mismatch')
    public, steps = project_trace(trace)
    data = snapshot.annotation.model_dump(exclude={'revision', 'saved_at'})
    validate_label(dict(data, expected_revision=snapshot.annotation.revision - 1), public)
    if snapshot.original_evidence_steps != [steps[s] for s in snapshot.annotation.evidence_steps]:
        raise ValueError('original evidence linkage mismatch')
    if snapshot.original_scenario_evidence != [steps[s] for s in snapshot.annotation.scenario_evidence]:
        raise ValueError('original scenario evidence linkage mismatch')


class WorkingCase(Model):
    trace: Trace
    snapshot: Snapshot


class Coverage(Model):
    traces: int = Field(ge=0)
    tasks: int = Field(ge=0)
    families: int = Field(ge=0)
    repeat_traces_beyond_tasks: int = Field(ge=0)
    annotated: int = Field(ge=0)
    unannotated: int = Field(ge=0)
    correct: int = Field(ge=0)
    incorrect: int = Field(ge=0)
    unassessable: int = Field(ge=0)
    scenario_flags: int = Field(ge=0)

    @model_validator(mode='after')
    def totals(self):
        if (self.traces > 0 and self.families == 0) or not self.traces >= self.tasks >= self.families:
            raise ValueError('invalid population/task/family counts')
        if self.annotated + self.unannotated != self.traces or self.correct + self.incorrect + self.unassessable != self.annotated:
            raise ValueError('coverage/verdict totals mismatch')
        if self.repeat_traces_beyond_tasks != self.traces - self.tasks or self.scenario_flags > self.annotated:
            raise ValueError('repeat/scenario totals mismatch')
        return self


class Triage(Model):
    format: Literal['failure-triage-v1'] = 'failure-triage-v1'
    data_kind: Literal['test_fixture', 'human_reference']
    dataset_version: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    rubric_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    inventory_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    annotation_snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    coverage: dict[str, Coverage]
    working_cases: list[WorkingCase]

    @model_validator(mode='after')
    def links(self):
        if set(self.coverage) != {'working', 'heldout'}:
            raise ValueError('working and heldout coverage required')
        working = self.coverage['working']
        labels = [c.snapshot.annotation for c in self.working_cases]
        if working.annotated != len(labels) or any(getattr(working, v) != sum(l.verdict == v for l in labels) for v in ['correct', 'incorrect', 'unassessable']):
            raise ValueError('working labels/coverage mismatch')
        if working.scenario_flags != sum(l.scenario_problem for l in labels):
            raise ValueError('working scenario count mismatch')
        observed_tasks = {c.trace.task_id for c in self.working_cases}
        observed_families = {c.trace.family_id for c in self.working_cases}
        for declared, observed in [(working.tasks, len(observed_tasks)), (working.families, len(observed_families))]:
            if not observed <= declared <= observed + working.unannotated:
                raise ValueError('working task/family counts impossible given retained labels and missing traces')
        ids = set(); case_ids = set(); task_families = {}
        for case in self.working_cases:
            if case.trace.id in ids or case.snapshot.case_id in case_ids:
                raise ValueError('duplicate working trace/case identity')
            ids.add(case.trace.id); case_ids.add(case.snapshot.case_id)
            if case.trace.split != 'working' or case.trace.agent_version != self.agent_version:
                raise ValueError('only working traces of declared agent version can be triaged')
            if case.trace.task_id in task_families and task_families[case.trace.task_id] != case.trace.family_id:
                raise ValueError('working task has conflicting families')
            task_families[case.trace.task_id] = case.trace.family_id
            validate_snapshot(case.trace, case.snapshot, self.rubric_sha256)
        return self


class Issue(Model):
    id: str
    kind: Kind
    trace_id: str
    task_id: str
    family_id: str
    observation: str
    evidence_steps: list[str]
    error_types: list[str]
    source_sha256: str
    annotation_sha256: str


class Verification(Model):
    primary_measure: str = Field(min_length=1)
    success_criterion: str = Field(min_length=1)
    regression_checks: list[str] = Field(min_length=1)
    repeat_runs: int | None = Field(default=None, ge=2)
    keep_fixed: list[str] = Field(min_length=1)
    protocol: Literal['working_selection_then_family_heldout_check', 'scenario_revision_review', 'evidence_investigation']

    @model_validator(mode='after')
    def repeats(self):
        if self.protocol == 'working_selection_then_family_heldout_check' and self.repeat_runs is None:
            raise ValueError('assistant experiment needs at least two repeats')
        if self.protocol != 'working_selection_then_family_heldout_check' and self.repeat_runs is not None:
            raise ValueError('repeat_runs belongs to assistant comparison, not data review or diagnosis')
        return self


class Action(Model):
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    title: str = Field(min_length=1)
    issue_ids: list[str] = Field(min_length=1)
    observed_summary: str = Field(min_length=1)
    cause_hypothesis: str | None = None
    intervention: Literal['prompt', 'context', 'tools', 'scaffold', 'retrieval', 'model', 'training_data', 'post_training', 'scenario_data', 'investigation']
    recipient: Literal['product', 'backend', 'ml', 'evaluation']
    proposed_change: str = Field(min_length=1)
    verification: Verification
    status: Literal['draft_pending_human_selection'] = 'draft_pending_human_selection'

    @model_validator(mode='after')
    def meaningful(self):
        strings = [self.title, self.observed_summary, self.proposed_change,
                   self.verification.primary_measure, self.verification.success_criterion,
                   *self.verification.regression_checks, *self.verification.keep_fixed]
        if self.cause_hypothesis is not None:
            strings.append(self.cause_hypothesis)
        if any(not value.strip() or value.strip().lower() in ['todo', 'tbd'] for value in strings):
            raise ValueError('fill concrete observation/change/verification fields')
        if len(set(self.issue_ids)) != len(self.issue_ids):
            raise ValueError('duplicate issue in action')
        return self


class Plan(Model):
    format: Literal['failure-action-plan-v1'] = 'failure-action-plan-v1'
    triage_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    analyst: str = Field(min_length=1)
    actions: list[Action]


def records(path):
    text = Path(path).read_text()
    value = [strict_json(line) for line in text.splitlines() if line.strip()] if Path(path).suffix == '.jsonl' else strict_json(text)
    if not isinstance(value, list):
        raise ValueError('expected JSON array or JSONL records')
    return value


def prepare(traces, annotations, rubric, dataset_version, data_kind):
    inventory = [Trace.model_validate(row) for row in traces]
    if not inventory or len({t.id for t in inventory}) != len(inventory):
        raise ValueError('nonempty unique complete trace inventory required')
    by_id = {t.id: t for t in inventory}; family_splits = {}; tasks = {}; agent_versions = set()
    for trace in inventory:
        if not trace.task_id or not trace.family_id or not trace.split or not trace.agent_version:
            raise ValueError('explicit task/family/split/agent version required')
        if trace.task is not None:
            for key in ['family_id', 'split']:
                value = getattr(trace.task, key)
                if value is not None and value != getattr(trace, key):
                    raise ValueError('embedded task identity conflict')
        if trace.family_id in family_splits and family_splits[trace.family_id] != trace.split:
            raise ValueError('family crosses working/heldout')
        family_splits[trace.family_id] = trace.split
        identity = (trace.family_id, trace.split)
        if trace.task_id in tasks and tasks[trace.task_id] != identity:
            raise ValueError('task family/split conflict')
        tasks[trace.task_id] = identity; agent_versions.add(trace.agent_version)
    if len(agent_versions) != 1:
        raise ValueError('select one explicit agent version')
    snapshots = {}; case_ids = set()
    for row in annotations:
        snap = Snapshot.model_validate(row)
        if snap.trace_id not in by_id or snap.trace_id in snapshots or snap.case_id in case_ids:
            raise ValueError('unknown/duplicate annotation trace/case')
        validate_snapshot(by_id[snap.trace_id], snap, rubric)
        snapshots[snap.trace_id] = snap; case_ids.add(snap.case_id)
    coverage = {}
    for split in ['working', 'heldout']:
        population = [t for t in inventory if t.split == split]
        annotated = [snapshots[t.id] for t in population if t.id in snapshots]
        verdicts = Counter(s.annotation.verdict for s in annotated)
        coverage[split] = {'traces': len(population), 'tasks': len({t.task_id for t in population}),
                           'families': len({t.family_id for t in population}),
                           'repeat_traces_beyond_tasks': len(population) - len({t.task_id for t in population}),
                           'annotated': len(annotated), 'unannotated': len(population) - len(annotated),
                           **{v: verdicts[v] for v in ['correct', 'incorrect', 'unassessable']},
                           'scenario_flags': sum(s.annotation.scenario_problem for s in annotated)}
    return Triage(data_kind=data_kind, dataset_version=dataset_version, agent_version=next(iter(agent_versions)),
                  rubric_sha256=rubric, inventory_sha256=digest([t.model_dump(mode='json') for t in inventory]),
                  annotation_snapshot_sha256=digest([s.model_dump(mode='json') for s in snapshots.values()]),
                  coverage=coverage,
                  working_cases=[WorkingCase(trace=t, snapshot=snapshots[t.id]) for t in inventory if t.split == 'working' and t.id in snapshots])


def issues(triage):
    result = []
    for case in triage.working_cases:
        snap = case.snapshot; label = snap.annotation
        candidates = []
        if label.verdict == 'incorrect':
            candidates.append(('assistant_failure', label.comment or 'Human reference marked this continuation incorrect; no explanation supplied.', snap.original_evidence_steps, label.error_types))
        if label.scenario_problem:
            candidates.append(('scenario_defect', label.scenario_comment, snap.original_scenario_evidence, []))
        if label.verdict == 'unassessable':
            candidates.append(('evaluation_gap', label.comment, snap.original_evidence_steps, []))
        for kind, observation, evidence, error_types in candidates:
            ahash = digest(snap.model_dump(mode='json'))
            iid = 'issue-' + digest([snap.trace_id, snap.source_sha256, ahash, kind])[:32]
            result.append(Issue(id=iid, kind=kind, trace_id=snap.trace_id, task_id=snap.task_id, family_id=snap.family_id,
                                observation=observation, evidence_steps=evidence, error_types=error_types,
                                source_sha256=snap.source_sha256, annotation_sha256=ahash))
    return sorted(result, key=lambda i: i.id)


def template(triage):
    return {'format': 'failure-action-plan-v1', 'triage_sha256': digest(triage.model_dump(mode='json')), 'analyst': '',
            'actions': [dict(id='action-' + str(n), title='', issue_ids=[i.id], observed_summary='', cause_hypothesis=None,
                             intervention=None, recipient=None, proposed_change='', status='draft_pending_human_selection',
                             verification=dict(primary_measure='', success_criterion='', regression_checks=[], repeat_runs=2,
                                               keep_fixed=[], protocol=None))
                        for n, i in enumerate(issues(triage), 1)]}


def report(triage, plan):
    if not isinstance(triage, Triage):
        triage = Triage.model_validate(triage)
    if not isinstance(plan, Plan):
        plan = Plan.model_validate(plan)
    if not plan.analyst.strip() or plan.triage_sha256 != digest(triage.model_dump(mode='json')):
        raise ValueError('plan analyst/triage hash mismatch')
    candidates = {i.id: i for i in issues(triage)}; seen = set(); case_map = {c.trace.id: c for c in triage.working_cases}
    drafts = []
    for action in plan.actions:
        if action.id in seen:
            raise ValueError('duplicate action id')
        seen.add(action.id)
        if not set(action.issue_ids) <= set(candidates):
            raise ValueError('unknown/non-working issue referenced')
        selected = [candidates[i] for i in action.issue_ids]; kinds = {i.kind for i in selected}
        if len(kinds) != 1:
            raise ValueError('keep assistant/scenario/evaluation actions separate')
        kind = next(iter(kinds))
        if kind == 'scenario_defect' and (action.recipient != 'evaluation' or action.intervention not in ['scenario_data', 'investigation']):
            raise ValueError('scenario defect is an evaluation-data issue, not an assistant-training task')
        if kind == 'evaluation_gap' and (action.recipient not in ['evaluation', 'backend'] or action.intervention != 'investigation'):
            raise ValueError('unassessable gap needs investigation before an assistant intervention')
        if action.intervention in ['training_data', 'post_training', 'model'] and action.recipient != 'ml':
            raise ValueError('model/training proposal recipient must be ML')
        expected_protocol = 'evidence_investigation' if action.intervention == 'investigation' else 'scenario_revision_review' if kind == 'scenario_defect' else 'working_selection_then_family_heldout_check'
        if action.verification.protocol != expected_protocol:
            raise ValueError('verification protocol does not match proposed action scope')
        linked = []
        for issue in selected:
            case = case_map[issue.trace_id]; messages = {m.step_id: m for m in case.trace.messages}
            linked.append(dict(issue=issue.model_dump(), human_snapshot=case.snapshot.model_dump(mode='json'),
                               evidence=[messages[s].model_dump(mode='json') for s in issue.evidence_steps]))
        drafts.append(dict(**action.model_dump(), issue_kind=kind, analyst=plan.analyst,
                           cause_status='unverified_hypothesis' if action.cause_hypothesis is not None else 'unknown',
                           support_counts={'traces': len({i.trace_id for i in selected}), 'tasks': len({i.task_id for i in selected}),
                                           'families': len({i.family_id for i in selected})}, evidence=linked))
    return {'format': 'failure-action-report-v1', 'data_kind': triage.data_kind,
            'dataset_version': triage.dataset_version, 'agent_version': triage.agent_version,
            'rubric_sha256': triage.rubric_sha256, 'triage_sha256': digest(triage.model_dump(mode='json')),
            'plan_sha256': digest(plan.model_dump(mode='json')), 'coverage': {k: c.model_dump() for k, c in triage.coverage.items()},
            'issue_counts': dict(Counter(i.kind for i in candidates.values())),
            'unassigned_issue_ids': sorted(set(candidates) - {i for a in plan.actions for i in a.issue_ids}),
            'label_inventory': [c.snapshot.model_dump(mode='json') for c in triage.working_cases],
            'actions': drafts,
            'limitations': ['All proposals are drafts pending human selection; nothing is executed.',
                            'A human reference/error type does not prove a cause; interventions are curated proposals.',
                            'Heldout is aggregate only and cannot supply evidence for tuning here.',
                            'Counts are descriptive; repeated traces are not independent tasks or families.',
                            'TEST FIXTURE ONLY; no actual human gold or improvement.' if triage.data_kind == 'test_fixture' else 'Human reference provenance is self-declared; no automatic correctness or improvement claim.']}


def safe_text(value):
    return str(value).replace('|', r'\|').replace('\r', ' ').replace('\n', ' ')


def ticket(action, data_kind):
    lines = ['---', 'artifact: improvement_task_draft', f'proposal_status: {action["status"]}', f'data_kind: {data_kind}',
             f'id: {action["id"]}', f'recipient: {action["recipient"]}', '---', '', '# ' + safe_text(action['title']), '',
             '**Draft for human selection. No execution or measured effect.**', '',
             'Issue type: ' + action['issue_kind'], '', 'Observed human-reference finding: ' + safe_text(action['observed_summary']), '',
             'Cause status: ' + action['cause_status'] + '.',
             'Unverified cause hypothesis: ' + safe_text(action['cause_hypothesis'] or 'Unknown; investigation is allowed.'), '',
             'Proposed intervention: ' + action['intervention'] + '.', 'Proposed change: ' + safe_text(action['proposed_change']), '',
             'Support: ' + json.dumps(action['support_counts']), '', '## Source evidence', '']
    for link in action['evidence']:
        issue = link['issue']; label = link['human_snapshot']['annotation']
        lines += [f'- {safe_text(issue["trace_id"])}: steps {safe_text(issue["evidence_steps"])}; assessor {safe_text(label["annotator"])}; revision {label["revision"]}; source SHA256 {issue["source_sha256"]}.',
                  '  Reference observation: ' + safe_text(issue['observation']), '```json', json.dumps(link['evidence'], ensure_ascii=False, indent=2), '```', '']
    lines += ['## Verification plan', '', '```json', json.dumps(action['verification'], ensure_ascii=False, indent=2), '```', '',
              'Human review: check the evidence and hypothesis, choose one intervention, agree a bounded experiment. This file is not a started project task.', '']
    return '\n'.join(lines)


def markdown(result):
    lines = ['# Failure-to-action drafts', '', '**' + result['data_kind'].upper() + '**', '', *['- ' + l for l in result['limitations']], '',
             '## Coverage', '', '| Split | Traces | Tasks | Families | Repeats beyond tasks | Annotated | Missing | Incorrect | Unassessable | Scenario flags |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for split, c in result['coverage'].items():
        lines.append('| ' + ' | '.join(map(str, [split, c['traces'], c['tasks'], c['families'], c['repeat_traces_beyond_tasks'], c['annotated'], c['unannotated'], c['incorrect'], c['unassessable'], c['scenario_flags']])) + ' |')
    lines += ['', 'Working issue counts: ' + json.dumps(result['issue_counts']) + '.',
              'Unassigned working issues: ' + str(len(result['unassigned_issue_ids'])) + '.', '', '## Proposed tasks', '',
              '| Draft | Issue type | Recipient | Intervention | Traces / tasks / families | Cause |', '|---|---|---|---|---|---|']
    for a in result['actions']:
        c = a['support_counts']; lines.append(f'| [{safe_text(a["title"])}](drafts/{a["id"]}.md) | {a["issue_kind"]} | {a["recipient"]} | {a["intervention"]} | {c["traces"]} / {c["tasks"]} / {c["families"]} | {a["cause_status"]} |')
    lines += ['', 'Only working evidence is included. Full label provenance and unassigned findings remain in JSON/triage; heldout identifiers/content are omitted.', '']
    return '\n'.join(lines)


def publish(target, files):
    target = Path(target)
    if target.exists():
        raise ValueError('output directory exists; use a fresh path')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temp:
        staged = Path(temp) / 'result'; staged.mkdir()
        for name, value in files.items():
            path = staged / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        os.rename(staged, target)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for key in ['traces', 'human', 'rubric', 'dataset-version', 'data-kind', 'output-dir']:
        p.add_argument('--' + key, required=True)
    p = sub.add_parser('report')
    for key in ['triage', 'plan', 'output-dir']:
        p.add_argument('--' + key, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            triage = prepare(records(args.traces), records(args.human), hashlib.sha256(Path(args.rubric).read_bytes()).hexdigest(), args.dataset_version, args.data_kind)
            issue_rows = [i.model_dump() for i in issues(triage)]
            publish(args.output_dir, {'triage.json': triage.model_dump_json(indent=2) + '\n',
                                      'issues.json': encoded(issue_rows), 'plan-template.json': encoded(template(triage))})
        else:
            triage = Triage.model_validate(strict_json(Path(args.triage).read_text())); plan = Plan.model_validate(strict_json(Path(args.plan).read_text()))
            result = report(triage, plan); files = {'report.json': encoded(result), 'report.md': markdown(result)}
            files.update({'drafts/' + a['id'] + '.md': ticket(a, result['data_kind']) for a in result['actions']})
            publish(args.output_dir, files)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
