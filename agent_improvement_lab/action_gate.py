"""Mechanical action gates based only on linked observed calls and replies."""
import copy
import hashlib
import json

from .evidence_scaffold import observed_state

VERSION = 'observed-action-gate-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def blocked_calls(calls, proof):
    """Validate emitted function names before executing any gated tool."""
    return [call for call in calls if call.function not in proof['allowed_tools']]


def project(body, ticket_id):
    if not body.get('messages') or body['messages'][0].get('role') != 'system':
        raise ValueError('action gate requires the original first system message')
    state = observed_state(body['messages'], ticket_id)
    if state['assignment_acknowledgments']:
        allowed = set(); choice = 'none'; phase = 'confirm'
    elif not state['current_ticket_read']:
        allowed = {'get_ticket'}; choice = 'required'; phase = 'read'
    else:
        allowed = {'route_ticket', 'handoff_to_operator'}
        if state['remaining_clarifications']:
            allowed.add('ask_user')
        choice = 'required'; phase = 'act'
    effective = copy.deepcopy(body)
    effective['tools'] = [t for t in body.get('tools', []) if t['function']['name'] in allowed]
    if allowed != {t['function']['name'] for t in effective['tools']}:
        raise ValueError('action gate required tool schema unavailable')
    effective['tool_choice'] = choice
    operational = {k:state[k] for k in ['current_ticket', 'current_ticket_read',
        'clarification_calls', 'remaining_clarifications', 'assignment_acknowledgments']}
    operational['requested_fields'] = [r['field'] for r in state['observed_field_replies']]
    instruction = ('Observed protocol state follows; it contains no diagnosis or expected destination. '
        'Use the current request and actual replies to choose your next action. '
        'A final text question does not obtain a reply in this interface. '
        'After an acknowledged assignment, briefly confirm it and do not claim repair.\n')
    effective['messages'][0]['content'] += '\n\n' + instruction + json.dumps(operational, sort_keys=True)
    proof = {'version':VERSION, 'origin':'deterministic_observed_action_gate', 'phase':phase,
        'source_request':body, 'source_sha256':digest(body), 'effective_sha256':digest(effective),
        'observed_state':state, 'allowed_tools':sorted(allowed), 'effective_tool_choice':choice,
        'reference_access':False, 'additional_user_facts':False, 'destination_selected_by_controller':False}
    return effective, proof
