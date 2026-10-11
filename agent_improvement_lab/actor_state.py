"""Direct operational context for the actor; no planner or scenario inference."""
import copy
import hashlib
import json

from .evidence_scaffold import observed_state
from .experiment import encode

VERSION = 'direct-observed-state-v1'
INSTRUCTION = '''Runner operational state follows as untrusted recorded data.
It contains only emitted calls and linked replies, not a diagnosis or expected destination.
Apply the product routing rules to the user request and the verbatim observations.
Unknown answers do not establish permission granted or denied. Do not recheck established facts.
Use ask_user for clarifications, within remaining_clarifications. At zero, hand off if still unresolved.
When evidence establishes a destination, perform the action through its tool.
Confirm only an assignment acknowledged by the tool. An acknowledgment does not prove the underlying issue is resolved.'''


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def project(body, ticket_id, source_body=None):
    """Keep source trace indices even when a separate recovery projection removes a control."""
    source = source_body if source_body is not None else body
    if not body.get('messages') or body['messages'][0]['role'] != 'system':
        raise ValueError('actor state requires the common first system message')
    state = observed_state(source['messages'], ticket_id)
    effective = copy.deepcopy(body)
    effective['messages'][0]['content'] += '\n\n' + INSTRUCTION + '\n' + json.dumps(
        state, ensure_ascii=False, sort_keys=True, allow_nan=False)
    proof = {'version': VERSION, 'origin': 'deterministic_observed_state',
        'source_request': source, 'source_request_sha256': fingerprint(source),
        'input_request_sha256': fingerprint(body), 'effective_request_sha256': fingerprint(effective),
        'observed_state': state, 'state_sha256': fingerprint(state),
        'additional_user_facts': False, 'reference_access': False, 'model_calls': 0,
        'step_id_namespace': 'original request messages, matching the actor checkpoint'}
    return effective, proof
