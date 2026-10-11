"""Export an allowlisted NovaDocs fixture into a dependency-free browser demo."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
ASSETS=['index.html','app.js','style.css','guided.html','pilot.html','showcase.css','family.js','story.js','results.js']
FAMILY_ASSETS=['family-experiment.json','family-examples.json','family-stability.json','family-transfer.json','story-data.json']
REPLAY_ASSETS=['curated-experiment.json','curated-story.json','curated-transfer.json']
CATALOG_ASSETS=['catalog.html','catalog.js']
catalog_spec=importlib.util.spec_from_file_location('public_catalog_boundary',ROOT/'web_demo/build_catalog.py')
catalog_builder=importlib.util.module_from_spec(catalog_spec);catalog_spec.loader.exec_module(catalog_builder)
experiment_spec=importlib.util.spec_from_file_location('saved_experiment_boundary',ROOT/'web_demo/build_experiment.py')
experiment_builder=importlib.util.module_from_spec(experiment_spec);experiment_spec.loader.exec_module(experiment_builder)
EXPERIMENT_ASSETS=['experiment.html','experiment.js']
story_spec=importlib.util.spec_from_file_location('recorded_story_boundary',ROOT/'web_demo/build_story.py')
story_builder=importlib.util.module_from_spec(story_spec);story_spec.loader.exec_module(story_builder)
replay_spec=importlib.util.spec_from_file_location('illustrative_replay_boundary',ROOT/'web_demo/build_replay.py')
replay_builder=importlib.util.module_from_spec(replay_spec);replay_spec.loader.exec_module(replay_builder)


def read(path):return json.loads(Path(path).read_text())
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(fixture,output):
    fixture=Path(fixture).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Choose a fresh demo output directory')
    summary=read(fixture/'SUMMARY.json');provenance=read(fixture/'inputs/provenance.json')
    if summary['mode']!='authored_fixture_replay' or summary['model_calls']!=0 or summary['real_human_labels']!=0 or provenance['kind']!='authored_test_fixture':raise ValueError('Only the explicitly authored NovaDocs fixture is allowed')
    if summary['queue']['reviewed']!=0:raise ValueError('Real annotation decisions must not be exported')
    reports={arm:read(fixture/f'criteria-{arm}/report.json') for arm in ['baseline','candidate']}
    traces=[];decisions=[]
    for arm,report in reports.items():
        for trace in report['traces']:
            if trace['provenance']!='authored_test_fixture':raise ValueError('Nonfixture trace')
            traces.append({key:trace[key] for key in ['id','task_id','family_id','split','agent_version','language','messages']}|{'arm':arm,'repeat_id':trace['metadata']['repeat_id'],'provenance':'authored_test_fixture'})
        for result in report['results']:
            decisions.append({key:result[key] for key in ['trace_id','criterion_id','status','verdict','evidence_steps','explanation']}|{'origin':'python_deterministic_check' if result['criterion_id']=='citation_sources' else 'saved_authored_semantic_decision'})
    validation=read(fixture/'workflow/judge-validation.json')
    references=[{'trace_id':row['trace_id'],'family_id':row['family_id'],'reference':row['human']['verdict'],'judge':row['judge']['verdict'],'explanation':row['human']['explanation'],'evidence_steps':row['human']['evidence_steps'],'origin':'shared_authored_fixture_no_human_gold'} for row in validation['label_inventory']]
    action=read(fixture/'workflow/actions.json')['actions'][0]
    allowed_action=['id','title','intervention','recipient','observed_summary','cause_hypothesis','cause_status','proposed_change','verification','support_counts']
    action_data={key:action[key] for key in allowed_action}
    action_data['evidence']=[{key:item['issue'][key] for key in ['id','trace_id','family_id','observation','evidence_steps']} for item in action['evidence']]
    action_data['origin']='authored_product_action_not_applied'
    inputs=['SUMMARY.json','inputs/provenance.json','criteria-baseline/report.json','criteria-candidate/report.json','criteria-baseline/metrics.json','criteria-candidate/metrics.json','criteria-comparison.json','workflow/judge-validation.json','workflow/actions.json','workflow/comparison.json']
    data={'format':'agent-improvement-web-demo-v1','dataset_version':provenance['dataset_version'],'provenance':{'kind':'authored_test_fixture','model_calls':0,'real_human_labels':0,'candidate':'prescribed replay; intervention not applied','semantic_decisions':'saved authored decisions, not live model judgments','reference':'shared authored source, not human gold','repeats':'identical authored copies, not repeated model observations','external_sources':[]},'population':{'traces':16,'tasks':4,'families':4,'repeats_per_arm':2,'split':'working','heldout_families':0},'traces':traces,'criteria':reports['baseline']['pack']['criteria'],'decisions':decisions,'references':references,'action':action_data,'expected':{'metrics':{arm:read(fixture/f'criteria-{arm}/metrics.json') for arm in reports},'criteria_comparison':read(fixture/'criteria-comparison.json'),'overall_pairs':read(fixture/'workflow/comparison.json')['working_pairs'],'reference_confusion':validation['overall']['confusion']['matrix']},'input_hashes':{name:digest(fixture/name) for name in inputs},'generation':{'version':'novadocs-variants-v1','kind':'authored_template_variants','does_not_create_traces_or_scores':True,'independence':'variants retain source family; not independent validation tasks'}}
    text=json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    for forbidden in ['/Users/','C:\\Users\\','annotations.sqlite3','checkpoints.sqlite3']:
        if forbidden in text:raise ValueError('Private or machine-specific field in public export')
    catalog=catalog_builder.validate_catalog(read(ROOT/'web_demo/catalog-fixture.json'))
    catalog_text=json.dumps(catalog,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    experiment=experiment_builder.validate(read(ROOT/'web_demo/experiment-fixture.json'))
    experiment_text=json.dumps(experiment,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    family_binding=read(ROOT/'web_demo/family-transfer.json')
    if set(family_binding['files'])!=set(FAMILY_ASSETS)-{'family-transfer.json'} or any(digest(ROOT/'web_demo'/name)!=sha for name,sha in family_binding['files'].items()):raise ValueError('Recorded family data binding changed')
    family=read(ROOT/'web_demo/family-experiment.json');examples=read(ROOT/'web_demo/family-examples.json')
    story_builder.validate(family,examples)
    story=read(ROOT/'web_demo/story-data.json');story_builder.validate_projection(family,story,examples)
    if story['source_fixture_sha256']!=digest(ROOT/'web_demo/family-experiment.json'):raise ValueError('Story belongs to another experiment')
    if examples['experiment_fixture_sha256']!=digest(ROOT/'web_demo/family-experiment.json'):raise ValueError('Illustrations belong to another experiment')
    output.mkdir(parents=True)
    for name in ASSETS+CATALOG_ASSETS+EXPERIMENT_ASSETS+FAMILY_ASSETS:shutil.copyfile(ROOT/'web_demo'/name,output/name)
    replay_binding,_=replay_builder.build(ROOT/'web_demo',ROOT/'web_demo/replay-plan.json',output)
    (output/'demo-data.json').write_text(text)
    (output/'demo-data.js').write_text('window.AIL_DEMO_DATA = '+text.strip().replace('<','\\u003c')+';\n')
    (output/'catalog-data.json').write_text(catalog_text)
    (output/'catalog-data.js').write_text('window.AIL_CATALOG_DATA = '+catalog_text.strip().replace('<','\\u003c')+';\n')
    (output/'experiment-data.json').write_text(experiment_text)
    (output/'experiment-data.js').write_text('window.AIL_EXPERIMENT_DATA = '+experiment_text.strip().replace('<','\\u003c')+';\n')
    manifest={'kind':'unpublished_static_demo','files':{p.name:digest(p) for p in sorted(output.iterdir())},'model_calls':0,'real_human_labels':0,'saved_model_requests':experiment['provenance']['original_model_requests'],'saved_model_request_scope':'earlier delivery pilot only','includes_saved_experiment':True,'includes_family_experiment':True,'saved_family_records':len(family['traces']),'family_units':family_binding['families'],'license_and_hosting':'pending separate decision'}
    manifest.update(includes_curated_replay=True,curated_authored_traces=replay_binding['authored_traces'],curated_recorded_traces=replay_binding['recorded_traces'],curated_inferential_result=False)
    (output/'DEMO-MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fixture-run',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    try:print(json.dumps(export(args.fixture_run,args.output),indent=2))
    except (ValueError,KeyError) as error:parser.exit(2,str(error)+'\n')
