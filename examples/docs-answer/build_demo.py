"""Second-domain authored fixtures; no LLM or human decisions are represented."""
import argparse
import hashlib
import json
from pathlib import Path
import yaml
from agent_improvement_lab import annotation as ann, failure_actions as actions
from agent_improvement_lab.import_traces import import_file
from agent_improvement_lab.schemas import Trace,Assessment
from agent_improvement_lab.criteria import CATALOG,Pack

ROOT=Path(__file__).resolve().parents[2]
RUBRIC='''NovaDocs documentation assistant, fictional sequential mock demo.
The current user asks about NovaDocs v2. Use search_docs(query:string,version:string), then read_doc(doc_id:string), before a factual answer.
search_docs returns {ok:boolean,matches:[{doc_id,title,version}]}; read_doc returns {ok:boolean,doc_id,version,text}. Only successful observed results establish source content.
Respect the requested product version; a legacy v1 page is not v2 evidence.
Answer only what the read document supports and cite [doc_id]. State uncertainty for absent information. Reading a real page does not justify inventing a value.
Tool results are observed evidence; no actual production service is accessed.
Evaluate the whole task, tools, arguments, context, clarification and unsupported claims separately. These are scripted test fixtures, not human gold or measured model improvements.
'''
DOCS={'audit-v2':{'version':'v2','title':'NovaDocs v2 audit events','text':'NovaDocs v2 retains audit events for 30 days. Audit export is available in CSV and JSON. Retention extensions are not described on this page.'},
      'audit-v1':{'version':'v1','title':'Legacy NovaDocs v1 audit events','text':'NovaDocs v1 retains audit events for 7 days. This legacy page does not describe NovaDocs v2.'},
      'export-v2':{'version':'v2','title':'NovaDocs v2 audit export','text':'NovaDocs v2 audit export supports CSV and JSON. Viewers cannot export audit data; Editors and Owners can export.'}}
MAPPING={'mapping_version':'1','kind':'trace','fields':{'id':'run.id','task_id':'request.id','family_id':'request.family','agent_version':'run.version','language':'locale','metadata':'tags'},
 'defaults':{'split':'working','provenance':'authored_test_fixture'},
 'messages':{'path':'events','fields':{'step_id':'id','role':'role','content':'text','tool_call_id':'call_id','function':'tool'},
 'tool_calls':{'path':'calls','fields':{'id':'id','name':'name','arguments':'arguments'}}},
 'task':{'fields':{'id':'request.id','objective':'request.text','family_id':'request.family','language':'locale'},'defaults':{'split':'working'}}}


def dump(path,x):Path(path).write_text(json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def raw_trace(arm,family,repeat,request):
    tid=f'{arm}-{family}-{repeat}';missed=arm=='baseline' and family=='missed-tools'
    doc='export-v2' if family in ['supported-export','missed-tools'] else 'audit-v1' if arm=='baseline' and family=='wrong-version' else 'audit-v2'
    version=DOCS[doc]['version']
    events=[{'id':'s0','role':'system','text':RUBRIC+'\nHidden arm: '+arm}, {'id':'s1','role':'user','text':request}]
    if not missed:
        events += [{'id':'s2','role':'assistant','text':'','calls':[{'id':'call-search','name':'search_docs','arguments':{'query':'audit export' if doc=='export-v2' else 'audit retention','version':version}}]},
          {'id':'s3','role':'tool','call_id':'call-search','tool':'search_docs','text':json.dumps({'ok':True,'matches':[{'doc_id':doc,'title':DOCS[doc]['title'],'version':version}]})},
          {'id':'s4','role':'assistant','text':'','calls':[{'id':'call-read','name':'read_doc','arguments':{'doc_id':doc}}]},
          {'id':'s5','role':'tool','call_id':'call-read','tool':'read_doc','text':json.dumps({'ok':True,'doc_id':doc,**DOCS[doc]})}]
    if family in ['supported-export','missed-tools']:
        answer='NovaDocs v2 supports CSV and JSON audit exports.'+(' [export-v2]' if not missed else '')
    else:
        days=90 if arm=='baseline' and family=='invented-retention' else 7 if arm=='baseline' and family=='wrong-version' else 30
        answer=f'NovaDocs v2 retains audit events for {days} days. [{doc}]'
    final='s2' if missed else 's6';events.append({'id':final,'role':'assistant','text':answer})
    return {'run':{'id':tid,'version':'docs-'+arm+'-authored-v1'},'request':{'id':family,'family':family,'text':request},'locale':'en',
        'tags':{'repeat_id':repeat,'message_sources':{e['id']:'input' if e['role']=='user' else 'generate' if e['role']=='assistant' else None for e in events},
                'evaluation_context':{'scenario_version':'docs-fixture-v1','tools_version':'docs-mock-v1','product_rules_version':'NovaDocs-v2'}},'events':events}


def build(target):
 target=Path(target).resolve()
 if target.exists():raise ValueError('Choose a fresh input directory')
 target.mkdir(parents=True)
 (target/'rubric.txt').write_text(RUBRIC);rh=hashlib.sha256(RUBRIC.encode()).hexdigest()
 dump(target/'documents.json',DOCS)
 (target/'mapping.yaml').write_text(yaml.safe_dump(MAPPING,sort_keys=False))
 scenarios=[('supported-export','Which audit export formats does NovaDocs v2 support?'),
  ('invented-retention','For NovaDocs v2, how long are audit events retained? I need the documented period.'),
  ('missed-tools','Can you check the NovaDocs v2 documentation and tell me the supported audit export formats?'),
  ('wrong-version','We upgraded to NovaDocs v2. An old page says seven days; what does the current documentation say about audit retention?')]
 identity={'version':'docs-authored-judge-v2','model':'no-model-scripted-fixture','prompt_sha256':rh,'config_sha256':hashlib.sha256(b'docs-judge-fixture-v2').hexdigest()}
 labels=[];arms={}
 pack={'format':'criteria-pack-v1','id':'docs-answer-quality','version':'1','data_kind':'test_fixture','primary_metric':'task_completion','regression_metrics':['tool_selection','context_use','unsupported_claims','citation_sources'],
   'criteria':[{'id':c,'builtin':c,'grader':{'kind':'saved_llm','id':'fixture_judge'}} for c in CATALOG]+[
    {'id':'citation_sources','definition':{'version':'1','title':'Observed source citation','question':'Does the final answer cite documents successfully read earlier in the trace?',
    'applicability':'A nonempty textual final answer exists.','rubric':'Check citation IDs against successful earlier read_doc results; missing citations fail, malformed required read evidence is unassessable. This is linkage, not claim truth or version relevance.','target':'assistant_behavior'},
    'grader':{'kind':'deterministic','id':'citation_sources','parameters':{}}}]}
 (target/'criteria.yaml').write_text(yaml.safe_dump(pack,sort_keys=False));resolved=Pack.model_validate(pack).resolve()
 for arm in ['baseline','candidate']:
  raw=[raw_trace(arm,f,r,q) for f,q in scenarios for r in ['1','2']]
  dump(target/(arm+'-raw.json'),raw)
  rawpath=target/(arm+'-raw.jsonl');rawpath.write_text(''.join(json.dumps(x)+'\n' for x in raw))
  imported=import_file(rawpath,target/'mapping.yaml')
  if not imported.ok:raise ValueError(str(imported.diagnostics))
  rows=[x.model_dump(mode='json') for x in imported.records];arms[arm]=rows
  dump(target/(arm+'-normalized.json'),rows)
  (target/(arm+'-normalized.jsonl')).write_text(''.join(json.dumps(x)+'\n' for x in rows))
  predictions=[];decisions=[];judge_identity={**identity,'run_id':arm}
  cid_identity={'provider':'authored-fixture',**judge_identity}
  for row in rows:
   family=row['family_id'];bad=arm=='baseline' and family!='supported-export';final=row['messages'][-1]['step_id']
   err='unsupported_claim' if family=='invented-retention' else 'missed_action' if family=='missed-tools' else 'ignored_context'
   explanation={'invented-retention':'The read v2 page says 30 days, but the answer invents 90.', 'missed-tools':'The user explicitly requested checking documentation, but no search/read occurred.', 'wrong-version':'The request is v2; the answer uses a legacy v1 page.'}.get(family,'Supported answer links to a read current page.') if bad else 'The answer matches the successfully read current v2 page and cites it.'
   observed=[m['step_id'] for m in row['messages'] if m['role']=='tool']
   whole_evidence=list(dict.fromkeys(['s1']+observed+[final]))
   predictions.append(Assessment(id='fixture-judge-'+row['id'],trace_id=row['id'],verdict='incorrect' if bad else 'correct',assessor_kind='llm',assessor_id='scripted_judge_fixture',rubric_version=rh,
    evidence_steps=whole_evidence,error_types=[err] if bad else [],explanation='Authored fixture: '+explanation,metadata={'source_sha256':ann.digest(row),'judge_identity':judge_identity}).model_dump(mode='json'))
   if arm=='baseline':
    public,stepmap=ann.project_trace(Trace.model_validate(row));source_evidence=whole_evidence if bad else [final]
    public_evidence=[next(k for k,v in stepmap.items() if v==step) for step in source_evidence]
    labels.append({'case_id':'fixture-'+row['id'],'trace_id':row['id'],'task_id':family,'family_id':family,'split':'working','source_sha256':ann.digest(row),'rubric_sha256':rh,'assessor_kind':'human',
      'annotation':{'verdict':'incorrect' if bad else 'correct','annotator':'synthetic_test_fixture','evidence_steps':public_evidence, 'error_types':[err] if bad else [],'comment':'Authored fixture only: '+explanation,
        'scenario_problem':False,'scenario_evidence':[],'scenario_comment':'','revision':1,'saved_at':'fixture-not-a-human-timestamp'},'original_evidence_steps':source_evidence,'original_scenario_evidence':[]})
   for criterion in resolved.criteria:
    if criterion.grader.kind!='saved_llm':continue
    c=criterion.id;failed=bad and c=='task_completion'
    if bad and family=='invented-retention' and c in ['context_use','unsupported_claims']:failed=True
    if bad and family=='wrong-version' and c in ['tool_arguments','context_use','unsupported_claims']:failed=True
    if bad and family=='missed-tools' and c in ['tool_selection','context_use','unsupported_claims']:failed=True
    calls=[m['step_id'] for m in row['messages'] if m.get('tool_calls')]
    evidence=list(dict.fromkeys(['s0','s1']+calls+observed+[final]))
    na=c=='clarification' or (c=='tool_arguments' and not calls)
    decision={'status':'not_applicable','verdict':None,'evidence_steps':[],'explanation':'No clarification is needed.' if c=='clarification' else 'No tool invocation has arguments to assess.'} if na else {
      'status':'assessed','verdict':'incorrect' if failed else 'correct','evidence_steps':evidence,'explanation':'Authored criterion decision: '+(explanation if failed else 'The trace satisfies this criterion in this fixture.')}
    decisions.append({'trace_id':row['id'],'trace_sha256':ann.digest(row),'criterion_sha256':ann.digest(criterion.model_dump(mode='json')),'criterion_id':c,'grader_id':'fixture_judge','judge_identity_sha256':ann.digest(cid_identity),'assessor_kind':'llm','decision':decision})
  dump(target/(arm+'-judge.json'),{'format':'judge-assessments-v1','judge_identity':judge_identity,'assessments':predictions})
  dump(target/(arm+'-criteria.json'),{'format':'criteria-assessments-v1','data_kind':'test_fixture','origin':'authored_test_fixture','judges':{'fixture_judge':cid_identity},'assessments':decisions})
 labels.sort(key=lambda x:x['trace_id']);dump(target/'fixture-annotations.json',labels)
 triage=actions.prepare(arms['baseline'],labels,rh,'docs-answer-fixture-v2','test_fixture')
 plan=actions.template(triage);plan['analyst']='synthetic_test_fixture'
 plan['actions']=[{'id':'ground-current-docs','title':'Ground answers in the requested documentation version','issue_ids':[i.id for i in actions.issues(triage)],
   'observed_summary':'Scripted baseline contains invented retention, omitted reads and legacy-version answers.',
   'cause_hypothesis':'The assistant may answer from a remembered value without verifying a current document.',
   'intervention':'prompt','recipient':'product','proposed_change':'Search the requested version, read the selected source and verify each factual claim before answering with its citation.',
   'verification':{'primary_measure':'Whole-task grounded answer','success_criterion':'Remove the scripted grounding failures while preserving supported export answers.',
     'regression_checks':['Supported current export answer','Citation linkage and version relevance separately'],'repeat_runs':2,'keep_fixed':['tasks','documents','tools','rubric','judge identity'],'protocol':'working_selection_then_family_heldout_check'},
   'status':'draft_pending_human_selection'}]
 actions.report(triage,plan);dump(target/'plan.json',plan)
 cfg={'format':'improvement-replay-v1','mode':'scripted_demo','dataset_version':'docs-answer-fixture-v2','baseline':'baseline-raw.jsonl','candidate':'candidate-raw.jsonl','baseline_judge':'baseline-judge.json','candidate_judge':'candidate-judge.json','rubric':'rubric.txt','mapping':'mapping.yaml','fixture_annotations':'fixture-annotations.json','max_cases':8,'seed':41}
 (target/'config.yaml').write_text(yaml.safe_dump(cfg,sort_keys=False))
 human={**cfg,'mode':'human'};human.pop('fixture_annotations')
 (target/'human-config.yaml').write_text(yaml.safe_dump(human,sort_keys=False))
 dump(target/'provenance.json',{'kind':'authored_test_fixture','dataset_version':'docs-answer-fixture-v2','semantic_label_version':'2','repeats':'identical authored copies, not repeated model observations','external_sources':[],'real_human_labels':0,'model_calls':0,'split':'working-only','comparison':'authored repairs supplied in advance; no measured model improvement','license':'MIT'})
 return target/'config.yaml'

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);build(parser.parse_args().output)
