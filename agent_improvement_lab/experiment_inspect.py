"""Optional Inspect executor for controlled routing, caller-managed localhost only."""
import json
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from inspect_ai.model import ChatMessageSystem, ChatMessageUser, execute_tools
from inspect_ai.solver import solver
from inspect_ai.tool import tool
from .experiment import ExecutionStop as InfrastructureStop, atomic, read, encode

VERSION = 'structured-routing-user-v2'
RECOVERY_VERSION = 'zero-call-recovery-v1'
RECOVERY_CONTROL = ('Runner control: The previous assistant output contained no tool call, '
    'and no ticket assignment has been recorded. In this environment, clarification reaches '
    'the simulated user only through ask_user. Continue using the available tools under the '
    'existing rules. Do not invent a user answer or infer missing facts.')
TEAMS = {'payments', 'accounts', 'technical', 'general'}
FieldName = Literal['own_access', 'own_role', 'action_permission', 'current_plan',
                    'financial_transaction', 'observed_symptom']

@dataclass
class MockTicket:
    ticket:dict
    read:bool=False
    assigned_to:str|None=None
    def get(self,ticket_id):
        if ticket_id!=self.ticket['ticket_id']:return {'status':'ERROR','code':'WRONG_TICKET'}
        self.read=True;return {'status':'OK','ticket_id':ticket_id,'ticket_status':self.ticket['status'],'history':self.ticket['history'],'mock':True}
    def assign(self,ticket_id,team):
        if ticket_id!=self.ticket['ticket_id']:return {'status':'ERROR','code':'WRONG_TICKET'}
        if not self.read:return {'status':'ERROR','code':'READ_REQUIRED'}
        if team not in TEAMS|{'operator'}:return {'status':'ERROR','code':'UNKNOWN_TEAM'}
        self.assigned_to=team
        return {'status':'OK','ticket_id':ticket_id,'assigned_to':team,'mock':True}

def mock_tools(mock):
    @tool
    def get_ticket():
        async def execute(ticket_id:str)->str:
            """Read this mock support ticket and its recorded history.

            Args:
                ticket_id: The current support ticket identifier.
            """
            return json.dumps(mock.get(ticket_id),ensure_ascii=False)
        return execute
    @tool
    def route_ticket():
        async def execute(ticket_id:str,team:str)->str:
            """Assign the current mock ticket to payments, accounts, technical or general.

            Args:
                ticket_id: The current support ticket identifier.
                team: One of payments, accounts, technical, general; operator uses a separate tool.
            """
            if team not in TEAMS:return json.dumps({'status':'ERROR','code':'UNKNOWN_TEAM'})
            return json.dumps(mock.assign(ticket_id,team))
        return execute
    @tool
    def handoff_to_operator():
        async def execute(ticket_id:str)->str:
            """Hand the current mock ticket to an operator when ambiguity remains.

            Args:
                ticket_id: The current support ticket identifier.
            """
            return json.dumps(mock.assign(ticket_id,'operator'))
        return execute
    return [get_ticket(),route_ticket(),handoff_to_operator()]

class FactUser:
    def __init__(self, case):
        self.ticket_id = case['ticket_id']
        self.facts = {case['fact_kinds'][key]: text for key, text in case['facts'].items()}
        if len(self.facts) != len(case['facts']):
            raise ValueError('Each scenario field must have exactly one authored fact')
        self.requested = []; self.events = []

    def answer(self, ticket_id, field, question):
        if ticket_id != self.ticket_id:
            value = {'status': 'ERROR', 'code': 'WRONG_TICKET', 'mock': True}
        elif field not in self.facts:
            value = {'status': 'ERROR', 'code': 'UNSUPPORTED_FIELD', 'mock': True}
        elif not isinstance(question, str) or not question.strip():
            value = {'status': 'ERROR', 'code': 'EMPTY_QUESTION', 'mock': True}
        elif len(self.requested) >= 2:
            value = {'status': 'ERROR', 'code': 'CLARIFICATION_LIMIT', 'mock': True}
        else:
            self.requested.append(field)
            value = {'status': 'OK', 'field': field, 'answer': self.facts[field], 'mock': True}
        self.events.append({'field': field, 'question': question, 'response': value})
        return value


def tools(mock, user):
    @tool
    def ask_user():
        async def execute(ticket_id: str, field: FieldName, question: str) -> str:
            """Ask the synthetic user for one factual field about the current issue.

            Args:
                ticket_id: Current ticket ID; read get_ticket before asking.
                field: own_access=sign-in/viewing; own_role=project role;
                    action_permission=permission for the failing action;
                    current_plan=plan/allowance; financial_transaction=receipt/payment;
                    observed_symptom=what the user observes. Choose one field.
                question: Natural-language question requesting only that field.
            """
            if not mock.read:
                return json.dumps({'status': 'ERROR', 'code': 'READ_REQUIRED', 'mock': True})
            return json.dumps(user.answer(ticket_id, field, question), ensure_ascii=False)
        return execute
    return mock_tools(mock) + [ask_user()]


@solver
def structured_actor(rules, common, case, slot, folder, budget):
    """One uninterrupted Inspect trajectory; no semantic selector or second LLM."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    async def solve(state, generate_fn):
        if (folder / 'actor-checkpoint.json').exists():
            raise InfrastructureStop('Started structured slot cannot be silently replayed')
        mock = MockTicket(case['mock_ticket']); user = FactUser(case)
        state.messages.insert(0, ChatMessageSystem(content=rules + '\n' + common +
                              '\nCurrent ticket: ' + case['ticket_id']))
        state.tools = tools(mock, user); calls = []; attempts = 0; started = time.time()
        termination = 'running'; inflight = False; choices = []
        tool_control = slot.get('tool_control', 'auto')
        if tool_control not in ('auto', 'required'): raise ValueError('Unknown scaffold tool control')
        recovery_limit = slot.get('zero_call_recovery', 0)
        if type(recovery_limit) is not int or recovery_limit not in (0, 1):
            raise ValueError('Unknown zero-call recovery limit')
        recovery_used = False; control_events = []; recovery_declined = None
        evidence_mode = slot.get('evidence_first', False)
        if type(evidence_mode) is not bool or evidence_mode and recovery_limit:
            raise ValueError('Evidence planning is a separate strict opt-in intervention')
        state_guard = slot.get('planner_state_guard', False)
        if type(state_guard) is not bool or state_guard and not evidence_mode:
            raise ValueError('Planner state guard requires evidence planning')
        actor_state_mode = slot.get('actor_state', False)
        action_gate_mode = slot.get('action_gate', False)
        if type(actor_state_mode) is not bool or actor_state_mode and evidence_mode:
            raise ValueError('Direct actor state requires an actor without planning')
        planner_events = []; planner_attempts = 0; actor_attempts = 0

        def save():
            metadata = dict(termination=termination, logical_epoch=int(slot['repeat_id']),
                assigned_to_observed=mock.assigned_to, model_calls=len(calls),
                generation_attempts=attempts, model_outputs=calls,
                simulation_events=user.events, requested_fields=user.requested,
                scaffold_tool_control=tool_control, requested_tool_choices=choices,
                zero_call_recovery_limit=recovery_limit, zero_call_recovery_used=recovery_used,
                control_events=control_events, recovery_declined_reason=recovery_declined,
                evidence_first=evidence_mode, planner_state_guard=state_guard, actor_state=actor_state_mode, action_gate=action_gate_mode, planner_events=planner_events,
                planner_attempts=planner_attempts, actor_attempts=actor_attempts,
                user_simulator=VERSION, measurement_status='unassessable' if termination == 'infrastructure_stop' else 'observed')
            state.metadata.update(metadata)
            atomic(folder / 'actor-checkpoint.json', {'started': started, 'inflight': inflight,
                'execution_identity': {'run_id': slot['run_id'], 'task_id': slot['task_id'], 'arm': slot['arm']},
                'messages': [m.model_dump(mode='json') for m in state.messages], 'calls': calls,
                'mock_read': mock.read, 'mock_assigned_to': mock.assigned_to, **metadata})
        try:
            while attempts < budget.config.max_generations:
                budget.check()
                if time.time() - started >= budget.config.sample_s: raise InfrastructureStop('sample wall exhausted')
                if evidence_mode and mock.read and mock.assigned_to is None:
                    from .evidence_scaffold import (visible_evidence, validate_plan, digest, observed_state,
                        PLANNER_INSTRUCTION, STATE_INSTRUCTION, VERSION as EVIDENCE_VERSION, GUARDED_VERSION)
                    if budget.config.max_generations - attempts < 2:
                        termination = 'turn_budget'; save(); break
                    snapshot = visible_evidence(state.messages)
                    control_state = (observed_state(request_body(CONTEXT['settings'].model_id, state.messages,
                        [], 'none', GenerateConfig())['messages'], case['ticket_id']) if state_guard else None)
                    planner_input = {'observations': snapshot, 'observed_state': control_state} if state_guard else snapshot
                    planner_version = GUARDED_VERSION if state_guard else EVIDENCE_VERSION
                    planner_attempts += 1; attempts += 1; inflight = True; save()
                    CONTEXT['phase'] = 'planner'; CONTEXT['evidence_record'] = None
                    plan_output = await CONTEXT['model'].generate([
                        ChatMessageSystem(content=rules + '\n\n' + PLANNER_INSTRUCTION + ('\n' + STATE_INSTRUCTION if state_guard else '')),
                        ChatMessageUser(content='Recorded observations for this preliminary analysis task:\n' + json.dumps(planner_input, ensure_ascii=False))],
                        tools=[], tool_choice='none', config=GenerateConfig(seed=1000 + int(slot['repeat_id'])), cache=False)
                    calls.append(plan_output.model_dump(mode='json'))
                    record = {'origin': 'model_generated_analysis', 'version': planner_version,
                        'ordinal': planner_attempts, 'generation_attempt': attempts,
                        'output_ordinal': len(calls), 'snapshot': snapshot,
                        'snapshot_sha256': digest(snapshot), 'raw_text': plan_output.message.text,
                        'status': 'validated_structure', 'plan': None, 'error_code': None}
                    if state_guard:
                        record.update(observed_state=control_state, state_sha256=digest(control_state))
                    try:
                        if plan_output.message.tool_calls:
                            raise ValueError('PLANNER_TOOL_CALL_NOT_ALLOWED')
                        record['plan'] = validate_plan(record['raw_text'], snapshot, control_state)
                    except ValueError as error:
                        record.update(status='rejected_state' if str(error) == 'CLARIFICATION_BUDGET_EXHAUSTED' else 'rejected_structure', error_code=str(error))
                    path = folder / 'planner' / f'{planner_attempts:04d}.json'
                    atomic(path, record)
                    planner_events.append({'origin': record['origin'], 'version': planner_version,
                        'ordinal': planner_attempts, 'status': record['status'], 'error_code': record['error_code'],
                        'generation_attempt': attempts, 'output_ordinal': len(calls),
                        'record': str(path.relative_to(folder)), 'record_sha256': hashlib.sha256(encode(record).encode()).hexdigest()})
                    CONTEXT['evidence_record'] = record
                    inflight = False; save()
                    budget.check()
                CONTEXT['phase'] = 'actor'
                if mock.assigned_to is not None:
                    CONTEXT['evidence_record'] = None
                state.tool_choice = ('any' if mock.assigned_to is None else 'none') if tool_control == 'required' else 'auto'
                choices.append(state.tool_choice)
                inflight = True; attempts += 1; actor_attempts += 1
                if control_events and control_events[-1]['next_generation_attempt'] is None:
                    control_events[-1]['next_generation_attempt'] = attempts
                save()
                state = await generate_fn(state, tool_calls='none', seed=1000 + int(slot['repeat_id']), cache=False)
                output = state.output; calls.append(output.model_dump(mode='json'))
                if control_events and control_events[-1]['next_output_ordinal'] is None:
                    control_events[-1]['next_output_ordinal'] = len(calls)
                if len(output.message.tool_calls or []) > 1:
                    inflight = False; termination = 'unsupported_multi_tool_call'; save(); break
                if output.message.tool_calls:
                    blocked = []
                    if action_gate_mode:
                        from .action_gate import blocked_calls
                        proof = read(folder / 'provider-calls' / f'{CONTEXT["calls"]:04d}-gate-projection.json')
                        blocked = blocked_calls(output.message.tool_calls, proof)
                    if blocked:
                        from inspect_ai.model import ChatMessageTool
                        # Preserve the proposal and a linked controller rejection;
                        # do not execute an out-of-state action or reveal a user fact.
                        for call in output.message.tool_calls:
                            state.messages.append(ChatMessageTool(tool_call_id=call.id, function=call.function,
                                content=json.dumps({'status':'ERROR','code':'ACTION_GATE_REJECTED',
                                    'origin':'scaffold_control','tool_executed':False,'mock':True})))
                    else:
                        result = await execute_tools(state.messages, state.tools, max_output=200000)
                        state.messages.extend(result.messages)
                else:
                    if mock.assigned_to is None and recovery_limit and not recovery_used:
                        if attempts < budget.config.max_generations:
                            recovery_used = True
                            control_events.append({'origin': 'scaffold_control', 'version': RECOVERY_VERSION,
                                'source_step_id': f's{len(state.messages)-1:03d}',
                                'source_output_ordinal': len(calls),
                                'source_output_sha256': hashlib.sha256(encode(calls[-1]).encode()).hexdigest(),
                                'control_step_id': f's{len(state.messages):03d}',
                                'control_text_sha256': hashlib.sha256(RECOVERY_CONTROL.encode()).hexdigest(),
                                'trigger_generation_attempt': attempts,
                                'next_generation_attempt': None, 'next_output_ordinal': None})
                            state.messages.append(ChatMessageSystem(content=RECOVERY_CONTROL))
                            inflight = False; save(); continue
                        recovery_declined = 'max_generations'
                    termination = 'final_after_assignment' if mock.assigned_to is not None else 'final_without_assignment'
                    inflight = False; save(); break
                inflight = False; save()
            else: termination = 'turn_budget'; save()
        except Exception as error:
            termination = 'infrastructure_stop'
            state.metadata['runner_error'] = {'type': type(error).__name__, 'message': str(error)}
            save()
        state.completed = True
        return state
    return solve


# Each worker owns one trajectory. Context is never shared across workers.
CONTEXT = None


from inspect_ai.model import ModelAPI, ModelCall, GenerateConfig, get_model, modelapi
from .local_http import request_body, parse_response, NoRedirect


def project_recovery_control(body, ctx):
    """Preserve chronological trace controls, use initial-system-only provider roles."""
    if not ctx['slot'].get('zero_call_recovery', 0):
        return body, None
    events = read(ctx['folder'] / 'actor-checkpoint.json').get('control_events', [])
    if not events:
        return body, None
    if len(events) != 1:
        raise ValueError('Recovery projection requires exactly one consumed control')
    event = events[0]; index = int(event['control_step_id'][1:])
    original = body['messages']
    if index <= 0 or index >= len(original) or original[0]['role'] != 'system' or original[index] != {'role': 'system', 'content': RECOVERY_CONTROL}:
        raise ValueError('Recovery control differs from checkpoint/provider transcript')
    source = int(event['source_step_id'][1:])
    if source != index - 1 or original[source]['role'] != 'assistant' or original[source].get('tool_calls'):
        raise ValueError('Recovery trigger is not the preserved zero-call output')
    messages = [dict(message) for ordinal, message in enumerate(original) if ordinal != index]
    messages[0]['content'] += '\n\n' + RECOVERY_CONTROL
    projected = dict(body, messages=messages)
    return projected, {'version': 'recovery-control-projection-v1', 'origin': 'scaffold_control',
        'trace_control_step_id': event['control_step_id'], 'trace_source_step_id': event['source_step_id'],
        'original_request_sha256': hashlib.sha256(encode(body).encode()).hexdigest(),
        'effective_request_sha256': hashlib.sha256(encode(projected).encode()).hexdigest(),
        'original_messages': original, 'effective_system_message_index': 0,
        'control_text_sha256': event['control_text_sha256']}


@modelapi(name='ail_experiment')
class ExperimentModel(ModelAPI):
    async def generate(self, input, tools, tool_choice, config):
        import asyncio
        import urllib.request
        import urllib.error
        from .experiment import encode, once
        ctx = CONTEXT
        body = request_body(self.model_name, input, tools, tool_choice, config)
        source_body = body
        phase = ctx.get('phase', 'actor')
        projection = None
        if phase == 'actor':
            body, projection = project_recovery_control(body, ctx)
            from .evidence_scaffold import project_plan
            body, evidence_projection = project_plan(body, ctx.get('evidence_record'), ctx.get('current_ticket'),
                ctx['slot'].get('planner_state_guard', False))
        else:
            evidence_projection = None
        state_projection = None
        if phase == 'actor' and ctx['slot'].get('actor_state', False):
            from .actor_state import project
            body, state_projection = project(body, ctx['current_ticket'], source_body)
        gate_projection = None
        if phase == 'actor' and ctx['slot'].get('action_gate', False):
            from .action_gate import project
            body, gate_projection = project(body, ctx['current_ticket'])
        if ctx['settings'].backend == 'polza':
            body = ctx['polza_client'].body(body)
        ordinal = ctx['calls'] + 1; ctx['calls'] = ordinal
        started = time.time(); folder = ctx['folder'] / 'provider-calls'
        if projection is not None:
            once(folder / f'{ordinal:04d}-control-projection.json', projection)
        if state_projection is not None:
            state_projection['provider_request_sha256'] = hashlib.sha256(encode(body).encode()).hexdigest()
            once(folder / f'{ordinal:04d}-state-projection.json', state_projection)
        if gate_projection is not None:
            gate_projection['provider_request_sha256'] = hashlib.sha256(encode(body).encode()).hexdigest()
            once(folder / f'{ordinal:04d}-gate-projection.json', gate_projection)
        if evidence_projection is not None:
            once(folder / f'{ordinal:04d}-evidence-projection.json', evidence_projection)
        if ctx['slot'].get('evidence_first', False):
            once(folder / f'{ordinal:04d}-phase.json', {'phase': phase,
                'origin': 'model_generated_analysis' if phase == 'planner' else 'assistant_generation'})
        once(folder / f'{ordinal:04d}-request.json', body)
        if ctx['settings'].backend == 'fixture':
            if ordinal > len(ctx['script']):
                raise RuntimeError('scripted fixture exhausted; no provider fallback')
            response = ctx['script'][ordinal-1]; error = None
            if response.get('fixture_delay_seconds'):
                await asyncio.sleep(response['fixture_delay_seconds'])
        elif ctx['settings'].backend == 'polza':
            response = await asyncio.to_thread(ctx['polza_client'].generate, body, ctx['slot']['run_id'], ordinal)
            error = None
        else:
            ctx['budget'].reserve(body, ctx['slot']['run_id'])
            def send():
                request = urllib.request.Request(ctx['settings'].endpoint,
                    data=encode(body).encode(), headers={'Content-Type': 'application/json'})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
                try:
                    with opener.open(request, timeout=min(config.timeout, ctx['budget'].config.request_s)) as stream:
                        return read_response(stream.read()), None
                except urllib.error.HTTPError as error:
                    return {'http_status': error.code, 'body': error.read().decode(errors='replace')}, RuntimeError('provider HTTP ' + str(error.code))
            response, error = await asyncio.to_thread(send)
        once(folder / f'{ordinal:04d}-response.json', response)
        return error or parse_response(response, self.model_name), ModelCall(request=body, response=response, time=time.time()-started)


def read_response(payload):
    from .import_traces import strict_json
    return strict_json(payload.decode())


def worker(workspace, run_id):
    from inspect_ai import Task, eval as inspect_eval
    from inspect_ai.dataset import Sample
    from inspect_ai.log import read_eval_log
    from .experiment import Budget, trace_for, once, sha
    import inspect_ai._util.appdirs as appdirs
    import os
    import threading
    global CONTEXT
    workspace = Path(workspace).resolve(); budget = Budget(workspace); settings = budget.settings
    state = read(workspace / 'state.json'); approval = read(workspace / 'approval.json')
    if state['phase'] != 'running' or approval['decision'] != 'run' or sha(workspace / 'approval.json') != state['approval_sha256']:
        raise ValueError('worker requires an approved running parent execution')
    slot = next(s for s in read(workspace / 'slots.json') if s['run_id'] == run_id)
    folder = workspace / 'slots' / run_id
    if read(folder / 'started.json')['slot'] != slot or (folder / 'actor-checkpoint.json').exists() or (folder / 'result.json').exists():
        raise ValueError('worker cannot replay a started trajectory')
    parent = read(folder / 'started.json')['parent_pid']
    if os.getppid() != parent:
        raise ValueError('worker must belong to its owning execution parent')
    worker_deadline = min(budget.execution['deadline'], read(folder / 'started.json')['time'] + settings.limits.sample_s)
    def watch_parent():
        while True:
            if os.getppid() != parent or time.time() >= worker_deadline:
                atomic(folder / 'worker-stop.json', {'reason': 'owner exited or original deadline exhausted', 'time': time.time()})
                os._exit(75)
            time.sleep(.2)
    threading.Thread(target=watch_parent, daemon=True).start()
    case = next(c for c in read(workspace / 'inputs/cases') if c['id'] == slot['task_id'])
    CONTEXT = {'budget': budget, 'settings': settings, 'slot': slot, 'folder': folder, 'calls': 0, 'current_ticket': case['ticket_id'],
               'script': read(workspace / 'inputs/fixture_outputs')[run_id] if settings.backend == 'fixture' else None}
    if settings.backend == 'polza':
        from .polza_http import Client
        CONTEXT['polza_client'] = Client(workspace, settings, budget.execution['deadline'])
    appdirs.user_data_path = lambda package: workspace / 'runtime-data' / package
    appdirs.user_cache_path = lambda package: workspace / 'runtime-cache' / package
    model = get_model('ail_experiment/' + settings.model_id,
        config=GenerateConfig(temperature=settings.temperature, max_tokens=settings.limits.max_output_tokens,
            max_retries=0, timeout=settings.limits.request_s, attempt_timeout=settings.limits.request_s,
            max_connections=1, parallel_tool_calls=False, max_tool_output=200000), memoize=False)
    CONTEXT['model'] = model
    task = Task(dataset=[Sample(id=case['id'], input=case['user_request'])],
        solver=structured_actor((workspace / ('inputs/' + slot['arm'] + '_prompt')).read_text(),
            (workspace / 'inputs/common').read_text(), case, slot, folder, budget),
        epochs=1, name='controlled_routing', version=settings.agent_version_prefix + '-' + slot['arm'])
    logs = inspect_eval(task, model=model, score=False, display='none', log_dir=str(folder / 'inspect-logs'),
        log_format='json', max_samples=1, max_tasks=1, log_model_api=True, log_buffer=1,
        fail_on_error=False, message_limit=50, time_limit=settings.limits.sample_s, retry_on_error=0)
    log = read_eval_log(logs[0].location)
    if len(log.samples or []) != 1:
        raise ValueError('exactly one Inspect trajectory required')
    sample = log.samples[0]; checkpoint = read(folder / 'actor-checkpoint.json'); messages = []
    for ordinal, message in enumerate(sample.messages):
        item = {'step_id': f's{ordinal:03d}', 'role': message.role, 'content': message.text}
        if getattr(message, 'tool_calls', None):
            item['tool_calls'] = [{'id': c.id, 'name': c.function, 'arguments': c.arguments} for c in message.tool_calls]
        if getattr(message, 'tool_call_id', None): item['tool_call_id'] = message.tool_call_id
        if getattr(message, 'function', None): item['function'] = message.function
        messages.append(item)
    metadata = {k: checkpoint[k] for k in ['termination', 'assigned_to_observed', 'generation_attempts',
        'simulation_events', 'requested_fields', 'scaffold_tool_control', 'requested_tool_choices', 'measurement_status',
        'zero_call_recovery_limit', 'zero_call_recovery_used', 'control_events', 'recovery_declined_reason']}
    metadata.update({k: checkpoint[k] for k in ['evidence_first', 'planner_events', 'planner_attempts', 'actor_attempts']})
    metadata['planner_state_guard'] = checkpoint['planner_state_guard']
    metadata['actor_state'] = checkpoint['actor_state']
    metadata['action_gate'] = checkpoint['action_gate']
    metadata.update(duration_seconds=time.time()-checkpoint['started'], model_outputs_returned=len(checkpoint['calls']),
        inspect_log=str(Path(logs[0].location).relative_to(workspace)),
        simulation_semantics='typed field -> exact authored fact; no LLM user or human gold',
        stop_reasons=[c['choices'][0]['stop_reason'] for c in checkpoint['calls']])
    if sample.error or sample.limit:
        metadata.update(termination='framework_error_or_limit', measurement_status='unassessable')
    trace = trace_for(workspace, slot, messages, metadata)
    once(folder / 'result.json', {'slot': slot, 'trace': trace})
    return {'run_id': run_id, 'termination': metadata['termination']}
