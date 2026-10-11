"""Manually checkable statistics and integrity checks; never real human labels."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from agent_improvement_lab.judge_metrics import Input, compare, prepare, markdown, LABELS
from agent_improvement_lab.annotation import digest

RUBRIC = hashlib.sha256(b'Fixture rules').hexdigest()
IDENTITY = {'version':'fixture-judge-v1','model':'mock-no-model-call','run_id':'fixture-run',
            'prompt_sha256':'1'*64,'config_sha256':'2'*64}


def fixture():
    cases=[]
    for i, h in enumerate(LABELS):
        for j, v in enumerate(LABELS):
            index=i*3+j
            cases.append({'trace_id':f'trace-{index}','task_id':f'task-{index}', 'family_id':f'family-{i}',
                          'split':'working','source_sha256':str(index)*64,'length_chars':[100,3000,17000][j],
                          'noise':['clean','noisy','unknown'][j],
                          'human':{'verdict':h,'assessor_id':'fixture-only','revision':1},
                          'judge':{'verdict':v,'assessor_id':'fixture-only-judge'}})
    for i,(human,judge) in enumerate([(True,False),(False,True),(False,False)]):
        c=copy.deepcopy(cases[0]);c.update(trace_id=f'missing-{i}',task_id=f'missing-task-{i}',family_id=f'missing-family-{i}')
        c['human']=c['human'] if human else None;c['judge']=c['judge'] if judge else None;cases.append(c)
    return {'format':'judge-validation-input-v1','data_kind':'test_fixture','dataset_version':'manual-12-v1',
            'agent_version':'mock-agent-v1','rubric_sha256':RUBRIC,'judge_identity':IDENTITY,'cases':cases}


def adapter_fixture():
    trace={'id':'t1','task_id':'task1','family_id':'family1','split':'working','agent_version':'a1',
           'messages':[{'step_id':'s1','role':'user','content':'Request'},{'step_id':'s2','role':'assistant','content':'Done'}]}
    from agent_improvement_lab.schemas import Trace
    fingerprint=digest(Trace.model_validate(trace).model_dump(mode='json'))
    human={'trace_id':'t1','task_id':'task1','family_id':'family1','split':'working','source_sha256':fingerprint,
           'rubric_sha256':RUBRIC,'assessor_kind':'human','original_evidence_steps':['s2'],
           'annotation':{'verdict':'incorrect','annotator':'fixture-only','revision':2,'comment':'Fixture','scenario_problem':False}}
    judge={'format':'judge-assessments-v1','judge_identity':copy.deepcopy(IDENTITY),'assessments':[
        {'id':'judge1','trace_id':'t1','verdict':'correct','assessor_kind':'llm','assessor_id':'fixture',
         'rubric_version':RUBRIC,'evidence_steps':['s2'],'metadata':{'source_sha256':fingerprint,'judge_identity':copy.deepcopy(IDENTITY)}}]}
    return [trace],[human],judge


class MetricsChecks(unittest.TestCase):
    def test_manual_matrix_and_denominators(self):
        r=compare(fixture(),samples=100);o=r['overall'];c=o['counts']
        self.assertEqual(o['confusion']['matrix'],[[1,1,1]]*3)
        self.assertEqual((c['population'],c['human_present'],c['judge_present'],c['paired']),(12,10,10,9))
        self.assertEqual((c['human_missing'],c['judge_missing'],c['both_assessable']),(2,2,4))
        self.assertEqual(c['human_unassessable'],3);self.assertEqual(c['judge_unassessable'],3)
        self.assertAlmostEqual(o['paired_exact_agreement']['value'],1/3)
        self.assertEqual(o['both_assessable_agreement'],{'value':.5,'denominator':4})
        for v in o['per_class'].values():
            self.assertEqual(v['support'],3);self.assertEqual(v['predicted'],3)
            for metric in ['precision','recall','f1']:self.assertAlmostEqual(v[metric],1/3)
        self.assertEqual(r['slices']['split']['heldout']['counts']['population'],0)
    def test_undefined_and_f1_zero_are_distinct(self):
        f=fixture();f['cases']=f['cases'][:1];f['cases'][0]['judge']['verdict']='incorrect'
        p=compare(f,samples=100)['overall']['per_class']
        self.assertIsNone(p['correct']['precision']);self.assertEqual(p['correct']['recall'],0);self.assertEqual(p['correct']['f1'],0)
        self.assertEqual(p['incorrect']['precision'],0);self.assertIsNone(p['incorrect']['recall']);self.assertEqual(p['incorrect']['f1'],0)
        self.assertIsNone(p['unassessable']['f1'])
    def test_all_missing_is_not_agreement_or_abstention(self):
        f=fixture()
        for c in f['cases']:c['human']=c['judge']=None
        r=compare(f,samples=100);self.assertEqual(r['overall']['counts']['human_unassessable'],0)
        self.assertIsNone(r['overall']['paired_exact_agreement']['value'])
        self.assertEqual(r['uncertainty']['status'],'insufficient_paired_families')
        self.assertEqual(len(r['missing_labels']),12)
    def test_all_unassessable_and_agreement_extremes(self):
        for same in [True,False]:
            f=fixture();f['cases']=f['cases'][:9]
            for c in f['cases']:
                c['human']['verdict']='unassessable';c['judge']['verdict']='unassessable' if same else 'correct'
            r=compare(f,samples=100);self.assertEqual(r['overall']['paired_exact_agreement']['value'],int(same))
            self.assertEqual(r['overall']['counts']['both_assessable'],0)
            self.assertTrue(r['uncertainty']['metrics']['paired_exact_agreement']['degenerate'])
    def test_family_bootstrap_keeps_correlated_repeats(self):
        f=fixture();f['cases']=f['cases'][:2]
        f['cases'][0]['family_id']='good';f['cases'][0]['judge']['verdict']='correct'
        f['cases'][1]['family_id']='bad';f['cases'][1]['judge']['verdict']='incorrect'
        r1=compare(f,samples=400,seed=123)
        repeated=copy.deepcopy(f)
        for c in copy.deepcopy(f['cases']):
            c['trace_id']+='-repeat';repeated['cases'].append(c)
        r2=compare(repeated,samples=400,seed=123)
        self.assertEqual(r1['uncertainty'],r2['uncertainty'])
        self.assertEqual(r2['overall']['counts']['unique_tasks'],2)
        self.assertEqual(r2['overall']['counts']['paired_families'],2)
        self.assertEqual(r1,compare(f,samples=400,seed=123))
    def test_one_family_has_no_interval_and_unequal_weights_explicit(self):
        f=fixture();f['cases']=f['cases'][:3]
        r=compare(f,samples=100);self.assertIsNone(r['uncertainty']['metrics']['paired_exact_agreement']['interval'])
        f['cases'][1]['human']['verdict']=f['cases'][1]['judge']['verdict']='correct'
        f['cases'][2]['family_id']='other';f['cases'][2]['human']['verdict']='correct';f['cases'][2]['judge']['verdict']='incorrect'
        r=compare(f,samples=100)['overall']
        self.assertAlmostEqual(r['paired_exact_agreement']['value'],2/3)
        self.assertEqual(r['equal_family_agreement']['value'],.5)
    def test_reject_invalid_population_versions_and_slice_data(self):
        mutations=[lambda f:f['cases'].append(copy.deepcopy(f['cases'][0])),
                   lambda f:f['cases'][1].update(split='heldout'),
                   lambda f:f['cases'][0].update(noise='guessed'),
                   lambda f:f['cases'][0]['human'].update(revision=None),
                   lambda f:f['cases'][0]['judge'].update(revision=1),
                   lambda f:f.update(cases=[])]
        for mutation in mutations:
            f=fixture();mutation(f)
            with self.assertRaises(ValueError):Input.model_validate(f)
    def test_adapter_full_inventory_and_latest_revision(self):
        traces,human,judge=adapter_fixture();second=copy.deepcopy(traces[0]);second['id']='t2';traces.append(second)
        data=prepare(traces,human,judge,RUBRIC,'dataset-v1','test_fixture')
        self.assertEqual(data.cases[0].human.revision,2);self.assertIsNone(data.cases[1].human)
        r=compare(data,samples=100);self.assertEqual(r['overall']['counts']['human_missing'],1)
        self.assertEqual(len(r['disagreements']),1)
    def test_adapter_rejects_integrity_and_duplicate_predictions(self):
        for side,key,value in [('human','source_sha256','3'*64),('human','rubric_sha256','4'*64),('human','task_id','wrong'),
                               ('human','assessor_kind','generator'),('judge','rubric_version','5'*64),('judge','assessor_kind','deterministic')]:
            traces,human,judge=adapter_fixture();row=human[0] if side=='human' else judge['assessments'][0];row[key]=value
            with self.subTest(side=side,key=key),self.assertRaises(ValueError):prepare(traces,human,judge,RUBRIC,'v1','test_fixture')
        traces,human,judge=adapter_fixture();judge['assessments'].append(copy.deepcopy(judge['assessments'][0]))
        with self.assertRaises(ValueError):prepare(traces,human,judge,RUBRIC,'v1','test_fixture')
        traces,human,judge=adapter_fixture();judge['judge_identity']['version']='new'
        with self.assertRaises(ValueError):prepare(traces,human,judge,RUBRIC,'v1','test_fixture')
    def test_all_labels_preserve_provenance_even_agreeing_and_unpaired(self):
        f=fixture()
        for c in f['cases']:
            if c['human'] and c['judge']:c['judge']['verdict']=c['human']['verdict']
        r=compare(f,samples=100)
        self.assertEqual(r['disagreements'],[])
        self.assertEqual(sum(c['human'] is not None for c in r['label_inventory']),10)
        self.assertEqual(sum(c['judge'] is not None for c in r['label_inventory']),10)
        self.assertTrue(all(c['human']['revision']==1 for c in r['label_inventory'] if c['human']))
        self.assertIn('fixture-only',markdown(r));self.assertIn('missing-0',markdown(r))
    def test_scenario_evidence_and_reason_preserved_and_source_checked(self):
        traces,human,judge=adapter_fixture()
        human[0]['annotation'].update(scenario_problem=True, scenario_comment='Authored input problem',error_types=['wrong_route'])
        human[0]['original_scenario_evidence']=['s1']
        data=prepare(traces,human,judge,RUBRIC,'fixture-v1','test_fixture')
        r=compare(data,samples=100);h=r['label_inventory'][0]['human']
        self.assertEqual(h['scenario_evidence'],['s1']);self.assertEqual(h['scenario_comment'],'Authored input problem')
        self.assertEqual(h['error_types'],['wrong_route']);self.assertIn('Authored input problem',markdown(r))
        human[0]['original_scenario_evidence']=['missing-step']
        with self.assertRaises(ValueError):prepare(traces,human,judge,RUBRIC,'fixture-v1','test_fixture')

    def test_report_provenance_and_cli_no_overwrite(self):
        f=fixture();r=compare(f,samples=100);text=markdown(r)
        self.assertIn('descriptive only',text);self.assertIn('only defined draws',text);self.assertIn('TEST_FIXTURE',text);self.assertIn('no human gold',text);self.assertIn('Rows: human. Columns: judge.',text)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);inputfile=path/'input.json';inputfile.write_text(json.dumps(f));target=path/'report'
            import sys
            command=[sys.executable,'-m','agent_improvement_lab.judge_metrics','compare','--input',str(inputfile),'--output-dir',str(target),'--resamples','100']
            result=subprocess.run(command,capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stderr)
            before=(target/'report.json').read_bytes()
            again=subprocess.run(command,capture_output=True,text=True);self.assertEqual(again.returncode,2)
            self.assertEqual((target/'report.json').read_bytes(),before)

if __name__=='__main__':unittest.main()
