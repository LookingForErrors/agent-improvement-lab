import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent_improvement_lab import routing_measurement as rm
from agent_improvement_lab.schemas import Trace


REF = {'ticket_id': 'T1', 'expected_team': 'accounts', 'initial_facts': {},
       'propositions': {'action_permission': 'The user is allowed to edit the current project.',
                        'financial_transaction': 'A relevant payment completed.'},
       'answers': {'action_permission': {'answer': 'I may not edit this project.', 'state': 'known_negative'},
                   'financial_transaction': {'answer': 'I cannot determine the payment status.', 'state': 'unknown'}},
       'routing_paths': [{'destination': 'accounts', 'requires': {'action_permission': 'known_negative'}}]}


def trace(events=(), termination='final_after_assignment'):
    messages = [{'step_id': 's000', 'role': 'user', 'content': 'Please route this edit restriction.'}]
    for name, arguments, value in events:
        cid = 'c' + str(len(messages)); messages.append({'step_id': 's' + str(len(messages)), 'role': 'assistant',
            'content': '', 'tool_calls': [{'id': cid, 'name': name, 'arguments': {'ticket_id': 'T1', **arguments}}]})
        if value is not None:
            messages.append({'step_id': 's' + str(len(messages)), 'role': 'tool', 'tool_call_id': cid,
                             'function': name, 'content': json.dumps(value)})
    return Trace(id='r1', task_id='case1', messages=messages, metadata={'termination': termination})


READ = ('get_ticket', {}, {'status': 'OK', 'ticket_id': 'T1'})
ASK = ('ask_user', {'field': 'action_permission', 'question': 'Are you permitted to edit?'},
       {'status': 'OK', 'field': 'action_permission', 'answer': REF['answers']['action_permission']['answer']})
ROUTE = ('route_ticket', {'team': 'accounts'}, {'status': 'OK', 'ticket_id': 'T1', 'assigned_to': 'accounts'})
HANDOFF = ('handoff_to_operator', {}, {'status': 'OK', 'ticket_id': 'T1', 'assigned_to': 'operator'})


class RoutingMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.parameters = {'cases': {'case1': copy.deepcopy(REF)}}

    def test_acknowledged_partial_action_survives_without_whole_trace_pass(self):
        t = trace([READ, ASK, ROUTE], 'infrastructure_stop')
        self.assertEqual(rm.observed_action(t, self.parameters).verdict, 'correct')
        self.assertEqual(rm.destination(t, self.parameters).verdict, 'correct')
        self.assertEqual(rm.support(t, self.parameters).verdict, 'correct')
        self.assertEqual(rm.protocol(t, self.parameters).verdict, 'unassessable')
        self.assertEqual(rm.confirmation(t, self.parameters).verdict, 'unassessable')
        self.assertEqual(rm.product_success(t, self.parameters).verdict, 'unassessable')

    def test_pending_call_and_tool_failure_are_not_agent_violation(self):
        for event in [('ask_user', ASK[1], None), ('ask_user', ASK[1], {'status': 'ERROR', 'code': 'TOOL_UNAVAILABLE'})]:
            t = trace([READ, event], 'infrastructure_stop')
            self.assertEqual(rm.protocol(t, self.parameters).verdict, 'unassessable')
            self.assertEqual(rm.observed_action(t, self.parameters).verdict, 'unassessable')
        self.assertEqual(rm.observed_action(trace([READ]), self.parameters).verdict, 'incorrect')

    def test_wrong_ticket_and_acknowledgment_mismatch_cannot_succeed(self):
        for arguments, value, expected_protocol in [({'ticket_id': 'OTHER', 'team': 'accounts'}, ROUTE[2], 'incorrect'),
                                (ROUTE[1], {**ROUTE[2], 'ticket_id': 'OTHER'}, 'unassessable'),
                                (ROUTE[1], {**ROUTE[2], 'assigned_to': 'payments'}, 'unassessable')]:
            t = trace([READ, ASK, ('route_ticket', arguments, value)], 'infrastructure_stop')
            self.assertEqual(rm.observed_action(t, self.parameters).verdict, 'incorrect')
            self.assertEqual(rm.protocol(t, self.parameters).verdict, expected_protocol)

    def test_valid_decision_with_tool_failure_is_not_bad_assistant_support(self):
        failed = ('route_ticket', ROUTE[1], {'status': 'ERROR', 'code': 'TOOL_UNAVAILABLE'})
        t = trace([READ, ASK, failed])
        self.assertEqual(rm.support(t, self.parameters).verdict, 'correct')
        self.assertEqual(rm.protocol(t, self.parameters).verdict, 'unassessable')
        self.assertEqual(rm.observed_action(t, self.parameters).verdict, 'incorrect')
        self.assertEqual(rm.product_success(t, self.parameters).verdict, 'incorrect')

    def test_valid_read_request_with_inconsistent_ack_is_not_an_omitted_read(self):
        t = trace([('get_ticket', {}, {'status': 'OK', 'ticket_id': 'OTHER'})])
        self.assertEqual(rm.protocol(t, self.parameters).verdict, 'unassessable')
        self.assertEqual(rm.protocol(trace(), self.parameters).verdict, 'incorrect')

    def test_unknown_is_not_established_fact_and_can_support_budgeted_fallback(self):
        ref = self.parameters['cases']['case1']; ref['expected_team'] = 'operator'
        ref['answers']['action_permission'] = {'answer': 'I cannot check my permissions.', 'state': 'unknown'}
        first = ('ask_user', ASK[1], {'status': 'OK', 'field': 'action_permission', 'answer': 'I cannot check my permissions.'})
        second = ('ask_user', {'field': 'financial_transaction', 'question': 'Is there a completed payment?'},
                  {'status': 'OK', 'field': 'financial_transaction', 'answer': ref['answers']['financial_transaction']['answer']})
        self.assertEqual(rm.support(trace([READ, first, ROUTE]), self.parameters).verdict, 'incorrect')
        self.assertEqual(rm.support(trace([READ, first, second, HANDOFF]), self.parameters).verdict, 'correct')
        self.assertEqual(rm.support(trace([READ, first, HANDOFF]), self.parameters).verdict, 'incorrect')

    def test_known_bad_prefix_not_hidden_by_timeout_or_later_good_route(self):
        wrong = ('route_ticket', {'team': 'payments'}, {'status': 'OK', 'ticket_id': 'T1', 'assigned_to': 'payments'})
        t = trace([READ, ASK, wrong, ROUTE], 'infrastructure_stop')
        self.assertEqual(rm.destination(t, self.parameters).verdict, 'correct')
        self.assertEqual(rm.support(t, self.parameters).verdict, 'incorrect')
        self.assertEqual(rm.product_success(t, self.parameters).verdict, 'incorrect')

    def test_unknown_unmapped_answer_does_not_become_false_or_true(self):
        ask = ('ask_user', ASK[1], {**ASK[2], 'answer': 'An unexpected untyped reply.'})
        self.assertEqual(rm.support(trace([READ, ask, ROUTE]), self.parameters).verdict, 'unassessable')

    def test_initial_known_fact_survives_a_consistent_redundant_check(self):
        self.parameters['cases']['case1']['initial_facts'] = {'action_permission': 'known_negative'}
        self.assertEqual(rm.support(trace([READ, ASK, ROUTE]), self.parameters).verdict, 'correct')

    def test_every_question_consumes_budget_even_duplicate_field(self):
        t = trace([READ, ASK, ASK, ASK, ROUTE], 'infrastructure_stop')
        self.assertEqual(rm.protocol(t, self.parameters).verdict, 'incorrect')
        from agent_improvement_lab.experiment_inspect import FactUser
        user = FactUser({'ticket_id': 'T1', 'facts': {'a': 'Unknown'}, 'fact_kinds': {'a': 'action_permission'}})
        self.assertEqual([user.answer('T1', 'action_permission', 'Question?')['status'] for _ in range(3)], ['OK', 'OK', 'ERROR'])

    def test_response_presence_does_not_semantically_validate_an_intention(self):
        t = trace([READ, ASK, ROUTE]); t.messages[1].content = 'I will route this.'
        self.assertEqual(rm.confirmation(t, self.parameters).verdict, 'incorrect')

    def test_primary_needs_bound_semantic_review_and_rejects_changed_trace(self):
        t = trace([READ, ASK, ROUTE])
        from agent_improvement_lab.schemas import Message
        t.messages.append(Message(step_id='final', role='assistant', content='Your ticket was sent to Accounts.'))
        self.assertEqual(rm.product_success(t, self.parameters).verdict, 'unassessable')
        fingerprint = hashlib.sha256(json.dumps(t.model_dump(mode='json'), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self.parameters['semantic_decisions'] = {'r1': {'trace_sha256': fingerprint, 'rubric_version': rm.VERSION,
            'semantic_contract_sha256': rm.semantic_identity(t, self.parameters),
            'assessor_kind': 'llm', 'assessor_id': 'synthetic-test', 'decision': {'verdict': 'correct', 'evidence_steps': ['s000'], 'explanation': 'Fixture semantic label.'}}}
        self.assertEqual(rm.product_success(t, self.parameters).verdict, 'correct')
        self.parameters['cases']['case1']['answers']['financial_transaction']['answer'] += ' Changed reference'
        with self.assertRaisesRegex(ValueError, 'not bound'): rm.product_success(t, self.parameters)
        self.parameters['cases']['case1']['answers']['financial_transaction']['answer'] = REF['answers']['financial_transaction']['answer']
        t.messages[0].content += ' Changed'
        with self.assertRaisesRegex(ValueError, 'not bound'): rm.product_success(t, self.parameters)

    def test_withheld_bad_plan_is_a_diagnostic_not_an_automatic_product_failure(self):
        from agent_improvement_lab.schemas import Message
        from agent_improvement_lab.criteria import digest
        ref = self.parameters['cases']['case1']; ref['expected_team'] = 'operator'
        ref['answers']['action_permission'] = {'answer': 'Unknown permission.', 'state': 'unknown'}
        first = ('ask_user', ASK[1], {'status': 'OK', 'field': 'action_permission', 'answer': 'Unknown permission.'})
        second = ('ask_user', {'field': 'financial_transaction', 'question': 'What does the receipt say?'},
                  {'status': 'OK', 'field': 'financial_transaction', 'answer': ref['answers']['financial_transaction']['answer']})
        t = trace([READ, first, second, HANDOFF])
        t.messages.append(Message(step_id='final', role='assistant', content='Your ticket was sent to an operator.'))
        t.metadata['planner_events'] = [{'status': 'rejected_state', 'error_code': 'CLARIFICATION_BUDGET_EXHAUSTED'}]
        t.metadata['semantic_external_records'] = []
        for verdict in ('correct', 'incorrect'):
            self.parameters['semantic_decisions'] = {'r1': {'trace_sha256': digest(t.model_dump(mode='json')),
                'rubric_version': rm.VERSION, 'semantic_contract_sha256': rm.semantic_identity(t, self.parameters),
                'assessor_kind': 'llm', 'assessor_id': 'synthetic-test',
                'decision': {'verdict': verdict, 'evidence_steps': ['s000'], 'explanation': 'Prescribed fixture semantic decision.'}}}
            self.assertEqual(rm.product_success(t, self.parameters).verdict, verdict)

    def test_native_projection_preserves_ids_links_and_rejects_unsupported_blocks(self):
        native = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Question'}]},
                  {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'function': 'get_ticket', 'arguments': {'ticket_id': 'T1'}}]},
                  {'role': 'tool', 'content': '{}', 'tool_call_id': 'c1', 'function': 'get_ticket'}]
        t = Trace(id='t', messages=rm.project_messages(native))
        self.assertEqual([m.step_id for m in t.messages], ['s000', 's001', 's002'])
        self.assertEqual(t.messages[2].tool_call_id, 'c1')
        native[0]['content'].append({'type': 'image', 'data': 'not text'})
        with self.assertRaisesRegex(ValueError, 'unsupported'): rm.project_messages(native)

    def test_future_answer_and_read_do_not_authorize_past_action(self):
        from agent_improvement_lab.schemas import Message
        t = trace([READ, ('ask_user', ASK[1], None), ROUTE])
        pending_id = t.messages[3].tool_calls[0].id
        t.messages.append(Message(step_id='delayed', role='tool', tool_call_id=pending_id, function='ask_user', content=json.dumps(ASK[2])))
        self.assertEqual(rm.support(t, self.parameters).verdict, 'unassessable')
        t = trace([('get_ticket', {}, None), ROUTE]); cid = t.messages[1].tool_calls[0].id
        t.messages.append(Message(step_id='delayed', role='tool', tool_call_id=cid, function='get_ticket', content=json.dumps(READ[2])))
        self.assertEqual(rm.protocol(t, self.parameters).verdict, 'incorrect')

    def test_failed_second_reply_is_measurement_uncertainty_for_handoff(self):
        ref = self.parameters['cases']['case1']; ref['answers']['action_permission'] = {'answer': 'Unknown', 'state': 'unknown'}
        first = ('ask_user', ASK[1], {**ASK[2], 'answer': 'Unknown'})
        second = ('ask_user', {'field': 'financial_transaction', 'question': 'Any receipt?'}, {'status': 'ERROR', 'code': 'TOOL_UNAVAILABLE'})
        t = trace([READ, first, second, HANDOFF], 'infrastructure_stop')
        self.assertEqual(rm.support(t, self.parameters).verdict, 'unassessable')
        self.assertEqual(rm.product_success(t, self.parameters).verdict, 'unassessable')


if __name__ == '__main__':
    unittest.main()
