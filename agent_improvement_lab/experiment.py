"""Frozen, approved paired execution; deterministic stages stay ordinary code."""
import argparse
from contextlib import contextmanager
from collections import defaultdict
import hashlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import platform
import random
import shutil
import signal
import subprocess
import sys
import time
import uuid

from pydantic import Field, model_validator
from typing import Literal
import yaml
from .schemas import Model, Trace, Task as TraceTask
from .import_traces import UniqueSafeLoader, strict_json
from .failure_actions import Action
from . import failure_actions as actions
from . import criteria
from .graders import default_registry, SavedAssessments, load_plugin
from .routing_criteria import register
from .routing_measurement import checkpoint_prefix, register as register_measurement
from .routing_task import register as register_task_outcome


class ExecutionStop(RuntimeError):
    pass


class Limits(Model):
    wall_s: int = Field(default=1800, ge=1)
    http_starts: int = Field(default=96, ge=1)
    request_s: int = Field(default=90, ge=1)
    sample_s: int = Field(default=180, ge=1)
    max_generations: int = Field(default=8, ge=1, le=8)
    max_output_tokens: int = Field(default=256, ge=1)
    context: int = Field(default=8192, ge=1)
    RAM_reserve_bytes: int = Field(default=2 * 2**30, ge=1)
    swap_growth_bytes: int = Field(default=4 * 2**30, ge=0)

    @model_validator(mode='after')
    def order(self):
        if self.request_s > self.sample_s or self.sample_s > self.wall_s:
            raise ValueError('request <= sample <= overall wall budget required')
        return self


class Config(Model):
    format: Literal['improvement-experiment-v1'] = 'improvement-experiment-v1'
    backend: Literal['fixture', 'local_http', 'polza']
    dataset_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    environment: Literal['structured-routing-v1'] = 'structured-routing-v1'
    endpoint: str = 'http://127.0.0.1:1234/v1/chat/completions'
    cases: str
    baseline_prompt: str
    candidate_prompt: str
    common: str
    criteria: str
    action: str
    evidence: str
    evidence_traces: str
    decision_rule: str
    fixture_outputs: str | None = None
    repeats: int = Field(default=2, ge=1)
    repeat_start: int = Field(default=1, ge=1, le=2)
    run_id_prefix: str = Field(default='', pattern=r'^(?:[a-z][a-z0-9_-]{0,31})?$')
    confirmatory_protocol: str | None = None
    campaign_deadline_unix: float | None = Field(default=None, ge=1, allow_inf_nan=False)
    seed: int = 17
    temperature: float = Field(default=.4, ge=0, le=2)
    agent_version_prefix: str = Field(default='experiment-v1', min_length=1)
    tool_control: dict[str, Literal['auto', 'required']] = Field(default_factory=lambda: {'baseline': 'auto', 'candidate': 'auto'})
    zero_call_recovery: dict[str, int] = Field(default_factory=lambda: {'baseline': 0, 'candidate': 0})
    evidence_first: dict[str, bool] = Field(default_factory=lambda: {'baseline': False, 'candidate': False})
    actor_state: dict[str, bool] = Field(default_factory=lambda: {'baseline': False, 'candidate': False})
    action_gate: dict[str, bool] = Field(default_factory=lambda: {'baseline': False, 'candidate': False})
    study_mode: Literal['calibration', 'confirmatory'] = 'calibration'
    polza_transport_policy: Literal['legacy-v1', 'paced-catalog-v2'] = 'legacy-v1'
    planner_state_guard: dict[str, bool] = Field(default_factory=lambda: {'baseline': False, 'candidate': False})
    measurement_contract: Literal['routing-measurement-v2', 'legacy-v1'] = 'routing-measurement-v2'
    parallel_workers: int = Field(default=1, ge=1, le=4)
    polza_cap_rub: float | None = Field(default=None, ge=1, le=100)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode='after')
    def boundaries(self):
        from urllib.parse import urlparse
        url = urlparse(self.endpoint)
        if self.backend == 'polza':
            from .polza_http import ENDPOINT, MODEL
            if self.endpoint != ENDPOINT or self.model_id != MODEL or self.model_version != MODEL or self.polza_cap_rub is None:
                raise ValueError('Polza requires exact endpoint/model/version and explicit bounded RUB cap')
            if (self.study_mode == 'calibration' and (self.repeats != 2 or self.repeat_start != 1)) or self.limits.wall_s > 1800 or self.limits.http_starts > 384 or self.limits.request_s > 60 or self.limits.sample_s > 180 or self.limits.max_output_tokens > 256 or self.limits.context > 8192:
                raise ValueError('Polza calibration exceeds approved request/time/token envelope')
            if self.measurement_contract != 'routing-measurement-v2':
                raise ValueError('Polza calibration requires measurement v2')
        elif url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.username or url.password or url.path != '/v1/chat/completions' or url.query or url.fragment:
            raise ValueError('explicit HTTP 127.0.0.1 port and /v1/chat/completions required')
        if self.study_mode == 'confirmatory':
            if self.repeats != 1 or not self.confirmatory_protocol or not self.run_id_prefix:
                raise ValueError('confirmatory batches require one declared repeat, frozen protocol and unique prefix')
            if self.backend == 'polza' and self.campaign_deadline_unix is None:
                raise ValueError('paid confirmatory batches require an immutable absolute campaign deadline')
        elif self.confirmatory_protocol is not None or self.repeat_start != 1 or self.repeats < 2:
            raise ValueError('calibration preserves two repeats and no confirmatory override')
        if self.backend != 'polza' and self.polza_cap_rub is not None:
            raise ValueError('RUB budget belongs only to explicit Polza backend')
        if self.backend == 'local_http' and self.parallel_workers != 1:
            raise ValueError('local-model concurrency remains one worker')
        if (self.backend == 'fixture') != (self.fixture_outputs is not None):
            raise ValueError('scripted outputs belong only to fixture backend')
        if set(self.tool_control) != {'baseline', 'candidate'}:
            raise ValueError('baseline and candidate tool policies required')
        if set(self.zero_call_recovery) != {'baseline', 'candidate'} or any(
            type(value) is not int or value not in (0, 1) for value in self.zero_call_recovery.values()
        ):
            raise ValueError('baseline and candidate recovery limits must be 0 or 1')
        if set(self.evidence_first) != {'baseline', 'candidate'}:
            raise ValueError('baseline and candidate evidence modes required')
        if set(self.planner_state_guard) != {'baseline', 'candidate'} or any(
            self.planner_state_guard[a] and not self.evidence_first[a] for a in self.planner_state_guard):
            raise ValueError('baseline and candidate state guard requires evidence planning')
        if set(self.actor_state) != {'baseline', 'candidate'} or any(
            self.actor_state[a] and self.evidence_first[a] for a in self.actor_state):
            raise ValueError('Direct actor state and LLM planning are separate interventions')
        if set(self.action_gate) != {'baseline', 'candidate'} or any(self.action_gate[a] and
            (self.actor_state[a] or self.evidence_first[a] or self.zero_call_recovery[a]) for a in self.action_gate):
            raise ValueError('Action gating is separate from planner/state/recovery interventions')
        if self.backend != 'polza' and self.polza_transport_policy != 'legacy-v1':
            raise ValueError('Polza transport policy requires its explicit backend')
        if any(self.evidence_first[a] and self.zero_call_recovery[a] for a in self.evidence_first):
            raise ValueError('Evidence planning and recovery are separate interventions')
        return self


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


def read(path):
    return strict_json(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('x') as stream:
        stream.write(encode(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


def once(path, value):
    path = Path(path); payload = encode(value)
    if path.exists():
        if path.read_text() != payload:
            raise ValueError('immutable artifact changed: ' + path.name)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())


@contextmanager
def writer(workspace):
    import fcntl
    with (Path(workspace) / 'writer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another experiment writer is active') from None
        yield lock


SOURCES = ['experiment.py', 'experiment_inspect.py', 'evidence_scaffold.py', 'actor_state.py', 'action_gate.py', 'routing_task.py', 'routing_criteria.py', 'workflow_cli.py',
           'local_http.py', 'schemas.py', 'import_traces.py', 'failure_actions.py',
           'criteria.py', 'graders.py', 'annotation.py', 'judge_metrics.py', 'routing_measurement.py', 'polza_http.py']


def runtime(backend):
    names = ['pydantic', 'PyYAML', 'inspect-ai'] + (['psutil'] if backend in ('local_http', 'polza') else [])
    try:
        return {'python': platform.python_version(), 'packages': {name: md.version(name) for name in names}}
    except md.PackageNotFoundError as error:
        raise ValueError('execution runtime unavailable: ' + str(error)) from None


def select(triage_path, plan_path, action_id, output):
    """Export a validated working action from the existing analysis workflow."""
    triage = actions.Triage.model_validate(read(triage_path))
    plan = actions.Plan.model_validate(read(plan_path)); actions.report(triage, plan)
    selected = [a for a in plan.actions if a.id == action_id]
    if len(selected) != 1:
        raise ValueError('select one existing action ID')
    action = selected[0]; source_issues = {i.id: i for i in actions.issues(triage)}
    traces = {case.trace.id: case.trace for case in triage.working_cases}
    output = Path(output).resolve()
    if output.exists(): raise ValueError('selection directory exists')
    output.mkdir(parents=True)
    once(output / 'action.json', {'action': action.model_dump(mode='json'), 'selection': 'pending_exact_execution_approval'})
    rows = []
    for iid in action.issue_ids:
        issue = source_issues[iid]
        rows.append({**issue.model_dump(mode='json'), 'split': 'working', 'trace_sha256': issue.source_sha256})
    once(output / 'evidence-traces.json', [traces[tid].model_dump(mode='json') for tid in sorted({i['trace_id'] for i in rows})])
    once(output / 'evidence.json', {'data_kind': 'test_fixture' if triage.data_kind == 'test_fixture' else 'recorded_evaluation',
        'issues': rows, 'upstream': {'triage': triage.model_dump(mode='json'), 'plan': plan.model_dump(mode='json')},
        'human_validation': False, 'source_provenance': 'validated snapshot bindings; human reference remains self-declared'})
    return {'selected_action_id': action.id, 'output': str(output), 'execution_approved': False}


def prepare(config_path, workspace, plugin_paths=()):
    config_path = Path(config_path).resolve(); workspace = Path(workspace).resolve()
    config = Config.model_validate(yaml.load(config_path.read_text(), Loader=UniqueSafeLoader))
    if workspace.exists():
        raise ValueError('workspace exists; resume it or choose a fresh directory')
    names = ['cases', 'baseline_prompt', 'candidate_prompt', 'common', 'criteria', 'action', 'evidence', 'evidence_traces', 'decision_rule']
    if config.confirmatory_protocol:
        names.append('confirmatory_protocol')
    if config.fixture_outputs:
        names.append('fixture_outputs')
    payloads = {key: (config_path.parent / getattr(config, key)).read_bytes() for key in names}
    for ordinal, path in enumerate(plugin_paths):
        payloads[f'plugin_{ordinal:04d}.py'] = Path(path).read_bytes()
    cases = strict_json(payloads['cases'].decode()); seen = set(); families = {}
    if not isinstance(cases, list) or not cases:
        raise ValueError('nonempty cases required')
    if config.backend == 'polza' and (len(cases) > 12 or config.study_mode == 'calibration' and any(case.get('split') != 'working' for case in cases)):
        raise ValueError('approved Polza calibration covers at most12 WORKING cases')
    for case in cases:
        if case['id'] in seen or case['split'] not in ('working', 'heldout') or case['ticket_id'] != case['mock_ticket']['ticket_id']:
            raise ValueError('unique valid routing cases required')
        seen.add(case['id'])
        if families.setdefault(case['family_id'], case['split']) != case['split']:
            raise ValueError('family split leakage')
        if set(case['facts']) != set(case['fact_kinds']) or len(set(case['fact_kinds'].values())) != len(case['facts']):
            raise ValueError('one authored fact per typed field required')
        if not set(case['fact_kinds'].values()) <= {'own_access', 'own_role', 'action_permission', 'current_plan', 'financial_transaction', 'observed_symptom'}:
            raise ValueError('unsupported controlled field')
    action_data = strict_json(payloads['action'].decode())
    action = Action.model_validate(action_data.get('action', action_data))
    evidence = strict_json(payloads['evidence'].decode())
    if evidence.get('data_kind') != ('test_fixture' if config.backend == 'fixture' else 'recorded_evaluation'):
        raise ValueError('explicit fixture/recorded evidence kind required')
    if 'upstream' in evidence:
        triage = actions.Triage.model_validate(evidence['upstream']['triage'])
        proposal = actions.Plan.model_validate(evidence['upstream']['plan']); actions.report(triage, proposal)
        if action.model_dump(mode='json') not in [a.model_dump(mode='json') for a in proposal.actions]:
            raise ValueError('selected action differs from validated upstream plan')
    trace_text = payloads['evidence_traces'].decode()
    trace_rows = strict_json(trace_text) if trace_text.lstrip().startswith('[') else [strict_json(line) for line in trace_text.splitlines() if line.strip()]
    source_traces = {t.id: t for t in criteria.inventory(trace_rows)}
    if len({row['id'] for row in evidence['issues']}) != len(evidence['issues']):
        raise ValueError('duplicate source issue IDs')
    issues = {row['id']: row for row in evidence['issues']}
    if not set(action.issue_ids) <= set(issues) or any(issues[i].get('split') != 'working' for i in action.issue_ids):
        raise ValueError('action requires bound working evidence')
    if action.intervention not in ('prompt', 'context', 'scaffold') or any(issues[i].get('kind') != 'assistant_failure' for i in action.issue_ids):
        raise ValueError('executor accepts assistant failures and prompt/context/scaffold changes only')
    for issue_id in action.issue_ids:
        issue = issues[issue_id]; trace = source_traces.get(issue.get('trace_id'))
        if trace is None or trace.split != 'working' or issue.get('trace_sha256') != criteria.digest(trace.model_dump(mode='json')):
            raise ValueError('source trace/hash/working split differs')
        if trace.provenance in ('missing_measurement_placeholder', 'runner_no_model_output') or trace.metadata.get('measurement_status') == 'unassessable':
            raise ValueError('measurement gap needs investigation, not an assistant intervention')
        if not issue.get('evidence_steps') or not set(issue['evidence_steps']) <= {m.step_id for m in trace.messages}:
            raise ValueError('source evidence steps missing')
        if 'upstream' in evidence:
            original = next(i for i in actions.issues(triage) if i.id == issue_id)
            if (issue['kind'], issue['trace_id'], issue['trace_sha256'], issue['evidence_steps']) != (original.kind, original.trace_id, original.source_sha256, original.evidence_steps):
                raise ValueError('issue differs from validated upstream evidence')
        scripted = trace.provenance in ('scripted_model_outputs_mock_tools', 'authored_test_fixture')
        if scripted != (config.backend == 'fixture'):
            raise ValueError('scripted source evidence cannot authorize a recorded experiment')
    if action.verification.protocol != 'working_selection_then_family_heldout_check' or (config.repeats < action.verification.repeat_runs and config.study_mode != 'confirmatory'):
        raise ValueError('action and repeat protocol differ')
    if config.study_mode == 'confirmatory':
        protocol = strict_json(payloads['confirmatory_protocol'].decode())
        if protocol.get('format') != 'prospective-family-comparison-v1' or protocol.get('primary_repeat') != 1 or protocol.get('semantic_contract') != 'routing-task-outcome-v2':
            raise ValueError('frozen confirmatory family protocol required')
        if any(case['split'] != 'heldout' for case in cases):
            raise ValueError('confirmatory batches contain only frozen heldout cases')
    pack = criteria.Pack.model_validate(yaml.load(payloads['criteria'], Loader=UniqueSafeLoader)).resolve()
    if pack.data_kind != ('test_fixture' if config.backend == 'fixture' else 'recorded_evaluation'):
        raise ValueError('fixture/live criterion provenance differs')
    if action.verification.primary_measure != pack.primary_metric or set(action.verification.regression_checks) != set(pack.regression_metrics):
        raise ValueError('executable action must name the same primary/regression criterion IDs')
    registry = default_registry(); register(registry)
    register_measurement(registry); register_task_outcome(registry)
    for path in plugin_paths: load_plugin(registry, path)
    for criterion in pack.criteria:
        if criterion.grader.kind != 'deterministic':
            raise ValueError('execution pack requires deterministic graders; saved judge validation remains in replay')
        registry.signature(criterion.grader.id)
    rule = strict_json(payloads['decision_rule'].decode())
    if rule['primary'] != pack.primary_metric or rule['regressions'] != pack.regression_metrics:
        raise ValueError('decision rule must match selected primary/regressions')
    split = rule.get('evaluation_split', 'working')
    expected = sum(case['split'] == split for case in cases) * config.repeats
    if not expected or rule['expected_pairs'] != expected:
        raise ValueError('frozen decision pair inventory differs')
    slots = []; rng = random.Random(config.seed)
    for case in cases:
        for repeat in range(config.repeat_start, config.repeat_start + config.repeats):
            arms = ['baseline', 'candidate']; rng.shuffle(arms)
            for arm in arms:
                slots.append({'run_id': f'{config.run_id_prefix}s{len(slots)+1:04d}', 'task_id': case['id'], 'family_id': case['family_id'],
                              'split': case['split'], 'repeat_id': str(repeat), 'arm': arm,
                              'tool_control': config.tool_control[arm],
                              'zero_call_recovery': config.zero_call_recovery[arm],
                              'evidence_first': config.evidence_first[arm],
                              'planner_state_guard': config.planner_state_guard[arm],
                              'actor_state': config.actor_state[arm]})
                slots[-1]['action_gate'] = config.action_gate[arm]
    if config.fixture_outputs:
        scripts = strict_json(payloads['fixture_outputs'].decode())
        if set(scripts) != {s['run_id'] for s in slots} or any(not isinstance(scripts[s['run_id']], list) or not scripts[s['run_id']] for s in slots):
            raise ValueError('complete per-slot scripted fixture outputs required')
    identity = runtime(config.backend)
    workspace.mkdir(parents=True); sealed = {}
    for key, payload in payloads.items():
        target = workspace / 'inputs' / key; target.parent.mkdir(exist_ok=True); target.write_bytes(payload)
        sealed['inputs/' + key] = sha(target)
    once(workspace / 'config.json', config.model_dump(mode='json')); sealed['config.json'] = sha(workspace / 'config.json')
    once(workspace / 'slots.json', slots); sealed['slots.json'] = sha(workspace / 'slots.json')
    engines = {}
    for name in SOURCES:
        source = Path(__file__).parent / name; target = workspace / 'source' / name
        target.parent.mkdir(exist_ok=True); shutil.copyfile(source, target); engines[name] = sha(source)
    once(workspace / 'manifest.json', {'format': 'experiment-manifest-v1', 'sealed': sealed,
         'engine': engines, 'runtime': identity, 'backend': config.backend,
         'selected_action_id': action.id, 'environment': config.environment,
         'model_version_attestation': config.model_version, 'model_version_verified_by_runner': False,
         'paid_envelope': {'endpoint': config.endpoint, 'model': config.model_id, 'provider_request_pin': 'mistral',
            'fallbacks': False, 'cap_rub': config.polza_cap_rub, 'workers': config.parallel_workers,
            'transport_policy': config.polza_transport_policy} if config.backend == 'polza' else None,
         'limitations': 'Controlled authored facts, mock tools, no human gold or automatic promotion. Remote provider identity is verified only when returned; local endpoint is caller-managed.'})
    atomic(workspace / 'state.json', {'phase': 'prepared', 'manifest_sha256': sha(workspace / 'manifest.json')})
    return status(workspace)


def prepare_supplement(config_path, workspace, source, plugin_paths=()):
    """Fresh attempt for technical gaps, with sealed retained terminal outcomes.

    Never reopen a finalized run or replay a completed behavioral failure.
    This combined view is explicitly distinct from its first-attempt analysis.
    """
    source = Path(source).resolve(); workspace = Path(workspace).resolve()
    old_state = read(source / 'state.json'); old_manifest = read(source / 'manifest.json')
    if old_state['phase'] != 'complete' or old_state['manifest_sha256'] != sha(source / 'manifest.json'):
        raise ValueError('supplement requires an unchanged finalized source')
    source_seals = {**old_manifest['sealed'], **old_state['outputs']}
    source_seals.update({'source/' + name: value for name, value in old_manifest['engine'].items()})
    source_seals.update(read(source / 'finalization.json')['raw'])
    for rel, expected in source_seals.items():
        if sha(source / rel) != expected:
            raise ValueError('supplement source changed: ' + rel)
    old_config = Config.model_validate(read(source / 'config.json'))
    new_config = Config.model_validate(yaml.load(Path(config_path).read_text(), Loader=UniqueSafeLoader))
    old_values = old_config.model_dump(mode='json'); new_values = new_config.model_dump(mode='json')
    # Paths refer to byte-identical prepared inputs; all agent behavior stays fixed.
    paths = {'cases', 'baseline_prompt', 'candidate_prompt', 'common', 'criteria', 'action',
             'evidence', 'evidence_traces', 'decision_rule', 'fixture_outputs'}
    for key in paths | {'limits', 'polza_cap_rub'}:
        old_values.pop(key); new_values.pop(key)
    if old_values != new_values:
        raise ValueError('supplement changes model/agent/environment configuration')
    if any(getattr(new_config.limits, key) > getattr(old_config.limits, key)
           for key in Limits.model_fields):
        raise ValueError('supplement expands a resource limit')
    retained = []; eligible = []
    slots = read(source / 'slots.json')
    for slot in slots:
        path = source / 'slots' / slot['run_id'] / 'result.json'
        value = read(path) if path.is_file() else None
        terminal = (value is not None and
            value['trace']['metadata'].get('trajectory_completeness') == 'complete' and
            value['trace']['metadata'].get('measurement_status') == 'observed')
        row = {'slot': slot, 'prior_result_sha256': sha(path) if value is not None else None,
               'prior_started': (path.parent / 'started.json').is_file()}
        (retained if terminal else eligible).append(row)
    if not eligible:
        raise ValueError('source already has every planned terminal outcome')
    aggregate = None
    if old_config.backend == 'polza':
        prior = old_manifest.get('collection_lineage', {}).get('aggregate_budget', {})
        ledger = read(source / 'polza/ledger.json')
        from .polza_http import exposure
        held = prior.get('carried_exposure_rub', 0) + exposure(ledger)
        starts = prior.get('carried_starts', 0) + len(ledger['calls'])
        cap = prior.get('cap_rub', old_config.polza_cap_rub)
        starts_cap = prior.get('starts_cap', old_config.limits.http_starts)
        if new_config.polza_cap_rub + held > cap + 1e-8 or new_config.limits.http_starts + starts > starts_cap:
            raise ValueError('supplement exceeds aggregate original cost/start budget')
        aggregate = {'cap_rub': cap, 'starts_cap': starts_cap,
                     'carried_exposure_rub': held, 'carried_starts': starts}
    prepare(config_path, workspace, plugin_paths)
    manifest = read(workspace / 'manifest.json')
    if read(workspace / 'slots.json') != slots:
        raise ValueError('supplement changed planned slot identities')
    for rel, expected in old_manifest['sealed'].items():
        if rel.startswith('inputs/') and manifest['sealed'].get(rel) != expected:
            raise ValueError('supplement changed an input: ' + rel)
    lineage = {'format': 'technical-gap-supplement-v1', 'source_workspace': str(source),
        'source_manifest_sha256': sha(source / 'manifest.json'), 'source_state_sha256': sha(source / 'state.json'),
        'source_seals': source_seals, 'retained': retained, 'eligible': eligible,
        'selection_rule': 'Retain all complete observed trajectories regardless of their grade; retry technical gaps only.',
        'fresh_attempts_not_checkpoint_resumes': True, 'original_prospective_analysis_unchanged': True}
    once(workspace / 'retained-source.json', lineage)
    manifest['sealed']['retained-source.json'] = sha(workspace / 'retained-source.json')
    for row in retained:
        rel = 'slots/' + row['slot']['run_id'] + '/result.json'
        target = workspace / rel; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / rel, target)
        manifest['sealed'][rel] = sha(target)
    manifest['collection_lineage'] = {'format': lineage['format'], 'retained_slots': len(retained),
        'fresh_attempt_slots': len(eligible), 'source_manifest_sha256': lineage['source_manifest_sha256'],
        'aggregate_budget': aggregate,
        'limitations': 'Supplemented dataset, not the original first-attempt prospective experiment; prior failed attempts and unknown costs remain in source.'}
    atomic(workspace / 'manifest.json', manifest)
    atomic(workspace / 'state.json', {'phase': 'prepared', 'manifest_sha256': sha(workspace / 'manifest.json')})
    return status(workspace)


def verify(workspace):
    workspace = Path(workspace); state = read(workspace / 'state.json'); manifest = read(workspace / 'manifest.json')
    if state['manifest_sha256'] != sha(workspace / 'manifest.json'):
        raise ValueError('manifest changed')
    if manifest['runtime'] != runtime(manifest['backend']):
        raise ValueError('execution runtime changed')
    for rel, expected in manifest['sealed'].items():
        if sha(workspace / rel) != expected:
            raise ValueError('sealed input changed: ' + rel)
    for name, expected in manifest['engine'].items():
        if sha(Path(__file__).parent / name) != expected or sha(workspace / 'source' / name) != expected:
            raise ValueError('execution engine/archive changed: ' + name)
    for name, expected in state.get('outputs', {}).items():
        if sha(workspace / name) != expected:
            raise ValueError('completed evidence changed: ' + name)
    return Config.model_validate(read(workspace / 'config.json'))


def status(workspace):
    workspace = Path(workspace); config = verify(workspace); state = read(workspace / 'state.json')
    slots = read(workspace / 'slots.json')
    visible = {key: state[key] for key in ('phase', 'manifest_sha256', 'decision', 'approval_sha256') if key in state}
    return {**visible, 'backend': config.backend, 'selected_action_id': read(workspace / 'manifest.json')['selected_action_id'],
            'planned_slots': len(slots),
            'completed_slots': sum((workspace / 'slots' / slot['run_id'] / 'result.json').exists() for slot in slots),
            'http_starts': len((workspace / 'requests.jsonl').read_text().splitlines()) if (workspace / 'requests.jsonl').exists() else 0,
            'report': 'REPORT.md' if state['phase'] == 'complete' else None,
            'registry': 'registry.json' if state['phase'] == 'complete' else None,
            'workspace': str(workspace.resolve())}


def approve(workspace, manifest_sha256, reviewer, reject=False):
    workspace = Path(workspace)
    with writer(workspace):
        config = verify(workspace); state = read(workspace / 'state.json')
        if state['phase'] != 'prepared' or manifest_sha256 != state['manifest_sha256'] or not reviewer or not reviewer.strip():
            raise ValueError('prepared state, exact manifest identity and explicit reviewer required')
        once(workspace / 'approval.json', {'decision': 'reject' if reject else 'run', 'reviewer': reviewer,
             'manifest_sha256': manifest_sha256, 'backend': config.backend, 'time': time.time(),
             'approval_scope': 'this exact execution; not human annotation or publication'})
        atomic(workspace / 'state.json', {**state, 'phase': 'rejected' if reject else 'approved',
               'approval_sha256': sha(workspace / 'approval.json')})
    return status(workspace)


class Budget:
    def __init__(self, workspace):
        self.workspace = Path(workspace); self.settings = verify(workspace); self.config = self.settings.limits
        self.execution = read(self.workspace / 'execution.json')

    def check(self):
        if (self.workspace / 'stop.json').exists():
            raise ExecutionStop('execution stop marker present')
        if time.time() >= self.execution['deadline']:
            raise ExecutionStop('overall wall budget exhausted')
        if self.settings.backend == 'polza' and (self.workspace / 'polza/ledger.json').exists():
            if read(self.workspace / 'polza/ledger.json')['fatal']:
                raise ExecutionStop('paid provider/accounting stop')
        if self.settings.backend in ('local_http', 'polza'):
            import psutil
            memory = {'time': time.time(), 'available': psutil.virtual_memory().available, 'swap': psutil.swap_memory().used}
            with (self.workspace / 'memory.jsonl').open('a') as file:
                file.write(json.dumps(memory) + '\n')
            baseline = self.execution['memory_baseline']
            if memory['available'] < self.config.RAM_reserve_bytes or memory['swap'] - baseline['swap'] > self.config.swap_growth_bytes:
                raise ExecutionStop('memory reserve/swap growth guard')

    def reserve(self, body, run_id):
        self.check(); path = self.workspace / 'requests.jsonl'
        rows = path.read_text().splitlines() if path.exists() else []
        if len(rows) >= self.config.http_starts:
            raise ExecutionStop('HTTP starts budget exhausted')
        with path.open('a') as file:
            file.write(json.dumps({'sequence': len(rows)+1, 'time': time.time(), 'run_id': run_id,
                                  'request_sha256': hashlib.sha256(encode(body).encode()).hexdigest()}) + '\n')
            file.flush(); os.fsync(file.fileno())


def trace_for(workspace, slot, messages, metadata, missing=False):
    config = Config.model_validate(read(workspace / 'config.json'))
    case = next(c for c in read(workspace / 'inputs/cases') if c['id'] == slot['task_id'])
    return Trace.model_validate({'id': slot['run_id'], 'task_id': case['id'], 'family_id': case['family_id'],
        'split': case['split'], 'language': case.get('language', 'en'),
        'task': TraceTask(id=case['id'], objective=case['user_request'], family_id=case['family_id'],
                         split=case['split'], language=case.get('language', 'en'),
                         context={'environment': config.environment, 'scenario': case}).model_dump(mode='json'),
        'agent_version': config.agent_version_prefix + '-' + slot['arm'],
        'provenance': 'missing_measurement_placeholder' if missing else 'scripted_model_outputs_mock_tools' if config.backend == 'fixture' else ('live_polza_llm_mock_tools' if config.backend == 'polza' else 'live_local_llm_mock_tools') if metadata.get('model_outputs_returned') else 'runner_no_model_output',
        'messages': messages, 'metadata': {**metadata, 'repeat_id': slot['repeat_id'],
            'measurement_contract': config.measurement_contract,
            'trajectory_completeness': metadata.get('trajectory_completeness',
                'missing' if missing else 'complete' if metadata.get('termination') in ('final_after_assignment', 'final_without_assignment', 'turn_budget') else 'partial'),
            'reference_is_human_gold': False, 'environment_version': config.environment,
            'message_sources': {m['step_id']: 'input' if m['role'] == 'user' else 'intervention' if m['role'] == 'system' else 'generate' for m in messages},
            'evaluation_context': {'environment_version': config.environment, 'cases_sha256': sha(workspace / 'inputs/cases')}}}).model_dump(mode='json')


def missing_trace(workspace, slot, reason):
    case = next(c for c in read(workspace / 'inputs/cases') if c['id'] == slot['task_id'])
    return trace_for(workspace, slot, [{'step_id': 's001', 'role': 'user', 'content': case['user_request']}],
                     {'termination': 'missing_measurement', 'measurement_status': 'unassessable', 'reason': reason}, True)


def interrupted_trace(workspace, slot):
    config = Config.model_validate(read(workspace / 'config.json'))
    folder = workspace / 'slots' / slot['run_id']
    if config.measurement_contract != 'routing-measurement-v2' or not (folder / 'actor-checkpoint.json').is_file():
        return missing_trace(workspace, slot, 'unstarted, interrupted or failed execution; never regenerated')
    try:
        case = next(c for c in read(workspace / 'inputs/cases') if c['id'] == slot['task_id'])
        messages, metadata = checkpoint_prefix(workspace, slot, case)
        return trace_for(workspace, slot, messages, metadata)
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as error:
        return missing_trace(workspace, slot, 'checkpoint extraction rejected: ' + str(error))


def uncertainty(left, right, seed, minimum_repeats=2):
    """Descriptive family bootstrap; too few clusters are explicitly unestimable."""
    lhs, rhs = criteria.pair_index(left.traces, minimum_repeats), criteria.pair_index(right.traces, minimum_repeats)
    lc = {(c.trace_id, c.criterion_id): c for c in left.results}
    rc = {(c.trace_id, c.criterion_id): c for c in right.results}
    result = {}
    for split in ('working', 'heldout'):
        result[split] = {}
        for criterion in left.pack.criteria:
            tasks = defaultdict(list)
            for key in lhs:
                if key[2] != split: continue
                a, b = lc[(lhs[key].id, criterion.id)], rc[(rhs[key].id, criterion.id)]
                if a.status == b.status == 'assessed' and a.verdict != 'unassessable' and b.verdict != 'unassessable':
                    tasks[(key[1], key[0])].append(int(b.verdict == 'correct')-int(a.verdict == 'correct'))
            families = defaultdict(list)
            for (family, _), values in tasks.items(): families[family].append(sum(values)/len(values))
            values = [sum(v)/len(v) for v in families.values()]
            interval = None
            if len(values) >= 10:
                rng = random.Random(seed)
                draws = sorted(sum(rng.choices(values, k=len(values)))/len(values) for _ in range(2000))
                interval = [draws[49], draws[1949]]
            result[split][criterion.id] = {'status': 'descriptive_interval' if interval else 'insufficient_families',
                'paired_families': len(values), 'interval_95': interval, 'method': 'family_cluster_percentile',
                'minimum_clusters_guard': 10, 'seed': seed, 'resamples': 2000 if interval else 0,
                'limitations': 'Conditional on jointly assessable observed families; degenerate intervals do not establish certainty. No power or significance claim.'}
    return result


def finalize(workspace):
    workspace = Path(workspace); config = verify(workspace); slots = read(workspace / 'slots.json')
    state = read(workspace / 'state.json')
    if state['phase'] not in ('running', 'finalizing', 'complete'):
        raise ValueError('finalization requires a started execution')
    raw = [name for name in ['approval.json', 'execution.json', 'requests.jsonl', 'memory.jsonl', 'stop.json'] if (workspace / name).exists()]
    raw += [str(p.relative_to(workspace)) for p in (workspace / 'slots').rglob('*')
            if p.is_file() and (p.name in ['started.json', 'actor-checkpoint.json', 'worker-stop.json', 'failure.json', 'result.json']
                               or p.parent.name in ('provider-calls', 'planner')
                               or ('inspect-logs' in p.parts and p.suffix == '.json'))]
    if config.backend == 'polza':
        raw += [str(p.relative_to(workspace)) for p in (workspace / 'polza').glob('*.json')]
    if (workspace / 'finalization.json').exists():
        boundary = read(workspace / 'finalization.json')
        if set(boundary['raw']) != set(raw) or any(sha(workspace / name) != fingerprint for name, fingerprint in boundary['raw'].items()):
            raise ValueError('frozen execution evidence changed during finalization')
    else:
        once(workspace / 'finalization.json', {'raw': {name: sha(workspace / name) for name in sorted(raw)}})
    if state['phase'] != 'complete':
        atomic(workspace / 'state.json', {**state, 'phase': 'finalizing', 'finalization_sha256': sha(workspace / 'finalization.json')})
    pack = criteria.load_pack(workspace / 'inputs/criteria'); registry = default_registry(); register(registry); register_measurement(registry); register_task_outcome(registry)
    for rel in read(workspace / 'manifest.json')['sealed']:
        if rel.startswith('inputs/plugin_'): load_plugin(registry, workspace / rel)
    reports = {}; recorded = 0
    for arm in ('baseline', 'candidate'):
        traces = []
        for slot in slots:
            if slot['arm'] != arm:
                continue
            path = workspace / 'slots' / slot['run_id'] / 'result.json'
            if path.exists():
                trace = read(path)['trace']; recorded += 1
            else:
                trace = interrupted_trace(workspace, slot)
            traces.append(trace)
        reports[arm] = criteria.evaluate(traces, pack, registry, SavedAssessments(None, traces, pack))
        once(workspace / (arm + '-criteria.json'), reports[arm].model_dump(mode='json'))
        (workspace / (arm + '-traces.jsonl')).write_text(''.join(json.dumps(t) + '\n' for t in traces))
    minimum_repeats = 1 if config.study_mode == 'confirmatory' else 2
    comparison = criteria.compare(reports['baseline'], reports['candidate'], minimum_repeats=minimum_repeats); once(workspace / 'comparison.json', comparison)
    once(workspace / 'uncertainty.json', uncertainty(reports['baseline'], reports['candidate'], config.seed, minimum_repeats))
    rule = read(workspace / 'inputs/decision_rule'); split = rule.get('evaluation_split', 'working')
    metrics = comparison.get('splits', {}).get(split, {})
    selected = [rule['primary'], *rule['regressions']]
    if comparison['status'] != 'descriptive_paired' or recorded != len(slots) or any(metrics[c]['jointly_assessable_pairs'] != rule['expected_pairs'] for c in selected):
        decision = 'inconclusive_measurement'
    elif any(metrics[c]['regressed'] for c in selected):
        decision = 'do_not_adopt'
    elif metrics[rule['primary']]['improved']:
        decision = 'advance_to_larger_test'
    else:
        decision = 'no_observed_improvement'
    result = {'decision': decision, 'recorded_slots': recorded, 'planned_slots': len(slots),
              'backend': config.backend, 'default_promoted': False, 'human_gold': False,
              'statistical_effect_established': False, 'rule': rule}
    once(workspace / 'result.json', result)
    (workspace / 'comparison.md').write_text(criteria.comparison_markdown(comparison))
    origin = 'SCRIPTED FIXTURE; no real model calls' if config.backend == 'fixture' else 'RECORDED POLZA MODEL OUTPUTS; mock tools' if config.backend == 'polza' else 'RECORDED LOCAL MODEL OUTPUTS; mock tools'
    lines = ['# Experiment result', '', origin, '', '**' + decision + '**', '',
             f'{recorded}/{len(slots)} recorded executions. Missing and unassessable results remain in the paired inventory.', '',
             '| Criterion | Baseline correct/assessed | Candidate correct/assessed | Improved pairs | Regressed pairs | Excluded pairs |',
             '|---|---:|---:|---:|---:|---:|']
    for cid in selected:
        values = []
        for arm in ('baseline', 'candidate'):
            cells = [c for c in reports[arm].results if c.criterion_id == cid and next(t.split for t in reports[arm].traces if t.id == c.trace_id) == split]
            assessed = [c for c in cells if c.status == 'assessed' and c.verdict in ('correct', 'incorrect')]
            values.append(f'{sum(c.verdict == "correct" for c in assessed)}/{len(assessed)}')
        m = metrics.get(cid, {})
        lines.append('| ' + ' | '.join([cid, *values, str(m.get('improved')), str(m.get('regressed')), str(m.get('excluded_pairs'))]) + ' |')
    if any(config.zero_call_recovery.values()):
        controls = {arm: sum(len(t.metadata.get('control_events', [])) for t in report.traces)
                    for arm, report in reports.items()}
        lines += ['', f'Scaffold recovery controls: baseline {controls["baseline"]}, candidate {controls["candidate"]}. '
                  'Each is visible in the dialogues and uses the existing generation/request/deadline limits; '
                  'these counts are not a quality criterion.']
    if any(config.evidence_first.values()):
        for arm, report in reports.items():
            events = [e for t in report.traces for e in t.metadata.get('planner_events', [])]
            lines += ['', f'{arm}: {len(events)} preliminary model analyses; '
                f'{sum(e["status"] == "rejected_structure" for e in events)} structurally rejected. '
                'Analysis is not additional user evidence or semantic validation; '
                'planner and actor calls share the same generation/request limits.']
    lines += ['', '[Dialogues](TRACES.md) · [Paired criteria](comparison.md) · [Uncertainty](uncertainty.json) · [Decision and rule](result.json)', '',
             'Authored facts are not human gold. A small diagnostic comparison does not establish statistical or production effect. The decision never promotes a default.', '']
    (workspace / 'REPORT.md').write_text('\n'.join(lines))
    dialogues = ['# Recorded dialogue slots', '', origin, '']
    for report in reports.values():
        for trace in report.traces:
            dialogues += ['## ' + trace.id, '', trace.metadata['termination'], '']
            for event in trace.metadata.get('planner_events', []):
                dialogues += ['Model-generated preliminary analysis: ' + event['status'] +
                    ' · generation ' + str(event['generation_attempt']) +
                    ' · [record](slots/' + trace.id + '/' + event['record'] + ')', '']
            control_steps = {event['control_step_id'] for event in trace.metadata.get('control_events', [])}
            for message in trace.messages:
                if message.role == 'system':
                    if message.step_id in control_steps:
                        dialogues += ['**Scaffold control** · ' + message.step_id, '', message.content, '']
                    continue
                dialogues += ['**' + message.role + '** · ' + message.step_id, '', message.content, '']
                if message.tool_calls:
                    dialogues += ['```json', encode([c.model_dump(mode='json') for c in message.tool_calls]), '```', '']
    (workspace / 'TRACES.md').write_text('\n'.join(dialogues))
    outputs = ['result.json', 'comparison.json', 'uncertainty.json', 'baseline-criteria.json', 'candidate-criteria.json',
               'baseline-traces.jsonl', 'candidate-traces.jsonl', 'REPORT.md', 'TRACES.md', 'comparison.md']
    outputs += raw + ['finalization.json']
    once(workspace / 'registry.json', {'manifest_sha256': sha(workspace / 'manifest.json'),
        'approval_sha256': sha(workspace / 'approval.json'), 'execution': read(workspace / 'execution.json'),
        'selected_action_id': read(workspace / 'manifest.json')['selected_action_id'],
        'artifacts': {name: sha(workspace / name) for name in outputs}, 'decision': decision,
        'model_version_verified_by_runner': False, 'human_validation': False})
    outputs.append('registry.json')
    state = read(workspace / 'state.json')
    atomic(workspace / 'state.json', {**state, 'phase': 'complete', 'decision': decision,
        'outputs': {name: sha(workspace / name) for name in outputs}})
    return status(workspace)


def terminate_worker(process):
    # Watchdog os._exit can leave a network descendant even after owner exit.
    # Every worker owns a separate process group; kill surviving descendants too.
    if process.poll() is not None:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        return
    if process.poll() is None:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=2)
    else:
        process.wait()
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: pass


def run(workspace, resume=False):
    workspace = Path(workspace).resolve()
    with writer(workspace) as lock:
        config = verify(workspace); state = read(workspace / 'state.json')
        if state['phase'] == 'complete':
            return status(workspace)
        if state['phase'] not in ('approved', 'running', 'finalizing') or (state['phase'] in ('running', 'finalizing') and not resume):
            raise ValueError('exact approval required; use resume for an interrupted run')
        approval = read(workspace / 'approval.json')
        if sha(workspace / 'approval.json') != state['approval_sha256'] or approval['decision'] != 'run' or approval['manifest_sha256'] != state['manifest_sha256']:
            raise ValueError('execution approval changed')
        if config.backend == 'polza':
            from .polza_http import interrupted
            # Exclusive writer lock is inherited by workers. Acquiring it proves
            # prior workers ended; keep their unknown cost but release concurrency.
            interrupted(workspace)
        if state['phase'] == 'finalizing' or (workspace / 'finalization.json').exists() or (workspace / 'stop.json').exists():
            return finalize(workspace)
        if not (workspace / 'execution.json').exists():
            baseline = None
            if config.backend in ('local_http', 'polza'):
                import psutil
                baseline = {'available': psutil.virtual_memory().available, 'swap': psutil.swap_memory().used}
            now = time.time(); deadline=now+config.limits.wall_s
            if config.campaign_deadline_unix is not None:deadline=min(deadline,config.campaign_deadline_unix)
            once(workspace / 'execution.json', {'started': now, 'deadline': deadline,
                'campaign_deadline_unix':config.campaign_deadline_unix, 'memory_baseline': baseline})
        atomic(workspace / 'state.json', {**state, 'phase': 'running'})
        budget = Budget(workspace)
        active = {}
        def end_worker(item, reason=None):
            process, output, folder, deadline = item
            try: terminate_worker(process)
            finally: output.close()
            if config.backend == 'polza':
                interrupted(workspace, folder.name)
            if reason or process.returncode:
                once(folder / 'failure.json', {'returncode': process.returncode,
                    'reason': reason or 'worker exited unsuccessfully', 'stop_scope': 'sample',
                    'meaning': 'measurement incomplete; no retry; later slots may continue under global guards'})
        try:
            if config.backend == 'polza' and not (workspace / 'polza/preflight.json').exists():
                from .polza_http import Client
                Client(workspace, config, budget.execution['deadline']).preflight()
            pending = iter(read(workspace / 'slots.json')); exhausted = False
            while not exhausted or active:
                budget.check()
                while not exhausted and len(active) < config.parallel_workers:
                    try: slot = next(pending)
                    except StopIteration:
                        exhausted = True; break
                    folder = workspace / 'slots' / slot['run_id']; folder.mkdir(parents=True, exist_ok=True)
                    if (folder / 'result.json').exists() or (folder / 'started.json').exists():
                        continue  # Interrupted started slots are unknown, never replayed.
                    budget.check(); once(folder / 'started.json', {'slot': slot, 'time': time.time(), 'parent_pid': os.getpid()})
                    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
                    output = (folder / 'worker.log').open('w')
                    process = subprocess.Popen([sys.executable, '-m', 'agent_improvement_lab.experiment', 'worker',
                        '--workspace', str(workspace), '--run-id', slot['run_id']], cwd=workspace,
                        env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                        pass_fds=(lock.fileno(),))
                    active[slot['run_id']] = (process, output, folder, min(budget.execution['deadline'], time.time()+config.limits.sample_s))
                for run_id, item in list(active.items()):
                    if item[0].poll() is not None or time.time() >= item[3]:
                        end_worker(item, 'sample wall budget exhausted' if item[0].poll() is None else None)
                        del active[run_id]
                if active: time.sleep(.1)
        except (ExecutionStop, RuntimeError) as error:
            if not (workspace / 'stop.json').exists():
                once(workspace / 'stop.json', {'reason': str(error), 'time': time.time()})
        finally:
            cleanup_errors = []
            for item in active.values():
                try: end_worker(item, 'global stop or owning execution interrupted')
                except Exception as error: cleanup_errors.append(type(error).__name__)
            if cleanup_errors:
                raise ExecutionStop('worker cleanup incomplete: ' + ', '.join(cleanup_errors))
            if config.backend == 'polza':
                from .polza_http import Client, interrupted
                interrupted(workspace)
                try:
                    once(workspace / 'polza/balance-after.json', Client(workspace, config, budget.execution['deadline']).metadata('/api/v2/balance'))
                except Exception as error:
                    once(workspace / 'polza/balance-after-error.json', {'type': type(error).__name__, 'meaning': 'balance reconciliation unavailable; reserved exposures retained'})
        return finalize(workspace)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['select', 'prepare', 'approve', 'reject', 'run', 'resume', 'status', 'report', 'worker'])
    parser.add_argument('--workspace', required=True); parser.add_argument('--config')
    parser.add_argument('--manifest-sha256'); parser.add_argument('--reviewer'); parser.add_argument('--run-id')
    parser.add_argument('--plugin', action='append', default=[], help='Explicit trusted self-contained grader code; not selected by YAML')
    parser.add_argument('--triage'); parser.add_argument('--plan'); parser.add_argument('--action-id')
    args = parser.parse_args(argv); workspace = Path(args.workspace)
    try:
        if args.command == 'select': value = select(args.triage, args.plan, args.action_id, workspace)
        elif args.command == 'prepare': value = prepare(args.config, workspace, args.plugin)
        elif args.command in ('approve', 'reject'): value = approve(workspace, args.manifest_sha256, args.reviewer, args.command == 'reject')
        elif args.command in ('run', 'resume'): value = run(workspace, args.command == 'resume')
        elif args.command == 'worker':
            from .experiment_inspect import worker
            value = worker(workspace, args.run_id)
        elif args.command == 'report':
            with writer(workspace):
                if read(workspace / 'state.json')['phase'] != 'complete':
                    raise ValueError('report is for a completed execution; use resume to finalize interrupted work')
                value = finalize(workspace)
        else: value = status(workspace)
        print(encode(value))
    except (ValueError, KeyError, OSError, TypeError, ExecutionStop) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
