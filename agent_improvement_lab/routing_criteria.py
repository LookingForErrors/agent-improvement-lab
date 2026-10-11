"""Packaged deterministic routing graders; no model/runtime access."""
import hashlib
import json
from pathlib import Path
from .criteria import Decision
PLUGIN_API_VERSION = 1
VERSION = 'structured-routing-v1'

def _measurement(trace):
    return trace.provenance == 'missing_measurement_placeholder' or trace.metadata.get('termination') in (
        'infrastructure_stop', 'framework_error_or_limit', 'unsupported_multi_tool_call')


def _calls(trace):
    results = {m.tool_call_id: m for m in trace.messages if m.role == 'tool'}
    for message in trace.messages:
        for call in message.tool_calls:
            result = results.get(call.id)
            try: value = json.loads(result.content) if result else None
            except (ValueError, TypeError): value = None
            yield message, call, result, value


def _unknown(trace):
    return Decision(verdict='unassessable', explanation='Measurement incomplete; no agent quality verdict.')


def destination(trace, parameters):
    if _measurement(trace): return _unknown(trace)
    expected = parameters['cases'][trace.task_id]['expected_team']
    assignments = [(m, c, r, v) for m, c, r, v in _calls(trace)
        if c.name in ('route_ticket', 'handoff_to_operator') and v and v.get('status') == 'OK']
    correct = bool(assignments) and assignments[-1][3].get('assigned_to') == expected
    evidence = [assignments[-1][2].step_id] if assignments else [trace.messages[-1].step_id]
    return Decision(verdict='correct' if correct else 'incorrect', evidence_steps=evidence,
        explanation=f'Observed mock destination {assignments[-1][3].get("assigned_to") if assignments else None}; authored objective {expected}.')


def routed_terminal(trace, parameters):
    if _measurement(trace): return _unknown(trace)
    assigned = [(m, c, r, v) for m, c, r, v in _calls(trace)
        if c.name in ('route_ticket', 'handoff_to_operator') and v and v.get('status') == 'OK']
    return Decision(verdict='correct' if assigned else 'incorrect',
        evidence_steps=[assigned[-1][2].step_id] if assigned else [trace.messages[-1].step_id],
        explanation='A mock routing/handoff was confirmed; destination validity is separate.' if assigned else 'Execution ended without confirmed routing/handoff.')


def support(trace, parameters):
    if _measurement(trace): return _unknown(trace)
    reference = parameters['cases'][trace.task_id]; seen = set(); evidence = []; bad = []; assignments = 0
    for m, c, r, v in _calls(trace):
        if c.name == 'ask_user' and v and v.get('status') == 'OK':
            seen.add(v['field']); evidence.append(r.step_id)
        if c.name in ('route_ticket', 'handoff_to_operator') and v and v.get('status') == 'OK':
            needed = set(reference['required_support'])
            assigned = v.get('assigned_to')
            # Exact destination is a product target. A safe fallback can still be
            # valid assistant behavior while missing that direct-routing target.
            correct = ((assigned == reference['expected_team'] and needed <= seen)
                or (assigned == 'operator' and not (needed & seen)))
            assignments += 1; evidence.append(m.step_id)
            if not correct: bad.append(m.step_id)
    if assignments:
        return Decision(verdict='incorrect' if bad else 'correct', evidence_steps=bad or evidence,
            explanation='Every routing action has the required supporting fields.' if not bad else 'A routing action lacks the required supporting field or contradicts the closed rule.')
    return Decision(verdict='incorrect', evidence_steps=[trace.messages[-1].step_id], explanation='No confirmed routing action to justify.')


def protocol(trace, parameters):
    if _measurement(trace): return _unknown(trace)
    reference = parameters['cases'][trace.task_id]; seen = set(); read_ticket = False; bad = []; evidence = []
    for m, c, r, v in _calls(trace):
        evidence.append(m.step_id)
        if c.arguments.get('ticket_id') != reference['ticket_id'] or not v or v.get('status') != 'OK': bad.append(m.step_id)
        if c.name == 'get_ticket': read_ticket = bool(v and v.get('status') == 'OK')
        elif c.name == 'ask_user':
            if not read_ticket: bad.append(m.step_id)
            seen.add(c.arguments.get('field'))
            if len(seen) > 2: bad.append(m.step_id)
        elif c.name in ('route_ticket', 'handoff_to_operator'):
            if not read_ticket: bad.append(m.step_id)
        else: bad.append(m.step_id)
    if not read_ticket: bad.append(trace.messages[-1].step_id)
    return Decision(verdict='incorrect' if bad else 'correct', evidence_steps=list(dict.fromkeys(bad or evidence)),
        explanation='Read/order/current-ticket/tool-result/two-field contract checked; question wording is not semantically graded.')


def register(registry):
    previous = registry._source
    registry._source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        for name, function in [('routed_terminal', routed_terminal), ('closed_destination', destination), ('routing_support', support), ('routing_protocol', protocol)]:
            registry.register(name, VERSION, function)
    finally: registry._source = previous

