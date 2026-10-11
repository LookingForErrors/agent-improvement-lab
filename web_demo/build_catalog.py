"""Curate a working-only scripted routing fixture; never import human decisions."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

VARIANTS = ['success','wrong_team','wrong_ticket','missed_tool','fabricated_result','tool_error_ignored']
TEAMS = ['payments','accounts','technical','general','operator']
STYLES = ['terse','angry','correction','topic_shift','overloaded']


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(path):
    with Path(path).open() as stream:
        for line in stream:
            if line.strip(): yield json.loads(line)


def action_evidence(case, trace):
    actions = [(m, call) for m in trace['messages'] for call in m.get('tool_calls', []) if call['function'] in ['route_ticket','handoff_to_operator']]
    if not actions: return None
    if len(actions) != 1: raise ValueError('Expected exactly one routing action')
    message, call = actions[0]
    results = [m for m in trace['messages'] if m.get('tool_call_id') == call['id']]
    result = results[0] if len(results) == 1 else None
    payload = json.loads(result['content']) if result else {}
    return {'ticket_id': call['arguments']['ticket_id'], 'destination': call['arguments'].get('team','operator'), 'status':payload.get('status'), 'call_step':message['step_id'], 'result_step':result['step_id'] if result else None}


def validate_catalog(data):
    if set(data) != {'format','dataset_version','provenance','population','rules','cases'}: raise ValueError('Unexpected catalog fields')
    if data.get('format') != 'routing-trace-catalog-v1' or data.get('provenance',{}).get('kind') != 'curated_scripted_synthetic_not_model_run': raise ValueError('Unexpected catalog provenance')
    if data['provenance'].get('model_calls') != 0 or data['provenance'].get('human_labels') != 0: raise ValueError('Only scripted synthetic catalog inputs are allowed')
    seen_cases, seen_traces, families = set(), set(), set()
    for case in data['cases']:
        if set(case) != {'id','source_task_id','family_id','split','title','style','language','ticket_id','active_request','opening_request','input_user_turns','clarifications','history_chars','history_records','fact_position','intended_destination','intent_origin','unsupported_claim','curation_edits','traces'}: raise ValueError('Unexpected scenario fields')
        if case['id'] in seen_cases or case['family_id'] in families: raise ValueError('Catalog must use one task per working family')
        seen_cases.add(case['id']); families.add(case['family_id'])
        if case['split'] != 'working' or case['intended_destination'] not in TEAMS or case['style'] not in STYLES: raise ValueError('Unsupported or heldout scenario')
        if case['intent_origin'] != 'authored_scenario_intent_not_human_gold': raise ValueError('Unexpected scenario reference origin')
        if not case['title'] or not case['ticket_id'] or not case['active_request']: raise ValueError('Incomplete scenario')
        if [t['variant'] for t in case['traces']] != VARIANTS: raise ValueError('Incomplete scripted variant inventory')
        for trace in case['traces']:
            if set(trace) != {'id','source_trace_id','task_id','variant','provenance','messages'}: raise ValueError('Unexpected trace fields')
            if trace['id'] in seen_traces: raise ValueError('Duplicate trace identity')
            seen_traces.add(trace['id'])
            if trace['task_id'] != case['id'] or trace['provenance'] != 'curated_scripted_synthetic_not_model_run': raise ValueError('Trace identity/provenance mismatch')
            steps, calls = set(), {}
            for message in trace['messages']:
                if set(message)-{'step_id','role','content','tool_calls','tool_call_id','function'}: raise ValueError('Unexpected message fields')
                if message['step_id'] in steps or message['role'] not in ['user','assistant','tool']: raise ValueError('Invalid sequential message')
                steps.add(message['step_id'])
                if not isinstance(message.get('content',''), str): raise ValueError('Textual trace content required')
                for call in message.get('tool_calls',[]):
                    if set(call) != {'id','function','arguments'} or set(call['arguments'])-{'ticket_id','team'}: raise ValueError('Unexpected tool fields')
                    if call['id'] in calls or call['function'] not in ['get_ticket','route_ticket','handoff_to_operator']: raise ValueError('Invalid tool call')
                    calls[call['id']] = call
                if message['role'] == 'tool':
                    if message.get('tool_call_id') not in calls or message.get('function') != calls[message['tool_call_id']]['function']: raise ValueError('Unlinked tool response')
                    payload=json.loads(message['content'])
                    if not isinstance(payload,dict): raise ValueError('Structured tool payload required')
                    if message['function']=='get_ticket':
                        if set(payload) != {'ticket_id','status','history'} or not isinstance(payload['history'],list) or not all(isinstance(payload[key],str) for key in ['ticket_id','status']): raise ValueError('Unexpected ticket fields')
                        for record in payload['history']:
                            if set(record) != {'record_id','kind','closed','text'} or not isinstance(record['closed'],bool) or not all(isinstance(record[key],str) for key in ['record_id','kind','text']): raise ValueError('Unexpected history record fields')
                    elif payload.get('status')=='ERROR':
                        if set(payload) != {'status','code','message'} or not all(isinstance(value,str) for value in payload.values()): raise ValueError('Unexpected tool error fields')
                    elif payload.get('status')=='OK':
                        if set(payload) != {'status','ticket_id','assigned_to'}: raise ValueError('Unexpected assignment fields')
                        call=calls[message['tool_call_id']]
                        if payload['ticket_id'] != call['arguments']['ticket_id'] or payload['assigned_to'] != call['arguments'].get('team','operator'): raise ValueError('Tool result conflicts with recorded call')
                    else: raise ValueError('Unexpected assignment status')
            if sum(m['role']=='user' for m in trace['messages']) != case['input_user_turns'] + case['clarifications']: raise ValueError('Clarification turn inventory mismatch')
            evidence = action_evidence(case, trace); variant=trace['variant']
            if variant == 'missed_tool':
                if evidence is not None: raise ValueError('Missing-action variant contains an action')
            else:
                if evidence is None: raise ValueError('Routing action missing')
                correct_ticket = evidence['ticket_id'] == case['ticket_id']
                correct_team = evidence['destination'] == case['intended_destination']
                if correct_ticket != (variant != 'wrong_ticket') or correct_team != (variant != 'wrong_team'): raise ValueError('Scripted action variant does not match its authored intent')
                if evidence['status'] != ('ERROR' if variant == 'tool_error_ignored' else 'OK'): raise ValueError('Tool status variant mismatch')
            if variant == 'fabricated_result' and not trace['messages'][-1]['content'].endswith(case['unsupported_claim']): raise ValueError('Unsupported-claim injection missing')
    expected = {'scenarios':len(seen_cases),'families':len(families),'traces':len(seen_traces)}
    if data['population'] != expected or len(seen_cases) != 30: raise ValueError('Catalog population mismatch')
    text=json.dumps(data, ensure_ascii=False)
    for forbidden in ['/Users/','C:\\Users\\','annotations.sqlite3','checkpoints.sqlite3','human_snapshot','annotation_data']:
        if forbidden in text: raise ValueError('Private or machine-specific catalog content')
    return data


def build(dataset, selection):
    dataset, selection_path = Path(dataset).resolve(), Path(selection).resolve()
    selected=json.loads(selection_path.read_text())
    if selected.get('format') != 'routing-catalog-selection-v1' or selected.get('scope') != 'working_only': raise ValueError('Expected a working-only curated selection')
    names=['tasks-working.jsonl','traces-working.jsonl','private/task-references.jsonl','private/trace-references.jsonl','rules.txt']
    manifest=json.loads((dataset/'manifest.json').read_text())
    bindings=manifest.get('files')
    if not isinstance(bindings,dict): raise ValueError('Missing source corpus bindings')
    for name in names:
        entry=bindings.get(name)
        expected=entry.get('sha256') if isinstance(entry,dict) else None
        if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('Missing or invalid source corpus SHA: '+name)
        if (dataset/name).is_symlink() or digest(dataset/name) != expected:
            raise ValueError('Changed source corpus: '+name)
    wanted=[t['id'] for t in selected['tasks']]
    if len(wanted) != len(set(wanted)) or set(selected['patches'])-set(wanted): raise ValueError('Duplicate selection or foreign phrasing patch')
    wanted_set=set(wanted)
    tasks={t['id']:t for t in rows(dataset/'tasks-working.jsonl') if t['id'] in wanted_set}
    intents={t['task_id']:t for t in rows(dataset/'private/task-references.jsonl') if t['task_id'] in wanted_set}
    references={t['trace_id']:t for t in rows(dataset/'private/trace-references.jsonl') if t['task_id'] in wanted_set}
    traces_by_task={id:{} for id in wanted}
    for trace in rows(dataset/'traces-working.jsonl'):
        if trace['task_id'] not in traces_by_task: continue
        ref=references[trace['id']]
        if ref['reference_status'] != 'generator_reference_not_human_gold' or trace['split'] != 'working' or trace['provenance'] != 'scripted_synthetic_not_model_run': raise ValueError('Unexpected trace source')
        if ref['variant'] in traces_by_task[trace['task_id']]: raise ValueError('Duplicate source variant')
        traces_by_task[trace['task_id']][ref['variant']]=trace
    cases=[]
    for item in selected['tasks']:
        source=tasks.get(item['id']); intent=intents.get(item['id'])
        if not source or source['split'] != 'working' or source['provenance'] != 'authored_synthetic' or intent['reference_status'] != 'generator_reference_not_human_gold': raise ValueError('Selection is not authored working data')
        patch=selected['patches'].get(source['id'], {})
        if set(patch)-{'user_request','simulated_replies'}: raise ValueError('Unsupported phrasing patch')
        request=patch.get('user_request',source['user_request']); replies=patch.get('simulated_replies',source['simulated_replies'])
        if len(replies)!=len(source['simulated_replies']): raise ValueError('Phrasing must preserve clarification count')
        prefix_users=[source['user_request']]+[text for role,text in source['preceding_dialogue'] if role=='user']
        source_users=prefix_users+source['simulated_replies']
        new_users=[request]+prefix_users[1:]+replies
        task_id='catalog-'+source['id']; trace_items=[]
        for variant in VARIANTS:
            original=traces_by_task[source['id']][variant]
            messages=copy.deepcopy(original['messages']); user_messages=[m for m in messages if m['role']=='user']
            if [m['content'] for m in user_messages] != source_users: raise ValueError('Unexpected source dialogue')
            for message,text in zip(user_messages,new_users): message['content']=text
            if variant=='fabricated_result':
                success=traces_by_task[source['id']]['success']['messages'][-1]['content']
                messages[-1]['content']=success+' '+selected['unsupported_claims'][intent['expected_team']]
            trace_items.append({'id':'catalog-'+original['id'],'source_trace_id':original['id'],'task_id':task_id,'variant':variant,'provenance':'curated_scripted_synthetic_not_model_run','messages':messages})
        ticket=source['mock_ticket']; history_chars=sum(len(record['text']) for record in ticket['history'])
        cases.append({'id':task_id,'source_task_id':source['id'],'family_id':source['family_id'],'split':'working','title':item['title'],'style':source['style'],'language':'en','ticket_id':source['ticket_id'],'active_request':new_users[len(prefix_users)-1],'opening_request':request,'input_user_turns':len(prefix_users),'clarifications':len(replies),'history_chars':history_chars,'history_records':len(ticket['history']),'fact_position':source['fact_position'],'intended_destination':intent['expected_team'],'intent_origin':'authored_scenario_intent_not_human_gold','unsupported_claim':selected['unsupported_claims'][intent['expected_team']],'curation_edits':list(patch)+['variant_specific_unsupported_claim'],'traces':trace_items})
    data={'format':'routing-trace-catalog-v1','dataset_version':selected['dataset_version'],'provenance':{'kind':'curated_scripted_synthetic_not_model_run','model_calls':0,'human_labels':0,'source_dataset':selected['source_dataset'],'split':'working_only','heldout_raw_included':False,'notes':selected['notes'],'source_hashes':{'tasks':digest(dataset/'tasks-working.jsonl'),'traces':digest(dataset/'traces-working.jsonl'),'authored_task_intents':digest(dataset/'private/task-references.jsonl'),'authored_variants':digest(dataset/'private/trace-references.jsonl'),'rules':digest(dataset/'rules.txt'),'selection':digest(selection_path)}},'population':{'scenarios':len(cases),'families':len({c['family_id'] for c in cases}),'traces':sum(len(c['traces']) for c in cases)},'rules':(dataset/'rules.txt').read_text(),'cases':cases}
    return validate_catalog(data)


def export(dataset, selection, output):
    output=Path(output).resolve()
    if output.exists(): raise ValueError('Choose a fresh catalog output directory')
    data=build(dataset,selection)
    output.mkdir(parents=True)
    path=output/'catalog-fixture.json'; path.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    record={'kind':'curated_scripted_synthetic_catalog','files':{path.name:digest(path)},'population':data['population'],'style_counts':dict(Counter(c['style'] for c in data['cases'])),'clarification_counts':dict(Counter(c['clarifications'] for c in data['cases'])),'destination_counts':dict(Counter(c['intended_destination'] for c in data['cases'])),'model_calls':0,'human_labels':0}
    (output/'CATALOG-MANIFEST.json').write_text(json.dumps(record,indent=2)+'\n')
    return record


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--dataset',required=True);parser.add_argument('--selection',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    try: print(json.dumps(export(args.dataset,args.selection,args.output),indent=2))
    except (ValueError,KeyError) as error: parser.exit(2,str(error)+'\n')
