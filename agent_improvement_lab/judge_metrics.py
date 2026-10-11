"""Deterministic, analyst-only comparison of frozen human and judge decisions."""
import argparse
import hashlib
import json
import math
import os
import random
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from .schemas import Model, Trace, Assessment
from .import_traces import strict_json
from .annotation import digest

LABELS = ['correct', 'incorrect', 'unassessable']
Verdict = Literal['correct', 'incorrect', 'unassessable']


class Decision(Model):
    verdict: Verdict
    assessor_id: str = Field(min_length=1)
    evidence_steps: list[str] = Field(default_factory=list)
    explanation: str = ''
    revision: int | None = Field(default=None, ge=1)
    scenario_problem: bool = False
    scenario_evidence: list[str] = Field(default_factory=list)
    scenario_comment: str = ''
    error_types: list[str] = Field(default_factory=list)
    saved_at: str | None = None

    @model_validator(mode='after')
    def evidence_shape(self):
        for steps in [self.evidence_steps, self.scenario_evidence]:
            if len(set(steps)) != len(steps):
                raise ValueError('duplicate evidence step')
        if self.scenario_problem and not self.scenario_comment.strip():
            raise ValueError('scenario issue needs explanation')
        if not self.scenario_problem and (self.scenario_evidence or self.scenario_comment):
            raise ValueError('scenario evidence requires scenario flag')
        return self


class JudgeIdentity(Model):
    version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    config_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class Case(Model):
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    family_id: str = Field(min_length=1)
    split: Literal['working', 'heldout']
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    length_chars: int = Field(ge=0)
    noise: Literal['clean', 'noisy', 'unknown'] = 'unknown'
    human: Decision | None = None
    judge: Decision | None = None


class Input(Model):
    format: Literal['judge-validation-input-v1'] = 'judge-validation-input-v1'
    data_kind: Literal['test_fixture', 'human_validation']
    dataset_version: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    rubric_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    judge_identity: JudgeIdentity
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode='after')
    def inventory(self):
        seen = set(); families = {}; tasks = {}
        for case in self.cases:
            if case.trace_id in seen:
                raise ValueError('duplicate trace in declared population')
            seen.add(case.trace_id)
            if case.family_id in families and families[case.family_id] != case.split:
                raise ValueError('family crosses working/heldout split')
            families[case.family_id] = case.split
            identity = (case.family_id, case.split)
            if case.task_id in tasks and tasks[case.task_id] != identity:
                raise ValueError('task has conflicting family/split')
            tasks[case.task_id] = identity
            if case.human is not None and case.human.revision is None:
                raise ValueError('human decision requires latest saved revision')
            if case.judge is not None and (case.judge.revision is not None or case.judge.saved_at is not None or case.judge.scenario_problem):
                raise ValueError('judge cannot carry human revision/scenario annotation')
        return self


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def point(cases):
    pairs = [c for c in cases if c.human is not None and c.judge is not None]
    matrix = [[0] * 3 for _ in LABELS]
    for c in pairs:
        matrix[LABELS.index(c.human.verdict)][LABELS.index(c.judge.verdict)] += 1
    classes = {}
    for i, label in enumerate(LABELS):
        tp = matrix[i][i]; support = sum(matrix[i]); predicted = sum(r[i] for r in matrix)
        classes[label] = {'true_positive': tp, 'false_positive': predicted - tp,
                          'false_negative': support - tp, 'support': support,
                          'predicted': predicted, 'precision': ratio(tp, predicted),
                          'recall': ratio(tp, support), 'f1': ratio(2 * tp, support + predicted)}
    family_pairs = defaultdict(list)
    for c in pairs:
        family_pairs[c.family_id].append(int(c.human.verdict == c.judge.verdict))
    comparable = [c for c in pairs if c.human.verdict != 'unassessable' and c.judge.verdict != 'unassessable']
    human = [c for c in cases if c.human is not None]
    judge = [c for c in cases if c.judge is not None]
    n = len(cases); paired = len(pairs)
    return {'counts': {'population': n, 'unique_tasks': len({c.task_id for c in cases}),
                        'families': len({c.family_id for c in cases}), 'paired_families': len(family_pairs),
                        'human_present': len(human), 'human_missing': n - len(human),
                        'judge_present': len(judge), 'judge_missing': n - len(judge),
                        'paired': paired, 'unpaired': n - paired,
                        'human_unassessable': sum(c.human.verdict == 'unassessable' for c in human),
                        'judge_unassessable': sum(c.judge.verdict == 'unassessable' for c in judge),
                        'both_assessable': len(comparable),
                        'scenario_problem': sum(c.human.scenario_problem for c in human)},
            'coverage': {'human': ratio(len(human), n), 'judge': ratio(len(judge), n), 'paired': ratio(paired, n),
                         'human_unassessable_given_present': ratio(sum(c.human.verdict == 'unassessable' for c in human), len(human)),
                         'judge_unassessable_given_present': ratio(sum(c.judge.verdict == 'unassessable' for c in judge), len(judge))},
            'confusion': {'labels': LABELS, 'rows': 'human', 'columns': 'judge', 'matrix': matrix},
            'per_class': classes,
            'paired_exact_agreement': {'value': ratio(sum(matrix[i][i] for i in range(3)), paired), 'denominator': paired},
            'equal_family_agreement': {'value': ratio(sum(sum(v) / len(v) for v in family_pairs.values()), len(family_pairs)), 'denominator': len(family_pairs)},
            'both_assessable_agreement': {'value': ratio(sum(c.human.verdict == c.judge.verdict for c in comparable), len(comparable)), 'denominator': len(comparable)}}


def scalar_metrics(result):
    values = {'paired_exact_agreement': result['paired_exact_agreement']['value']}
    for label, metrics in result['per_class'].items():
        for metric in ['precision', 'recall', 'f1']:
            values[label + '.' + metric] = metrics[metric]
    return values


def quantile(values, p):
    values = sorted(values); position = (len(values) - 1) * p
    low = math.floor(position); high = math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def uncertainty(cases, samples, seed):
    # Paired conditional estimand: missingness is reported, not imputed.
    groups = defaultdict(list)
    for c in cases:
        if c.human is not None and c.judge is not None:
            groups[c.family_id].append(c)
    keys = sorted(groups); values = {key: [] for key in scalar_metrics(point(cases))}
    result = {'method': 'family_cluster_percentile', 'confidence_level': .95,
              'resampling_unit': 'family_id', 'paired_families': len(keys),
              'requested_resamples': samples, 'seed': seed,
              'conditioning': 'observed paired labels and sampled families', 'metrics': {}}
    if len(keys) < 2:
        result['status'] = 'insufficient_paired_families'
        result['metrics'] = {k: {'interval': None, 'defined_resamples': 0, 'undefined_resamples': 0} for k in values}
        return result
    rng = random.Random(seed)
    for _ in range(samples):
        # Every selected family contributes all its rows; draws retain multiplicity.
        selected = [c for key in rng.choices(keys, k=len(keys)) for c in groups[key]]
        for key, value in scalar_metrics(point(selected)).items():
            if value is not None:
                values[key].append(value)
    result['status'] = 'descriptive_only'
    for key, vals in values.items():
        interval = [quantile(vals, .025), quantile(vals, .975)] if vals else None
        result['metrics'][key] = {'interval': interval, 'defined_resamples': len(vals),
                                 'undefined_resamples': samples - len(vals),
                                 'conditional_on_defined_resamples': len(vals) != samples,
                                 'degenerate': interval is not None and interval[0] == interval[1]}
    return result


def length_bin(c):
    return 'short_lt_2000' if c.length_chars < 2000 else 'medium_2000_to_15999' if c.length_chars < 16000 else 'long_ge_16000'


def compare(data, samples=2000, seed=17):
    if not isinstance(data, Input):
        data = Input.model_validate(data)
    if type(samples) is not int or not 100 <= samples <= 10000 or type(seed) is not int:
        raise ValueError('use 100..10000 resamples and an integer seed')
    cases = sorted(data.cases, key=lambda c: c.trace_id)
    warnings = ['Agreement measures consistency with this human assessor, not objective correctness or causal improvement.',
                'Intervals condition on observed paired labels; missing labels may be non-random.',
                'Percentile bootstrap may be unreliable with few independent families; it is not an acceptance gate.',
                'Overall metrics pool working/heldout when both are supplied and are descriptive only; read split slices separately.']
    if data.data_kind == 'test_fixture':
        warnings.insert(0, 'TEST FIXTURE ONLY: no human gold, live judge validity or product-quality result.')
    if any(c.noise == 'unknown' for c in cases):
        warnings.append('Noise metadata is unknown for some traces; no noise class is inferred from verdicts.')
    result = {'format': 'judge-validation-report-v1', 'data_kind': data.data_kind,
              'input_sha256': digest(data.model_dump(mode='json')), 'dataset_version': data.dataset_version,
              'agent_version': data.agent_version, 'rubric_sha256': data.rubric_sha256,
              'judge_identity': data.judge_identity.model_dump(), 'warnings': warnings,
              'overall': point(cases), 'uncertainty': uncertainty(cases, samples, seed),
              'slice_definitions': {'version': '1', 'length': 'Unicode character count of JSON-serialized non-system/non-developer content and reasoning plus canonical tool arguments; includes JSON quotes/null; bins <2000, 2000..15999, >=16000',
                                    'noise': 'explicit metadata: clean/noisy/unknown; never guessed',
                                    'split': 'working/heldout kept separate; pooled overall is descriptive only'}, 'slices': {}}
    definitions = {'length': (['short_lt_2000', 'medium_2000_to_15999', 'long_ge_16000'], length_bin),
                   'noise': (['clean', 'noisy', 'unknown'], lambda c: c.noise),
                   'split': (['working', 'heldout'], lambda c: c.split),
                   'family': (sorted({c.family_id for c in cases}), lambda c: c.family_id)}
    for name, (bins, key) in definitions.items():
        result['slices'][name] = {label: point([c for c in cases if key(c) == label]) for label in bins}
    result['label_inventory'] = [c.model_dump(mode='json') for c in cases if c.human is not None or c.judge is not None]
    result['disagreements'] = [dict(trace_id=c.trace_id, task_id=c.task_id, family_id=c.family_id,
                                   human=c.human.model_dump(), judge=c.judge.model_dump())
                                for c in cases if c.human and c.judge and c.human.verdict != c.judge.verdict]
    result['missing_labels'] = [dict(trace_id=c.trace_id, human_missing=c.human is None, judge_missing=c.judge is None)
                                for c in cases if c.human is None or c.judge is None]
    return result


def load_records(path):
    text = Path(path).read_text()
    value = strict_json(text) if Path(path).suffix != '.jsonl' else [strict_json(line) for line in text.splitlines() if line.strip()]
    if not isinstance(value, list):
        raise ValueError('expected a JSON array or JSONL records')
    return value


def prepare(traces, human_rows, judge_file, rubric_sha256, dataset_version, data_kind):
    inventory = [Trace.model_validate(r) for r in traces]
    if not inventory or len({t.id for t in inventory}) != len(inventory):
        raise ValueError('nonempty unique complete trace inventory required')
    by_id = {t.id: t for t in inventory}; fingerprints = {t.id: digest(t.model_dump(mode='json')) for t in inventory}
    identities = {t.agent_version for t in inventory}
    if len(identities) != 1 or None in identities or '' in identities:
        raise ValueError('select one explicit agent version')
    if set(judge_file) != {'format', 'judge_identity', 'assessments'} or judge_file['format'] != 'judge-assessments-v1':
        raise ValueError('expected versioned judge-assessments-v1 file')
    judge_identity = JudgeIdentity.model_validate(judge_file['judge_identity'])
    humans = {}; judges = {}
    for row in human_rows:
        tid = row['trace_id']
        if tid in humans or tid not in by_id or row.get('assessor_kind') != 'human':
            raise ValueError('unknown, duplicate or nonhuman annotation')
        trace = by_id[tid]
        for key in ['task_id', 'family_id', 'split']:
            if row.get(key) != getattr(trace, key):
                raise ValueError('human task/family/split mismatch')
        if row.get('source_sha256') != fingerprints[tid] or row.get('rubric_sha256') != rubric_sha256:
            raise ValueError('human source/rubric mismatch')
        annotation = row['annotation']; evidence = row['original_evidence_steps']
        scenario_evidence = row.get('original_scenario_evidence', [])
        steps = {m.step_id for m in trace.messages}
        if not set(evidence) <= steps or not set(scenario_evidence) <= steps or len(set(evidence)) != len(evidence) or len(set(scenario_evidence)) != len(scenario_evidence):
            raise ValueError('invalid original human evidence')
        humans[tid] = Decision(verdict=annotation['verdict'], assessor_id=annotation['annotator'],
                               revision=annotation['revision'], evidence_steps=evidence,
                               explanation=annotation['comment'], scenario_problem=annotation['scenario_problem'],
                               scenario_evidence=scenario_evidence, scenario_comment=annotation.get('scenario_comment', ''),
                               error_types=annotation.get('error_types', []), saved_at=annotation.get('saved_at'))
    for row in judge_file['assessments']:
        assessment = Assessment.model_validate(row); tid = assessment.trace_id
        if tid in judges or tid not in by_id or assessment.assessor_kind != 'llm':
            raise ValueError('unknown, duplicate or non-LLM judge prediction')
        assessment.validate_against(by_id[tid])
        metadata = assessment.metadata
        if assessment.rubric_version != rubric_sha256 or metadata.get('source_sha256') != fingerprints[tid]:
            raise ValueError('judge source/rubric mismatch')
        if metadata.get('judge_identity') != judge_identity.model_dump():
            raise ValueError('judge version/model/prompt/config/run mismatch')
        if len(set(assessment.evidence_steps)) != len(assessment.evidence_steps):
            raise ValueError('duplicate judge evidence')
        judges[tid] = Decision(verdict=assessment.verdict, assessor_id=assessment.assessor_id,
                               evidence_steps=assessment.evidence_steps, explanation=assessment.explanation, error_types=assessment.error_types)
    cases = []
    for trace in inventory:
        length = sum(len(json.dumps(m.content, ensure_ascii=False)) + len(json.dumps(m.reasoning, ensure_ascii=False)) +
                     sum(len(json.dumps(c.arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) for c in m.tool_calls)
                     for m in trace.messages if m.role not in ['system', 'developer'])
        cases.append(Case(trace_id=trace.id, task_id=trace.task_id, family_id=trace.family_id, split=trace.split,
                          source_sha256=fingerprints[trace.id], length_chars=length,
                          noise=trace.metadata.get('noise', 'unknown'), human=humans.get(trace.id), judge=judges.get(trace.id)))
    return Input(data_kind=data_kind, dataset_version=dataset_version, agent_version=next(iter(identities)),
                 rubric_sha256=rubric_sha256, judge_identity=judge_identity, cases=cases)


def markdown(report):
    def cell(v):
        return str(v).replace('|', r'\|').replace('\n', ' ').replace('\r', ' ')
    def number(v):
        return 'undefined' if v is None else f'{v:.4f}'
    lines = ['# Human / judge comparison', '', '**' + report['data_kind'].upper() + '**', '',
             *[('- ' + w) for w in report['warnings']], '', '## Fixed inputs', '',
             '```json', json.dumps({k: report[k] for k in ['input_sha256', 'dataset_version', 'agent_version', 'rubric_sha256', 'judge_identity']}, indent=2), '```', '',
             '## Population and coverage', '', '| Count | Value |', '|---|---:|']
    overall = report['overall']
    lines += [f'| {k} | {v} |' for k, v in overall['counts'].items()]
    lines += ['', '| Coverage rate | Value |', '|---|---:|']
    lines += [f'| {key} | {number(value)} |' for key, value in overall['coverage'].items()]
    lines += ['', 'Paired exact agreement: ' + number(overall['paired_exact_agreement']['value']) +
              ' on ' + str(overall['paired_exact_agreement']['denominator']) + ' pairs.',
              'Equal-family agreement: ' + number(overall['equal_family_agreement']['value']) +
              ' on ' + str(overall['equal_family_agreement']['denominator']) + ' paired families.', '',
              '## Confusion matrix', '', 'Rows: human. Columns: judge.', '', '| human / judge | correct | incorrect | unassessable |', '|---|---:|---:|---:|']
    lines += ['| ' + label + ' | ' + ' | '.join(map(str, row)) + ' |' for label, row in zip(LABELS, overall['confusion']['matrix'])]
    lines += ['', '## Per-class metrics', '', '| Class | Support | Predicted | Precision | Recall | F1 |', '|---|---:|---:|---:|---:|---:|']
    lines += [f'| {label} | {m["support"]} | {m["predicted"]} | {number(m["precision"])} | {number(m["recall"])} | {number(m["f1"])} |' for label, m in overall['per_class'].items()]
    u = report['uncertainty']
    lines += ['', '## Family-cluster uncertainty', '', f'Status: {u["status"]}; paired families: {u["paired_families"]}; seed: {u["seed"]}; requested resamples: {u["requested_resamples"]}.',
              '95% percentile intervals conditional on observed paired families. For undefined metric draws, intervals use only defined draws and are conditional on that subset; they are not unconditional 95% intervals. Degenerate intervals do not establish certainty.', '',
              '| Metric | Interval | Defined draws | Undefined draws | Conditional subset | Degenerate |', '|---|---|---:|---:|---|---|']
    lines += [f'| {key} | {cell(v["interval"])} | {v["defined_resamples"]} | {v["undefined_resamples"]} | {v.get("conditional_on_defined_resamples", False)} | {v.get("degenerate", False)} |' for key, v in u['metrics'].items()]
    lines += ['', '## Slices', '', '| Dimension | Slice | Population | Pairs | Families | Paired families | Agreement |', '|---|---|---:|---:|---:|---:|---:|']
    for dimension, bins in report['slices'].items():
        for label, v in bins.items():
            c = v['counts']; lines.append(f'| {dimension} | {cell(label)} | {c["population"]} | {c["paired"]} | {c["families"]} | {c["paired_families"]} | {number(v["paired_exact_agreement"]["value"])} |')
    lines += ['', '## Disagreements', '', '| Trace | Human | Judge | Human evidence | Judge evidence |', '|---|---|---|---|---|']
    lines += ['| ' + ' | '.join(cell(v) for v in [c['trace_id'], c['human']['verdict'], c['judge']['verdict'], c['human']['evidence_steps'], c['judge']['evidence_steps']]) + ' |' for c in report['disagreements']]
    if not report['disagreements']:
        lines.append('No paired disagreements. This is not a judge-validity conclusion.')
    lines += ['', '## Label provenance', '', 'Every present label, including agreeing and unpaired decisions. Full explanations, error types and source hashes remain in JSON label_inventory.', '',
              '| Trace | Human assessor | Revision | Saved at | Human verdict | Human evidence | Scenario evidence | Scenario comment | Judge assessor |', '|---|---|---:|---|---|---|---|---|---|']
    for case in report['label_inventory']:
        h = case['human'] or {}; j = case['judge'] or {}
        lines.append('| ' + ' | '.join(cell(v) for v in [case['trace_id'], h.get('assessor_id', ''), h.get('revision', ''), h.get('saved_at', ''), h.get('verdict', ''), h.get('evidence_steps', []), h.get('scenario_evidence', []), h.get('scenario_comment', ''), j.get('assessor_id', '')]) + ' |')
    lines += ['', '## Missing labels', ''] + [f'- {cell(c["trace_id"])}: human missing={c["human_missing"]}, judge missing={c["judge_missing"]}' for c in report['missing_labels']]
    lines += ['', 'Analyst-only artifact: never expose judge outputs to the blind annotation UI.', '']
    return '\n'.join(lines)


def write_new(path, content):
    path = Path(path)
    # Exclusive creation never overwrites user labels or prior reports.
    with path.open('x') as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for name in ['traces', 'human', 'judge', 'rubric', 'dataset-version', 'data-kind', 'output']:
        p.add_argument('--' + name, required=True)
    c = sub.add_parser('compare'); c.add_argument('--input', required=True); c.add_argument('--output-dir', required=True)
    c.add_argument('--resamples', type=int, default=2000); c.add_argument('--seed', type=int, default=17)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            data = prepare(load_records(args.traces), load_records(args.human), strict_json(Path(args.judge).read_text()),
                           hashlib.sha256(Path(args.rubric).read_bytes()).hexdigest(), args.dataset_version, args.data_kind)
            write_new(args.output, data.model_dump_json(indent=2) + '\n')
        else:
            data = Input.model_validate(strict_json(Path(args.input).read_text()))
            result = compare(data, args.resamples, args.seed)
            target = Path(args.output_dir)
            if target.exists():
                raise ValueError('output directory exists; use a fresh directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            # Stage both files and publish directory together after successful writes.
            with tempfile.TemporaryDirectory(dir=target.parent) as temp:
                staged = Path(temp) / 'report'; staged.mkdir()
                write_new(staged / 'report.json', json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
                write_new(staged / 'report.md', markdown(result))
                os.rename(staged, target)
    except (ValueError, KeyError, OSError, TypeError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
