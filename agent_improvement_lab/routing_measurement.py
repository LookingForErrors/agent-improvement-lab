"""Routing measurement v2: observed prefixes, closed typed facts, no semantic judge."""
import hashlib
import json
from pathlib import Path

from .criteria import Decision
from .schemas import Trace

PLUGIN_API_VERSION = 1
VERSION = 'routing-measurement-v2'
TERMINAL = {'final_after_assignment', 'final_without_assignment', 'turn_budget'}
FIELDS = {'own_access', 'own_role', 'action_permission', 'current_plan',
          'financial_transaction', 'observed_symptom'}
TEAMS = {'payments', 'accounts', 'technical', 'general', 'operator'}
STATES = {'known_positive', 'known_negative', 'unknown'}
SEMANTIC_RUBRIC = ('Judge the whole observed trajectory: questions must request their declared factual field, '
    'current user corrections override stale history, plans supplied in the trace must not turn unknown into a known fact, '
    'and the final response must truthfully confirm the observed action without invented results or timing. '
    'Cite evidence for each defect. A partial trajectory cannot establish full compliance. '
    'Tools are trusted as observed acknowledgments; backend reality is outside this contract.')


def complete(trace):
    return (trace.provenance != 'missing_measurement_placeholder'
            and trace.metadata.get('termination') in TERMINAL
            and trace.metadata.get('trajectory_completeness') != 'partial')


def calls(trace):
    results = {m.tool_call_id: m for m in trace.messages if m.role == 'tool'}
    for message in trace.messages:
        for call in message.tool_calls:
            result = results.get(call.id)
            try:
                value = json.loads(result.content) if result else None
            except (ValueError, TypeError):
                value = None
            yield message, call, result, value if isinstance(value, dict) else None


def reference(trace, parameters):
    ref = parameters['cases'][trace.task_id]
    if not isinstance(ref['ticket_id'], str) or ref['expected_team'] not in TEAMS:
        raise ValueError('valid ticket and authored destination required')
    if type(ref.get('max_questions', 2)) is not int or ref.get('max_questions', 2) != 2:
        raise ValueError('this contract permits two clarification questions')
    if type(ref.get('min_handoff_questions', 2)) is not int or ref.get('min_handoff_questions', 2) != 2:
        raise ValueError('ordinary fallback follows two questions; early exceptions must be explicit')
    used = set(ref.get('initial_facts', {})) | set(ref.get('answers', {}))
    if any(not isinstance(ref.get('propositions', {}).get(field), str)
           or not ref['propositions'][field].strip() for field in used):
        raise ValueError('every typed field requires an explicit evaluator-side proposition')
    for field, fact in ref.get('answers', {}).items():
        if field not in FIELDS or fact['state'] not in STATES or not isinstance(fact['answer'], str):
            raise ValueError('typed exact authored answers required')
    for field, state in ref.get('initial_facts', {}).items():
        if field not in FIELDS or state not in STATES:
            raise ValueError('typed initial facts required')
    for path in ref['routing_paths']:
        if path['destination'] not in TEAMS - {'operator'} or any(
                field not in FIELDS or state not in STATES - {'unknown'}
                for field, state in path['requires'].items()):
            raise ValueError('direct routes require established facts, never unknowns')
    return ref


def decision(verdict, explanation, steps=()):
    return Decision(verdict=verdict, explanation=explanation,
                    evidence_steps=list(dict.fromkeys(steps)))


def assignment(call, value, ref):
    """A success acknowledgment must agree with the requested action and entity."""
    if call.name not in {'route_ticket', 'handoff_to_operator'} or not value or value.get('status') != 'OK':
        return None
    destination = 'operator' if call.name == 'handoff_to_operator' else call.arguments.get('team')
    return (destination if isinstance(destination, str) and destination in (TEAMS if call.name == 'handoff_to_operator' else TEAMS - {'operator'})
            and call.arguments.get('ticket_id') == ref['ticket_id']
            and value.get('ticket_id') == ref['ticket_id']
            and value.get('assigned_to') == destination else False)


def observed_action(trace, parameters):
    ref = reference(trace, parameters); good = []; inconsistent = []
    for m, call, result, value in calls(trace):
        assigned = assignment(call, value, ref)
        if assigned is False:
            inconsistent.extend([m.step_id, result.step_id])
        elif assigned:
            good.extend([m.step_id, result.step_id])
    if good:
        return decision('correct', 'A linked acknowledgment confirms an action on the current ticket; backend state and other behavior are separate.', good)
    if inconsistent:
        return decision('incorrect', 'The success acknowledgment disagrees with the requested ticket or destination.', inconsistent)
    if complete(trace):
        return decision('incorrect', 'The complete trajectory contains no confirmed current-ticket action.', [trace.messages[-1].step_id])
    return decision('unassessable', 'The observed prefix contains no confirmed action; absence in a partial trace is not failure.')


def destination(trace, parameters):
    ref = reference(trace, parameters)
    assigned = [(m, r, assignment(c, v, ref)) for m, c, r, v in calls(trace)
                if assignment(c, v, ref)]
    if assigned:
        m, result, team = assigned[-1]
        return decision('correct' if team == ref['expected_team'] else 'incorrect',
                        f'Last confirmed destination {team}; authored target {ref["expected_team"]}. This does not validate decision justification.', [m.step_id, result.step_id])
    return observed_action(trace, parameters)


def support(trace, parameters):
    ref = reference(trace, parameters); assignments = []; bad = []; uncertain = []; proof = []
    positions = {m.step_id: i for i, m in enumerate(trace.messages)}
    all_calls = list(calls(trace)); early_fields = set(ref.get('early_handoff_fields', []))
    for m, call, result, value in calls(trace):
        if call.name not in {'route_ticket', 'handoff_to_operator'}:
            continue
        assigned = 'operator' if call.name == 'handoff_to_operator' else call.arguments.get('team')
        if call.arguments.get('ticket_id') != ref['ticket_id'] or not isinstance(assigned, str) or assigned not in TEAMS:
            bad.append(m.step_id)
            continue
        facts = dict(ref.get('initial_facts', {})); questions = 0; unresolved_delivery = False
        unmapped = set(); early = False
        for prior, request, reply, answer in all_calls:
            if positions[prior.step_id] >= positions[m.step_id] or request.name != 'ask_user':
                continue
            questions += 1
            field = request.arguments.get('field')
            field = field if isinstance(field, str) else None
            authored = ref.get('answers', {}).get(field)
            if not reply or positions[reply.step_id] >= positions[m.step_id] or not answer or answer.get('status') != 'OK':
                unresolved_delivery = True; unmapped.add(field)
            elif answer.get('field') != field or not authored or answer.get('answer') != authored['answer']:
                unmapped.add(field)
            else:
                facts[field] = authored['state']; proof.append(reply.step_id)
                early = early or field in early_fields
        proof.extend([message.step_id for message in trace.messages[:positions[m.step_id]] if message.role == 'user'][:1])
        assignments.append(m.step_id)
        if result:
            assignments.append(result.step_id)
        available = [path['destination'] for path in ref['routing_paths'] if all(
            facts.get(field) == state for field, state in path['requires'].items())]
        if assigned == 'operator':
            justified = ((questions >= ref.get('min_handoff_questions', 2) and not available) or early)
        else:
            justified = assigned in available
        if not justified:
            could_be_supported = any(path['destination'] == assigned
                and any(field in unmapped for field in path['requires'])
                and all(field in unmapped or facts.get(field) == state for field, state in path['requires'].items())
                for path in ref['routing_paths'])
            is_uncertain = could_be_supported or assigned == 'operator' and unresolved_delivery
            (uncertain if is_uncertain else bad).append(m.step_id)
        elif assigned == 'operator' and (unresolved_delivery or unmapped):
            uncertain.append(m.step_id)
    if bad:
        return decision('incorrect', 'A requested decision lacks its required visible established facts or hands off before the permitted fallback.', bad + proof)
    if uncertain:
        return decision('unassessable', 'A decision depends on an answer without an exact typed reference mapping.', uncertain + proof)
    if assignments:
        return decision('correct', 'Every requested decision in this observed prefix is supported by typed visible facts or the declared fallback. Execution and question meaning are separate.', assignments + proof)
    return decision('unassessable', 'No routing decision was observed to justify; task execution is a separate criterion.')


def protocol(trace, parameters):
    ref = reference(trace, parameters); read = False; questions = 0; bad = []; uncertain = []; evidence = []
    agent_error_codes = {'WRONG_TICKET', 'READ_REQUIRED', 'UNSUPPORTED_FIELD', 'EMPTY_QUESTION', 'CLARIFICATION_LIMIT'}
    positions = {m.step_id: i for i, m in enumerate(trace.messages)}
    all_calls = list(calls(trace))
    for m, call, result, value in all_calls:
        evidence.append(m.step_id)
        read = any(c.name == 'get_ticket' and c.arguments.get('ticket_id') == ref['ticket_id']
                   and r and positions[r.step_id] < positions[m.step_id]
                   and v and v.get('status') == 'OK' and v.get('ticket_id') == ref['ticket_id']
                   for _, c, r, v in all_calls)
        if call.arguments.get('ticket_id') != ref['ticket_id']:
            bad.append(m.step_id)
        keys = {'get_ticket': {'ticket_id'}, 'ask_user': {'ticket_id', 'field', 'question'},
                'route_ticket': {'ticket_id', 'team'}, 'handoff_to_operator': {'ticket_id'}}
        if call.name in keys and set(call.arguments) != keys[call.name]:
            bad.append(m.step_id)
        if call.name not in {'get_ticket', 'ask_user', 'route_ticket', 'handoff_to_operator'}:
            bad.append(m.step_id)
        elif call.name == 'get_ticket':
            if value and value.get('status') == 'OK' and value.get('ticket_id') != ref['ticket_id']:
                uncertain.append(m.step_id)
        else:
            if not read:
                bad.append(m.step_id)
            if call.name == 'ask_user':
                questions += 1
                if questions > 2 or not isinstance(call.arguments.get('field'), str) or call.arguments['field'] not in FIELDS or not isinstance(call.arguments.get('question'), str) or not call.arguments['question'].strip():
                    bad.append(m.step_id)
            if call.name == 'route_ticket' and (not isinstance(call.arguments.get('team'), str) or call.arguments['team'] not in TEAMS - {'operator'}):
                bad.append(m.step_id)
            if assignment(call, value, ref) is False:
                uncertain.extend([m.step_id, result.step_id])  # response inconsistency alone does not prove agent fault
        if value and value.get('status') == 'ERROR' and value.get('code') in agent_error_codes:
            bad.append(m.step_id)
        elif not value or value.get('status') != 'OK':
            uncertain.append(m.step_id)  # pending/transport/tool failure is not an agent violation
    if bad:
        return decision('incorrect', 'An observed request/order/ticket/question-budget violation is established, even in a partial trajectory.', bad)
    if not complete(trace) or uncertain:
        return decision('unassessable', 'The observed prefix has no proven protocol violation; completeness or tool delivery is unresolved.', uncertain)
    if not any(c.name == 'get_ticket' and c.arguments.get('ticket_id') == ref['ticket_id']
               and v and v.get('status') == 'OK' and v.get('ticket_id') == ref['ticket_id'] for _, c, _, v in all_calls):
        return decision('incorrect', 'The complete trajectory omitted the required ticket read.', [trace.messages[-1].step_id])
    return decision('correct', 'Current ticket, tool order and two-question budget checked. Natural-language question relevance is not proven.', evidence)


def confirmation(trace, parameters):
    ref = reference(trace, parameters); ack = None
    for m, call, result, value in calls(trace):
        if assignment(call, value, ref):
            ack = result
    if ack:
        after = False
        for message in trace.messages:
            if message.step_id == ack.step_id:
                after = True
            elif after and message.role == 'assistant' and not message.tool_calls and isinstance(message.content, str) and message.content.strip():
                return decision('correct', 'A user-facing response follows acknowledgment; its meaning and grounding require semantic review.', [ack.step_id, message.step_id])
    if complete(trace):
        return decision('incorrect', 'No user-facing response after a confirmed action.', [trace.messages[-1].step_id])
    return decision('unassessable', 'Final user-facing confirmation is absent from the partial trajectory.')


def product_success(trace, parameters):
    """Whole-trajectory primary: deterministic evidence AND a bound semantic review."""
    checks = [observed_action(trace, parameters), support(trace, parameters), protocol(trace, parameters), confirmation(trace, parameters)]
    review = parameters.get('semantic_decisions', {}).get(trace.id)
    if review is not None:
        fingerprint = hashlib.sha256(json.dumps(trace.model_dump(mode='json'), sort_keys=True,
                                                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if (review.get('trace_sha256') != fingerprint or review.get('rubric_version') != VERSION
                or review.get('semantic_contract_sha256') != semantic_identity(trace, parameters)
                or review.get('assessor_kind') not in {'human', 'llm'} or not review.get('assessor_id')):
            raise ValueError('semantic review is not bound to this exact trace/rubric/assessor')
        semantic = Decision.model_validate(review['decision'])
        if not set(semantic.evidence_steps) <= {m.step_id for m in trace.messages}:
            raise ValueError('semantic evidence references absent steps')
        checks.append(semantic)
    bad = [check for check in checks if check.verdict == 'incorrect']
    if bad:
        return decision('incorrect', 'An established execution, decision, protocol or semantic defect violates the whole-trajectory success contract.', [s for c in bad for s in c.evidence_steps])
    if review is None or any(check.verdict != 'correct' for check in checks):
        return decision('unassessable', 'Whole-trajectory success needs all mechanical checks and an exact-bound semantic review; positive action evidence is retained separately.')
    return decision('correct', 'Confirmed current-ticket action, justified decision, protocol and bound semantic review all pass.', [s for c in checks for s in c.evidence_steps])


def semantic_identity(trace, parameters):
    payload = {'version': VERSION, 'reference': reference(trace, parameters), 'semantic_rubric': SEMANTIC_RUBRIC}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def project_messages(messages):
    """Preserve ordered native text; never flatten unsupported media or reasoning silently."""
    projected = []
    for ordinal, native in enumerate(messages):
        content = native.get('content', '')
        if isinstance(content, list):
            if any(not isinstance(b, dict) or b.get('type') != 'text' or not isinstance(b.get('text'), str) for b in content):
                raise ValueError('unsupported native content block')
            content = '\n'.join(b['text'] for b in content)
        if not isinstance(content, str):
            raise ValueError('native content must be text')
        item = {'step_id': f's{ordinal:03d}', 'role': native['role'], 'content': content}
        if native.get('tool_calls'):
            item['tool_calls'] = [{'id': c['id'], 'name': c['function'], 'arguments': c['arguments']}
                                  for c in native['tool_calls']]
        for key in ('tool_call_id', 'function', 'reasoning'):
            if native.get(key) is not None:
                item[key] = native[key]
        projected.append(item)
    return projected


def checkpoint_prefix(workspace, slot, case, allow_legacy=False):
    """Bind an interrupted prefix to sealed inputs and the start record, not summary flags."""
    workspace = Path(workspace); folder = workspace / 'slots' / slot['run_id']
    read = lambda path: json.loads(path.read_text())
    started = read(folder / 'started.json'); checkpoint = read(folder / 'actor-checkpoint.json')
    if started['slot'] != slot or any(case[key] != slot[key] for key in ('family_id', 'split')) or case['id'] != slot['task_id']:
        raise ValueError('checkpoint slot/case identity mismatch')
    for key, expected in [('logical_epoch', int(slot['repeat_id'])),
            ('scaffold_tool_control', slot.get('tool_control', 'auto')),
            ('evidence_first', slot.get('evidence_first', False)),
            ('zero_call_recovery_limit', slot.get('zero_call_recovery', 0))]:
        if checkpoint.get(key) != expected:
            raise ValueError('checkpoint control identity mismatch: ' + key)
    if checkpoint.get('planner_state_guard', False) != slot.get('planner_state_guard', False):
        raise ValueError('checkpoint control identity mismatch: planner_state_guard')
    if checkpoint.get('actor_state', False) != slot.get('actor_state', False):
        raise ValueError('checkpoint control identity mismatch: actor_state')
    messages = project_messages(checkpoint['messages'])
    expected_system = ((workspace / ('inputs/' + slot['arm'] + '_prompt')).read_text()
                       + '\n' + (workspace / 'inputs/common').read_text() + '\nCurrent ticket: ' + case['ticket_id'])
    if len(messages) < 2 or messages[0]['role'] != 'system' or messages[0]['content'] != expected_system or messages[1]['role'] != 'user' or messages[1]['content'] != case['user_request']:
        raise ValueError('checkpoint objective/system binding mismatch')
    identity = {'run_id': slot['run_id'], 'task_id': slot['task_id'], 'arm': slot['arm']}
    if 'execution_identity' not in checkpoint and not allow_legacy:
        raise ValueError('future v2 checkpoints require explicit execution identity')
    if 'execution_identity' in checkpoint and checkpoint['execution_identity'] != identity:
        raise ValueError('checkpoint explicit execution identity mismatch')
    # Validation rejects dangling result links, duplicate steps/calls and parallel calls.
    Trace(id=slot['run_id'], messages=messages)
    paths = [folder / name for name in ('started.json', 'actor-checkpoint.json', 'failure.json') if (folder / name).exists()]
    metadata = {key: checkpoint[key] for key in ('simulation_events', 'requested_fields', 'planner_events', 'control_events', 'planner_state_guard', 'actor_state') if key in checkpoint}
    metadata.update(termination='infrastructure_stop', trajectory_completeness='partial',
                    measurement_contract=VERSION, measurement_status='partial_observed',
                    model_outputs_returned=len(checkpoint.get('calls', [])),
                    checkpoint_native_ids={f's{i:03d}': m.get('id') for i, m in enumerate(checkpoint['messages'])},
                    extraction_binding='explicit_identity_and_inputs' if 'execution_identity' in checkpoint else 'legacy_start_record_and_inputs',
                    checkpoint_evidence={str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    return messages, metadata


def register(registry):
    previous = registry._source
    registry._source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        for name, function in [('observed_action_v2', observed_action), ('closed_destination_v2', destination),
                ('routing_support_v2', support), ('routing_protocol_v2', protocol), ('response_after_action_v2', confirmation)]:
            registry.register(name, VERSION, function)
        registry.register('product_success_v2', VERSION, product_success)
    finally:
        registry._source = previous
