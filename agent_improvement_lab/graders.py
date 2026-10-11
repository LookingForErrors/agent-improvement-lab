"""Explicit deterministic registry and strict saved LLM adapter. No model calls."""
import hashlib
import importlib.util
import platform
from importlib.metadata import version
from pathlib import Path

from pydantic import Field, model_validator

from .schemas import Model, Trace
from .criteria import Decision
from .annotation import digest
from .judge_metrics import JudgeIdentity


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def engine_identity():
    return {'python': platform.python_version(), 'pydantic': version('pydantic'),
            'sources': {name: file_sha(Path(__file__).parent / name) for name in ['criteria.py', 'graders.py', 'schemas.py']}}


class MissingAssessment(ValueError):
    pass


class SavedJudge(JudgeIdentity):
    provider: str = Field(min_length=1)

    @model_validator(mode='after')
    def meaningful(self):
        if any(not value.strip() for value in [self.provider, self.version, self.model, self.run_id]):
            raise ValueError('meaningful judge/provider identity required')
        return self


class BindingError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Registry:
    """Trusted in-process check functions; YAML names IDs, never imports code."""
    def __init__(self):
        self._entries = {}
        self._source = file_sha(__file__)

    def register(self, name, version_id, function):
        from .criteria import Grader
        Grader(kind='deterministic', id=name)
        if name in self._entries or not isinstance(version_id, str) or not version_id.strip() or not callable(function):
            raise ValueError('duplicate/invalid registered grader')
        self._entries[name] = (version_id, function, self._source)

    def signature(self, name):
        if name not in self._entries:
            raise ValueError('unknown deterministic grader; explicitly load its trusted plugin')
        v, _, source = self._entries[name]
        return {'kind': 'deterministic', 'id': name, 'version': v, 'implementation_sha256': source}

    def evaluate(self, name, trace, criterion):
        return self._entries[name][1](trace, criterion.grader.parameters)


def argument_fields(trace, parameters):
    """Narrow structural check, not full semantic tool-argument evaluation."""
    if set(parameters) != {'required_fields'} or not isinstance(parameters['required_fields'], list) or not parameters['required_fields'] or any(not isinstance(v, str) or not v for v in parameters['required_fields']):
        raise ValueError('required_fields must be a nonempty list of names')
    calls = [(m.step_id, c) for m in trace.messages for c in m.tool_calls]
    if not calls:
        return Decision(status='not_applicable', explanation='No tool call to check.')
    bad = [step for step, call in calls if not set(parameters['required_fields']) <= set(call.arguments)]
    return Decision(verdict='incorrect' if bad else 'correct', evidence_steps=bad or [step for step, _ in calls],
                    explanation='Required argument fields are missing.' if bad else 'All calls include the configured fields; values are not semantically verified.')


def default_registry():
    registry = Registry()
    registry.register('argument_fields', '1', argument_fields)
    return registry


def load_plugin(registry, path):
    """Executes trusted local Python selected by --plugin, never by YAML.

    This is intentionally NOT a sandbox. Only load code you trust. Plugins should
    be self-contained: the recorded file hash does not seal imported dependencies.
    """
    path = Path(path).resolve()
    fingerprint = file_sha(path)
    spec = importlib.util.spec_from_file_location('ail_plugin_' + fingerprint, path)
    if spec is None or spec.loader is None:
        raise ValueError('plugin must be a Python source file')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    api = getattr(module, 'PLUGIN_API_VERSION', None)
    if type(api) is not int or api != 1 or not callable(getattr(module, 'register', None)):
        raise ValueError('plugin API1 and register(registry) required')
    previous = registry._source
    registry._source = fingerprint
    try:
        module.register(registry)
    finally:
        registry._source = previous


class SavedRow(Model):
    trace_id: str
    criterion_id: str
    trace_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    criterion_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    grader_id: str
    judge_identity_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    assessor_kind: str
    decision: dict


class SavedAssessments:
    def __init__(self, document, rows, pack):
        self._judges = {}; self._rows = {}; self._document_sha = None; self._origin = 'no_assessments'
        if document is None:
            return
        if set(document) != {'format', 'data_kind', 'origin', 'judges', 'assessments'} or document['format'] != 'criteria-assessments-v1':
            raise ValueError('expected criteria-assessments-v1 document')
        if document['data_kind'] != pack.data_kind or document['origin'] not in ['authored_test_fixture', 'recorded_model']:
            raise ValueError('assessment data/provenance mismatch')
        if (pack.data_kind == 'test_fixture') != (document['origin'] == 'authored_test_fixture'):
            raise ValueError('fixture vs recorded provenance mismatch')
        if not isinstance(document['judges'], dict) or not isinstance(document['assessments'], list):
            raise ValueError('judge mapping and assessment list required')
        expected = {c.id: c for c in pack.criteria if c.grader.kind == 'saved_llm'}
        if set(document['judges']) != {c.grader.id for c in expected.values()}:
            raise ValueError('saved grader identity inventory mismatch')
        self._judges = {name: SavedJudge.model_validate(identity) for name, identity in document['judges'].items()}
        by_id = {Trace.model_validate(row).id for row in rows}
        for record in document['assessments']:
            row = SavedRow.model_validate(record)
            key = (row.trace_id, row.criterion_id)
            if key in self._rows or row.trace_id not in by_id or row.criterion_id not in expected or row.grader_id != expected[row.criterion_id].grader.id:
                raise ValueError('unknown/duplicate/mismatched saved assessment')
            self._rows[key] = row
        self._document_sha = digest(document); self._origin = document['origin']

    def signature(self, name):
        if name not in self._judges:
            return {'kind': 'saved_llm', 'id': name, 'availability': 'missing_identity'}
        identity = self._judges[name].model_dump(exclude={'run_id'})
        return {'kind': 'saved_llm', 'id': name, 'identity': identity}

    def evaluate(self, trace, criterion):
        row = self._rows.get((trace.id, criterion.id))
        if row is None:
            raise MissingAssessment('No saved assessment for this expected trace/criterion.')
        identity = self._judges[row.grader_id]
        if row.assessor_kind != 'llm' or row.judge_identity_sha256 != digest(identity.model_dump()):
            raise BindingError('saved_identity_mismatch')
        if row.trace_sha256 != digest(trace.model_dump(mode='json')):
            raise BindingError('saved_source_mismatch')
        if row.criterion_sha256 != digest(criterion.model_dump(mode='json')):
            raise BindingError('saved_criterion_mismatch')
        return Decision.model_validate(row.decision).validate_trace(trace)

    def provenance(self):
        return {'saved_document_sha256': self._document_sha, 'origin': self._origin,
                'judge_runs': {name: identity.model_dump() for name, identity in self._judges.items()}}
