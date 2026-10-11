"""Project an already recorded, validated experiment into an allowlisted visitor artifact."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

INPUTS=['run/baseline-traces.jsonl','run/candidate-traces.jsonl','run/baseline-criteria.json','run/candidate-criteria.json','run/comparison.json','run/result.json','run/uncertainty.json','run/manifest.json','inputs/action.json','inputs/evidence.json','inputs/evidence-traces.json','inputs/baseline.txt','inputs/candidate.txt','inputs/common.txt','inputs/cases.json','inputs/rule.json','PROMPT-DELTA.txt']
TEAMS={'payments','accounts','technical','general','operator'}
CALL_FIELDS={'get_ticket':{'ticket_id'},'route_ticket':{'ticket_id','team'},'handoff_to_operator':{'ticket_id'},'ask_user':{'ticket_id','field','question'}}
PAYLOAD_FIELDS={'get_ticket':{'status','ticket_id','ticket_status','history','mock'},'route_ticket':{'status','ticket_id','assigned_to','mock'},'handoff_to_operator':{'status','ticket_id','assigned_to','mock'},'ask_user':{'status','field','answer','mock'}}
CELL_FIELDS={'trace_id','criterion_id','trace_sha256','criterion_sha256','status','verdict','evidence_steps','explanation','error_code'}

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def fields(value,allowed):
    if not isinstance(value,dict) or set(value)!=set(allowed):raise ValueError('Public envelope differs from allowlist')
def scalar_leaves(value,nested=()):
    """Declared scalar fields cannot conceal another metadata object."""
    for key,leaf in value.items():
        if key in nested:continue
        values=leaf if isinstance(leaf,list) else [leaf]
        if any(type(v) not in (str,int,float,bool,type(None)) or isinstance(v,float) and not math.isfinite(v) for v in values):raise ValueError('Unexpected public scalar shape')

def project_trace(trace,arm,prefix=''):
    from agent_improvement_lab.annotation import digest as source_digest
    body={'id':prefix+trace.id,'source_id':trace.id,'source_sha256':source_digest(trace.model_dump(mode='json')),'arm':arm,'task_id':trace.task_id,'family_id':trace.family_id,'split':trace.split,'agent_version':trace.agent_version,'repeat_id':str(trace.metadata['repeat_id']),'messages':[]}
    for m in trace.messages:
        body['messages'].append({'step_id':m.step_id,'role':m.role,'content':m.content,'function':m.function,'tool_call_id':m.tool_call_id,'tool_calls':[{'id':c.id,'name':c.name,'arguments':c.arguments} for c in m.tool_calls]})
    return dict(body,projection_sha256=digest(body))

def from_saved(source):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from agent_improvement_lab import criteria
    from agent_improvement_lab.schemas import Trace
    source=Path(source)
    for name in INPUTS:
        f=source/name
        if not f.is_file() or f.is_symlink():raise ValueError('Unsafe experiment source')
    reports={arm:criteria.Report.model_validate(read(source/f'run/{arm}-criteria.json')) for arm in ('baseline','candidate')}
    for arm,report in reports.items():
        rows=[Trace.model_validate(json.loads(line)) for line in (source/f'run/{arm}-traces.jsonl').read_text().splitlines()]
        if [r.model_dump(mode='json') for r in rows]!=[r.model_dump(mode='json') for r in report.traces]:raise ValueError('Trace/report sources differ')
    comparison=criteria.compare(reports['baseline'],reports['candidate'])
    if comparison!=read(source/'run/comparison.json'):raise ValueError('Saved comparison is not reproduced')
    decision=read(source/'run/result.json');rule=read(source/'inputs/rule.json')
    if decision['rule']!=rule:raise ValueError('Decision rule changed')
    metrics=comparison['splits'][rule['evaluation_split']];selected=[rule['primary'],*rule['regressions']]
    if any(metrics[c]['jointly_assessable_pairs']!=rule['expected_pairs'] for c in selected):raise ValueError('Incomplete recorded comparison')
    verdict='do_not_adopt' if any(metrics[c]['regressed'] for c in selected) else 'advance_to_larger_test' if metrics[rule['primary']]['improved'] else 'no_observed_improvement'
    if decision['decision']!=verdict:raise ValueError('Decision does not follow saved rule')
    baseline=(source/'inputs/baseline.txt').read_text();candidate=(source/'inputs/candidate.txt').read_text();delta=(source/'PROMPT-DELTA.txt').read_text()
    if candidate!=baseline+delta:raise ValueError('Prompt delta is not exact append')
    indexes={arm:criteria.pair_index(r.traces) for arm,r in reports.items()}
    pairs=[]
    titles={'P01-denied-action-1':'Disabled download','P01-unknown-cause-1':'Archive export restriction','D01-invoice-1':'Duplicate invoice charge','D01-role-1':'Viewer needs Editor access'}
    for key in sorted(indexes['baseline']):
        left=indexes['baseline'][key];right=indexes['candidate'][key]
        pairs.append({'id':left.task_id+'-repeat-'+str(left.metadata['repeat_id']),'task_id':left.task_id,'family_id':left.family_id,'repeat_id':str(left.metadata['repeat_id']),'title':titles[left.task_id],'baseline':left.id,'candidate':right.id})
    prior=[Trace.model_validate(t) for t in read(source/'inputs/evidence-traces.json')]
    projected_prior=[project_trace(t,'prior','prior-') for t in prior];prior_by={t['source_id']:t for t in projected_prior}
    issues=read(source/'inputs/evidence.json')['issues'];evidence=[]
    for issue in issues:
        trace=prior_by[issue['trace_id']]
        if issue['trace_sha256']!=trace['source_sha256']:raise ValueError('Prior action evidence hash mismatch')
        evidence.append({k:issue[k] for k in ['id','trace_sha256','evidence_steps','observation','assessor_kind','semantic_note_kind']}|{'trace_id':trace['id']})
    action=read(source/'inputs/action.json');manifest=read(source/'run/manifest.json')
    definitions=[]
    for criterion in reports['baseline'].pack.criteria:
        definitions.append({'id':criterion.id,'definition':criterion.definition.model_dump(mode='json'),'grader':reports['baseline'].grader_signatures[criterion.id],'criterion_sha256':next(c.criterion_sha256 for c in reports['baseline'].results if c.criterion_id==criterion.id)})
    data={'format':'saved-experiment-web-v1','id':'delivery-D01-20261009','title':'Clarification delivery','provenance':{'kind':'saved_local_model_experiment','model_attestation':manifest['model_version_attestation'],'model_version_verified_by_runner':manifest['model_version_verified_by_runner'],'source_hashes':{name:sha(source/name) for name in INPUTS},'tools':'mock','user_facts':'authored controlled replies','original_model_requests':52,'new_model_calls':0,'human_gold':False,'llm_judge_used':False},'criteria':definitions,'traces':[project_trace(t,arm) for arm,r in reports.items() for t in r.traces],'results':[c.model_dump(mode='json') for r in reports.values() for c in r.results],'pairs':pairs,'metrics':{arm:criteria.summaries(r) for arm,r in reports.items()},'comparison':comparison,'decision':decision,'uncertainty':read(source/'run/uncertainty.json'),'action':{k:action[k] for k in ['id','title','observed_summary','cause_hypothesis','intervention','recipient','proposed_change','verification','status']},'prior_traces':projected_prior,'action_evidence':evidence,'prompt':{'baseline':baseline,'delta':delta,'candidate':candidate,'common':(source/'inputs/common.txt').read_text()},'limitations':['Four working families, two correlated repeats; no held-out outcomes or statistical gain.','These are actual saved model outputs in a closed mock-user environment. A standalone text question could receive a reply in ordinary chat.','Deterministic target/support/tool checks are narrow; no validated LLM judge or new human gold.','Prior source traces motivated the change; their IDs are namespaced and excluded from pilot metrics.']}
    return validate(data)

def validate(data):
    fields(data,{'format','id','title','provenance','criteria','traces','results','pairs','metrics','comparison','decision','uncertainty','action','prior_traces','action_evidence','prompt','limitations'})
    scalar_leaves(data,{'provenance','criteria','traces','results','pairs','metrics','comparison','decision','uncertainty','action','prior_traces','action_evidence','prompt'})
    if data['format']!='saved-experiment-web-v1':raise ValueError('Unknown experiment format')
    fields(data['provenance'],{'kind','model_attestation','model_version_verified_by_runner','source_hashes','tools','user_facts','original_model_requests','new_model_calls','human_gold','llm_judge_used'})
    p=data['provenance']
    scalar_leaves(p,{'source_hashes'});scalar_leaves(p['source_hashes'])
    if p['kind']!='saved_local_model_experiment' or p['new_model_calls']!=0 or p['human_gold'] or p['llm_judge_used'] or p['tools']!='mock':raise ValueError('Unexpected provenance')
    if set(p['source_hashes'])!=set(INPUTS):raise ValueError('Incomplete source binding')
    fields(data['action'],{'id','title','observed_summary','cause_hypothesis','intervention','recipient','proposed_change','verification','status'})
    fields(data['action']['verification'],{'primary_measure','success_criterion','regression_checks','repeat_runs','keep_fixed','protocol'})
    scalar_leaves(data['action'],{'verification'});scalar_leaves(data['action']['verification'])
    fields(data['prompt'],{'baseline','delta','candidate','common'})
    scalar_leaves(data['prompt'])
    fields(data['decision'],{'backend','decision','default_promoted','human_gold','planned_slots','recorded_slots','rule','statistical_effect_established'})
    fields(data['decision']['rule'],{'version','primary','regressions','expected_pairs','evaluation_split','scope'})
    scalar_leaves(data['decision'],{'rule'});scalar_leaves(data['decision']['rule'])
    if data['decision']['default_promoted'] or data['decision']['human_gold'] or data['decision']['statistical_effect_established']:raise ValueError('Unsupported promotion or truth claim')
    fields(data['comparison'],{'format','data_kind','left_sha256','right_sha256','limitations','splits','status'})
    scalar_leaves(data['comparison'],{'splits'})
    ids=set();traces={}
    for trace in [*data['traces'],*data['prior_traces']]:
        fields(trace,{'id','source_id','source_sha256','projection_sha256','arm','task_id','family_id','split','agent_version','repeat_id','messages'})
        scalar_leaves(trace,{'messages'})
        if trace['id'] in ids or trace['split']!='working':raise ValueError('Duplicate or foreign trace')
        ids.add(trace['id']);traces[trace['id']]=trace
        if digest({k:v for k,v in trace.items() if k!='projection_sha256'})!=trace['projection_sha256']:raise ValueError('Projected trace changed')
        steps=set();calls={}
        for m in trace['messages']:
            fields(m,{'step_id','role','content','function','tool_call_id','tool_calls'})
            scalar_leaves(m,{'tool_calls'})
            if m['step_id'] in steps or m['role'] not in {'system','user','assistant','tool'}:raise ValueError('Invalid message identity')
            steps.add(m['step_id'])
            for call in m['tool_calls']:
                fields(call,{'id','name','arguments'})
                scalar_leaves(call,{'arguments'})
                if call['id'] in calls or call['name'] not in CALL_FIELDS or set(call['arguments'])!=CALL_FIELDS[call['name']]:raise ValueError('Unknown public call')
                if not all(isinstance(v,str) for v in call['arguments'].values()):raise ValueError('Call argument type')
                calls[call['id']]=call
            if m['role']=='tool':
                call=calls.get(m['tool_call_id'])
                if not call or call['name']!=m['function']:raise ValueError('Unlinked tool result')
                payload=json.loads(m['content']);fields(payload,PAYLOAD_FIELDS[m['function']])
                scalar_leaves(payload)
                if payload['mock'] is not True:raise ValueError('Nonmock tool payload')
                if m['function']=='get_ticket' and payload['history']!=[]:raise ValueError('Unexpected ticket history')
    if len(data['traces'])!=16 or len(data['prior_traces'])!=2:raise ValueError('Complete pilot inventory required')
    criteria={}
    for c in data['criteria']:
        fields(c,{'id','definition','grader','criterion_sha256'});fields(c['definition'],{'version','title','question','applicability','rubric','target'})
        fields(c['grader'],{'id','kind','version','implementation_sha256'})
        scalar_leaves(c,{'definition','grader'});scalar_leaves(c['definition']);scalar_leaves(c['grader'])
        if c['grader']['kind']!='deterministic' or c['id'] in criteria:raise ValueError('Unexpected grader inventory')
        criteria[c['id']]=c
    metric_fields={'expected','assessed','not_applicable','missing','grader_error','correct','incorrect','unassessable','assessable','decision_coverage','correct_rate_among_assessable','unassessable_rate_among_assessed','tasks','families','tasks_with_assessable_results','families_with_assessable_results','equal_family_task_mean_correct_rate'}
    comparison_fields={'expected_pairs','jointly_assessable_pairs','excluded_pairs','exclusion_reasons','improved','regressed','unchanged','tasks_with_pairs','families_with_pairs','equal_family_task_mean_correct_delta'}
    uncertainty_fields={'interval_95','limitations','method','minimum_clusters_guard','paired_families','resamples','seed','status'}
    fields(data['metrics'],{'baseline','candidate'});fields(data['comparison']['splits'],{'working','heldout'});fields(data['uncertainty'],{'working','heldout'})
    for split in ['working','heldout']:
        fields(data['comparison']['splits'][split],criteria);fields(data['uncertainty'][split],criteria)
        for cid in criteria:
            fields(data['comparison']['splits'][split][cid],comparison_fields);fields(data['uncertainty'][split][cid],uncertainty_fields)
            scalar_leaves(data['comparison']['splits'][split][cid],{'exclusion_reasons'});scalar_leaves(data['uncertainty'][split][cid])
            u=data['uncertainty'][split][cid]
            if u['interval_95'] is not None or any(type(u[k]) is not int or u[k]<0 for k in ['minimum_clusters_guard','paired_families','resamples','seed']):raise ValueError('Unexpected uncertainty shape in this pilot')
            if data['comparison']['splits'][split][cid]['exclusion_reasons']!={}:raise ValueError('Unexpected exclusions in this complete pilot')
            for arm in ['baseline','candidate']:
                fields(data['metrics'][arm],{'working','heldout'});fields(data['metrics'][arm][split],criteria);fields(data['metrics'][arm][split][cid],metric_fields)
                row=data['metrics'][arm][split][cid];scalar_leaves(row)
                rates={'decision_coverage','correct_rate_among_assessable','unassessable_rate_among_assessed','equal_family_task_mean_correct_rate'}
                if any(type(v) not in (int,float,type(None)) or v is not None and (not math.isfinite(v) or v<0 or (k in rates and v>1)) for k,v in row.items()):raise ValueError('Invalid metric scalar')
                if any(type(v) is not int for k,v in row.items() if k not in rates):raise ValueError('Metric counts must be integers')
    current={t['id'] for t in data['traces']};seen=set()
    for c in data['results']:
        fields(c,CELL_FIELDS);key=(c['trace_id'],c['criterion_id'])
        scalar_leaves(c)
        if key in seen or c['trace_id'] not in current or c['criterion_id'] not in criteria:raise ValueError('Unknown or duplicate result')
        seen.add(key);t=traces[c['trace_id']]
        if c['trace_sha256']!=t['source_sha256'] or c['criterion_sha256']!=criteria[c['criterion_id']]['criterion_sha256']:raise ValueError('Result binding changed')
        if not set(c['evidence_steps'])<={m['step_id'] for m in t['messages']}:raise ValueError('Missing evidence step')
        if c['status']!='assessed' or c['verdict'] not in {'correct','incorrect','unassessable'} or not c['explanation'].strip():raise ValueError('Unexpected saved result')
        if c['verdict'] in {'correct','incorrect'} and not c['evidence_steps']:raise ValueError('Verdict without evidence')
    if len(seen)!=len(current)*len(criteria):raise ValueError('Incomplete result matrix')
    pair_ids=set();paired=set()
    for pair in data['pairs']:
        fields(pair,{'id','task_id','family_id','repeat_id','title','baseline','candidate'})
        scalar_leaves(pair)
        if pair['id'] in pair_ids:raise ValueError('Duplicate pair')
        pair_ids.add(pair['id'])
        for arm in ['baseline','candidate']:
            t=traces[pair[arm]]
            if t['arm']!=arm or any(t[k]!=pair[k] for k in ['task_id','family_id','repeat_id']):raise ValueError('Pair identity mismatch')
            if t['id'] in paired:raise ValueError('Repeated pair member')
            paired.add(t['id'])
    if paired!=current or len(pair_ids)!=8:raise ValueError('Incomplete pair inventory')
    cells={(c['trace_id'],c['criterion_id']):c for c in data['results']}
    for arm in ['baseline','candidate']:
        selected=[t for t in data['traces'] if t['arm']==arm]
        for cid in criteria:
            saved=data['metrics'][arm]['working'][cid];values=[cells[(t['id'],cid)] for t in selected]
            if saved['expected']!=len(selected) or saved['assessed']!=len(selected):raise ValueError('Metric coverage differs')
            for verdict in ['correct','incorrect','unassessable']:
                if saved[verdict]!=sum(c['verdict']==verdict for c in values):raise ValueError('Metric counts differ')
    for cid in criteria:
        deltas=[]
        for pair in data['pairs']:
            a=cells[(pair['baseline'],cid)];b=cells[(pair['candidate'],cid)]
            if a['verdict']!='unassessable' and b['verdict']!='unassessable':deltas.append(int(b['verdict']=='correct')-int(a['verdict']=='correct'))
        saved=data['comparison']['splits']['working'][cid]
        if any(saved[name]!=value for name,value in {'expected_pairs':8,'jointly_assessable_pairs':len(deltas),'improved':deltas.count(1),'regressed':deltas.count(-1),'unchanged':deltas.count(0)}.items()):raise ValueError('Pair comparison differs')
    rule=data['decision']['rule'];selected=[rule['primary'],*rule['regressions']]
    if any(data['comparison']['splits']['working'][c]['regressed'] for c in selected) and data['decision']['decision']!='do_not_adopt':raise ValueError('Saved decision contradicted')
    for issue in data['action_evidence']:
        fields(issue,{'id','trace_id','trace_sha256','evidence_steps','observation','assessor_kind','semantic_note_kind'})
        scalar_leaves(issue)
        t=traces[issue['trace_id']]
        if t['arm']!='prior' or t['source_sha256']!=issue['trace_sha256'] or not set(issue['evidence_steps'])<={m['step_id'] for m in t['messages']}:raise ValueError('Prior evidence changed')
    if data['prompt']['candidate']!=data['prompt']['baseline']+data['prompt']['delta']:raise ValueError('Prompt delta changed')
    text=json.dumps(data,ensure_ascii=False)
    if any(s in text for s in ['/Users/','/private/','localhost','127.0.0.1','annotations.sqlite','provider-calls','reasoning"']):raise ValueError('Private field or machine path in visitor data')
    return data

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output)
    if output.exists():parser.error('Choose a fresh output file')
    data=from_saved(args.source);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(data,sort_keys=True,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'traces':len(data['traces']),'results':len(data['results']),'pairs':len(data['pairs']),'decision':data['decision']['decision']}))
