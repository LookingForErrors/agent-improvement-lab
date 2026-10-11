"""Synthetic workflow/evidence checks; no real annotation workspace or inference."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from agent_improvement_lab.annotation import digest, project_trace
from agent_improvement_lab.schemas import Trace
from agent_improvement_lab.failure_actions import prepare, issues, template, report, markdown, ticket, Triage

RULES='Routing fixture rules: receipts belong to payments. Tools OK reports routing only.'
RUBRIC=hashlib.sha256(RULES.encode()).hexdigest()


def fixture():
    traces=[]; snapshots=[]
    declarations=[('wrong-r1','task-wrong','family-wrong','working','incorrect'),
                  ('wrong-r2','task-wrong','family-wrong','working','incorrect'),
                  ('scenario','task-scenario','family-scenario','working','correct'),
                  ('gap','task-gap','family-gap','working','unassessable'),
                  ('unlabeled','task-unlabeled','family-unlabeled','working',None),
                  ('HELDOUT_SECRET_TRACE','HELDOUT_SECRET_TASK','HELDOUT_SECRET_FAMILY','heldout','incorrect')]
    for tid,task,family,split,verdict in declarations:
        team='payments' if verdict=='correct' else 'accounts'
        messages=[{'step_id':'s0','role':'system','content':'Current ticket: TICKET-1\n'+RULES},
                  {'step_id':'s1','role':'user','content':'I need the receipt for the last payment.'},
                  {'step_id':'s2','role':'assistant','content':'','tool_calls':[{'id':'c1','name':'route_ticket','arguments':{'ticket_id':'TICKET-1','team':team}}]},
                  {'step_id':'s3','role':'tool','content':json.dumps({'status':'OK','assigned_to':team}),'tool_call_id':'c1','function':'route_ticket'},
                  {'step_id':'s4','role':'assistant','content':'I routed your request to '+team+'.'}]
        if tid=='gap':
            messages=messages[:3];messages[2]['tool_calls'][0]['name']='get_ticket';messages[2]['tool_calls'][0]['arguments']={'ticket_id':'TICKET-1'}
        if split=='heldout':messages[1]['content']='HELDOUT_SECRET_REQUEST'
        trace=Trace.model_validate({'id':tid,'task_id':task,'family_id':family,'split':split,'agent_version':'fixture-agent-v1',
                 'messages':messages,'metadata':{'message_sources':{m['step_id']:'input' if m['role']=='user' else 'generate' for m in messages}}})
        traces.append(trace.model_dump(mode='json'))
        if verdict is None:continue
        public,step_map=project_trace(trace)
        comment='Route tool sent the current receipt request to accounts, whereas the fixture rules require payments.' if verdict=='incorrect' else 'Routes the receipt request to payments.'
        if verdict=='unassessable':comment='The recorded get_ticket call has no result; completion cannot be assessed.'
        if split=='heldout':comment='HELDOUT_SECRET_LABEL'
        evidence=['m02']
        if tid.startswith('wrong'):evidence.append('m03')
        label={'verdict':verdict,'annotator':'fixture-reference-not-real-human','evidence_steps':evidence,
               'error_types':['wrong_route'] if verdict=='incorrect' else [],'comment':comment,
               'scenario_problem':tid=='scenario','scenario_evidence':['m01'] if tid=='scenario' else [],
               'scenario_comment':'The authored request accidentally omits which payment needs a receipt.' if tid=='scenario' else '',
               'revision':1,'saved_at':'2026-10-02T10:00:00Z'}
        snapshots.append({'case_id':'fixture-'+tid,'trace_id':tid,'task_id':task,'family_id':family,'split':split,
                          'source_sha256':digest(trace.model_dump(mode='json')),'rubric_sha256':RUBRIC,'assessor_kind':'human',
                          'annotation':label,'original_evidence_steps':[step_map[s] for s in evidence],
                          'original_scenario_evidence':[step_map[s] for s in label['scenario_evidence']]})
    return traces,snapshots


def curated(triage):
    selected=issues(triage);groups={kind:[i.id for i in selected if i.kind==kind] for kind in ['assistant_failure','scenario_defect','evaluation_gap']}
    common={'primary_measure':'Task-level human verdict under fixed fixture rules.',
            'success_criterion':'The selected defect no longer occurs on independently reserved families; report uncertainty.',
            'regression_checks':['Preserve correct routing in accounts/technical/general scenarios.','Keep two-clarification limit and operator fallback.'],
            'repeat_runs':2,'keep_fixed':['dataset version and family split','routing rubric','mock tools','model/runtime except chosen intervention'],
            'protocol':'working_selection_then_family_heldout_check'}
    actions=[{'id':'receipt-routing','title':'Check a prompt rule for receipt routing','issue_ids':groups['assistant_failure'],
              'observed_summary':'Two repeats routed the same receipt task to accounts instead of payments.',
              'cause_hypothesis':None,
              'intervention':'prompt','recipient':'product','proposed_change':'Trial one explicit instruction: classify the current requested document/action before selecting the routing team.',
              'verification':common},
             {'id':'scenario-payment-id','title':'Fix the authored receipt scenario','issue_ids':groups['scenario_defect'],
              'observed_summary':'The human-shaped fixture label flags a missing payment identifier in authored context.',
              'cause_hypothesis':None,'intervention':'scenario_data','recipient':'evaluation',
              'proposed_change':'Review and revise the working scenario to state the intended transaction, keeping ambiguous cases separately.',
              'verification':{'primary_measure':'Review of the newly versioned working scenario and preserved annotation/source bindings.',
                              'success_criterion':'A reviewer confirms the intended transaction is explicit or deliberately ambiguous; original labels remain bound to their original source version.',
                              'regression_checks':['Keep the old scenario version and its human labels intact.','Preserve family splits; do not inspect heldout content for scenario edits.'],
                              'repeat_runs':None,'keep_fixed':['original source and annotation revisions kept separately','family split assignment'],
                              'protocol':'scenario_revision_review'}},
             {'id':'inspect-missing-result','title':'Investigate the missing tool result','issue_ids':groups['evaluation_gap'],
              'observed_summary':'The trace stops at get_ticket, so the fixture reference cannot assess completion.',
              'cause_hypothesis':None,'intervention':'investigation','recipient':'backend',
              'proposed_change':'Inspect recording and termination artifacts to distinguish truncation, runner stop and actual tool failure before proposing a fix.',
              'verification':{'primary_measure':'Call, recording and termination evidence linked to the incomplete trace.',
                              'success_criterion':'Document a supported diagnosis or an explicit unresolved gap, distinguishing recording truncation, runner stop and tool failure; do not claim a fix.',
                              'regression_checks':['Preserve the original trace and label.','Do not convert missing recording evidence into assistant blame.'],
                              'repeat_runs':None,'keep_fixed':['original trace hash','original human annotation revision'],
                              'protocol':'evidence_investigation'}}]
    return {'format':'failure-action-plan-v1','triage_sha256':digest(triage.model_dump(mode='json')),
            'analyst':'fixture-analyst-no-real-review','actions':actions}


class FailureActionsChecks(unittest.TestCase):
    def setUp(self):
        self.traces,self.labels=fixture();self.triage=prepare(self.traces,self.labels,RUBRIC,'fixture-v1','test_fixture');self.plan=curated(self.triage)
    def test_correct_coverage_counts_and_separate_issue_types(self):
        c=self.triage.coverage['working'].model_dump()
        self.assertEqual((c['traces'],c['tasks'],c['families'],c['repeat_traces_beyond_tasks'],c['annotated'],c['unannotated']),(5,4,4,1,4,1))
        self.assertEqual((c['incorrect'],c['correct'],c['unassessable'],c['scenario_flags']),(2,1,1,1))
        self.assertEqual(sorted(i.kind for i in issues(self.triage)),['assistant_failure']*2+['evaluation_gap','scenario_defect'])
    def test_exact_source_label_and_evidence_preservation(self):
        r=report(self.triage,self.plan);a=next(a for a in r['actions'] if a['id']=='receipt-routing')
        self.assertEqual(a['support_counts'],{'traces':2,'tasks':1,'families':1})
        self.assertEqual(a['status'],'draft_pending_human_selection');self.assertEqual(a['cause_status'],'unknown')
        source=next(s for s in self.labels if s['trace_id']=='wrong-r1');link=next(e for e in a['evidence'] if e['issue']['trace_id']=='wrong-r1')
        self.assertEqual(link['human_snapshot'],source);self.assertEqual([m['step_id'] for m in link['evidence']],['s2','s3'])
        self.assertEqual(link['evidence'][0]['tool_calls'][0]['arguments']['team'],'accounts')
        self.assertIn('revision 1',ticket(a,'test_fixture'))
    def test_unknown_cause_is_valid_and_not_invented(self):
        p=copy.deepcopy(self.plan);p['actions'][0]['cause_hypothesis']=None
        r=report(self.triage,p);self.assertEqual(r['actions'][0]['cause_status'],'unknown')
        self.assertIn('Unknown; investigation is allowed',ticket(r['actions'][0],'test_fixture'))
    def test_heldout_details_omitted_everywhere_and_cannot_route(self):
        r=report(self.triage,self.plan)
        outputs=[self.triage.model_dump_json(),json.dumps(template(self.triage)),json.dumps(r),markdown(r),*[ticket(a,'test_fixture') for a in r['actions']]]
        for value in outputs:self.assertNotIn('HELDOUT_SECRET',value)
        p=copy.deepcopy(self.plan);p['actions'][0]['issue_ids']=['issue-HELDOUT_SECRET_TRACE']
        with self.assertRaises(ValueError):report(self.triage,p)
        self.assertEqual(r['coverage']['heldout']['traces'],1)
    def test_mismatch_duplicate_and_invalid_revisions_fail_closed(self):
        mutations=[lambda rows:rows[0].update(source_sha256='1'*64),lambda rows:rows[0].update(rubric_sha256='2'*64),
                   lambda rows:rows[0].update(task_id='wrong'),lambda rows:rows.append(copy.deepcopy(rows[0])),
                   lambda rows:rows[0]['annotation'].update(revision=0),lambda rows:rows[0].update(assessor_kind='generator'),
                   lambda rows:rows[0].update(original_evidence_steps=['s4']),lambda rows:rows[2].update(original_scenario_evidence=['s9'])]
        for mutate in mutations:
            labels=copy.deepcopy(self.labels);mutate(labels)
            with self.assertRaises(ValueError):prepare(self.traces,labels,RUBRIC,'fixture-v1','test_fixture')
    def test_authored_context_and_tool_only_cannot_blame_agent(self):
        for public_steps,original_steps in [(['m01'],['s1']),(['m03'],['s3'])]:
            labels=copy.deepcopy(self.labels);labels[0]['annotation']['evidence_steps']=public_steps;labels[0]['original_evidence_steps']=original_steps
            with self.assertRaises(ValueError):prepare(self.traces,labels,RUBRIC,'fixture-v1','test_fixture')
    def test_changes_to_snapshot_invalidate_curated_plan(self):
        labels=copy.deepcopy(self.labels);labels[0]['annotation']['revision']=2
        changed=prepare(self.traces,labels,RUBRIC,'fixture-v1','test_fixture')
        with self.assertRaises(ValueError):report(changed,self.plan)
        raw=self.triage.model_dump(mode='json');raw['working_cases'][0]['trace']['messages'][1]['content']='tampered'
        with self.assertRaises(ValueError):Triage.model_validate(raw)
    def test_scenario_and_gap_not_converted_to_assistant_training(self):
        for index in [1,2]:
            p=copy.deepcopy(self.plan);p['actions'][index].update(intervention='post_training',recipient='ml')
            with self.assertRaises(ValueError):report(self.triage,p)
        p=copy.deepcopy(self.plan);p['actions'][0]['issue_ids']+=p['actions'][1]['issue_ids']
        with self.assertRaises(ValueError):report(self.triage,p)
    def test_verification_scope_and_speculative_hypothesis(self):
        for index in [1,2]:
            p=copy.deepcopy(self.plan);p['actions'][index]['verification']=copy.deepcopy(p['actions'][0]['verification'])
            with self.assertRaises(ValueError):report(self.triage,p)
        p=copy.deepcopy(self.plan);p['actions'][0]['cause_hypothesis']='The rule may not have controlled team selection; mechanism remains unknown.'
        r=report(self.triage,p);self.assertEqual(r['actions'][0]['cause_status'],'unverified_hypothesis')
        self.assertIn('Unverified cause hypothesis',ticket(r['actions'][0],'test_fixture'))

    def test_no_execution_fields_or_invalid_draft_filenames(self):
        for change in [{'status':'approved'},{'measured_result':'improved'},{'execute':True},{'id':'../escape'}, {'proposed_change':'TODO'}]:
            p=copy.deepcopy(self.plan);p['actions'][0].update(change)
            with self.assertRaises(ValueError):report(self.triage,p)
        with self.assertRaises(ValueError):report(self.triage,template(self.triage))
    def test_unassigned_findings_and_unannotated_not_silently_passed(self):
        p=copy.deepcopy(self.plan);p['actions']=[];r=report(self.triage,p)
        self.assertEqual(len(r['unassigned_issue_ids']),4);self.assertEqual(len(r['label_inventory']),4)
        self.assertEqual(r['coverage']['working']['unannotated'],1)
        empty=prepare(self.traces,[],RUBRIC,'fixture-v1','test_fixture')
        self.assertEqual(issues(empty),[]);self.assertEqual(empty.coverage['working'].unannotated,5)
    def test_family_split_identity_conflicts_rejected(self):
        for key,value in [('family_id','family-wrong'),('task_id','task-wrong')]:
            traces=copy.deepcopy(self.traces);traces[-1][key]=value
            with self.assertRaises(ValueError):prepare(traces,self.labels,RUBRIC,'fixture-v1','test_fixture')
    def test_invalid_prepared_coverage_and_real_export_contract(self):
        raw=self.triage.model_dump(mode='json');raw['coverage']['working']['annotated']=1
        with self.assertRaises(ValueError):Triage.model_validate(raw)
        raw=self.triage.model_dump(mode='json');raw['coverage']['working'].update(tasks=5,families=5,repeat_traces_beyond_tasks=0)
        with self.assertRaises(ValueError):Triage.model_validate(raw)
        from agent_improvement_lab.annotation import prepare as annotation_prepare, save, queue, export_private
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);trace=self.traces[0];snapshot=self.labels[0]
            (p/'traces.jsonl').write_text(json.dumps(trace)+'\n');(p/'rules.txt').write_text(RULES)
            db=p/'fixture-only.sqlite3';annotation_prepare(db,p/'traces.jsonl',p/'rules.txt')
            cid=queue(db)['cases'][0]['id'];label={k:v for k,v in snapshot['annotation'].items() if k not in ['revision','saved_at']}
            save(db,cid,dict(label,expected_revision=0))
            actual=export_private(db);triage=prepare([trace],actual,RUBRIC,'fixture-v1','test_fixture')
            self.assertEqual(triage.working_cases[0].snapshot.model_dump(),actual[0])

    def test_cli_roundtrip_reproducibility_and_nooverwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'traces.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in self.traces));(p/'human.json').write_text(json.dumps(self.labels));(p/'rules.txt').write_text(RULES)
            prefix=[sys.executable,'-m','agent_improvement_lab.failure_actions']
            command=prefix+['prepare','--traces',str(p/'traces.jsonl'),'--human',str(p/'human.json'),'--rubric',str(p/'rules.txt'),'--dataset-version','fixture-v1','--data-kind','test_fixture','--output-dir',str(p/'triage')]
            result=subprocess.run(command,capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)
            frozen=(p/'triage/triage.json').read_bytes();again=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(again.returncode,2);self.assertEqual((p/'triage/triage.json').read_bytes(),frozen)
            (p/'plan.json').write_text(json.dumps(self.plan))
            result=subprocess.run(prefix+['report','--triage',str(p/'triage/triage.json'),'--plan',str(p/'plan.json'),'--output-dir',str(p/'report')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);actual=json.loads((p/'report/report.json').read_text())
            self.assertEqual(actual,report(self.triage,self.plan));self.assertEqual((p/'report/report.md').read_text(),markdown(actual))
            self.assertEqual(len(list((p/'report/drafts').glob('*.md'))),3)

if __name__=='__main__':unittest.main()
