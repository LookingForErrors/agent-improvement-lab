"""Task routing outcome, separate from ancillary whole-trace assessment."""
import hashlib
import json
from pathlib import Path

from .criteria import Decision
from . import routing_measurement as rm

VERSION = 'routing-task-outcome-v1'
RUBRIC = ('Assess whether the current user request was correctly and justifiably routed with a linked '
    'tool acknowledgment. Questions must request the declared fact; current corrections override stale context; '
    'unknown facts do not justify a team. Apply the read and two-question policy. '
    'A partial capture cannot establish success. Treat tools as observed acknowledgments. '
    'Assess extra final-response claims and planner bookkeeping separately; they do not alone change routing completion.')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def semantic_identity(trace, parameters):
    return fingerprint({'version': VERSION, 'reference': rm.reference(trace, parameters), 'rubric': RUBRIC})


def success(trace, parameters):
    checks = [rm.observed_action(trace, parameters), rm.destination(trace, parameters),
              rm.support(trace, parameters), rm.protocol(trace, parameters)]
    review = parameters.get('task_semantic_decisions', {}).get(trace.id)
    if review is not None:
        if (review.get('trace_sha256') != fingerprint(trace.model_dump(mode='json'))
                or review.get('rubric_version') != VERSION
                or review.get('semantic_contract_sha256') != semantic_identity(trace, parameters)
                or review.get('assessor_kind') not in {'human', 'llm'} or not review.get('assessor_id')):
            raise ValueError('routing task review is not bound to trace/rubric/assessor')
        semantic = Decision.model_validate(review['decision'])
        if not set(semantic.evidence_steps) <= {m.step_id for m in trace.messages}:
            raise ValueError('routing task evidence references absent steps')
        checks.append(semantic)
    bad = [c for c in checks if c.verdict == 'incorrect']
    if bad:
        return rm.decision('incorrect', 'An observed action, destination, support or protocol defect prevents valid routing.',
            [s for c in bad for s in c.evidence_steps])
    if not rm.complete(trace) or review is None or any(c.verdict != 'correct' for c in checks):
        return rm.decision('unassessable', 'Routing success requires a complete capture and an exact-bound routing semantic review.')
    return rm.decision('correct', 'Correct supported current-ticket assignment and routing semantics pass; ancillary claims are separate.',
        [s for c in checks for s in c.evidence_steps])


def register(registry):
    previous = registry._source
    registry._source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        registry.register('routing_task_success_v1', VERSION, success)
    finally:
        registry._source = previous
